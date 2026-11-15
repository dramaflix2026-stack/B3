"""Descriptive diagnostics for frozen V7 paper accounts. Never places orders."""
import datetime as dt
from collections import defaultdict


def _round(n):
    return round(float(n), 2)


def _daily(trades):
    buckets = defaultdict(lambda: {"trades": 0, "net_usdt": 0.0, "fees_usdt": 0.0, "slippage_usdt": 0.0})
    for t in trades:
        day = str(t.get("exit_utc", ""))[:10]
        if len(day) != 10:
            continue
        b = buckets[day]
        b["trades"] += 1
        b["net_usdt"] += float(t["net_usdt"])
        b["fees_usdt"] += float(t["commissions_usdt"])
        b["slippage_usdt"] += float(t["slippage_usdt"])
    return [{"day_utc": d, "trades": v["trades"], "net_usdt": _round(v["net_usdt"]),
             "fees_usdt": _round(v["fees_usdt"]), "slippage_usdt": _round(v["slippage_usdt"])}
            for d, v in sorted(buckets.items(), reverse=True)[:14]]


def diagnostics(state, worker_state, now_ms):
    """All values are derived from committed paper-state; no external calls."""
    latest_ms = int(state["last_ms"])
    age = max(0.0, (now_ms-latest_ms-60_000) / 60_000)
    feed_phase = state.get("phase")
    worker_phase = worker_state.get("phase")
    if feed_phase == "paused_data_gap":
        feed_status = "paused_data_gap"
    elif worker_phase == "feed_error":
        feed_status = "source_error"
    elif age >= 5:
        feed_status = "stale"
    elif feed_phase == "observing" and worker_phase == "running":
        feed_status = "healthy"
    else:
        feed_status = "initializing"

    trades = state.get("trades", [])
    total_trades = int(state.get("total_trades", len(trades)))
    win = [float(t["net_usdt"]) for t in trades if float(t["net_usdt"]) > 0]
    loss = [float(t["net_usdt"]) for t in trades if float(t["net_usdt"]) <= 0]
    net_recent = sum(win) + sum(loss)
    fee_recent = sum(float(t["commissions_usdt"]) for t in trades)
    slippage_recent = sum(float(t["slippage_usdt"]) for t in trades)
    equity = float(state["cash"]) + float(state["btc"])*float(state["last_price"])
    realized = float(state["realized"])
    gross_before_costs_recent = sum(float(t["gross_before_costs_usdt"]) for t in trades)
    # This drawdown uses the CLOSED-TRADE sample only, not intratrade mark-to-market.
    closed_curve = 0.0
    max_closed_curve = 0.0
    max_closed_drawdown = 0.0
    for t in trades:
        closed_curve += float(t["net_usdt"])
        max_closed_curve = max(max_closed_curve, closed_curve)
        max_closed_drawdown = max(max_closed_drawdown, max_closed_curve-closed_curve)
    n = len(trades)
    return {
        "feed": {
            "status": feed_status,
            "last_candle_utc": dt.datetime.fromtimestamp(latest_ms/1000,dt.timezone.utc).isoformat(),
            "age_min": round(age, 1),
            "last_success_utc": state.get("feed_last_success_utc"),
            "worker": worker_phase,
            "last_error": state.get("error") or worker_state.get("last_error"),
            "alerts": ([("Dados com atraso superior a 5 minutos" if feed_status == "stale"
                         else "Lacuna detectada; conta virtual pausada"
                         if feed_status == "paused_data_gap"
                         else "Falha na fonte pública; verifique a última coleta"
                         if feed_status == "source_error"
                         else "Aguardando primeira atualização")] if feed_status != "healthy" else []),
        },
        "paper": {
            "realized_net_usdt": _round(realized),
            "marked_equity_usdt": _round(equity),
            "marked_pnl_usdt": _round(equity-1000.0),
            "marked_return_pct": round((equity/1000.0-1)*100,3),
            "btc_position_open": float(state["btc"])>0,
            "total_trades_recorded": total_trades,
            "recent_trades_analyzed": n,
            "recent_sample_is_complete": n == total_trades,
            "win_rate_pct_recent": round(100*len(win)/n,2) if n else None,
            "avg_win_usdt_recent": _round(sum(win)/len(win)) if win else None,
            "avg_loss_usdt_recent": _round(sum(loss)/len(loss)) if loss else None,
            "profit_factor_recent": round(sum(win)/abs(sum(x for x in loss if x < 0)),3)
            if any(x < 0 for x in loss) else None,
            "gross_before_costs_usdt_recent": _round(gross_before_costs_recent),
            "net_usdt_recent": _round(net_recent),
            "fees_usdt_recent": _round(fee_recent),
            "slippage_usdt_recent": _round(slippage_recent),
            "fees_usdt_all": _round(state["fees"]),
            "slippage_usdt_all": _round(state["slippage"]),
            "max_closed_trade_drawdown_usdt_recent": _round(max_closed_drawdown),
            "equity_risk_tracking_since_utc": (
                dt.datetime.fromtimestamp(state["equity_monitoring_started_ms"]/1000, dt.timezone.utc).isoformat()
                if state.get("equity_monitoring_started_ms") is not None else None),
            "peak_closed_candle_equity_usdt": (
                _round(state["equity_peak_usdt"]) if "equity_peak_usdt" in state else None),
            "max_closed_candle_drawdown_usdt": (
                _round(state["max_close_drawdown_usdt"])
                if "max_close_drawdown_usdt" in state else None),
            "max_closed_candle_drawdown_pct": (
                round(float(state["max_close_drawdown_pct"]), 3)
                if "max_close_drawdown_pct" in state else None),
            "recent_daily_close_equity_utc": list(state.get("daily_close_equity_utc", []))[-30:],
            "closed_candle_drawdown_note": (
                "Drawdown amostrado no fechamento de candles de 1 minuto desde o início indicado. "
                "Considera posição aberta marcada no fechamento, mas não os mínimos intraminuto "
                "nem custos hipotéticos de liquidação. Não representa drawdown completo de execução."),
            "daily_utc_recent": _daily(trades),
            "sufficient_sample": total_trades >= 100,
            "sample_warning": ("Nenhuma operação virtual ainda; não há taxa de acerto ou fator de lucro para avaliar."
                if total_trades == 0 else
                "Amostra pequena: menos de 100 operações virtuais; resultados não demonstram vantagem."
                if total_trades < 100 else
                "Amostra maior, mas ainda são necessários testes independentes, custos realistas e análise de risco."),
        },
        "rules": {"control_decision": state["frozen"]["control_decision"],
                  "shadow_rule_frozen": state["frozen"]["shadow_atr"],
                  "no_real_orders": True, "auto_promotion": False,
                  "require_new_candles": True},
        "caveats": ("Resultados retrospectivos V7 não são validação inédita. "
                    "Estatísticas de trades consideram no máximo os últimos 500 fechamentos e "
                    "drawdown por fechamento de 1 minuto não inclui variações intraminuto ou custos de liquidação. "
                    "Paper trading não modela livro de ofertas, liquidez ou latência."),
    }
