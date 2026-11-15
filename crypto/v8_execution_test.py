"""V8 - deterministic, ephemeral execution-path test using the frozen V7 paper engine.

Synthetic OHLC scenarios only. Tests mechanics, NOT strategy profitability or
market signal quality. Does not read/write the Railway paper state/volume,
invoke exchange APIs, or change the V7 strategy.
"""
from datetime import datetime, timezone
from math import isclose, isfinite

from paper_v7 import (
    CANDLE_MS, COOLDOWN_MINUTES, FEE, HOLD_MINUTES, INITIAL_CASH, SLIP,
    new_state, process_candle,
)

START_MS = 1_731_000_000_000 // CANDLE_MS * CANDLE_MS
SCENARIO_ATR = 0.2
TOLERANCE = 0.001


def _candle(ms, open_price=100.0, high=100.2, low=99.9, close=100.0):
    assert low <= open_price <= high and low <= close <= high
    return {"ms": ms, "o": open_price, "h": high, "l": low, "c": close}


def _state():
    # 721 fictitious contiguous closed candles, only to warm indicators.
    ready = [_candle(START_MS + i*CANDLE_MS) for i in range(721)]
    frozen = {
        "source_report": "V8_SYNTHETIC_ONLY",
        "sha256": "0"*64,
        "frozen_at_utc": "2026-11-15T00:00:00Z",
        "control_decision": "cash",
        "control_reason": "V7 controls remain unchanged",
        "shadow_atr": {"direction": "low", "threshold_pct": 0.1679},
        "fee_each_side": FEE,
        "slippage_each_side": SLIP,
    }
    return new_state(ready, frozen)


def _inject_synthetic_signal(state):
    """Seed only this isolated copy to test next-open fills; NOT a real signal."""
    state["pending"] = {
        "atr": SCENARIO_ATR,
        "atr_pct": SCENARIO_ATR / 100.0 * 100.0,
        "signal_ms": state["last_ms"],
    }


def _verify(state, name, expected_reason, expected_duration, expected_exit_raw):
    checks = []
    def record(key, condition):
        checks.append({"key": key, "passed": bool(condition)})
    trades = state["trades"]
    trade = trades[-1] if trades else {}
    qty = float(trade.get("quantity_btc", 0)) if "quantity_btc" in trade else None
    # No production journal contains quantity in the recent-trade shape.
    # Recover it from initial allocation, so the fee and fill checks are independent.
    entry_price = 100.0 * (1+SLIP)
    stop_fraction = max(0.006, min(0.04, SCENARIO_ATR/100.0*2))
    expected_qty = min(
        INITIAL_CASH*0.25/entry_price,
        INITIAL_CASH*0.01/(entry_price*stop_fraction),
        INITIAL_CASH/(entry_price*(1+FEE)),
    )
    expected_exit_price = expected_exit_raw*(1-SLIP)
    expected_fee = expected_qty*entry_price*FEE + expected_qty*expected_exit_price*FEE
    expected_slippage = expected_qty*(entry_price-100.0 + expected_exit_raw-expected_exit_price)
    expected_net = expected_qty*(expected_exit_price-entry_price)-expected_fee
    expected_cash = INITIAL_CASH+expected_net
    record("single_closed_trade", int(state["total_trades"]) == 1 and len(trades) == 1)
    record("next_open_buy_fill", isclose(float(trade.get("entry_price_usdt", 0)), entry_price, abs_tol=0.0001))
    record("correct_exit_reason", trade.get("reason") == expected_reason)
    record("correct_hold_duration", int(trade.get("duration_min", -1)) == expected_duration)
    record("sell_price_includes_slippage", isclose(float(trade.get("exit_price_usdt", 0)), expected_exit_price, abs_tol=0.0001))
    record("fees_on_both_sides", isclose(float(trade.get("commissions_usdt", -1)), expected_fee, abs_tol=TOLERANCE) and state["fees"] > 0)
    record("slippage_on_both_sides", isclose(float(trade.get("slippage_usdt", -1)), expected_slippage, abs_tol=TOLERANCE) and state["slippage"] > 0)
    record("gross_minus_costs_is_net",
           isclose(float(trade.get("gross_before_costs_usdt", 0))-
                   float(trade.get("commissions_usdt", 0))-
                   float(trade.get("slippage_usdt", 0)),
                   float(trade.get("net_usdt", 1)), abs_tol=TOLERANCE))
    record("independent_net_recalculation", isclose(float(trade.get("net_usdt", 0)), expected_net, abs_tol=TOLERANCE))
    record("cash_reconciled", isclose(float(state["cash"]), expected_cash, abs_tol=TOLERANCE))
    record("no_open_position", state["btc"] == 0 and state["entry"] is None)
    record("cooldown_applied", state["cool"] == COOLDOWN_MINUTES-1)
    record("control_still_cash", state["frozen"]["control_decision"] == "cash")
    record("one_virtual_entry_and_exit", [e["type"] for e in state["events"] if e["type"] in ("paper_buy", "paper_sell")] == ["paper_buy", "paper_sell"])
    record("finite_values", all(isfinite(float(v)) for v in (state["cash"],state["fees"],state["slippage"],state["realized"])))
    if qty is not None:
        record("optional_quantity", isclose(qty,expected_qty,abs_tol=1e-8))
    return {
        "scenario": name,
        "status": "passed" if all(x["passed"] for x in checks) else "failed",
        "checks_passed": sum(x["passed"] for x in checks),
        "checks_total": len(checks),
        "failed_checks": [x["key"] for x in checks if not x["passed"]],
        "entry_price_usdt": round(float(trade.get("entry_price_usdt", 0)), 4),
        "exit_price_usdt": round(float(trade.get("exit_price_usdt", 0)), 4),
        "duration_minutes": trade.get("duration_min"),
        "exit_reason": trade.get("reason"),
        "gross_usdt": round(float(trade.get("gross_before_costs_usdt", 0)), 4),
        "fees_usdt": round(float(trade.get("commissions_usdt", 0)), 4),
        "slippage_usdt": round(float(trade.get("slippage_usdt", 0)), 4),
        "net_usdt": round(float(trade.get("net_usdt", 0)), 4),
        "final_cash_usdt": round(float(state["cash"]), 4),
    }


def _test_stop():
    state = _state()
    _inject_synthetic_signal(state)
    first = state["last_ms"]+CANDLE_MS
    # Buy at the following open; first candle does NOT touch stop.
    process_candle(state, _candle(first))
    # One minute later lower wick touches stop = fill * (1-0.6%).
    expected_raw = 100*(1+SLIP)*(1-0.006)
    process_candle(state, _candle(first+CANDLE_MS, open_price=100, high=100.1, low=98.5, close=99))
    return _verify(state,"STOP_LOSS","stop_loss",1,expected_raw)


def _test_time_exit():
    state = _state()
    _inject_synthetic_signal(state)
    first = state["last_ms"]+CANDLE_MS
    process_candle(state, _candle(first))
    # No price ever touches stop. End exactly at the actual 720-min hold limit.
    for i in range(1,HOLD_MINUTES):
        process_candle(state, _candle(first+i*CANDLE_MS))
    exit_ms = first+HOLD_MINUTES*CANDLE_MS
    process_candle(state, _candle(exit_ms,open_price=100,high=102.2,low=99.9,close=102.0))
    return _verify(state,"MAX_HOLD","max_hold",HOLD_MINUTES,102.0)


def _test_gap_pause():
    state = _state()
    previous = state["last_ms"]
    process_candle(state,_candle(previous+2*CANDLE_MS))
    checks = {
        "missing_candle_pauses":state["phase"] == "paused_data_gap",
        "no_virtual_fill":state["cash"] == INITIAL_CASH and state["btc"] == 0,
        "no_fabricated_trade":state["total_trades"] == 0 and not state["trades"],
        "no_fabricated_processed_candles":state["processed"] == 0,
        "last_candle_unchanged":state["last_ms"] == previous,
    }
    return {"scenario":"DATA_GAP","status":"passed" if all(checks.values()) else "failed",
            "checks_passed":sum(checks.values()),"checks_total":len(checks),
            "failed_checks":[k for k,v in checks.items() if not v]}


def run():
    """No persistent writes, no external price/credential/order calls."""
    scenarios = [_test_stop(),_test_time_exit(),_test_gap_pause()]
    count = sum(r["checks_total"] for r in scenarios)
    passed = sum(r["checks_passed"] for r in scenarios)
    return {
        "version":"V8",
        "test_type":"synthetic_execution_mechanics",
        "status":"passed" if count == passed else "failed",
        "checked_at_utc":datetime.now(timezone.utc).isoformat(),
        "checks_passed":passed,
        "checks_total":count,
        "scenarios":scenarios,
        "mode":"in_memory_only",
        "order_execution":"none",
        "persisted_to_v7_state":False,
        "market_data":"synthetic_ohlc_not_binance",
        "warning":"Teste técnico com preços inventados e sinais forçados somente em memória. NÃO mede rentabilidade, não confirma sinais reais e não altera a V7.",
    }
