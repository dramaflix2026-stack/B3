"""Descriptive analysis of persisted V6 trades; no signals or backtest changes."""
from collections import defaultdict
from statistics import median
from math import isfinite

DURATION_BUCKETS = (
    ("same_minute", "Mesmo minuto", lambda n: n == 0),
    ("1_30m", "1 a 30 min", lambda n: 1 <= n <= 30),
    ("31_240m", "31 a 240 min", lambda n: 31 <= n <= 240),
    ("241plus", "Acima de 240 min", lambda n: n > 240),
)
MIN_SAMPLE_FOR_COMPARISON = 10


def _number(value, key):
    n = float(value)
    if not isfinite(n):
        raise ValueError("Valor inválido: " + key)
    return n


def _summarize(trades):
    count = len(trades)
    profits = [_number(t["net_usdt"], "net_usdt") for t in trades]
    winners = [v for v in profits if v > 0]
    losers = [v for v in profits if v <= 0]
    commission = sum(_number(t["commissions_usdt"], "commissions_usdt") for t in trades)
    slippage = sum(_number(t["slippage_usdt"], "slippage_usdt") for t in trades)
    gross = sum(_number(t["gross_before_costs_usdt"], "gross_before_costs_usdt") for t in trades)
    if any(_number(t["commissions_usdt"], "commissions_usdt") < 0 for t in trades):
        raise ValueError("Comissão negativa no registro")
    if any(_number(t["slippage_usdt"], "slippage_usdt") < 0 for t in trades):
        raise ValueError("Slippage negativo no registro")
    return {
        "trades": count,
        "wins": len(winners),
        "losses_or_zero": len(losers),
        "win_rate_pct": round(100 * len(winners) / count, 2) if count else None,
        "net_usdt": round(sum(profits), 2),
        "gross_before_costs_usdt": round(gross, 2),
        "commissions_usdt": round(commission, 2),
        "slippage_usdt": round(slippage, 2),
        "total_costs_usdt": round(commission + slippage, 2),
        "avg_net_usdt": round(sum(profits) / count, 3) if count else None,
        "avg_win_usdt": round(sum(winners) / len(winners), 3) if winners else None,
        "avg_loss_usdt": round(sum(losers) / len(losers), 3) if losers else None,
        "median_net_usdt": round(median(profits), 3) if count else None,
        "profit_factor": round(sum(winners) / -sum(v for v in losers if v < 0), 3)
                         if any(v < 0 for v in losers) else None,
        "sample_small": count < MIN_SAMPLE_FOR_COMPARISON,
    }


def _buckets(trades, key_fn, labels=None):
    groups = defaultdict(list)
    for trade in trades:
        groups[str(key_fn(trade))].append(trade)
    result = []
    for key, group in sorted(groups.items()):
        result.append({"key": key, "label": labels.get(key, key) if labels else key,
                       **_summarize(group)})
    return result


def analyze(ledger):
    if ledger.get("status") != "complete" or not isinstance(ledger.get("trades"), list):
        raise ValueError("Relatório de operações inválido")
    trades = ledger["trades"]
    robot = int(ledger.get("robot", 0))
    if robot not in (8, 9, 10):
        raise ValueError("Robô inválido")
    if not ledger.get("summary", {}).get("reconciled"):
        raise ValueError("Histórico ainda não conciliado com o backtest")
    if len(trades) != ledger["summary"]["trades"]:
        raise ValueError("Contagem de operações inconsistente")
    total = _summarize(trades)
    if abs(total["net_usdt"] - ledger["summary"]["net_usdt"]) > .03:
        raise ValueError("Resultado agregado não confere com o backtest")
    for t in trades:
        if int(t["robot"]) != robot:
            raise ValueError("Operação pertence a outro robô")
        duration = int(t["duration_minutes"])
        if duration < 0:
            raise ValueError("Duração negativa")
        if abs(_number(t["gross_before_costs_usdt"], "gross") -
               _number(t["commissions_usdt"], "commission") -
               _number(t["slippage_usdt"], "slippage") -
               _number(t["net_usdt"], "net")) > .002:
            raise ValueError("Inconsistência entre lucro bruto, custos e líquido")

    exit_labels = {
        "stop_loss": "Stop-loss", "take_profit": "Take-profit",
        "max_hold": "Tempo máximo", "window_end": "Fim da janela",
        "data_gap": "Lacuna nos dados",
    }
    durations = []
    for key, label, condition in DURATION_BUCKETS:
        group = [t for t in trades if condition(int(t["duration_minutes"]))]
        durations.append({"key": key, "label": label, **_summarize(group)})

    by_exit = _buckets(trades, lambda t: t["exit_reason"], exit_labels)
    by_window = _buckets(trades, lambda t: str(t["window"]))
    by_regime = _buckets(trades, lambda t: t.get("regime_on_entry") or "unknown", {
        "uptrend": "Tendência de alta", "downtrend": "Tendência de baixa",
        "sideways": "Lateralização", "unknown": "Não classificado",
    })
    # Analyse feature quartiles descriptively: no hindsight rule is generated.
    feature_rows = []
    for feature, title in (
        ("atr_14_pct", "ATR 14 (% da cotação)"),
        ("volatility_240_pct", "Volatilidade 240 candles (%)"),
        ("momentum_60_pct", "Momentum 60 candles (%)"),
    ):
        values = sorted((_number(t["signal"][feature], feature), idx)
                        for idx, t in enumerate(trades)
                        if isinstance(t.get("signal"), dict)
                        and t["signal"].get(feature) is not None)
        if len(values) < 4:
            continue
        for quartile in range(4):
            a = len(values) * quartile // 4
            b = len(values) * (quartile + 1) // 4
            selected = values[a:b]
            group = [trades[idx] for _, idx in selected]
            feature_rows.append({
                "feature": feature, "feature_label": title,
                "quartile": quartile + 1,
                "min_value": round(selected[0][0], 4),
                "max_value": round(selected[-1][0], 4),
                **_summarize(group),
            })

    stop = next((x for x in by_exit if x["key"] == "stop_loss"), None)
    time_exit = next((x for x in by_exit if x["key"] == "max_hold"), None)
    zero_min = next(x for x in durations if x["key"] == "same_minute")
    return {
        "status": "complete",
        "version": "trade-diagnostics-1",
        "robot": robot,
        "source_created_at": ledger.get("created_at"),
        "summary": total,
        "by_exit": by_exit,
        "by_duration": durations,
        "by_window": by_window,
        "by_regime": by_regime,
        "signal_quartiles": feature_rows,
        "observations": {
            "stop_loss_trades": stop["trades"] if stop else 0,
            "stop_loss_net_usdt": stop["net_usdt"] if stop else 0,
            "max_hold_trades": time_exit["trades"] if time_exit else 0,
            "max_hold_net_usdt": time_exit["net_usdt"] if time_exit else 0,
            "same_minute_trades": zero_min["trades"],
            "total_net_usdt": total["net_usdt"],
        },
        "notes": [
            "Grupos e quartis descrevem dados históricos já vistos; não são provas de causalidade nem filtros operacionais validados.",
            "Grupos com menos de 10 operações possuem amostra pequena; diferenças podem ocorrer por acaso.",
            "A simulação usa candles de um minuto e pressupõe stop executável dentro do candle, sem livro de ofertas, latência ou sequência intraminuto.",
            "Comissões e slippage já estão descontados dos resultados líquidos. Os cinco períodos reiniciam o capital e não representam juros compostos.",
        ],
    }
