"""Persistent, synthetic-only autonomous paper robots. No B3 feed, no real orders."""
import json, os, random, sqlite3, threading, time
from pathlib import Path
from datetime import datetime, timezone
DB=Path(os.environ.get("B3_DATA_DIR","/tmp"))/"b3_robots.sqlite3"
GUARD=threading.RLock()
NAMES=["Scalper","Tendência","Reversão","Conservador"]
def db():
    DB.parent.mkdir(parents=True,exist_ok=True)
    con=sqlite3.connect(DB,timeout=15)
    con.execute("CREATE TABLE IF NOT EXISTS robot_state (id INTEGER PRIMARY KEY, data TEXT NOT NULL)")
    return con
def initial():
    return {"symbol":"WIN","tick":0,"price":130000.0,"prices":[130000.0],"bots":[{"name":n,"status":"stopped","position":None,"realized":0.0,"count":0,"cooldown":0} for n in NAMES],"events":[],"last_update":None,"source":"synthetic","max_loss":100.0,"stop":30.0}
def load():
    with db() as con:
        row=con.execute("SELECT data FROM robot_state WHERE id=1").fetchone()
        if not row:
            state=initial();con.execute("INSERT INTO robot_state VALUES (1,?)",(json.dumps(state),));return state
        return json.loads(row[0])
def save(state):
    with db() as con:con.execute("INSERT OR REPLACE INTO robot_state VALUES (1,?)",(json.dumps(state),))
def log(s,msg):
    s["events"].insert(0,{"tick":s["tick"],"text":msg,"time":datetime.now(timezone.utc).isoformat()})
    s["events"]=s["events"][:100]
def specs(s):return (5.0,1.0) if s["symbol"]=="WIN" else (.5,5.0)
def pnl(s,b):
    p=b["position"]
    if not p:return 0.0
    t,v=specs(s);d=1 if p["side"]=="buy" else -1
    return round((s["price"]-d*t-p["entry"])/t*v*d,2)
def close(s,b,reason):
    if not b["position"]:return
    gain=pnl(s,b);b["realized"]=round(b["realized"]+gain,2);b["count"]+=1;b["position"]=None;b["cooldown"]=5
    log(s,b["name"]+" · "+reason+" · R$ "+format(gain,".2f"))
    if b["realized"]<=-s["max_loss"] or b["count"]>=30:
        b["status"]="stopped";log(s,b["name"]+" · limite de risco/entradas atingido")
def signal(s,i):
    p=s["prices"];t,_=specs(s)
    if len(p)<26:return None
    a=sum(p[-5:])/5;m=sum(p[-12:])/12;l=sum(p[-24:])/24;prior=p[-4];last=p[-1]
    if i==0:return "buy" if last-prior>3*t else "sell" if prior-last>3*t else None
    if i==1:return "buy" if a>m+t else "sell" if a<m-t else None
    if i==2:return "buy" if last<l-4*t else "sell" if last>l+4*t else None
    if a>m+2*t and m>l+t and last>prior+2*t:return "buy"
    if a<m-2*t and m<l-t and last<prior-2*t:return "sell"
    return None
def step(s):
    t,_=specs(s)
    s["price"]=max(t,s["price"]+random.choice((-3,-2,-1,1,2,3))*t)
    s["tick"]+=1;s["prices"].append(s["price"]);s["prices"]=s["prices"][-300:]
    for i,b in enumerate(s["bots"]):
        if b["status"]!="running":continue
        if b["position"]:
            pl=pnl(s,b)
            if pl<=-s["stop"]:close(s,b,"STOP")
            elif pl>=s["stop"]*1.5:close(s,b,"ALVO")
            elif s["tick"]-b["position"]["at"]>=16:close(s,b,"TEMPO")
        elif b["cooldown"]>0:b["cooldown"]-=1
        elif b["count"]>=30:b["status"]="stopped"
        else:
            sig=signal(s,i)
            if sig:
                b["position"]={"side":sig,"entry":s["price"]+(t if sig=="buy" else -t),"at":s["tick"]}
                log(s,b["name"]+" · "+("COMPRA" if sig=="buy" else "VENDA")+" · "+str(b["position"]["entry"]))
    s["last_update"]=datetime.now(timezone.utc).isoformat()
def public(s):
    out=json.loads(json.dumps(s))
    for b in out["bots"]:
        b["unrealized"]=pnl(s,b);b["total"]=round(b["realized"]+b["unrealized"],2)
    out["background"]=True;out["persistent"]=os.environ.get("B3_DATA_DIR") is not None
    return out
def command(action,robot=None,settings=None):
    with GUARD:
        s=load()
        if action=="reset":
            if any(b["status"]=="running" for b in s["bots"]):raise ValueError("Pare todos antes de reiniciar")
            s=initial()
        elif action=="configure":
            if any(b["status"]=="running" or b["position"] for b in s["bots"]):raise ValueError("Pare e zere posições antes de configurar")
            settings=settings or {}
            if settings.get("symbol") not in ("WIN","WDO"):raise ValueError("Ativo inválido")
            loss=float(settings.get("max_loss",100));stop=float(settings.get("stop",30))
            if not 1<=loss<=100000 or not 1<=stop<=100000:raise ValueError("Limites inválidos")
            s=initial();s["symbol"]=settings["symbol"];s["price"]=130000.0 if s["symbol"]=="WIN" else 5500.0;s["prices"]=[s["price"]];s["max_loss"]=loss;s["stop"]=stop
        elif action=="stop_all":
            for b in s["bots"]:
                if b["position"]:close(s,b,"PARADA GERAL")
                b["status"]="stopped"
        elif action in ("start","pause","stop"):
            if not isinstance(robot,int) or not 0<=robot<4:raise ValueError("Robô inválido")
            b=s["bots"][robot]
            if action=="start":
                if b["count"]>=30 or b["realized"]<=-s["max_loss"]:raise ValueError("Limite atingido. Reinicie a simulação")
                b["status"]="running"
            elif action=="pause":b["status"]="paused"
            else:
                if b["position"]:close(s,b,"PARADA MANUAL")
                b["status"]="stopped"
            log(s,b["name"]+" · "+action)
        else:raise ValueError("Ação inválida")
        save(s);return public(s)
def run():
    while True:
        time.sleep(1)
        try:
            with GUARD:
                s=load()
                if any(b["status"]=="running" for b in s["bots"]):step(s);save(s)
        except Exception as exc:print("Robot worker:",repr(exc),flush=True)
def start():
    db().close()
    threading.Thread(target=run,daemon=True,name="synthetic-robot-worker").start()
