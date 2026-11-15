#!/usr/bin/env python3
"""Coleta em segundo plano e expõe progresso; sem endpoints de negociação."""
import datetime as dt,glob,http.server,json,os,pathlib,threading,time,traceback,urllib.parse
from download_history import run
from spot_backtest_v4 import test
from spot_backtest_v5 import test as test_v5
from spot_backtest_v6 import test as test_v6
from cost_edge_audit import analyze as analyze_cost_edge
from trade_diagnostics import analyze as analyze_trade_diagnostics
from entry_quality import analyze as analyze_entry_quality
from v7_atr_walkforward import run as run_v7_atr
from paper_v7 import loop as paper_v7_loop, snapshot as paper_v7_snapshot, journal_page as paper_v7_journal_page
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
  if status.get("backtest")=="running" or experiment_status.get("state")=="running":return 409,{"error":"Já existe um teste em execução"}
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
def execute_v6():
 try:
  result=test_v6(lambda robot,pct:experiment_status.update(progress_pct=round(pct,1),current_robot=robot),windows="five",diagnostics=True)
  result["created_at"]=dt.datetime.now(dt.timezone.utc).isoformat()
  folder=DATA/"v6"
  folder.mkdir(parents=True,exist_ok=True)
  identifier=dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%S%f")
  tmp=folder/(identifier+".tmp")
  tmp.write_text(json.dumps(result,ensure_ascii=False,indent=2))
  tmp.replace(folder/(identifier+".json"))
  experiment_status.update(state="complete",progress_pct=100,result_id=identifier,error=None)
  print("V6 WALK FORWARD RESEARCH COMPLETE",identifier,flush=True)
 except Exception as e:
  experiment_status.update(state="error",error=str(e))
  traceback.print_exc()
def summarize_trade_ledger(trades,report,robot):
 windows=report["robots"][str(robot)]["windows"]
 per_window=[]
 for w in windows:
  window_trades=[t for t in trades if t["window"]==w["index"]]
  logged_net=sum(t["net_usdt"] for t in window_trades)
  if len(window_trades)!=w["trades"] or abs(logged_net-w["net_usdt"])>0.03:
   raise ValueError("Inconsistência entre operações e resultado da janela "+str(w["index"]))
  per_window.append({"index":w["index"],"trades":w["trades"],"net_usdt":w["net_usdt"],"fees_usdt":w["fees_usdt"],"max_drawdown_usdt":w["max_realized_drawdown_usdt"]})
 reasons={}
 regimes={}
 for t in trades:
  for source,key in ((reasons,t["exit_reason"]),(regimes,t["regime_on_entry"])):
   bucket=source.setdefault(key,{"trades":0,"net_usdt":0.0,"fees_usdt":0.0})
   bucket["trades"]+=1;bucket["net_usdt"]+=t["net_usdt"];bucket["fees_usdt"]+=t["commissions_usdt"]
 for source in (reasons,regimes):
  for bucket in source.values():
   bucket["net_usdt"]=round(bucket["net_usdt"],2)
   bucket["fees_usdt"]=round(bucket["fees_usdt"],2)
 wins=[t["net_usdt"] for t in trades if t["net_usdt"]>0]
 losses=[t["net_usdt"] for t in trades if t["net_usdt"]<=0]
 return {"robot":robot,"trades":len(trades),"winning_trades":len(wins),"losing_trades":len(losses),
         "net_usdt":round(sum(t["net_usdt"] for t in trades),2),
         "gross_before_costs_usdt":round(sum(t["gross_before_costs_usdt"] for t in trades),2),
         "commissions_usdt":round(sum(t["commissions_usdt"] for t in trades),2),
         "slippage_usdt":round(sum(t["slippage_usdt"] for t in trades),2),
         "avg_win_usdt":round(sum(wins)/len(wins),2) if wins else None,
         "avg_loss_usdt":round(sum(losses)/len(losses),2) if losses else None,
         "historical_net_per_trade_usdt":round(sum(t["net_usdt"] for t in trades)/len(trades),3) if trades else None,
         "profit_factor":round(sum(wins)/abs(sum(losses)),3) if sum(losses)<0 else None,
         "by_exit":reasons,"by_regime":regimes,"windows":per_window,
         "reconciled":True,"note":"Five independent USD 1000 historical windows; totals are not compounded."}
def execute_trade_ledger(robot):
 try:
  trades=[]
  report=test_v6(lambda k,p:experiment_status.update(progress_pct=round(p,1),current_robot=k),
                 robot_ids=[robot],windows="five",diagnostics=True,trade_log=trades)
  summary=summarize_trade_ledger(trades,report,robot)
  created=dt.datetime.now(dt.timezone.utc)
  identifier=created.strftime("%Y%m%dT%H%M%S%f")
  folder=DATA/"trade-ledger";folder.mkdir(parents=True,exist_ok=True)
  payload={"status":"complete","version":"v6-trade-ledger-1","robot":robot,
           "created_at":created.isoformat(),"source_engine":report["version"],
           "execution_assumptions":report["assumptions"],"summary":summary,"trades":trades}
  tmp=folder/("r"+str(robot)+"-"+identifier+".tmp")
  tmp.write_text(json.dumps(payload,ensure_ascii=False,separators=(",",":")))
  tmp.replace(folder/("r"+str(robot)+"-"+identifier+".json"))
  experiment_status.update(state="complete",progress_pct=100,result_id=identifier,error=None)
  print("TRADE LEDGER COMPLETE robot",robot,"trades",len(trades),"id",identifier,flush=True)
 except Exception as e:
  experiment_status.update(state="error",error=str(e))
  traceback.print_exc()
def execute_v7():
 try:
  source=latest_trade_ledger(9)
  if source is None:raise ValueError("Gere primeiro o histórico de operações do Robô 9")
  report=run_v7_atr(source,lambda robot,pct:experiment_status.update(progress_pct=round(pct,1),current_robot=robot))
  created=dt.datetime.now(dt.timezone.utc)
  report["created_at"]=created.isoformat()
  identifier=created.strftime("%Y%m%dT%H%M%S%f")
  folder=DATA/"v7";folder.mkdir(parents=True,exist_ok=True)
  tmp=folder/(identifier+".tmp")
  tmp.write_text(json.dumps(report,ensure_ascii=False,separators=(",",":")))
  tmp.replace(folder/(identifier+".json"))
  experiment_status.update(state="complete",progress_pct=100,result_id=identifier,error=None)
  print("V7 ATR EXECUTABLE COMPLETE",identifier,"trades",report["summary"]["trades"],flush=True)
 except Exception as e:
  experiment_status.update(state="error",error=str(e))
  traceback.print_exc()
def start_v7():
 with experiment_lock:
  if status.get("phase")!="complete":return 409,{"error":"Histórico BTC ainda não está pronto"}
  if experiment_status.get("state")=="running" or status.get("backtest")=="running":
   return 409,{"error":"Já existe um teste em execução"}
  if latest_trade_ledger(9) is None:
   return 409,{"error":"Gere as operações do Robô 9 antes do teste V7"}
  if time.monotonic()-experiment_last_run[0]<60:
   return 429,{"error":"Aguarde um minuto entre análises"}
  experiment_last_run[0]=time.monotonic()
  experiment_status.update(state="running",mode="v7",progress_pct=0,current_robot=9,error=None)
  threading.Thread(target=execute_v7,daemon=True).start()
  return 202,{"status":"running","message":"Reexecução ATR V7 iniciada"}
def start_trade_ledger(payload):
 try:
  robot=int(payload.get("robot"))
  if robot not in (8,9,10):raise ValueError("Escolha um robô V6: 8, 9 ou 10")
 except (TypeError,ValueError,AttributeError) as e:return 400,{"error":str(e)}
 with experiment_lock:
  if status.get("phase")!="complete":return 409,{"error":"Histórico ainda não está pronto"}
  if experiment_status.get("state")=="running" or status.get("backtest")=="running":return 409,{"error":"Já existe uma análise em execução"}
  if time.monotonic()-experiment_last_run[0]<60:return 429,{"error":"Aguarde um minuto entre análises"}
  experiment_last_run[0]=time.monotonic()
  experiment_status.update(state="running",mode="trade-ledger",progress_pct=0,current_robot=robot,error=None)
  threading.Thread(target=execute_trade_ledger,args=(robot,),daemon=True).start()
  return 202,{"status":"running","message":"Laboratório de operações iniciado","robot":robot}
def latest_trade_ledger(robot):
 folder=DATA/"trade-ledger"
 items=sorted(folder.glob("r"+str(robot)+"-*.json"),reverse=True)[:1] if folder.exists() else []
 return json.loads(items[0].read_text()) if items else None
def trade_ledger_page(query):
 try:
  robot=int(query.get("robot",["9"])[0])
  page=int(query.get("page",["1"])[0])
  window=int(query.get("window",["0"])[0])
  outcome=query.get("result",["all"])[0]
  reason=query.get("reason",["all"])[0]
  if robot not in (8,9,10) or page<1 or page>10000 or window not in range(6):
   raise ValueError("Parâmetros de consulta inválidos")
  if outcome not in ("all","win","loss"):raise ValueError("Resultado inválido")
  if reason not in ("all","stop_loss","take_profit","max_hold","window_end","data_gap"):
   raise ValueError("Motivo de saída inválido")
 except (TypeError,ValueError,IndexError) as e:return 400,{"error":str(e)}
 payload=latest_trade_ledger(robot)
 if not payload:return 200,{"status":"missing","message":"Execute o laboratório para gerar operações individuais.","robot":robot}
 trades=payload["trades"]
 filtered=[t for t in trades if
           (window==0 or t["window"]==window) and
           (outcome=="all" or (t["net_usdt"]>0 if outcome=="win" else t["net_usdt"]<=0)) and
           (reason=="all" or t["exit_reason"]==reason)]
 filtered.reverse()
 per_page=20
 start=(page-1)*per_page
 return 200,{"status":"complete","robot":robot,"created_at":payload["created_at"],"summary":payload["summary"],
             "total_filtered":len(filtered),"page":page,"per_page":per_page,
             "total_pages":max(1,(len(filtered)+per_page-1)//per_page),
             "filters":{"window":window,"result":outcome,"reason":reason},
             "trades":filtered[start:start+per_page]}
def start_v6():
 with experiment_lock:
  if status.get("phase")!="complete":return 409,{"error":"Histórico ainda não está pronto"}
  if experiment_status.get("state")=="running" or status.get("backtest")=="running":return 409,{"error":"Já existe uma análise em execução"}
  if time.monotonic()-experiment_last_run[0]<60:return 429,{"error":"Aguarde um minuto entre análises"}
  experiment_last_run[0]=time.monotonic()
  experiment_status.update(state="running",mode="v6",progress_pct=0,current_robot=8,error=None)
  threading.Thread(target=execute_v6,daemon=True).start()
  return 202,{"status":"running","message":"V6: três estratégias, cinco janelas e avaliação cronológica"}
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
  if path not in ("/run-backtest","/run-experiment","/run-batch","/run-windows","/run-v5","/run-v6","/run-trade-ledger","/run-v7"):self.send_error(404);return
  if path=="/run-v7":code,payload=start_v7()
  elif path=="/run-trade-ledger":
   try:
    length=int(self.headers.get("Content-Length","0"))
    if length<2 or length>1024:raise ValueError("Tamanho inválido")
    request=json.loads(self.rfile.read(length))
    if not isinstance(request,dict):raise ValueError("Formato inválido")
   except (ValueError,TypeError,json.JSONDecodeError):
    self.send_error(400);return
   code,payload=start_trade_ledger(request)
  elif path=="/run-v6":code,payload=start_v6()
  elif path=="/run-v5":code,payload=start_v5()
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
  if path not in ("/","/health","/status","/backtest","/backtest-v2","/backtest-v3","/experiment-status","/experiments","/windows","/v5","/v6","/cost-audit","/trade-ledger","/trade-diagnostics","/entry-quality","/v7","/paper-v7","/paper-v7-journal"):
   self.send_error(404);return
  if path=="/":
   body=(pathlib.Path(__file__).parent/"dashboard.html").read_bytes()
   self.send_response(200);self.send_header("Content-Type","text/html; charset=utf-8");self.send_header("Content-Length",str(len(body)));self.end_headers();self.wfile.write(body);return
  manifest=DATA/"BTCUSDT-manifest.json"
  records=json.loads(manifest.read_text()) if manifest.exists() else []
  payload={**status,"months_verified":len(records),"candles_verified":sum(x["rows"] for x in records),"gap_events":sum(len(x.get("gaps",[])) for x in records),"last_month":records[-1]["month"] if records else None,"note":"Historical research only; no live trading"}
  if path=="/experiment-status":payload=experiment_status.copy()
  if path=="/paper-v7":payload=paper_v7_snapshot()
  if path=="/paper-v7-journal":
   try:
    params=urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query)
    page=int(params.get("page",["1"])[0])
    size=int(params.get("size",["20"])[0])
    payload=paper_v7_journal_page(page_num=page,size=size)
   except (ValueError,TypeError,IndexError) as exc:
    body=json.dumps({"status":"error","error":str(exc)},ensure_ascii=False).encode()
    self.send_response(400);self.send_header("Content-Type","application/json")
    self.send_header("Content-Length",str(len(body)));self.end_headers();self.wfile.write(body);return
  if path=="/v7":
   folder=DATA/"v7"
   files=sorted(folder.glob("*.json"),reverse=True)[:2] if folder.exists() else []
   payload={"runs":[{k:v for k,v in json.loads(p.read_text()).items() if k!="executed_trade_log"} for p in files]}
  if path=="/entry-quality":
   try:
    params=urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query)
    robot=int(params.get("robot",["9"])[0])
    if robot not in (8,9,10):raise ValueError("Robô inválido")
    ledger=latest_trade_ledger(robot)
    payload=(analyze_entry_quality(ledger) if ledger else
             {"status":"missing","message":"Gere o histórico de operações deste robô antes da análise de entradas."})
   except (ValueError,TypeError,KeyError,IndexError,OverflowError) as exc:
    payload={"status":"error","error":str(exc)}
  if path=="/trade-diagnostics":
   try:
    params=urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query)
    robot=int(params.get("robot",["9"])[0])
    if robot not in (8,9,10):raise ValueError("Robô inválido")
    ledger=latest_trade_ledger(robot)
    payload=(analyze_trade_diagnostics(ledger) if ledger else
             {"status":"missing","message":"Gere as operações individuais deste robô antes de consultar o diagnóstico."})
   except (ValueError,TypeError,KeyError,IndexError) as exc:
    payload={"status":"error","error":str(exc)}
  if path=="/trade-ledger":
   code,payload=trade_ledger_page(urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query))
   if code!=200:
    body=json.dumps(payload).encode()
    self.send_response(code);self.send_header("Content-Type","application/json");self.send_header("Content-Length",str(len(body)));self.end_headers();self.wfile.write(body);return
  if path=="/cost-audit":
   folder=DATA/"v6"
   items=sorted(folder.glob("*.json"),reverse=True)[:1] if folder.exists() else []
   if items:
    try:
     payload=analyze_cost_edge(json.loads(items[0].read_text()))
     payload["source_report_id"]=items[0].stem
    except (ValueError,KeyError,TypeError) as exc:
     payload={"status":"error","error":"Relatório V6 inválido: "+str(exc)}
   else:payload={"status":"missing","message":"Execute a pesquisa V6 antes da auditoria de custos."}
  if path=="/v6":
   folder=DATA/"v6"
   items=sorted(folder.glob("*.json"),reverse=True)[:3] if folder.exists() else []
   payload={"runs":[{"id":p.stem,**json.loads(p.read_text())} for p in items]}
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
 threading.Thread(target=paper_v7_loop,daemon=True,name="v7-paper-feed").start()
 http.server.ThreadingHTTPServer(("0.0.0.0",PORT),Handler).serve_forever()
