"""Historical V6 entry-quality investigation. No orders, strategy changes, or forward claims.

A candidate screen is learned only from the two windows before a test window.
"Shadow" filtered trades are subsets of the ORIGINAL executed ledger: this is
NOT an executable alternative backtest (capital, fills, cooldown, sizing change).
"""
from math import isfinite
from statistics import median

FEATURES = (
    ("atr_14_pct", "ATR 14 (% preço)"),
    ("volatility_240_pct", "Volatilidade 240 (%)"),
    ("momentum_60_pct", "Momentum 60 (%)"),
)
EARLY_MINUTES = 30
MIN_TRAIN_TRADES = 10
MIN_TRADES_EACH_TRAIN_WINDOW = 3


def number(trade, field):
    value = float(trade[field])
    if not isfinite(value):
        raise ValueError("Valor inválido: " + field)
    return value


def signal_value(trade, feature):
    context = trade.get("signal")
    if not isinstance(context, dict) or context.get(feature) is None:
        return None
    value = float(context[feature])
    return value if isfinite(value) else None


def stats(trades):
    net = sum(number(t, "net_usdt") for t in trades)
    fee = sum(number(t, "commissions_usdt") for t in trades)
    slip = sum(number(t, "slippage_usdt") for t in trades)
    stop = [t for t in trades if t.get("exit_reason") == "stop_loss"]
    return {"trades": len(trades), "net_usdt": round(net, 2),
            "commissions_usdt": round(fee, 2), "slippage_usdt": round(slip, 2),
            "net_per_trade_usdt": round(net / len(trades), 3) if trades else None,
            "stop_loss_trades": len(stop),
            "stop_rate_pct": round(100 * len(stop) / len(trades), 2) if trades else None,
            "profitable_trades": sum(t["net_usdt"] > 0 for t in trades),
            "net_if_fees_plus_50_pct_usdt": round(net - .5 * fee, 2)}


def early_stop_report(trades):
    early = [t for t in trades if int(t["duration_minutes"]) <= EARLY_MINUTES]
    late = [t for t in trades if int(t["duration_minutes"]) > EARLY_MINUTES]
    early_stops = [t for t in early if t["exit_reason"] == "stop_loss"]
    early_wins = [t for t in early if t["net_usdt"] > 0]
    return {
        "early_minutes": EARLY_MINUTES,
        "early": stats(early), "later": stats(late),
        "early_stops": stats(early_stops),
        "early_wins": len(early_wins),
        "same_minute": stats([t for t in early if int(t["duration_minutes"]) == 0]),
        "warning": "A duração da operação só é conhecida depois da entrada: ela não pode ser usada como filtro de entrada sem viés de antecipação."
    }


def signal_contrast(trades):
    groups = (
        ("wins", "Vencedoras", lambda t: t["net_usdt"] > 0),
        ("losses", "Perdedoras/zero", lambda t: t["net_usdt"] <= 0),
        ("early_stops", "Stop até 30 min", lambda t: t["exit_reason"] == "stop_loss"
         and int(t["duration_minutes"]) <= EARLY_MINUTES),
    )
    output = []
    for key, title in FEATURES:
        rows = []
        for group_key, name, pred in groups:
            entries = [signal_value(t, key) for t in trades if pred(t)]
            values = [x for x in entries if x is not None]
            rows.append({"group": group_key, "label": name,
                         "trades": len(values), "median": round(median(values), 4) if values else None,
                         "small_sample": len(values) < 10})
        output.append({"feature": key, "label": title, "groups": rows})
    return output


def pass_filter(trade, feature, direction, threshold):
    value = signal_value(trade, feature)
    if value is None:
        return False
    return value >= threshold if direction == "high" else value < threshold


def chronological_shadow(trades):
    folds = []
    for test_window in (3, 4, 5):
        previous = (test_window - 2, test_window - 1)
        training = [t for t in trades if t["window"] in previous]
        test = [t for t in trades if t["window"] == test_window]
        candidates = []
        for feature, name in FEATURES:
            values = [signal_value(t, feature) for t in training]
            values = [v for v in values if v is not None]
            if len(values) < MIN_TRAIN_TRADES:
                continue
            threshold = median(values)
            for direction in ("high", "low"):
                selected_train = [t for t in training if pass_filter(t, feature, direction, threshold)]
                count_by_window = [sum(t["window"] == w for t in selected_train) for w in previous]
                summary = stats(selected_train)
                each_positive = all(
                    sum(number(t, "net_usdt") for t in selected_train if t["window"] == w) > 0
                    for w in previous
                )
                passes = (
                    summary["trades"] >= MIN_TRAIN_TRADES
                    and all(count >= MIN_TRADES_EACH_TRAIN_WINDOW for count in count_by_window)
                    and each_positive
                    and summary["net_if_fees_plus_50_pct_usdt"] > 0
                    and summary["net_usdt"] > summary["commissions_usdt"]
                )
                if passes:
                    # Score only from previous windows, never from current or future trades.
                    score = (summary["net_if_fees_plus_50_pct_usdt"]
                             - .3 * summary["commissions_usdt"])
                    if score > 0:
                        candidates.append((score, feature, name, direction, threshold, summary))
        candidates.sort(key=lambda x: x[0], reverse=True)
        selected = candidates[0] if candidates else None
        shadow = ([t for t in test if pass_filter(t, selected[1], selected[3], selected[4])]
                  if selected else [])
        folds.append({
            "train_windows": list(previous), "test_window": test_window,
            "candidates_considered": len(FEATURES) * 2,
            "eligible_candidates": len(candidates),
            "selected_rule": ({
                "feature": selected[1], "label": selected[2],
                "direction": selected[3], "threshold": round(selected[4], 4),
                "training": selected[5],
            } if selected else None),
            "observed_original_trades": stats(test),
            "shadow_retained_trades": stats(shadow),
            "shadow_skipped_trades": len(test) - len(shadow),
            "decision": "shadow_filter" if selected else "cash",
        })
    return {"folds": folds, "cash_folds": sum(x["decision"] == "cash" for x in folds),
            "note": "Experimental historical trade subset only, not an executable backtest. Skipping trades changes cooldown, sizing, fees and later signals. Uses prior-window outcomes for selection; historical data were already inspected."}


def analyze(ledger):
    trades = ledger.get("trades")
    if ledger.get("status") != "complete" or not isinstance(trades, list):
        raise ValueError("Histórico de operações inválido")
    robot = int(ledger.get("robot", 0))
    if robot not in (8, 9, 10):
        raise ValueError("Robô V6 inválido")
    summary = ledger.get("summary", {})
    if not summary.get("reconciled") or len(trades) != summary.get("trades"):
        raise ValueError("Histórico não conciliado")
    original_net = stats(trades)["net_usdt"]
    if abs(original_net - float(summary.get("net_usdt", 0))) > .03:
        raise ValueError("Resultados não conciliados")
    for t in trades:
        if t["window"] not in (1, 2, 3, 4, 5) or t["robot"] != robot:
            raise ValueError("Operação de outro robô ou janela")
    return {
        "status": "complete", "version": "v6-entry-investigation-1", "robot": robot,
        "source_created_at": ledger.get("created_at"),
        "overall": stats(trades),
        "early_stop": early_stop_report(trades),
        "signal_contrast": signal_contrast(trades),
        "chronological_shadow": chronological_shadow(trades),
        "limitations": [
            "A duração e o motivo da saída são posteriores à entrada; nunca utilizados para escolher sinais na seleção cronológica.",
            "Filtros de sombra usam apenas indicadores disponíveis antes da entrada, mas não simulam novamente execução ou capital.",
            "A seleção usa apenas duas janelas anteriores por rodada, porém todo o histórico foi previamente examinado no desenvolvimento das versões V4-V6.",
            "Amostras pequenas e múltiplas comparações podem gerar diferenças espúrias. Não autoriza operações reais.",
        ]
    }
