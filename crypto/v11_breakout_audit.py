"""V11 exploratory breakout audit on previously inspected historical BTCUSDT M1.

Compares alternative breakout buffers, and replays hypothetical entries with
V7's costs, next-open fills, stop/hold/cooldown. NEVER changes V7 strategy,
paper account, or real orders. These are in-sample sensitivity tests, not
out-of-sample validation or investment advice.
"""
import copy
import math
import pathlib
from collections import deque

import paper_v7 as paper
from v10_signal_diagnostics import _read_verified

BREAKOUT_BUFFERS=(0.0,0.001,0.002,0.004,0.006)
WARMUP=721

def _signal(closes, ranges, close, buffer):
    closes=list(closes)
    fast=sum(closes[-60:])/60
    mid=sum(closes[-240:])/240
    slow=sum(closes)/720
    vol=(max(closes[-240:])-min(closes[-240:]))/close
    previous_high=max(closes[-241:-1])
    atr=sum(ranges)/14
    basic=(fast>mid>slow and vol>=.009 and close>slow*1.006 and close/fast-1<.016)
    return basic and close>previous_high*(1+buffer),atr,close/previous_high-1

def _simulate(candles,freeze,buffer,collect_trades=False):
    """Local, independently accounted spot paper ledger; no state writes."""
    closes=deque((c["c"] for c in candles[:WARMUP][-720:]),maxlen=720)
    ranges=deque(maxlen=14)
    for i in range(WARMUP-14,WARMUP):
        c=candles[i];p=candles[i-1]["c"]
        ranges.append(max(c["h"]-c["l"],abs(c["h"]-p),abs(c["l"]-p)))
    prev=candles[WARMUP-1]["c"]
    cash=paper.INITIAL_CASH;qty=0.0;entry=None;pending=None;cool=0
    signals=0;atr_rejected=0;trades=0;wins=0;realized=0.0;fees=0.0;slip_cost=0.0
    peak=cash;max_dd=0.0;gross_profit=0.0;gross_loss=0.0
    rule=freeze["shadow_atr"]
    trade_details=[]
    entry_high=entry_low=None
    for candle in candles[WARMUP:]:
        ms,o,h,l,c=(candle[k] for k in ("ms","o","h","l","c"))
        if pending and not qty and not cool:
            fill=o*(1+paper.SLIP)
            stop=max(.006,min(.04,pending["atr"]/o*2))
            amount=min(cash*paper.MAX_ALLOC/fill,cash*paper.RISK_BUDGET/(fill*stop),cash/(fill*(1+paper.FEE)))
            if amount*fill>=5:
                fee=amount*fill*paper.FEE
                cash-=amount*fill+fee;qty=amount
                entry={"fill":fill,"fee":fee,"stop":stop,"ms":ms,"entry_raw":o}
                entry_high=o;entry_low=o
                fees+=fee;slip_cost+=amount*(fill-o)
        pending=None
        if qty:
            entry_high=max(entry_high,h)
            entry_low=min(entry_low,l)
            stop_price=entry["fill"]*(1-entry["stop"])
            if l<=stop_price:
                reason="stop"
                raw=min(o,stop_price)
            elif ms-entry["ms"]>=paper.HOLD_MINUTES*paper.CANDLE_MS:
                reason="time"
                raw=c
            else:
                raw=None
            if raw is not None:
                fill=raw*(1-paper.SLIP)
                fee=qty*fill*paper.FEE
                net=qty*(fill-entry["fill"])-entry["fee"]-fee
                cash+=qty*fill-fee;fees+=fee;slip_cost+=qty*(raw-fill)
                if collect_trades:
                    trade_details.append({"reason":reason,"entry_ms":entry["ms"],"exit_ms":ms,
                      "holding_minutes":round((ms-entry["ms"])/paper.CANDLE_MS,2),
                      "entry_price":round(entry["fill"],6),"exit_price":round(fill,6),
                      "quantity":qty,"gross_usdt":qty*(fill-entry["fill"]),
                      "entry_fee_usdt":entry["fee"],"exit_fee_usdt":fee,
                      "slippage_usdt":qty*(entry["fill"]-entry["entry_raw"])+qty*(raw-fill),
                      "net_usdt":net,"mfe_pct":(entry_high/entry["fill"]-1)*100,
                      "mae_pct":(entry_low/entry["fill"]-1)*100})
                realized+=net;trades+=1;wins+=net>0
                gross_profit+=max(net,0);gross_loss+=max(-net,0)
                qty=0;entry=None;cool=paper.COOLDOWN_MINUTES
        if cool:cool-=1
        tr=max(h-l,abs(h-prev),abs(l-prev));ranges.append(tr);closes.append(c);prev=c
        active,atr,_=_signal(closes,ranges,c,buffer)
        if active and not qty and not cool:
            signals+=1
            atr_pct=atr/c*100
            allow=(atr_pct<rule["threshold_pct"] if rule["direction"]=="low"
                   else atr_pct>=rule["threshold_pct"])
            if allow:pending={"atr":atr}
            else:atr_rejected+=1
        equity=cash+qty*c;peak=max(peak,equity)
        max_dd=max(max_dd,(peak-equity)/peak*100)
    equity=cash+qty*candles[-1]["c"]
    result={"buffer_pct":round(buffer*100,2),"signals":signals,
            "atr_rejected":atr_rejected,"trades_closed":trades,"open_position":bool(qty),
            "wins":wins,"win_rate_pct":round(wins/trades*100,2) if trades else None,
            "realized_net_usdt":round(realized,4),
            "marked_net_usdt":round(equity-paper.INITIAL_CASH,4),
            "fees_usdt":round(fees,4),"slippage_usdt":round(slip_cost,4),
            "max_close_drawdown_pct":round(max_dd,4),
            "profit_factor":round(gross_profit/gross_loss,3) if gross_loss else None}
    if collect_trades:result["trade_details"]=trade_details
    return result

def run(data_dir=None):
    root=pathlib.Path(data_dir if data_dir is not None else paper.DATA)
    row,candles=_read_verified(root)
    with paper.LOCK:
        live=paper._load()
        if live is None:raise ValueError("Conta V7 não inicializada")
        frozen=copy.deepcopy(live["frozen"])
    if frozen.get("control_decision")!="cash":raise ValueError("Controle V7 não está em caixa")
    if frozen.get("shadow_atr",{}).get("direction") not in ("low","high"):
        raise ValueError("Filtro ATR inválido")
    buffers={b:_simulate(candles,frozen,b) for b in BREAKOUT_BUFFERS}
    # Validate exact original V7 engine on the 0.6% baseline; do not trust
    # an independent simulator unless it agrees on signals and fills.
    control=paper.new_state(candles[:WARMUP],frozen)
    for candle in candles[WARMUP:]:
        paper.process_candle(control,candle)
        if control["phase"]!="observing":raise ValueError("Gap during baseline replay")
    baseline=buffers[.006]
    checks={
        "baseline_signals":baseline["signals"]==control["signals"],
        "baseline_atr_rejected":baseline["atr_rejected"]==control["blocked"],
        "baseline_trades":baseline["trades_closed"]==control["total_trades"],
        "baseline_equity":abs(baseline["marked_net_usdt"]-(control["cash"]+control["btc"]*control["last_price"]-paper.INITIAL_CASH))<.0002,
        "baseline_fees":abs(baseline["fees_usdt"]-control["fees"])<.0002,
        "baseline_slippage":abs(baseline["slippage_usdt"]-control["slippage"])<.0002,
        "historical_contiguity":len(candles)>WARMUP,
        "no_forced_entries":all(x["trades_closed"]<=x["signals"] for x in buffers.values()),
        "frozen_control_cash":frozen["control_decision"]=="cash",
    }
    total=sum(checks.values())
    # Original high is previous 240 closes, not candle wicks; label precisely.
    return {"version":"V11","status":"passed" if total==len(checks) else "failed",
            "source_month":row["month"],"candles_analyzed":len(candles)-WARMUP,
            "from_utc":paper.utc_iso(candles[WARMUP]["ms"]),
            "to_utc":paper.utc_iso(candles[-1]["ms"]),
            "reference_buffer_pct":0.6,
            "breakout_reference":"max of preceding 240 minute CLOSES, excluding current close (not candle highs)",
            "variants":[buffers[b] for b in BREAKOUT_BUFFERS],
            "checks_passed":total,"checks_total":len(checks),
            "failed_checks":[k for k,v in checks.items() if not v],
            "no_parameter_change_to_v7":True,"writes_v7_state":False,
            "executes_exchange_orders":False,
            "out_of_sample":False,
            "warning":"Exploração retrospectiva de limiares em dados conhecidos. Mais sinais ou P&L positivo não prova rentabilidade. Escolher parâmetros exigirá teste futuro independente."}
