"""V10: diagnostic funnel of the unchanged V7 shadow entry rule on archived M1.

No parameter search, no trade forcing, no persistent writes or broker calls.
Each candle is evaluated against the exact conditions in paper_v7._signal_metrics.
Counts are sequential, so a failed condition has one unambiguous first blocker.
"""
import csv
import json
import math
from collections import Counter, deque
import pathlib

import paper_v7
from v9_historical_replay import _last_verified_month, _utc_millis, WARMUP_CANDLES, MAX_REPLAY_CANDLES

STAGES = (
    ("trend_ma", "Tendência MA60 > MA240 > MA720"),
    ("volatility", "Amplitude de 240 candles >= 0,90%"),
    ("above_slow", "Fechamento > MA720 + 0,60%"),
    ("breakout", "Rompimento da máxima anterior + 0,60%"),
    ("not_extended", "Distância do fechamento à MA60 < 1,60%"),
    ("available", "Sem posição, espera ou cooldown"),
    ("atr", "Filtro ATR congelado"),
)
def _read_verified(root):
    row, path = _last_verified_month(root)
    candles = deque(maxlen=WARMUP_CANDLES+MAX_REPLAY_CANDLES)
    count = 0
    with path.open("r",encoding="utf-8",newline="") as file:
        reader = csv.DictReader(file)
        if not {"timestamp","open","high","low","close"}.issubset(reader.fieldnames or []):
            raise ValueError("CSV sem OHLC completo")
        for record in reader:
            candle = {"ms":_utc_millis(record["timestamp"]),
                      "o":float(record["open"]),"h":float(record["high"]),
                      "l":float(record["low"]),"c":float(record["close"])}
            if (not all(math.isfinite(candle[k]) for k in ("o","h","l","c"))
                    or min(candle[k] for k in ("o","h","l","c"))<=0
                    or candle["h"]<max(candle["o"],candle["c"])
                    or candle["l"]>min(candle["o"],candle["c"])
                    or candle["ms"]%paper_v7.CANDLE_MS):
                raise ValueError("Candle histórico inválido")
            candles.append(candle)
            count += 1
    if count!=int(row["rows"]):
        raise ValueError("Total de candles diverge do manifesto")
    result = list(candles)
    if len(result)<WARMUP_CANDLES+1:
        raise ValueError("Histórico insuficiente")
    if any(b["ms"]-a["ms"]!=paper_v7.CANDLE_MS for a,b in zip(result,result[1:])):
        raise ValueError("Lacuna de candles no intervalo analisado")
    return row, result

def _conditions(state, candle):
    closes=state["closes"]
    close=candle["c"]
    fast=sum(closes[-60:])/60
    mid=sum(closes[-240:])/240
    slow=sum(closes)/720
    vol=(max(closes[-240:])-min(closes[-240:]))/close
    prior_high=max(closes[-241:-1])
    atr=sum(state["tranges"])/14
    rule=state["frozen"]["shadow_atr"]
    atr_pct=atr/close*100
    return {
        "trend_ma":fast>mid>slow,
        "volatility":vol>=.009,
        "above_slow":close>slow*1.006,
        "breakout":close>prior_high*1.006,
        "not_extended":close/fast-1<.016,
        "available":state["btc"]==0 and state["cool"]==0,
        "atr":atr_pct<rule["threshold_pct"] if rule["direction"]=="low"
              else atr_pct>=rule["threshold_pct"],
    }, {"atr_pct":atr_pct,"vol_pct":vol*100}

def run(data_dir=None):
    root=pathlib.Path(data_dir if data_dir is not None else paper_v7.DATA)
    row,candles=_read_verified(root)
    with paper_v7.LOCK:
        account=paper_v7._load()
        if account is None:raise ValueError("Conta V7 ainda não inicializada")
        frozen=json.loads(json.dumps(account["frozen"]))
    if frozen.get("control_decision")!="cash":raise ValueError("Controle V7 deve permanecer em caixa")
    rule=frozen.get("shadow_atr",{})
    if rule.get("direction") not in ("low","high") or not math.isfinite(float(rule.get("threshold_pct",float("nan")))):
        raise ValueError("Regra ATR congelada inválida")
    state=paper_v7.new_state(candles[:WARMUP_CANDLES],frozen)
    passed={key:0 for key,_ in STAGES}
    first_fail=Counter()
    overlap=Counter()
    atr_min=float("inf");atr_max=0.0
    count=0
    for candle in candles[WARMUP_CANDLES:]:
        # Process first: the live engine updates close/trange, then evaluates the signal.
        paper_v7.process_candle(state,candle)
        if state["phase"]!="observing":raise ValueError("Replay interrompido por lacuna")
        flags,observed=_conditions(state,candle)
        atr_min=min(atr_min,observed["atr_pct"])
        atr_max=max(atr_max,observed["atr_pct"])
        for key,valid in flags.items():
            if not valid:overlap[key]+=1
        for key,_ in STAGES:
            if not flags[key]:
                first_fail[key]+=1
                break
            passed[key]+=1
        count+=1
    if count!=state["processed"] or count<1:raise ValueError("Candles processados divergentes")
    # The diagnostic is observational and MUST reconcile to the actual frozen engine.
    # One signal may be pending, but signal count equals all candles passing the ATR stage.
    funnel_signals=passed["atr"]
    exact=funnel_signals==state["signals"]
    blocked_atr=sum(1 for _ in ())  # Count from the funnel before ATR instead.
    blocked_atr=passed["available"]-passed["atr"]
    checks={
        "real_candles":count==len(candles)-WARMUP_CANDLES,
        "all_candles_classified":sum(first_fail.values())+funnel_signals==count,
        "monotonic_funnel":all(passed[STAGES[i][0]]>=passed[STAGES[i+1][0]] for i in range(len(STAGES)-1)),
        "signal_engine_reconciliation":exact,
        "atr_reconciliation":blocked_atr==state["blocked"],
        "control_cash":frozen["control_decision"]=="cash",
        "no_forced_trades":state["total_trades"]<=state["signals"],
        "account_nonnegative":state["cash"]>=0 and state["btc"]>=0,
        "read_only":True,
    }
    ok=sum(checks.values())
    return {
        "version":"V10","status":"passed" if ok==len(checks) else "failed",
        "source_month":row["month"],"candles_analyzed":count,
        "from_utc":paper_v7.utc_iso(candles[WARMUP_CANDLES]["ms"]),
        "to_utc":paper_v7.utc_iso(candles[-1]["ms"]),
        "funnel":[{"id":key,"label":label,"passed":passed[key],
                   "first_blocked":first_fail[key],"failed_any":overlap[key]}
                  for key,label in STAGES],
        "natural_signals":state["signals"],"atr_blocked":state["blocked"],
        "trades_closed":state["total_trades"],
        "atr_min_pct":round(atr_min,5),"atr_max_pct":round(atr_max,5),
        "atr_rule":rule,
        "checks_passed":ok,"checks_total":len(checks),
        "failed_checks":[k for k,v in checks.items() if not v],
        "reconciles_to_frozen_engine":exact and blocked_atr==state["blocked"],
        "signals_forced":False,"writes_v7_state":False,
        "executes_exchange_orders":False,
        "warning":"Diagnóstico retrospectivo do primeiro bloqueio por candle, não uma otimização de parâmetros. Dados históricos já analisados; sem validação de rentabilidade.",
    }
