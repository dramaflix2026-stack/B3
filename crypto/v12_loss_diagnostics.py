"""V12 forensic diagnosis of historical V11 virtual losses; read-only.

No new optimization, trading strategy, exchange calls, or writes to V7.
MFE/MAE use whole candle OHLC and are bounds, not executable opportunities.
"""
import copy
import math
import pathlib
import statistics

import paper_v7 as paper
from v10_signal_diagnostics import _read_verified
from v11_breakout_audit import BREAKOUT_BUFFERS, WARMUP, _simulate

def _average(xs):
    return round(statistics.mean(xs),4) if xs else None

def _variant(candles,frozen,buffer):
    result=_simulate(candles,frozen,buffer,collect_trades=True)
    trades=result.pop("trade_details")
    stops=[t for t in trades if t["reason"]=="stop"]
    timed=[t for t in trades if t["reason"]=="time"]
    wins=[t["net_usdt"] for t in trades if t["net_usdt"]>0]
    losses=[t["net_usdt"] for t in trades if t["net_usdt"]<=0]
    gross=sum(t["gross_usdt"] for t in trades)
    fees=sum(t["entry_fee_usdt"]+t["exit_fee_usdt"] for t in trades)
    slips=sum(t["slippage_usdt"] for t in trades)
    net=sum(t["net_usdt"] for t in trades)
    return {**result,
      "stop_count":len(stops),"time_count":len(timed),
      "average_win_usdt":_average(wins),"average_loss_usdt":_average(losses),
      "average_holding_minutes":_average([t["holding_minutes"] for t in trades]),
      "average_mfe_bar_pct":_average([t["mfe_pct"] for t in trades]),
      "average_mae_bar_pct":_average([t["mae_pct"] for t in trades]),
      "closed_gross_before_fees_usdt":round(gross,4),
      "closed_fees_usdt":round(fees,4),
      "closed_slippage_usdt":round(slips,4),
      "closed_net_usdt":round(net,4),
      "trades":[{"entry_utc":paper.utc_iso(t["entry_ms"]),
                 "exit_utc":paper.utc_iso(t["exit_ms"]),
                 "reason":t["reason"],"minutes":t["holding_minutes"],
                 "gross_usdt":round(t["gross_usdt"],4),
                 "fees_usdt":round(t["entry_fee_usdt"]+t["exit_fee_usdt"],4),
                 "slippage_usdt":round(t["slippage_usdt"],4),
                 "net_usdt":round(t["net_usdt"],4),
                 "mfe_bar_pct":round(t["mfe_pct"],4),
                 "mae_bar_pct":round(t["mae_pct"],4)}
                for t in trades],
      "ledger_reconciles":len(trades)==result["trades_closed"]
         and abs(net-result["realized_net_usdt"])<.0002
         and abs(fees-result["fees_usdt"])<.0002,
    }

def run(data_dir=None):
    root=pathlib.Path(data_dir if data_dir is not None else paper.DATA)
    row,candles=_read_verified(root)
    with paper.LOCK:
        account=paper._load()
        if account is None:raise ValueError("Conta V7 não inicializada")
        frozen=copy.deepcopy(account["frozen"])
    if frozen.get("control_decision")!="cash":raise ValueError("Controle V7 não está em caixa")
    if frozen.get("shadow_atr",{}).get("direction") not in ("low","high"):
        raise ValueError("Regra ATR congelada inválida")
    variants=[_variant(candles,frozen,b) for b in BREAKOUT_BUFFERS]
    checks={
      "ledger_all_variants":all(v["ledger_reconciles"] for v in variants),
      "all_exit_reasons":all(v["stop_count"]+v["time_count"]==v["trades_closed"] for v in variants),
      "gross_minus_fees_equals_net":all(abs(v["closed_gross_before_fees_usdt"]-
         v["closed_fees_usdt"]-v["closed_net_usdt"])<.0003 for v in variants),
      "no_forced_trades":all(v["trades_closed"]<=v["signals"] for v in variants),
      "baseline_reference_unchanged":variants[-1]["buffer_pct"]==.6,
      "finite_results":all(math.isfinite(v["marked_net_usdt"]) for v in variants),
      "cash_control":frozen["control_decision"]=="cash",
      "valid_month":len(candles)>WARMUP,
    }
    passed=sum(checks.values())
    return {"version":"V12","status":"passed" if passed==len(checks) else "failed",
      "source_month":row["month"],"candles_analyzed":len(candles)-WARMUP,
      "checks_passed":passed,"checks_total":len(checks),
      "failed_checks":[k for k,v in checks.items() if not v],
      "variants":variants,"writes_v7_state":False,"executes_exchange_orders":False,
      "out_of_sample":False,"changes_strategy":False,
      "mfe_mae_caveat":"Máxima e mínima da vela inteira; não representam preços necessariamente negociáveis após a entrada ou antes da saída.",
      "warning":"Diagnóstico de perdas de operações históricas simuladas. Não é validação independente nem indicação para negociar com dinheiro real."}
