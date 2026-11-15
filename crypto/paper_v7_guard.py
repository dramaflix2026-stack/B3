"""Production V7 paper-account audit watchdog: passive by default, fail closed on mismatch.

Runs on startup and at most every 15 NEW completed candle minutes. Saves
audit evidence in the same persistent state. Never places exchange orders
or changes signal, entry, exit, ATR or simulated fee rules.
"""
import datetime as dt
from paper_v7_audit import audit as reconcile

AUDIT_EVERY_MS = 15 * 60_000

def check(state, paper_dir, data_dir, *, force=False):
    """Update state in memory if audit is due; caller checkpoints before/after use."""
    if state.get("phase") not in ("observing", "paused_accounting_audit"):
        return None
    previous = state.get("audit_guard") or {}
    last = previous.get("last_candle_ms")
    if not force and last is not None and int(state["last_ms"]) - int(last) < AUDIT_EVERY_MS:
        return None
    try:
        result = reconcile(state, paper_dir, data_dir)
        passed = result.get("status") == "passed"
        failures = list(result.get("failures", []))
        total = int(result.get("checks_total", 0))
        passed_count = int(result.get("checks_passed", 0))
        if total == 0 or passed_count > total:
            passed = False
            failures.append("invalid_audit_report")
    except Exception as exc:
        passed = False
        failures = ["audit_exception"]
        total = 0
        passed_count = 0
        result = {"error": type(exc).__name__ + ": " + str(exc)[:180]}
    state["audit_guard"] = {
        "status": "passed" if passed else "failed",
        "checked_at_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
        "last_candle_ms": int(state["last_ms"]),
        "last_candle_utc": dt.datetime.fromtimestamp(state["last_ms"]/1000, dt.timezone.utc).isoformat(),
        "trades_checked": int(result.get("trades_checked", 0)),
        "checks_passed": passed_count,
        "checks_total": total,
        "failures": failures,
        "details": result.get("error") or None,
        "interval_minutes": AUDIT_EVERY_MS // 60_000,
    }
    if not passed:
        # Not just a dashboard warning: prevent new simulated fills until a
        # developer has reviewed the inconsistency. No automatic resume.
        state["phase"] = "paused_accounting_audit"
        state["error"] = "Auditoria contábil V7 reprovada: " + ", ".join(failures[:5])
    return state["audit_guard"]
