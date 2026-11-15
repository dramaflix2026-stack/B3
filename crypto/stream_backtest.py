"""Streaming exploratory backtest of four signal rules; no live orders."""
import csv,glob,json,os,pathlib
from collections import deque
from backtest import signal
DATA=pathlib.Path(os.getenv("CRYPTO_DATA_DIR","/data/crypto"))
def test():
 files=sorted(glob.glob(str(DATA/"BTCUSDT-1m-*.csv")))
 if len(files)!=60:raise ValueError(f"Expected 60 months; found {len(files)}")
 total=sum(max(0,sum(1 for _ in open(f,encoding="utf-8"))-1) for f in files)
 split=int(total*.7)
 results={}
 for k in range(4):
  segments=[]
  for start,end in ((0,split),(split,total)):
   closes=deque(maxlen=30);balance=1000.;peak=1000.;dd=0.;pos=None;pending=0;cool=0;trades=0;wins=0;fees=0.;gross=0.
   j=-1
   for file in files:
    with open(file,newline="",encoding="utf-8") as inp:
     for row in csv.DictReader(inp):
      j+=1
      if j<start:continue
      if j>=end:break
      o,h,l,c=[float(row[x]) for x in ("open","high","low","close")]
      if pending and pos is None and balance>0:
       entry=o+pending*2
       pos={"dir":pending,"entry":entry,"at":j}
      pending=0
      if pos:
       direction=pos["dir"];entry=pos["entry"];qty=.001
       stop_price=entry-direction*(5/qty-4)
       touched=l<=stop_price if direction==1 else h>=stop_price
       if touched:raw=min(o,stop_price) if direction==1 else max(o,stop_price)
       elif j-pos["at"]>=16 or j==end-1:raw=c
       else:raw=None
       if raw is not None:
        exit_price=raw-direction*2
        fee=.001*(entry+exit_price)*qty
        gain=(exit_price-entry)*qty*direction-fee
        fees+=fee;gross+=(exit_price-entry)*qty*direction
        balance+=gain;trades+=1;wins+=gain>0;pos=None;cool=5
        peak=max(peak,balance);dd=max(dd,peak-balance)
      if cool:cool-=1
      closes.append(c)
      if pos is None and not cool and j<end-1 and balance>0:pending=signal(k,closes)
    if j>=end:break
   segments.append({"net_usdt":round(balance-1000,2),"trades":trades,"win_rate_pct":round(wins/trades*100,2) if trades else 0,"max_realized_drawdown_usdt":round(dd,2),"fees_usdt":round(fees,2),"gross_usdt":round(gross,2)})
  results[str(k+1)]={"development":segments[0],"validation":segments[1]}
 return {"status":"complete","candles":total,"months":len(files),"assumptions":{"capital_usdt":1000,"quantity_btc":.001,"fee_rate_per_side":.001,"slippage_usdt_per_btc_per_side":2,"stop_target_usdt":5,"split":"70% development / 30% validation","shorts":"hypothetical short positions; not executable on spot","caveats":"exploratory only; no funding, spread, orderbook, borrow or latency modeling"},"robots":results}
if __name__=="__main__":print(json.dumps(test(),ensure_ascii=False,indent=2))
