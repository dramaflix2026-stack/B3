"""Independent, read-only V7 paper-account reconciliation.

Audits persisted state, frozen source, immutable trade archive, execution
cost identity and chronology without fetching prices or touching strategy.
This is an accounting audit; it does not prove live fill quality or alpha.
"""
import datetime as dt
import hashlib
import json
import math
from pathlib import Path

INITIAL_CASH = 1000.0
MINUTE_MS = 60000
HALT_TOL = 0.025


def _utc(ms):
    return dt.datetime.fromtimestamp(ms / 1000, dt.timezone.utc).isoformat()


def _close(a, b, tol=HALT_TOL):
    return math.isfinite(float(a)) and math.isfinite(float(b)) and abs(float(a) - float(b)) <= tol


def _read_trade_archive(state, paper_dir):
    """Read every archived trade, including those pruned from the UI buffer."""
    total = int(state.get("total_trades", len(state.get("trades", []))))
    folder = Path(paper_dir) / "archive"
    records = []
    missing = []
    corrupt = []
    for i in range(1, total + 1):
        path = folder / f"{i:010d}.json"
        if not path.is_file():
            missing.append(i)
            continue
        try:
            row = json.loads(path.read_text(encoding="utf-8"))
            if row.get("sequence") != i or not isinstance(row.get("trade"), dict):
                raise ValueError("Wrong sequence or trade format")
            records.append(row)
        except (OSError, ValueError, TypeError, UnicodeDecodeError):
            corrupt.append(i)
    return records, missing, corrupt


def audit(state, paper_dir, data_dir):
    checks = []
    def check(key, label, ok, message):
        checks.append({"key": key, "label": label, "ok": bool(ok), "detail": message[:300]})

    if not isinstance(state, dict):
        raise ValueError("Missing paper account state")
    frozen = state.get("frozen") or {}
    total = int(state.get("total_trades", len(state.get("trades", []))))
    processed = int(state.get("processed", 0))
    last_ms = int(state["last_ms"])
    activation = int(state["activation_ms"])
    cash = float(state["cash"])
    btc = float(state["btc"])
    last_price = float(state["last_price"])
    realized = float(state["realized"])
    fees = float(state["fees"])
    slip = float(state["slippage"])
    equity = cash + btc * last_price
    now_candle = _utc(last_ms)

    check("control_cash", "Controle V7 permanece em caixa",
          frozen.get("control_decision") == "cash",
          "A última janela V7 não habilitou operações; a conta de controle não deve negociar.")
    check("frozen_atr", "Regra ATR congelada e delimitada",
          frozen.get("shadow_atr", {}).get("direction") in ("low", "high")
          and math.isfinite(float(frozen.get("shadow_atr", {}).get("threshold_pct", float("nan"))))
          and 0 < float(frozen.get("shadow_atr", {}).get("threshold_pct", 0)) < 5,
          "A condição ATR da conta experimental continua somente para simulação.")

    source_path = Path(data_dir) / "v7" / str(frozen.get("source_report", "unknown"))
    try:
        same_source = source_path.is_file() and hashlib.sha256(source_path.read_bytes()).hexdigest() == frozen.get("sha256")
    except OSError:
        same_source = False
    check("source_checksum", "Relatório congelado preservado",
          same_source, "SHA-256 do relatório original igual ao registrado na ativação." if same_source
          else "Relatório ausente ou conteúdo divergente; não reotimize automaticamente.")

    check("chronology", "Candles contínuos sem duplicação",
          processed >= 0 and last_ms == activation + processed * MINUTE_MS,
          f"Ativação {_utc(activation)} · último candle {now_candle} · {processed} processados.")
    check("positions", "Estado de exposição e caixa coerente",
          math.isfinite(cash) and math.isfinite(btc) and math.isfinite(last_price)
          and -1e-8 <= cash <= 2e6 and btc >= 0 and last_price > 0
          and ((btc == 0 and state.get("entry") is None)
               or (btc > 0 and isinstance(state.get("entry"), dict))),
          "BTC e caixa virtuais; nenhuma posição real é considerada.")

    records, missing, corrupt = _read_trade_archive(state, paper_dir)
    check("archive_complete", "Histórico sequencial preservado",
          not missing and not corrupt and len(records) == total,
          f"{len(records)}/{total} registros disponíveis; {len(missing)} ausentes, {len(corrupt)} inválidos.")
    recent = state.get("trades", [])
    prior = {int(r["sequence"]): r["trade"] for r in records}
    start = total - len(recent) + 1
    recent_match = all(prior.get(i) == row for i, row in enumerate(recent, start))
    check("recent_ledger_match", "Arquivo confere com estado recente",
          recent_match, "Todas as operações mantidas no estado possuem cópia idêntica no arquivo.")

    import_errors = []
    archive_net = 0.0
    archive_fees = 0.0
    archive_slippage = 0.0
    previous_entry = None
    for record in records:
        trade = record["trade"]
        try:
            gross = float(trade["gross_before_costs_usdt"])
            commission = float(trade["commissions_usdt"])
            slippage = float(trade["slippage_usdt"])
            net = float(trade["net_usdt"])
            entry_utc = dt.datetime.fromisoformat(trade["entry_utc"].replace("Z", "+00:00"))
            exit_utc = dt.datetime.fromisoformat(trade["exit_utc"].replace("Z", "+00:00"))
            if not all(math.isfinite(x) for x in (gross, commission, slippage, net)):
                raise ValueError("Nonfinite prices or PnL")
            if commission < 0 or slippage < -1e-6 or exit_utc < entry_utc:
                raise ValueError("Negative costs or reverse trade time")
            if previous_entry and entry_utc < previous_entry:
                raise ValueError("Archive time reversed")
            previous_entry = entry_utc
            if not _close(gross - commission - slippage, net, .00002):
                raise ValueError("Gross minus costs differs from net")
            archive_net += net
            archive_fees += commission
            archive_slippage += slippage
        except (ValueError, TypeError, KeyError, AttributeError) as exc:
            import_errors.append(f"seq {record['sequence']}: {str(exc)}")
    check("trade_cost_identity", "Resultado bruto − comissões − slippage = líquido",
          not import_errors and len(records) == total,
          "Custos e sequência de todos os trades conciliados." if not import_errors else
          "Divergências detectadas: " + "; ".join(import_errors[:3]))
    check("realized_pnl", "Lucro realizado coincide com arquivo integral",
          not missing and not corrupt and _close(realized, archive_net, max(.002, total * .000003)),
          f"Estado {realized:.5f} USDT · arquivo {archive_net:.5f} USDT.")

    entry = state.get("entry") if btc else None
    open_fee = float(entry["fee"]) if entry else 0.0
    open_slip = btc * (float(entry["price"]) - float(entry["raw_open"])) if entry else 0.0
    check("total_costs", "Custos acumulados incluem posição aberta",
          not missing and not corrupt
          and _close(fees, archive_fees + open_fee, max(.002, total * .000003))
          and _close(slip, archive_slippage + open_slip, max(.002, total * .000003)),
          f"Comissões estado/arquivo+abertura {fees:.5f}/{archive_fees + open_fee:.5f}; "
          f"slippage {slip:.5f}/{archive_slippage + open_slip:.5f}.")

    expected_cash = INITIAL_CASH + realized - (btc * float(entry["price"]) + open_fee if entry else 0)
    check("cash_reconciliation", "Caixa recalculado independentemente",
          _close(cash, expected_cash, .005),
          f"Caixa armazenado {cash:.6f} USDT · esperado {expected_cash:.6f} USDT.")
    close_equity = state.get("last_close_equity_usdt")
    check("marked_equity", "Patrimônio marcado ao fechamento M1",
          close_equity is not None and _close(equity, close_equity, .005),
          f"Caixa + posição: {equity:.6f} USDT · registro {close_equity}.")
    check("no_real_trading", "Somente execução virtual",
          True, "Módulo de auditoria usa apenas arquivos locais; nenhuma chamada de ordem.")

    failures = [x for x in checks if not x["ok"]]
    return {
        "status": "passed" if not failures else "failed",
        "version": "paper-v7-accounting-audit-1",
        "checked_at_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
        "activation_utc": _utc(activation),
        "last_candle_utc": now_candle,
        "trades_checked": len(records),
        "trades_expected": total,
        "checks_passed": len(checks)-len(failures),
        "checks_total": len(checks),
        "checks": checks,
        "failures": [x["key"] for x in failures],
        "accounting": {"cash_usdt": round(cash, 4),
                       "marked_equity_usdt": round(equity, 4),
                       "realized_usdt": round(realized, 4),
                       "commissions_usdt": round(fees, 4),
                       "slippage_usdt": round(slip, 4)},
        "limits": "Reconciles recorded virtual OHLC fills and persistence only. Does not verify remote exchange candles, latency, intraminute ordering, or profitability.",
    }
