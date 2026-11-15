"""V6 fixed research candidates and chronological walk-forward selection. Spot simulation only."""
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
def test(on_progress=None, robot_ids=None, settings=None, windows=None, diagnostics=False, trade_log=None):
 settings=settings or {}
 stop=float(settings.get("stop_pct",STOP*100))/100
 edge=float(settings.get("min_edge_pct",MIN_EDGE*100))/100
 cooldown=int(settings.get("cooldown_minutes",COOLDOWN))
 hold=int(settings.get("max_hold_minutes",HOLD))
 selected=robot_ids or [8,9,10]
 files=sorted(glob.glob(str(DATA/"BTCUSDT-1m-*.csv")))
 if len(files)!=60:raise ValueError("Expected 60 monthly CSV files")
 counts=[sum(1 for _ in open(f,encoding="utf-8"))-1 for f in files]
 total=sum(counts);cutoff=int(total*.7)
 robots={}
 for k_id in selected:
  if k_id not in (8,9,10):raise ValueError("V6 robot must be 8, 9, or 10")
  k=k_id-8
  out=[]
  progress_index=selected.index(k_id)
  periods=([(int(total*a/5),int(total*(a+1)/5)) for a in range(5)] if windows=="five" else [(0,cutoff),(cutoff,total)])
  for window_index,(start,end) in enumerate(periods):
   cash=START_CASH;btc=0.;entry=0.;entry_fee=0.;opened=-1;cool=0;pending=False
   tranges=deque(maxlen=14);rsi_changes=deque(maxlen=14);previous_close=None;entry_stop_pct=stop;recovery_armed=False
   pending_context=None;entry_context=None;entry_time=None;entry_open=None
   closes=deque(maxlen=720);prev_ms=None;trades=0;wins=0;fees=0.;gross=0.;peak=START_CASH;maxdd=0.;gaps=0;halted=False
   index=0;last_close=None;first_open=None;sum60=0.;sum240=0.;sum720=0.
   exits={name:{"trades":0,"wins":0,"net_usdt":0.,"fees_usdt":0.} for name in ("stop_loss","take_profit","max_hold","window_end","data_gap")}
   winning_sum=0.;losing_sum=0.;winning_count=0;losing_count=0
   regime="unknown";entry_regime="unknown";regimes={name:{"candles":0,"trades":0,"wins":0,"net_usdt":0.,"fees_usdt":0.} for name in ("uptrend","downtrend","sideways","unknown")}
   for file in files:
    with open(file,newline="",encoding="utf-8") as stream:
     for row in csv.DictReader(stream):
      i=index;index+=1
      if i<start:continue
      if i>=end:break
      ts=row["timestamp"];ms=int(datetime.datetime.fromisoformat(ts.replace("Z","+00:00")).timestamp()*1000)
      o,h,l,c=[float(row[x]) for x in ("open","high","low","close")]
      if first_open is None:first_open=o
      if min(o,h,l,c)<=0 or h<max(o,c) or l>min(o,c):raise ValueError("Invalid OHLC")
      discontinuity=prev_ms is not None and ms-prev_ms!=60000
      if discontinuity:
       gaps+=1;closes.clear();tranges.clear();rsi_changes.clear();previous_close=None;recovery_armed=False;pending=False;pending_context=None;sum60=sum240=sum720=0.
      prev_ms=ms
      if pending and not btc and not halted:
       price=o*(1+SLIP)
       entry_stop_pct=(max(.006,min(.04,atr/o*2)) if k==1 and len(tranges)==14 else stop)
       quantity=min(cash*(.15 if k==2 else MAX_ALLOC)/price,cash*RISK/(price*entry_stop_pct),cash/(price*(1+FEE)))
       if quantity*price>=5 and quantity>0:
        entry=price;btc=quantity;entry_fee=quantity*price*FEE
        cash-=quantity*price+entry_fee;fees+=entry_fee;opened=i;entry_regime=regime
        if trade_log is not None:entry_context=dict(pending_context or {});entry_time=ts;entry_open=o
       pending=False
      if btc:
       stop_price=entry*(1-entry_stop_pct)
       if l<=stop_price:exit_raw=min(o,stop_price);exit_reason="stop_loss"
       elif k==2 and h>=entry*1.012:exit_raw=max(o,entry*1.012);exit_reason="take_profit"
       elif discontinuity:exit_raw=c;exit_reason="data_gap"
       elif i==end-1:exit_raw=c;exit_reason="window_end"
       elif i-opened>=hold:exit_raw=c;exit_reason="max_hold"
       else:exit_raw=None
       if exit_raw is not None:
        exit_price=exit_raw*(1-SLIP);proceeds=btc*exit_price;exit_fee=proceeds*FEE
        pnl=proceeds-exit_fee-btc*entry-entry_fee
        if trade_log is not None:
         qty=btc;commissions=entry_fee+exit_fee
         slippage_cost=qty*((entry-entry_open)+(exit_raw-exit_price))
         raw_pnl=qty*(exit_raw-entry_open)
         trade_log.append({"robot":k_id,"window":window_index+1,"entry_time_utc":entry_time,"exit_time_utc":ts,
          "entry_candle":opened,"exit_candle":i,"duration_minutes":i-opened,"signal":entry_context,
          "regime_on_entry":entry_regime,"raw_entry_open_usdt":round(entry_open,6),"entry_price_usdt":round(entry,6),
          "raw_exit_usdt":round(exit_raw,6),"exit_price_usdt":round(exit_price,6),"btc_quantity":round(qty,10),
          "notional_usdt":round(qty*entry,4),"stop_pct":round(entry_stop_pct*100,4),"exit_reason":exit_reason,
          "gross_before_costs_usdt":round(raw_pnl,6),"gross_after_slippage_usdt":round(qty*(exit_price-entry),6),
          "entry_fee_usdt":round(entry_fee,6),"exit_fee_usdt":round(exit_fee,6),"commissions_usdt":round(commissions,6),
          "slippage_usdt":round(slippage_cost,6),"net_usdt":round(pnl,6),"is_win":pnl>0})
        cash+=proceeds-exit_fee;fees+=exit_fee;gross+=btc*(exit_price-entry)
        wins+=pnl>0;trades+=1;btc=0.;cool=max(cooldown,1440 if k==2 else 720)
        if diagnostics:
         exit_bucket=exits[exit_reason];exit_bucket["trades"]+=1;exit_bucket["wins"]+=int(pnl>0);exit_bucket["net_usdt"]+=pnl;exit_bucket["fees_usdt"]+=entry_fee+exit_fee
         if pnl>0:winning_sum+=pnl;winning_count+=1
         else:losing_sum+=pnl;losing_count+=1
         bucket=regimes[entry_regime];bucket["trades"]+=1;bucket["wins"]+=int(pnl>0);bucket["net_usdt"]+=pnl;bucket["fees_usdt"]+=entry_fee+exit_fee
      equity=cash+btc*c
      peak=max(peak,equity);maxdd=max(maxdd,peak-equity)
      if equity<=START_CASH*.5:halted=True
      if cool:cool-=1
      n=len(closes)
      if n>=60:sum60-=closes[-60]
      if n>=240:sum240-=closes[-240]
      if n>=720:sum720-=closes[0]
      tr=max(h-l,abs(h-previous_close),abs(l-previous_close)) if previous_close is not None else h-l
      tranges.append(tr)
      if previous_close is not None:rsi_changes.append(c-previous_close)
      previous_close=c
      atr=sum(tranges)/len(tranges)
      closes.append(c)
      sum60+=c;sum240+=c;sum720+=c
      if on_progress and i%50000==0:on_progress(k_id,((progress_index*len(periods)+window_index+(i-start)/(end-start))/(len(selected)*len(periods)))*100)
      if diagnostics and len(closes)>=720:
       df=sum60/60;dm=sum240/240;ds=sum720/720
       regime="uptrend" if df>dm>ds else "downtrend" if df<dm<ds else "sideways"
       regimes[regime]["candles"]+=1
      if not btc and not cool and not halted and i<end-1 and not discontinuity:
       pending=False
       if len(closes)>=720:
        p=list(closes)
        fast=sum60/60;mid=sum240/240;slow=sum720/720
        volatility=(max(p[-240:])-min(p[-240:]))/c
        momentum=(c/p[-60]-1)
        prior_high=max(p[-241:-1])
        if k==0:
         # Confirmed breakout: close above prior 240-bar high with bounded extension and positive short momentum.
         pending=fast>mid>slow and volatility>=.009 and c>prior_high*(1+edge) and .002<momentum<.022 and 0<c/fast-1<.015
        elif k==1:
         # Volatility-adjusted stop at entry; same breakout trigger as V4 robot 2.
         pending=fast>mid>slow and volatility>=.009 and c>slow*(1+edge) and c>prior_high*(1+edge) and len(tranges)==14 and c/fast-1<.016
        else:
         # Reversion: previous 60-bar drawdown, oversold RSI, then price recovers above prior close.
         gains=sum(max(0.,v) for v in rsi_changes)
         losses=sum(max(0.,-v) for v in rsi_changes)
         rsi=100*gains/(gains+losses) if gains+losses else 50.
         if c<mid*.985 and rsi<29 and volatility>=.009:recovery_armed=True
         if recovery_armed and c>p[-2] and 38<rsi<55 and c<mid*.989 and volatility>=.009 and mid/c-1>.011:
          pending=True;recovery_armed=False
         if c>mid or rsi>60:recovery_armed=False
        if pending and trade_log is not None:
         pending_context={"signal_time_utc":ts,"signal_close_usdt":round(c,4),"regime":regime,
          "volatility_240_pct":round(volatility*100,4),"momentum_60_pct":round(momentum*100,4),
          "rsi_14":round(rsi,3) if k==2 else None,"atr_14_pct":round(atr/c*100,4),
          "ma_fast_60":round(fast,4),"ma_mid_240":round(mid,4),"ma_slow_720":round(slow,4),
          "prior_high_240":round(prior_high,4)}
      last_close=c
    if index>=end:break
   equity=cash+btc*(last_close or 0)
   benchmark_qty=START_CASH/(first_open*(1+FEE)) if first_open else 0
   benchmark_net=benchmark_qty*(last_close or 0)*(1-FEE)-START_CASH
   if diagnostics:
    for bucket in list(regimes.values())+list(exits.values()):
     bucket["net_usdt"]=round(bucket["net_usdt"],2);bucket["fees_usdt"]=round(bucket["fees_usdt"],2)
   trade_diagnostics=({"exit_reasons":exits,"avg_win_usdt":round(winning_sum/winning_count,2) if winning_count else None,"avg_loss_usdt":round(losing_sum/losing_count,2) if losing_count else None,"profit_factor":round(winning_sum/abs(losing_sum),3) if losing_sum<0 else None,"net_per_trade_usdt":round((winning_sum+losing_sum)/trades,3) if trades else None,"completed_trades":trades} if diagnostics else None)
   out.append({"trade_diagnostics":trade_diagnostics,"regime_diagnostics":regimes if diagnostics else None,"buy_hold_net_usdt":round(benchmark_net,2),"net_usdt":round(equity-START_CASH,2),"ending_equity_usdt":round(equity,2),"trades":trades,"win_rate_pct":round(100*wins/trades,2) if trades else 0,"max_realized_drawdown_usdt":round(maxdd,2),"fees_usdt":round(fees,2),"gross_usdt":round(gross,2),"gap_events":gaps,"risk_halt":halted})
  robots[str(k_id)]=({"windows":[{"index":j+1,"start_candle":a,"end_candle_exclusive":b,**out[j]} for j,(a,b) in enumerate(periods)]} if windows=="five" else {"development":out[0],"validation":out[1]})
  if on_progress:on_progress(k_id,(progress_index+1)/len(selected)*100.)
  print(f"V6 BACKTEST robot {k_id}/10 complete",flush=True)
 report={"status":"complete","version":"spot-v6-walk-forward-research" if windows=="five" else "spot-v6-experimental","candles":total,"months":len(files),"assumptions":{"capital_usdt":START_CASH,"spot_only":True,"shorts":False,"fee_rate_per_side":FEE,"slippage_fraction_per_side":SLIP,"stop_pct":stop*100,"risk_budget_pct":RISK*100,"max_position_allocation_pct":MAX_ALLOC*100,"halt_at_equity_usdt":500,"split":"Five chronological nonoverlapping windows, fixed parameters; NOT a true walk-forward retraining or untouched out-of-sample" if windows=="five" else "70% development / 30% previously inspected validation; NOT untouched out-of-sample","min_volatility_60m_pct":MIN_VOL*100,"min_edge_pct":edge*100,"cooldown_minutes":cooldown,"max_hold_minutes":hold,"caveats":"Pre-specified after inspecting previous BTC historical results; walk-forward windows are chronological but not pristine untouched out-of-sample. No orderbook/latency, intrabar assumptions, or live trading."},"robots":robots,"settings":settings,"selected_robots":selected,"diagnostics_enabled":diagnostics,"strategy_names":{"8":"Breakout with extension gate","9":"ATR breakout with cooldown","10":"Cost-gated oversold recovery"}}
 if windows=="five" and set(selected)=={8,9,10}:report["walk_forward"]=walk_forward_result(robots)
 return report
def choose_from_prior(robots, train_indices):
 """Select using ONLY past windows; cash is always an eligible candidate."""
 eligible=[]
 for robot_id in ("8","9","10"):
  windows=robots[robot_id]["windows"]
  history=[windows[j] for j in train_indices]
  trades=sum(w["trades"] for w in history)
  net=sum(w["net_usdt"] for w in history)
  fees=sum(w["fees_usdt"] for w in history)
  drawdown=max(w["max_realized_drawdown_usdt"] for w in history)
  if trades<12 or net<=0 or any(w["net_usdt"]<=0 for w in history):
   continue
  wins=sum(w["trades"]*w["win_rate_pct"]/100 for w in history)
  score=net-.5*drawdown-.2*fees
  if score>0:eligible.append((score,robot_id))
 if not eligible:return "cash"
 return max(eligible)[1]

def walk_forward_result(robots):
 """Train on two preceding chronological blocks, evaluate the next one."""
 folds=[]
 for test_window_index in (2,3,4):
  train_indices=(test_window_index-2,test_window_index-1)
  chosen=choose_from_prior(robots,train_indices)
  observed=(robots[chosen]["windows"][test_window_index] if chosen!="cash" else
    {"net_usdt":0.0,"trades":0,"fees_usdt":0.0,"max_realized_drawdown_usdt":0.0})
  folds.append({"train_windows":[j+1 for j in train_indices],
                "test_window":test_window_index+1,"selected":chosen,
                "selection_rule":"both training windows positive, >=12 total trades, positive net minus 0.5*max drawdown minus 0.2*fees; else cash",
                "test_net_usdt":observed["net_usdt"],
                "test_trades":observed["trades"],
                "test_fees_usdt":observed["fees_usdt"],
                "test_drawdown_usdt":observed["max_realized_drawdown_usdt"]})
 return {"folds":folds,
         "test_net_sum_usdt":round(sum(f["test_net_usdt"] for f in folds),2),
         "test_positive_folds":sum(f["test_net_usdt"]>0 for f in folds),
         "test_trades":sum(f["test_trades"] for f in folds),
         "cash_folds":sum(f["selected"]=="cash" for f in folds),
         "note":"Each fold uses earlier windows only; results are independent USD 1000 accounts, NOT a compounded equity curve."}

if __name__=="__main__":print(json.dumps(test(),indent=2))
