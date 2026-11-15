#!/usr/bin/env python3
"""Backtest exploratório, somente simulação, sobre CSVs M1 do coletor.
Sinais confirmados no fechamento; execução na abertura seguinte.
Stops avaliados em OHLC, conservador em gaps; sem book/liquidez.
"""
import argparse,csv,glob,json,pathlib
from collections import deque
def signal(k,closes):
 if len(closes)<26:return 0
 a=sum(list(closes)[-5:])/5;m=sum(list(closes)[-12:])/12;l=sum(list(closes)[-24:])/24
 p=closes[-1];prior=closes[-4];tick=max(.01,p*.0002) # limiar adaptativo de 0,02% do preço
 if k==0:return 1 if p-prior>3*tick else -1 if prior-p>3*tick else 0
 if k==1:return 1 if a>m+tick else -1 if a<m-tick else 0
 if k==2:return 1 if p<l-4*tick else -1 if p>l+4*tick else 0
 return 1 if a>m+2*tick and m>l+tick and p>prior+2*tick else -1 if a<m-2*tick and m<l-tick and p<prior-2*tick else 0
def load(files):
 prev=None
 for file in files:
  with open(file,newline="",encoding="utf-8") as f:
   for r in csv.DictReader(f):
    ts=r["timestamp"];o,h,l,c,v=(float(r[x]) for x in ("open","high","low","close","volume"))
    if min(o,h,l,c)<=0 or h<max(o,c) or l>min(o,c) or v<0:raise ValueError(f"OHLCV inválido: {file} {ts}")
    if prev is not None and ts<=prev:raise ValueError("Datas fora de ordem")
    prev=ts
    yield (ts,o,h,l,c)
def run(rows,k,capital,qty,fee,slippage,stop,split):
 cutoff=int(len(rows)*split);results={}
 for name,segment in (("development",rows[:cutoff]),("validation",rows[cutoff:])):
  closes=deque(maxlen=300);balance=capital;peak=capital;maxdd=0;pos=None;pending=0;trades=0;wins=0;cool=0
  for j,(ts,o,h,l,c) in enumerate(segment):
   if pending and pos is None and balance>0:
    pos={"dir":pending,"entry":o+pending*slippage,"at":j};pending=0
   else:pending=0
   if pos:
    direction=pos["dir"];entry=pos["entry"]
    stop_price=entry-direction*(stop/qty-2*slippage-2*fee/qty)
    touched=l<=stop_price if direction==1 else h>=stop_price
    if touched:
     raw=min(o,stop_price) if direction==1 else max(o,stop_price)
     reason="stop"
    elif j-pos["at"]>=16 or j==len(segment)-1:
     raw=c;reason="time"
    else:raw=None
    if raw is not None:
     exit_price=raw-direction*slippage
     gain=(exit_price-entry)*qty*direction-2*fee
     balance+=gain;trades+=1;wins+=gain>0;pos=None;cool=5
     peak=max(peak,balance);maxdd=max(maxdd,peak-balance)
   if cool:cool-=1
   closes.append(c)
   if pos is None and not cool and j<len(segment)-1 and balance>0:
    pending=signal(k,closes)
  results[name]={"net_usdt":round(balance-capital,2),"trades":trades,"wins":wins,"win_rate_pct":round(100*wins/trades,2) if trades else 0,"max_realized_drawdown_usdt":round(maxdd,2)}
 return results
if __name__=="__main__":
 p=argparse.ArgumentParser();p.add_argument("--data",default="data/crypto/BTCUSDT-1m-*.csv");p.add_argument("--qty",type=float,default=.001,help="Quantidade de BTC");p.add_argument("--fee",type=float,default=0,help="Taxa em USDT por lado, não percentual");p.add_argument("--slippage",type=float,default=0,help="Desvio de preço em USDT por lado");p.add_argument("--stop",type=float,default=5,help="Perda alvo por trade em USDT");p.add_argument("--capital",type=float,default=1000);p.add_argument("--split",type=float,default=.7);a=p.parse_args()
 if a.qty<=0 or a.capital<=0 or a.stop<=0 or not 0<a.split<1 or a.fee<0 or a.slippage<0:p.error("Parâmetros inválidos")
 files=sorted(glob.glob(a.data))
 if not files:p.error("Nenhum CSV encontrado")
 rows=list(load(files))
 if len(rows)<100:p.error("Histórico insuficiente")
 print(json.dumps({"candles":len(rows),"from":rows[0][0],"to":rows[-1][0],"parameters":vars(a),"robots":{str(k+1):run(rows,k,a.capital,a.qty,a.fee,a.slippage,a.stop,a.split) for k in range(4)}},indent=2,ensure_ascii=False))
