#!/usr/bin/env python3
"""Coleta em segundo plano e expõe progresso; sem endpoints de negociação."""
import datetime as dt,glob,http.server,json,os,pathlib,threading,time,traceback,urllib.parse
from download_history import run
from spot_backtest_v4 import test
from spot_backtest_v5 import test as test_v5
DATA=pathlib.Path(os.environ.get("CRYPTO_DATA_DIR","/data/crypto"))
START=os.environ.get("CRYPTO_START","2021-11")
END=os.environ.get("CRYPTO_END","2026-10")
PORT=int(os.environ.get("PORT","8080"))
status={"phase":"starting","start":START,"end":END,"backtest":"idle"}
lock=threading.Lock()
last_run=[0.0]
experiment_status={"state":"idle","progress_pct":0}
experiment_lock=threading.Lock()
experiment_last_run=[0.0]
def collect():
 try:
  status["phase"]="downloading"
  old=DATA/"manifest.json"
  canonical=DATA/"BTCUSDT-manifest.json"
  if old.exists() and not canonical.exists(): canonical.write_bytes(old.read_bytes())
  run(START,END,DATA,"BTCUSDT")
  manifest=DATA/"BTCUSDT-manifest.json"
  records=json.loads(manifest.read_text()) if manifest.exists() else []
  expected=(int(END[:4])-int(START[:4]))*12+int(END[5:])-int(START[5:])+1
  status.update(phase="complete" if len(records)==expected else "partial",months=len(records),expected_months=expected,candles=sum(r["rows"] for r in records))
  if len(records)==expected:
   status["backtest"]="idle"
 except Exception as e:
  status.update(phase="error",error=str(e));traceback.print_exc()
def execute_backtest():
 try:
  status.update(backtest="running",backtest_robot_completed=0,backtest_progress_pct=0,backtest_error=None)
  result=test(lambda k,pct: status.update(backtest_robot_completed=max(0,int(pct//25)),backtest_progress_pct=round(pct,1),backtest_current_robot=k))
  report=DATA/"backtest-results.json"
  previous=DATA/"backtest-v3-results.json"
  if report.exists() and not previous.exists():
   old=json.loads(report.read_text())
   if old.get("version","").startswith("spot-v3"):previous.write_bytes(report.read_bytes())
  tmp=DATA/"backtest-results.tmp"
  tmp.write_text(json.dumps(result,indent=2,ensure_ascii=False))
  tmp.replace(report)
  status.update(backtest="complete",backtest_progress_pct=100,backtest_robot_completed=4,backtest_finished_at=dt.datetime.now(dt.timezone.utc).isoformat())
  print("V4 BACKTEST COMPLETE: 4 robots, 60 months",flush=True)
 except Exception as e:
  status.update(backtest="error",backtest_error=str(e))
  traceback.print_exc()
def start_backtest():
 with lock:
  if status.get("phase")!="complete":return 409,{"error":"Histórico ainda não está pronto"}
  if status.get("backtest")=="running":return 409,{"error":"Já existe um teste em execução"}
  if time.monotonic()-last_run[0]<3600:return 429,{"error":"Aguarde 60 minutos entre execuções"}
  last_run[0]=time.monotonic()
  status.update(backtest="running",backtest_robot_completed=0)
  threading.Thread(target=execute_backtest,daemon=True).start()
  return 202,{"status":"running","message":"Backtest V4 iniciado"}
def execute_experiment(robot,settings):
 try:
  def progress(k,pct):
   experiment_status.update(progress_pct=round(pct,1),current_robot=k)
  result=test(progress,robot_ids=[robot],settings=settings)
  result["experiment_created_at"]=dt.datetime.now(dt.timezone.utc).isoformat()
  folder=DATA/"experiments"
  folder.mkdir(parents=True,exist_ok=True)
  identifier=dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%S%f")
  output=folder/(identifier+".json")
  tmp=folder/(identifier+".tmp")
  tmp.write_text(json.dumps(result,ensure_ascii=False,indent=2))
  tmp.replace(output)
  experiment_status.update(state="complete",progress_pct=100,result_id=identifier,error=None)
  print("EXPERIMENT COMPLETE robot",robot,identifier,flush=True)
 except Exception as e:
  experiment_status.update(state="error",error=str(e))
  traceback.print_exc()
def execute_batch(robot,variants):
 try:
  folder=DATA/"experiments"
  folder.mkdir(parents=True,exist_ok=True)
  completed=[]
  for idx,settings in enumerate(variants):
   experiment_status.update(batch_current=idx+1,progress_pct=round(idx/len(variants)*100,1))
   def progress(k,pct):
    experiment_status.update(progress_pct=round((idx+pct/100)/len(variants)*100,1),current_robot=k)
   result=test(progress,robot_ids=[robot],settings=settings)
   result["experiment_created_at"]=dt.datetime.now(dt.timezone.utc).isoformat()
   result["batch_index"]=idx+1
   result["batch_total"]=len(variants)
   identifier=dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%S%f")
   tmp=folder/(identifier+".tmp")
   tmp.write_text(json.dumps(result,ensure_ascii=False,indent=2))
   tmp.replace(folder/(identifier+".json"))
   completed.append(identifier)
  experiment_status.update(state="complete",progress_pct=100,batch_completed=len(completed),result_ids=completed,error=None)
  print("BATCH COMPLETE",robot,len(completed),flush=True)
 except Exception as e:
  experiment_status.update(state="error",error=str(e))
  traceback.print_exc()
def execute_windows(robot,settings):
 try:
  result=test(lambda k,p:experiment_status.update(progress_pct=round(p,1),current_robot=k),robot_ids=[robot],settings=settings,windows="five",diagnostics=True)
  result["created_at"]=dt.datetime.now(dt.timezone.utc).isoformat()
  folder=DATA/"windows"
  folder.mkdir(parents=True,exist_ok=True)
  name=dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%S%f")
  tmp=folder/(name+".tmp")
  tmp.write_text(json.dumps(result,ensure_ascii=False,indent=2))
  tmp.replace(folder/(name+".json"))
  experiment_status.update(state="complete",progress_pct=100,result_id=name,error=None)
  print("FIVE WINDOWS COMPLETE",robot,name,flush=True)
 except Exception as e:
  experiment_status.update(state="error",error=str(e))
  traceback.print_exc()
def execute_v5():
 try:
  result=test_v5(lambda k,p:experiment_status.update(progress_pct=round(p,1),current_robot=k),windows="five",diagnostics=True)
  result["created_at"]=dt.datetime.now(dt.timezone.utc).isoformat()
  folder=DATA/"v5"
  folder.mkdir(parents=True,exist_ok=True)
  identifier=dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%S%f")
  tmp=folder/(identifier+".tmp")
  tmp.write_text(json.dumps(result,ensure_ascii=False,indent=2))
  tmp.replace(folder/(identifier+".json"))
  experiment_status.update(state="complete",progress_pct=100,result_id=identifier,error=None)
  print("V5 RESEARCH COMPLETE",identifier,flush=True)
 except Exception as e:
  experiment_status.update(state="error",error=str(e))
  traceback.print_exc()
def start_v5():
 with experiment_lock:
  if status.get("phase")!="complete":return 409,{"error":"Histórico ainda não está pronto"}
  if experiment_status.get("state")=="running" or status.get("backtest")=="running":return 409,{"error":"Já existe um teste em execução"}
  if time.monotonic()-experiment_last_run[0]<60:return 429,{"error":"Aguarde um minuto entre testes"}
  experiment_last_run[0]=time.monotonic()
  experiment_status.update(state="running",mode="v5",progress_pct=0,current_robot=5,error=None)
  threading.Thread(target=execute_v5,daemon=True).start()
  return 202,{"status":"running","message":"V5: três estratégias, cinco janelas cada"}
def start_windows(payload):
 try:
  robot=int(payload.get("robot"))
  if robot not in (1,2,3,4):raise ValueError("Robô inválido")
  bounds={"stop_pct":(0.5,5.),"min_edge_pct":(0.1,3.),"cooldown_minutes":(30,1440),"max_hold_minutes":(60,2880)}
  settings={}
  for key,(lo,hi) in bounds.items():
   value=float(payload[key])
   if not lo<=value<=hi:raise ValueError(key+" fora dos limites")
   if key.endswith("minutes"):
    if not value.is_integer():raise ValueError(key+" deve ser inteiro")
    value=int(value)
   settings[key]=value
 except (ValueError,TypeError,KeyError,OverflowError) as e:return 400,{"error":"Parâmetros inválidos: "+str(e)}
 with experiment_lock:
  if status.get("phase")!="complete":return 409,{"error":"Histórico ainda não está pronto"}
  if experiment_status.get("state")=="running" or status.get("backtest")=="running":return 409,{"error":"Já existe um teste em execução"}
  if time.monotonic()-experiment_last_run[0]<60:return 429,{"error":"Aguarde um minuto entre testes"}
  experiment_last_run[0]=time.monotonic()
  experiment_status.update(state="running",mode="windows",progress_pct=0,current_robot=robot,error=None)
  threading.Thread(target=execute_windows,args=(robot,settings),daemon=True).start()
  return 202,{"status":"running","message":"Análise cronológica de cinco janelas iniciada"}
def start_batch(payload):
 try:
  robot=int(payload.get("robot"))
  if robot not in (1,2,3,4):raise ValueError("Robô inválido")
  variants=payload.get("variants")
  if not isinstance(variants,list) or len(variants)!=3:raise ValueError("São necessárias 3 configurações")
  bounds={"stop_pct":(0.5,5.),"min_edge_pct":(0.1,3.),"cooldown_minutes":(30,1440),"max_hold_minutes":(60,2880)}
  validated=[]
  for item in variants:
   if not isinstance(item,dict):raise ValueError("Configuração inválida")
   settings={}
   for key,(lo,hi) in bounds.items():
    value=float(item[key])
    if not lo<=value<=hi:raise ValueError(key+" fora dos limites")
    if key.endswith("minutes"):
     if not value.is_integer():raise ValueError(key+" deve ser inteiro")
     value=int(value)
    settings[key]=value
   validated.append(settings)
  if len({json.dumps(x,sort_keys=True) for x in validated})!=3:raise ValueError("Configurações devem ser distintas")
 except (ValueError,TypeError,KeyError,OverflowError) as e:return 400,{"error":"Parâmetros inválidos: "+str(e)}
 with experiment_lock:
  if status.get("phase")!="complete":return 409,{"error":"Histórico ainda não está pronto"}
  if experiment_status.get("state")=="running" or status.get("backtest")=="running":return 409,{"error":"Já existe um teste em execução"}
  if time.monotonic()-experiment_last_run[0]<60:return 429,{"error":"Aguarde um minuto entre baterias"}
  experiment_last_run[0]=time.monotonic()
  experiment_status.update(state="running",mode="batch",batch_total=3,batch_current=1,progress_pct=0,current_robot=robot,error=None)
  threading.Thread(target=execute_batch,args=(robot,validated),daemon=True).start()
  return 202,{"status":"running","message":"Bateria de 3 testes iniciada"}
def start_experiment(payload):
 try:
  robot=int(payload.get("robot"))
  if robot not in (1,2,3,4):raise ValueError("Robô inválido")
  bounds={"stop_pct":(0.5,5.),"min_edge_pct":(0.1,3.),"cooldown_minutes":(30,1440),"max_hold_minutes":(60,2880)}
  settings={}
  for key,(lo,hi) in bounds.items():
   value=float(payload[key])
   if not lo<=value<=hi:raise ValueError(key+" fora dos limites")
   if key.endswith("minutes"):
    if not value.is_integer():raise ValueError(key+" deve ser inteiro")
    value=int(value)
   settings[key]=value
 except (KeyError,ValueError,TypeError) as e:return 400,{"error":"Parâmetros inválidos: "+str(e)}
 with experiment_lock:
  if status.get("phase")!="complete":return 409,{"error":"Histórico ainda não está pronto"}
  if experiment_status.get("state")=="running" or status.get("backtest")=="running":return 409,{"error":"Já existe um backtest em execução"}
  if time.monotonic()-experiment_last_run[0]<60:return 429,{"error":"Aguarde um minuto entre experimentos"}
  experiment_last_run[0]=time.monotonic()
  experiment_status.update(state="running",mode="single",progress_pct=0,current_robot=robot,result_id=None,error=None)
  threading.Thread(target=execute_experiment,args=(robot,settings),daemon=True).start()
  return 202,{"status":"running","message":"Experimento iniciado"}
class Handler(http.server.BaseHTTPRequestHandler):
 def do_POST(self):
  path=urllib.parse.urlsplit(self.path).path
  if path not in ("/run-backtest","/run-experiment","/run-batch","/run-windows","/run-v5"):self.send_error(404);return
  if path=="/run-v5":code,payload=start_v5()
  elif path in ("/run-experiment","/run-batch","/run-windows"):
   try:
    length=int(self.headers.get("Content-Length","0"))
    if length<2 or length>4096:raise ValueError("Tamanho inválido")
    request=json.loads(self.rfile.read(length))
    if not isinstance(request,dict):raise ValueError("Formato inválido")
   except (ValueError,json.JSONDecodeError):
    self.send_error(400);return
   code,payload=start_batch(request) if path=="/run-batch" else start_windows(request) if path=="/run-windows" else start_experiment(request)
  else:code,payload=start_backtest()
  body=json.dumps(payload).encode()
  self.send_response(code);self.send_header("Content-Type","application/json");self.send_header("Content-Length",str(len(body)));self.end_headers();self.wfile.write(body)
 def do_GET(self):
  path=urllib.parse.urlsplit(self.path).path
  if path not in ("/","/health","/status","/backtest","/backtest-v2","/backtest-v3","/experiment-status","/experiments","/windows","/v5"):
   self.send_error(404);return
  if path=="/":
   body=(pathlib.Path(__file__).parent/"dashboard.html").read_bytes()
   self.send_response(200);self.send_header("Content-Type","text/html; charset=utf-8");self.send_header("Content-Length",str(len(body)));self.end_headers();self.wfile.write(body);return
  manifest=DATA/"BTCUSDT-manifest.json"
  records=json.loads(manifest.read_text()) if manifest.exists() else []
  payload={**status,"months_verified":len(records),"candles_verified":sum(x["rows"] for x in records),"gap_events":sum(len(x.get("gaps",[])) for x in records),"last_month":records[-1]["month"] if records else None,"note":"Historical research only; no live trading"}
  if path=="/experiment-status":payload=experiment_status.copy()
  if path=="/v5":
   folder=DATA/"v5"
   items=sorted(folder.glob("*.json"),reverse=True)[:3] if folder.exists() else []
   payload={"runs":[{"id":p.stem,**json.loads(p.read_text())} for p in items]}
  if path=="/windows":
   folder=DATA/"windows"
   items=sorted(folder.glob("*.json"),reverse=True)[:8] if folder.exists() else []
   payload={"runs":[{"id":p.stem,**json.loads(p.read_text())} for p in items]}
  if path=="/experiments":
   folder=DATA/"experiments"
   items=sorted(folder.glob("*.json"),reverse=True)[:12] if folder.exists() else []
   payload={"experiments":[{"id":p.stem,**json.loads(p.read_text())} for p in items]}
  if path in ("/backtest","/backtest-v2","/backtest-v3"):
   report=DATA/("backtest-v2-results.json" if path=="/backtest-v2" else "backtest-v3-results.json" if path=="/backtest-v3" else "backtest-results.json")
   payload=json.loads(report.read_text()) if report.exists() else {"status":status.get("backtest","pending"),"phase":status.get("phase"),"error":status.get("error")}
  body=json.dumps(payload).encode()
  self.send_response(200);self.send_header("Content-Type","application/json");self.send_header("Content-Length",str(len(body)));self.end_headers();self.wfile.write(body)
if __name__=="__main__":
 DATA.mkdir(parents=True,exist_ok=True)
 threading.Thread(target=collect,daemon=True).start()
 http.server.ThreadingHTTPServer(("0.0.0.0",PORT),Handler).serve_forever()
