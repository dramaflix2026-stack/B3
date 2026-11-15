"""Cost sensitivity and chronological eligibility audit for V6 research only.

This is a post-processing stress test, not a fee-adjusted execution backtest.
Fee scenarios keep fills, sizes, and trades fixed; slippage remains embedded.
"""
from math import isfinite

ROBOT_IDS = ("8", "9", "10")
FEE_MULTIPLIERS = (0.0, 0.5, 1.0, 1.5, 2.0)
MIN_TRAIN_TRADES = 20


def _num(value, field):
    number = float(value)
    if not isfinite(number):
        raise ValueError("Invalid numeric field: " + field)
    return number


def _round(amount):
    return round(float(amount), 2)


def _window_metrics(window):
    net = _num(window["net_usdt"], "net_usdt")
    fees = _num(window["fees_usdt"], "fees_usdt")
    trades = int(window["trades"])
    drawdown = _num(window["max_realized_drawdown_usdt"], "max_realized_drawdown_usdt")
    if fees < 0 or trades < 0 or drawdown < 0:
        raise ValueError("Invalid nonnegative trading metric")
    gross_after_slippage = net + fees
    return {
        "index": int(window["index"]),
        "trades": trades,
        "net_usdt": net,
        "fees_usdt": fees,
        "gross_after_slippage_usdt": gross_after_slippage,
        "drawdown_usdt": drawdown,
    }


def _fee_scenario(windows, multiplier):
    # Static approximation; changing commissions would change future equity/sizing.
    return _round(sum(w["net_usdt"] + (1.0 - multiplier) * w["fees_usdt"]
                      for w in windows))


def _past_gate(windows):
    total_trades = sum(w["trades"] for w in windows)
    net = sum(w["net_usdt"] for w in windows)
    fees = sum(w["fees_usdt"] for w in windows)
    net_stressed = sum(w["net_usdt"] - 0.5 * w["fees_usdt"] for w in windows)
    peak_dd = max(w["drawdown_usdt"] for w in windows)
    reasons = []
    if total_trades < MIN_TRAIN_TRADES:
        reasons.append("Amostra anterior inferior a 20 operações")
    if any(w["net_usdt"] <= 0 for w in windows):
        reasons.append("Ao menos uma janela anterior sem lucro líquido")
    if any(w["net_usdt"] - 0.5 * w["fees_usdt"] <= 0 for w in windows):
        reasons.append("Não resistiu a +50% nas comissões")
    if net <= 0.5 * fees:
        reasons.append("Lucro anterior insuficiente frente às comissões")
    if net <= peak_dd:
        reasons.append("Lucro anterior não superou o maior drawdown")
    score = net - 0.5 * fees - peak_dd
    return {
        "eligible": not reasons and score > 0,
        "reasons": reasons,
        "train_trades": total_trades,
        "train_net_usdt": _round(net),
        "train_net_stressed_usdt": _round(net_stressed),
        "train_fees_usdt": _round(fees),
        "train_max_drawdown_usdt": _round(peak_dd),
        "score_usdt": _round(score),
    }


def analyze(report):
    if not isinstance(report, dict) or not report.get("robots"):
        raise ValueError("V6 report is missing robot results")

    metrics_by_robot = {}
    robot_cards = []
    for robot_id in ROBOT_IDS:
        robot = report["robots"].get(robot_id)
        if not robot or len(robot.get("windows", [])) != 5:
            raise ValueError("V6 report requires five windows for robot " + robot_id)
        windows = [_window_metrics(w) for w in robot["windows"]]
        if [w["index"] for w in windows] != [1, 2, 3, 4, 5]:
            raise ValueError("Window indices must be chronological 1..5")
        metrics_by_robot[robot_id] = windows

        trades = sum(w["trades"] for w in windows)
        fees = sum(w["fees_usdt"] for w in windows)
        net = sum(w["net_usdt"] for w in windows)
        gross = net + fees
        cases = [{"fee_multiplier": multiplier,
                  "estimated_net_usdt": _fee_scenario(windows, multiplier)}
                 for multiplier in FEE_MULTIPLIERS]
        robot_cards.append({
            "robot": int(robot_id),
            "trades": trades,
            "net_usdt": _round(net),
            "fees_usdt": _round(fees),
            "gross_after_slippage_usdt": _round(gross),
            "net_per_trade_usdt": _round(net / trades) if trades else None,
            "fees_per_trade_usdt": _round(fees / trades) if trades else None,
            "max_window_drawdown_usdt": _round(max(w["drawdown_usdt"] for w in windows)),
            "profitable_windows": sum(w["net_usdt"] > 0 for w in windows),
            "fee_scenarios": cases,
            "caveat": "Static commission sensitivity; not a new backtest",
        })

    folds = []
    for test_index in (2, 3, 4):
        eligible = []
        candidates = []
        for robot_id in ROBOT_IDS:
            previous = metrics_by_robot[robot_id][test_index - 2:test_index]
            gate = _past_gate(previous)
            candidates.append({"robot": int(robot_id), **gate})
            if gate["eligible"]:
                eligible.append((gate["score_usdt"], robot_id))
        chosen = max(eligible)[1] if eligible else "cash"
        outcome = (metrics_by_robot[chosen][test_index]
                   if chosen != "cash" else {"net_usdt": 0.0, "trades": 0, "fees_usdt": 0.0})
        folds.append({
            "train_windows": [test_index - 1, test_index],
            "test_window": test_index + 1,
            "selected": chosen,
            "candidate_assessment": candidates,
            "test_net_usdt": _round(outcome["net_usdt"]),
            "test_trades": outcome["trades"],
            "test_fees_usdt": _round(outcome["fees_usdt"]),
        })

    return {
        "status": "complete",
        "version": "v6-cost-edge-audit-v1",
        "source_version": report.get("version"),
        "source_created_at": report.get("created_at"),
        "fee_assumption": {
            "base_fee_each_side": .001,
            "slippage_each_side": .0001,
            "fee_multipliers": list(FEE_MULTIPLIERS),
            "sensitivity_method": "Fixed historical trades and fills; net + (1-factor)*recorded fees",
            "warning": "Commission stress is approximate. Changed fees alter position size and trade path. Slippage is retained.",
        },
        "robot_audit": robot_cards,
        "past_only_selection": {
            "folds": folds,
            "cash_folds": sum(f["selected"] == "cash" for f in folds),
            "test_net_sum_usdt": _round(sum(f["test_net_usdt"] for f in folds)),
            "criteria": "At least 20 past trades; positive net in both previous windows even with +50% commissions; net exceeds half fees and peak drawdown. Otherwise cash.",
        },
        "limitations": "Retrospective selection using data already inspected in previous V4-V6 research. No independent out-of-sample, no live execution, and no prediction or guarantee.",
    }
