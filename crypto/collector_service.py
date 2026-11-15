#!/usr/bin/env python3
"""Coleta em segundo plano e expõe progresso; sem endpoints de negociação."""
import datetime as dt,glob,http.server,json,os,pathlib,threading,time,traceback,urllib.parse
from download_history import run
from spot_backtest_v4 import test
DATA=pathlib.Path(os.environ.get("CRYPTO_DATA_DIR","/data/crypto"))
START=os.environ.get("CRYPTO_START","2021-11")
END=os.environ.get("CRYPTO_END","2026-10")
PORT=int(os.environ.get("PORT","8080"))
status={"phase":"starting","start":START,"end":END,"backtest":"idle"}
lock=threading.Lock()
last_run=[0.0]
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
class Handler(http.server.BaseHTTPRequestHandler):
 def do_POST(self):
  path=urllib.parse.urlsplit(self.path).path
  if path!="/run-backtest":self.send_error(404);return
  code,payload=start_backtest()
  body=json.dumps(payload).encode()
  self.send_response(code);self.send_header("Content-Type","application/json");self.send_header("Content-Length",str(len(body)));self.end_headers();self.wfile.write(body)
 def do_GET(self):
  path=urllib.parse.urlsplit(self.path).path
  if path not in ("/","/health","/status","/backtest","/backtest-v2","/backtest-v3"):
   self.send_error(404);return
  if path=="/":
   body=(pathlib.Path(__file__).parent/"dashboard.html").read_bytes()
   self.send_response(200);self.send_header("Content-Type","text/html; charset=utf-8");self.send_header("Content-Length",str(len(body)));self.end_headers();self.wfile.write(body);return
  manifest=DATA/"BTCUSDT-manifest.json"
  records=json.loads(manifest.read_text()) if manifest.exists() else []
  payload={**status,"months_verified":len(records),"candles_verified":sum(x["rows"] for x in records),"gap_events":sum(len(x.get("gaps",[])) for x in records),"last_month":records[-1]["month"] if records else None,"note":"Historical research only; no live trading"}
  if path in ("/backtest","/backtest-v2","/backtest-v3"):
   report=DATA/("backtest-v2-results.json" if path=="/backtest-v2" else "backtest-v3-results.json" if path=="/backtest-v3" else "backtest-results.json")
   payload=json.loads(report.read_text()) if report.exists() else {"status":status.get("backtest","pending"),"phase":status.get("phase"),"error":status.get("error")}
  body=json.dumps(payload).encode()
  self.send_response(200);self.send_header("Content-Type","application/json");self.send_header("Content-Length",str(len(body)));self.end_headers();self.wfile.write(body)
if __name__=="__main__":
 DATA.mkdir(parents=True,exist_ok=True)
 threading.Thread(target=collect,daemon=True).start()
 http.server.ThreadingHTTPServer(("0.0.0.0",PORT),Handler).serve_forever()
