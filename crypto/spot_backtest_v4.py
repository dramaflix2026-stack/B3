"""Conservative, spot-only streaming research engine. No live trading."""
import csv,glob,json,os,pathlib,datetime
from collections import deque

DATA=pathlib.Path(os.getenv("CRYPTO_DATA_DIR","/data/crypto"))
FEE=.001
SLIP=.0001
START_CASH=1000.
RISK=.01
STOP=.015
MAX_ALLOC=.25
HOLD=720
COOLDOWN=360
MIN_VOL=.008
MIN_EDGE=.006
def test(on_progress=None):
 files=sorted(glob.glob(str(DATA/"BTCUSDT-1m-*.csv")))
 if len(files)!=60:raise ValueError("Expected 60 monthly CSV files")
 counts=[sum(1 for _ in open(f,encoding="utf-8"))-1 for f in files]
 total=sum(counts);cutoff=int(total*.7)
 robots={}
 for k in range(4):
  out=[]
  for start,end in ((0,cutoff),(cutoff,total)):
   cash=START_CASH;btc=0.;entry=0.;entry_fee=0.;opened=-1;cool=0;pending=False
   closes=deque(maxlen=720);prev_ms=None;trades=0;wins=0;fees=0.;gross=0.;peak=START_CASH;maxdd=0.;gaps=0;halted=False
   index=0;last_close=None
   for file in files:
    with open(file,newline="",encoding="utf-8") as stream:
     for row in csv.DictReader(stream):
      i=index;index+=1
      if i<start:continue
      if i>=end:break
      o,h,l,c=[float(row[x]) for x in ("open","high","low","close")]
      if min(o,h,l,c)<=0 or h<max(o,c) or l>min(o,c):raise ValueError("Invalid OHLC")
      if discontinuity:
       gaps+=1;closes.clear();pending=False
      prev_ms=ms
      ts=row["timestamp"];ms=int(datetime.datetime.fromisoformat(ts.replace("Z","+00:00")).timestamp()*1000)
      discontinuity=prev_ms is not None and ms-prev_ms!=60000
      if discontinuity:pending=False
      if pending and not btc and not halted:
       price=o*(1+SLIP)
       quantity=min(cash*MAX_ALLOC/price,cash*RISK/(price*STOP),cash/(price*(1+FEE)))
       if quantity*price>=5 and quantity>0:
        entry=price;btc=quantity;entry_fee=quantity*price*FEE
        cash-=quantity*price+entry_fee;fees+=entry_fee;opened=i
       pending=False
      if btc:
       stop_price=entry*(1-STOP)
       if l<=stop_price:exit_raw=min(o,stop_price)
       elif i-opened>=HOLD or i==end-1 or discontinuity:exit_raw=c
       else:exit_raw=None
       if exit_raw is not None:
        exit_price=exit_raw*(1-SLIP);proceeds=btc*exit_price;exit_fee=proceeds*FEE
        pnl=proceeds-exit_fee-btc*entry-entry_fee
        cash+=proceeds-exit_fee;fees+=exit_fee;gross+=btc*(exit_price-entry)
        wins+=pnl>0;trades+=1;btc=0.;cool=COOLDOWN
      equity=cash+btc*c
      peak=max(peak,equity);maxdd=max(maxdd,peak-equity)
      if equity<=START_CASH*.5:halted=True
      if cool:cool-=1
      closes.append(c)
      if not btc and not cool and not halted and i<end-1 and not discontinuity:
       pending=False
       if len(closes)>=720:
        p=list(closes)
        fast=sum(p[-60:])/60;mid=sum(p[-240:])/240;slow=sum(p)/720
        volatility=(max(p[-240:])-min(p[-240:]))/c
        momentum=(c/p[-60]-1)
        trend=fast>mid>slow and c>slow*(1+MIN_EDGE)
        if volatility>=MIN_VOL and trend:
         if k==0:pending=momentum>MIN_EDGE and c>max(p[-241:-1])
         elif k==1:pending=c>max(p[-241:-1])*(1+MIN_EDGE)
         elif k==2:pending=MIN_EDGE<momentum<.035 and c>fast*(1+MIN_EDGE)
         else:pending=momentum>.012 and fast/mid-1>MIN_EDGE
      last_close=c
    if index>=end:break
   equity=cash+btc*(last_close or 0)
   out.append({"net_usdt":round(equity-START_CASH,2),"ending_equity_usdt":round(equity,2),"trades":trades,"win_rate_pct":round(100*wins/trades,2) if trades else 0,"max_realized_drawdown_usdt":round(maxdd,2),"fees_usdt":round(fees,2),"gross_usdt":round(gross,2),"gap_events":gaps,"risk_halt":halted})
  robots[str(k+1)]={"development":out[0],"validation":out[1]}
  if on_progress:on_progress(k+1)
  print(f"V4 BACKTEST robot {k+1}/4 complete",flush=True)
 return {"status":"complete","version":"spot-v4-experimental","candles":total,"months":len(files),"assumptions":{"capital_usdt":START_CASH,"spot_only":True,"shorts":False,"fee_rate_per_side":FEE,"slippage_fraction_per_side":SLIP,"stop_pct":STOP*100,"risk_budget_pct":RISK*100,"max_position_allocation_pct":MAX_ALLOC*100,"halt_at_equity_usdt":500,"split":"70% development / 30% previously inspected validation; NOT untouched out-of-sample","min_volatility_60m_pct":MIN_VOL*100,"min_edge_pct":MIN_EDGE*100,"cooldown_minutes":COOLDOWN,"max_hold_minutes":HOLD,"caveats":"Exploratory OHLC simulation, no orderbook/latency; stop gap slippage may exceed threshold; no real trading"},"robots":robots}
if __name__=="__main__":print(json.dumps(test(),indent=2))
