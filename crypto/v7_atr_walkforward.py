"""Isolated executable ATR walk-forward experiment on BTC spot 1-minute history.

The selection source is the previously persisted, ORIGINAL V6 robot 9 ledger.
Each fold chooses a rule from only two preceding windows. Entry gating is then
applied to the V6 signal engine BEFORE order execution. Capital, cooldown,
fees and stop/fill logic are recomputed from scratch. No real orders.
"""
from entry_quality import analyze as analyze_entry_quality
from spot_backtest_v6 import test as run_v6

SOURCE_ROBOT = 9
TEST_WINDOWS = (3, 4, 5)
STRATEGY_NAME = "V7 · ATR filtered robot 9 (research only)"


def run(original_ledger, on_progress=None):
    if original_ledger is None or original_ledger.get("robot") != SOURCE_ROBOT:
        raise ValueError("V7 requires a persisted V6 robot 9 trade ledger")
    if original_ledger.get("source_engine") != "spot-v6-walk-forward-research":
        raise ValueError("V6 source engine version mismatch")
    source = analyze_entry_quality(original_ledger)
    shadow_folds = source["chronological_shadow"]["folds"]
    original = {int(w["index"]): w for w in original_ledger["summary"]["windows"]}
    rules = {}
    for fold in shadow_folds:
        window = int(fold["test_window"])
        rule = fold.get("selected_rule")
        # Only the ATR hypothesis is permitted in this isolated V7 test.
        # Prior selection of momentum or other features is treated as cash.
        if rule is not None and rule["feature"] == "atr_14_pct":
            rules[window] = {
                "direction": rule["direction"],
                "threshold": float(rule["threshold"]),
            }
        else:
            rules[window] = None
    if set(rules) != set(TEST_WINDOWS):
        raise ValueError("Chronological selection must cover P3, P4 and P5")

    executed_trades = []
    rerun = run_v6(
        on_progress=on_progress,
        robot_ids=[SOURCE_ROBOT],
        windows="five",
        diagnostics=True,
        trade_log=executed_trades,
        atr_gate_by_window=rules,
    )
    windows = {int(w["index"]): w for w in rerun["robots"][str(SOURCE_ROBOT)]["windows"]}

    # Reconciliation prevents a simulated fold from being declared complete when
    # trades and the newly recomputed V6 engine metrics diverge.
    for number, w in windows.items():
        local = [t for t in executed_trades if t["window"] == number]
        if len(local) != w["trades"]:
            raise ValueError("V7 trade-count discrepancy in P" + str(number))
        if abs(sum(t["net_usdt"] for t in local) - w["net_usdt"]) > .03:
            raise ValueError("V7 net reconciliation discrepancy in P" + str(number))
        if abs(sum(t["commissions_usdt"] for t in local) - w["fees_usdt"]) > .03:
            raise ValueError("V7 commission reconciliation discrepancy in P" + str(number))
    if any(windows[k]["trades"] != 0 for k in (1, 2)):
        raise ValueError("Training windows must remain cash-only in V7")

    folds = []
    for choice in shadow_folds:
        number = int(choice["test_window"])
        rule = rules[number]
        current = windows[number]
        baseline = original[number]
        if rule is None and (current["trades"] != 0 or abs(current["net_usdt"]) > .01):
            raise ValueError("Non-eligible fold must remain entirely in cash")
        counts = current.get("atr_gate_signal_counts") or {}
        folds.append({
            "train_windows": list(choice["train_windows"]),
            "test_window": number,
            "selected": "atr_gate" if rule is not None else "cash",
            "atr_rule": rule,
            "training_eligible_candidates": choice["eligible_candidates"],
            "original_v6": {
                "trades": baseline["trades"],
                "net_usdt": baseline["net_usdt"],
                "fees_usdt": baseline["fees_usdt"],
                "max_drawdown_usdt": baseline["max_drawdown_usdt"],
            },
            "executed_v7": {
                "trades": current["trades"],
                "wins_pct": current["win_rate_pct"],
                "net_usdt": current["net_usdt"],
                "fees_usdt": current["fees_usdt"],
                "gross_after_slippage_usdt": current["gross_usdt"],
                "max_drawdown_usdt": current["max_realized_drawdown_usdt"],
                "ending_equity_usdt": current["ending_equity_usdt"],
                "stop_loss_trades": (current.get("trade_diagnostics") or {}).get("exit_reasons", {}).get("stop_loss", {}).get("trades", 0),
                "signals_before_gate": counts.get("before", 0),
                "signals_passed_gate": counts.get("passed", 0),
                "signals_rejected_gate": counts.get("rejected", 0),
            },
            "net_difference_vs_v6_usdt": round(current["net_usdt"] - baseline["net_usdt"], 2),
        })
    total_net = round(sum(f["executed_v7"]["net_usdt"] for f in folds), 2)
    total_baseline = round(sum(f["original_v6"]["net_usdt"] for f in folds), 2)
    traded = [f for f in folds if f["selected"] != "cash"]
    return {
        "status": "complete",
        "version": "v7-atr-executable-walkforward-1",
        "strategy_name": STRATEGY_NAME,
        "source_v6_ledger_created_at": original_ledger["created_at"],
        "selected_robots": [SOURCE_ROBOT],
        "rules": rules,
        "source_selection": "entry_quality.chronological_shadow: 2 prior windows, 6 pre-entry candidate filters, ATR-only acceptance, otherwise cash",
        "folds": folds,
        "summary": {
            "folds": len(folds),
            "cash_folds": sum(f["selected"] == "cash" for f in folds),
            "trading_folds": len(traded),
            "net_sum_usdt": total_net,
            "original_v6_test_window_net_sum_usdt": total_baseline,
            "difference_vs_original_v6_usdt": round(total_net - total_baseline, 2),
            "trades": sum(f["executed_v7"]["trades"] for f in folds),
            "fees_usdt": round(sum(f["executed_v7"]["fees_usdt"] for f in folds), 2),
        },
        "execution_assumptions": rerun["assumptions"],
        "executed_trade_log": executed_trades,
        "validation_note": (
            "Executable historical OHLC simulation using fixed V6 robot 9 signals with a pre-entry ATR gate. "
            "Each evaluated fold is independently restarted at USD 1000 and trained only using two preceding "
            "baseline V6 windows. These periods were previously inspected in V4-V6 research; "
            "selection and validation are NOT untouched out-of-sample. "
            "Aggregate fold results are NOT compounded and are not evidence of future profitability."
        ),
    }
