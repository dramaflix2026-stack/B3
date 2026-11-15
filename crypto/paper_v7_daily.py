"""Persistent UTC daily paper-activity ledger and read-only research monitoring.

No signal changes, exchange account access, auto-promotion, or real orders.
Counters start only when this instrumentation is enabled, never retroactively.
"""
import datetime as dt

RETAIN_DAYS = 120
MIN_OBSERVED_DAYS = 30
MIN_CLOSED_TRADES = 100


def _utc(ms):
    return dt.datetime.fromtimestamp(ms / 1000, dt.timezone.utc).isoformat()


def _money(value):
    return round(float(value), 4)


def record_candle(state, timestamp_ms, *, signals, blocked, entries, exits,
                  fee_delta, slippage_delta, realized_delta):
    """Call exactly once for each newly processed, contiguous CLOSED candle."""
    date = _utc(timestamp_ms)[:10]
    days = state.setdefault("daily_activity_utc", [])
    if days and days[-1]["date_utc"] > date:
        raise ValueError("Paper daily dates cannot move backwards")
    if not days or days[-1]["date_utc"] != date:
        days.append({
            "date_utc": date, "first_candle_utc": _utc(timestamp_ms),
            "last_candle_utc": _utc(timestamp_ms),
            "candles": 0, "signals": 0, "atr_blocked": 0,
            "entries": 0, "closed_trades": 0,
            "fees_paid_usdt": 0.0, "slippage_paid_usdt": 0.0,
            "realized_net_on_exit_usdt": 0.0,
            "equity_close_usdt": None,
            "peak_equity_usdt": None,
        })
        state["daily_activity_utc"] = days[-RETAIN_DAYS:]
        days = state["daily_activity_utc"]
    row = days[-1]
    row["last_candle_utc"] = _utc(timestamp_ms)
    row["candles"] += 1
    row["signals"] += int(signals)
    row["atr_blocked"] += int(blocked)
    row["entries"] += int(entries)
    row["closed_trades"] += int(exits)
    row["fees_paid_usdt"] = _money(row["fees_paid_usdt"] + fee_delta)
    row["slippage_paid_usdt"] = _money(row["slippage_paid_usdt"] + slippage_delta)
    row["realized_net_on_exit_usdt"] = _money(row["realized_net_on_exit_usdt"] + realized_delta)
    row["equity_close_usdt"] = _money(float(state["cash"]) + float(state["btc"]) * float(state["last_price"]))
    row["peak_equity_usdt"] = _money(state.get("equity_peak_usdt", row["equity_close_usdt"]))
    return row


def status(state, now_ms):
    rows = state.get("daily_activity_utc", [])
    total = int(state.get("total_trades", len(state.get("trades", []))))
    start = int(state.get("activation_ms", state["last_ms"]))
    calendar_days = max(0, int((now_ms - start) // 86_400_000))
    observed_days = sum(1 for row in rows if row["candles"] > 0)
    valid_sample = observed_days >= MIN_OBSERVED_DAYS and total >= MIN_CLOSED_TRADES
    recent = [dict(row) for row in rows[-30:]][::-1]
    return {
        "status": "collecting" if state.get("phase") == "observing" else "paused",
        "activation_utc": _utc(start),
        "daily_tracking_since_utc": rows[0]["first_candle_utc"] if rows else None,
        "calendar_days_since_activation": calendar_days,
        "observed_days_in_retained_window": observed_days,
        "retention_days": RETAIN_DAYS,
        "target_observed_days": MIN_OBSERVED_DAYS,
        "target_closed_trades": MIN_CLOSED_TRADES,
        "closed_trades_since_activation": total,
        "days_remaining_in_observed_window": max(0, MIN_OBSERVED_DAYS - observed_days),
        "trades_remaining": max(0, MIN_CLOSED_TRADES - total),
        "minimum_research_sample_reached": valid_sample,
        "approval": "not_approved",
        "last_30_days_utc": recent,
        "caveat": (
            "Metas operacionais mínimas, não prova de vantagem nem autorização para negociar. "
            "Contadores diários iniciados após a instalação desta funcionalidade; "
            "janela retém até 120 dias e não reconstrói dias antigos. "
            "Lucro líquido realizado no dia inclui comissões da entrada, possivelmente pagas em outra data. "
            "Comissões e slippage pagos no dia representam caixa e execução virtual do próprio dia."
        ),
    }


def alerts(state, worker, now_ms, archive=None):
    """Active, read-only alerts. Warnings never change trading behavior."""
    result = []
    age = max(0, (now_ms - int(state["last_ms"]) - 60000)/60000)
    phase = state.get("phase")
    worker_phase = worker.get("phase")
    def emit(code, severity, label, detail):
        result.append({"code": code, "severity": severity, "label": label, "detail": detail})

    if phase == "paused_accounting_audit":
        failures = (state.get("audit_guard") or {}).get("failures") or ["unknown"]
        emit("accounting_audit_failed", "critical", "Auditoria contábil reprovada — paper pausado",
             "Verificações inconsistentes: " + ", ".join(failures[:6]) + ". Revisão técnica obrigatória.")
    if phase == "paused_data_gap":
        emit("feed_gap", "critical", "Coleta interrompida por lacuna",
             "A simulação foi pausada para não inventar preços ou execuções.")
    elif worker_phase == "feed_error":
        emit("feed_error", "critical", "Falha na coleta pública",
             str(worker.get("last_error") or "A origem dos candles não respondeu.")[:180])
    elif age >= 5:
        emit("feed_stale", "warning", "Candles atrasados",
             f"Último candle fechado está atrasado aproximadamente {age:.1f} minuto(s).")

    if archive and archive.get("status") == "partial":
        emit("journal_incomplete", "critical", "Arquivo de operações incompleto",
             f"{archive.get('missing', 0)} registro(s) imutável(is) ausente(s).")

    max_dd = state.get("max_close_drawdown_pct")
    if max_dd is not None and float(max_dd) >= 5:
        emit("drawdown_high", "warning", "Drawdown virtual elevado",
             f"Maior recuo registrado por fechamento M1: {float(max_dd):.2f}%. É um alerta, não uma ordem automática.")
    total = int(state.get("total_trades", len(state.get("trades", []))))
    if total < MIN_CLOSED_TRADES:
        emit("sample_small", "info", "Amostra ainda insuficiente",
             f"{total} de {MIN_CLOSED_TRADES} operações virtuais fechadas. Sem avaliação de rentabilidade conclusiva.")

    # Decision must not be changed or promoted based on exploratory paper results.
    if (state.get("frozen") or {}).get("control_decision") != "cash":
        emit("control_not_cash", "critical", "Controle fora do estado congelado",
             "A carteira de controle deveria permanecer em caixa. Revisão obrigatória.")
    return {"active": result, "critical": sum(x["severity"] == "critical" for x in result),
            "warnings": sum(x["severity"] == "warning" for x in result),
            "infos": sum(x["severity"] == "info" for x in result),
            "observed_at_utc": _utc(now_ms),
            "notification_mode": "in_dashboard_only",
            "note": "Alertas visíveis na interface; sem push, e-mail ou WhatsApp nesta versão."}
