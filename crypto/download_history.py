#!/usr/bin/env python3
"""Baixa e valida candles BTCUSDT spot M1 do arquivo público Binance Vision.
Uso: python crypto/download_history.py --start 2021-11 --end 2026-10 --out data/crypto
Não realiza ordens, não exige API key. Downloads são retomáveis por mês.
"""
import argparse,csv,datetime as dt,hashlib,io,json,pathlib,urllib.request,urllib.error,zipfile
BASE="https://data.binance.vision/data/spot/monthly/klines"
HEADERS={"User-Agent":"B3ScalperAI-HistoricalResearch/1.0"}
def months(a,b):
 y,m=map(int,a.split("-")); ey,em=map(int,b.split("-"))
 while (y,m)<=(ey,em):
  yield f"{y:04d}-{m:02d}"
  m+=1
  if m==13:y,m=y+1,1
def fetch(url):
 req=urllib.request.Request(url,headers=HEADERS)
 with urllib.request.urlopen(req,timeout=90) as response:return response.read()
def run(start,end,out,symbol):
 out.mkdir(parents=True,exist_ok=True)
 manifest_path=out/f"{symbol}-manifest.json"
 manifest=json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.exists() else []
 manifest=[entry for entry in manifest if (out/pathlib.Path(entry["file"]).name).exists()]
 for month in months(start,end):
  name=f"{symbol}-1m-{month}.zip"; url=f"{BASE}/{symbol}/1m/{name}"
  dest=out/f"{symbol}-1m-{month}.csv"
  if dest.exists() and dest.stat().st_size>100:
   print(f"Já existe: {dest}");continue
  try:
   archive=fetch(url);checksum=fetch(url+".CHECKSUM").decode().strip().split()[0].lower()
   actual=hashlib.sha256(archive).hexdigest()
   if actual!=checksum:raise ValueError("SHA256 divergente")
   with zipfile.ZipFile(io.BytesIO(archive)) as z:
    members=[x for x in z.namelist() if x.endswith(".csv")]
    if len(members)!=1:raise ValueError("ZIP inesperado")
    count=0;first=None;last=None;previous=None;gaps=[]
    tmp=dest.with_suffix(".tmp")
    try:
     with z.open(members[0]) as source,open(tmp,"w",newline="",encoding="utf-8") as output:
      reader=csv.reader(io.TextIOWrapper(source,encoding="utf-8"))
      writer=csv.writer(output)
      writer.writerow(["timestamp","open","high","low","close","volume"])
      for row in reader:
       if len(row)<6:raise ValueError("Linha incompleta")
       raw=int(row[0]);ms=raw//1000 if raw>10**14 else raw
       timestamp=dt.datetime.fromtimestamp(ms/1000,dt.timezone.utc)
       o,h,l,c,v=map(float,row[1:6])
       if min(o,h,l,c)<=0 or v<0 or h<max(o,c) or l>min(o,c):raise ValueError("OHLCV inválido")
       if previous is not None and ms<=previous:raise ValueError("Candles fora de ordem ou duplicados")
       if previous is not None and ms-previous!=60000:gaps.append({"after_ms":previous,"before_ms":ms,"missing_candles":(ms-previous)//60000-1})
       previous=ms;first=first or timestamp;last=timestamp
       writer.writerow([timestamp.isoformat().replace("+00:00","Z"),row[1],row[2],row[3],row[4],row[5]])
       count+=1
     tmp.replace(dest)
    finally:
     if tmp.exists():tmp.unlink()
   manifest.append({"month":month,"rows":count,"first_utc":first.isoformat(),"last_utc":last.isoformat(),"sha256":actual,"file":str(dest)})
   manifest_path.write_text(json.dumps(manifest,indent=2,ensure_ascii=False),encoding="utf-8")
   print(f"OK {month}: {count} candles, {len(gaps)} lacunas registradas")
  except (urllib.error.HTTPError,urllib.error.URLError,ValueError,zipfile.BadZipFile) as e:
   print(f"FALHOU {month}: {e}")
 print("Concluído. Arquivos mensais disponíveis em",out)
 print("Meses validados:",len(manifest),"/",len(list(months(start,end))))
if __name__=="__main__":
 p=argparse.ArgumentParser();p.add_argument("--start",default="2021-11");p.add_argument("--end",default="2026-10");p.add_argument("--out",type=pathlib.Path,default=pathlib.Path("data/crypto"));p.add_argument("--symbol",choices=["BTCUSDT","ETHUSDT"],default="BTCUSDT");a=p.parse_args();run(a.start,a.end,a.out,a.symbol)
