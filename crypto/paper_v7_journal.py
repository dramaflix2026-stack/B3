"""Append-only virtual trade archive and read-only pagination for V7 paper research.

A successful state.json checkpoint is authoritative; this archive is rebuilt
idempotently from its most recent 500 retained trades after a crash/redeploy.
Records are indexed by monotonic simulation sequence, never by wall-clock time.
No exchange credentials or trading functions.
"""
import hashlib
import json
import os
from pathlib import Path


def _encode(obj):
    return json.dumps(obj, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8")


def _records(state):
    recent = state.get("trades", [])
    count = int(state.get("total_trades", len(recent)))
    if count < len(recent) or count < 0:
        raise ValueError("Invalid virtual trade counter")
    for index, trade in enumerate(recent, count - len(recent) + 1):
        yield index, trade


def sync_archive(state, paper_dir):
    """Create missing immutable records and verify any existing ones.

    Call ONLY after the atomic account-state checkpoint, and again on restart.
    If storage disagrees with state, fail closed instead of silently rewriting.
    """
    root = Path(paper_dir) / "archive"
    root.mkdir(parents=True, exist_ok=True)
    saved = 0
    verified = 0
    for index, trade in _records(state):
        path = root / f"{index:010d}.json"
        payload = {"sequence": index, "trade": trade}
        content = _encode(payload)
        if path.exists():
            if path.read_bytes() != content:
                raise ValueError(f"Paper trade archive conflict at sequence {index}")
            verified += 1
        else:
            try:
                with path.open("xb") as file:
                    file.write(content)
                    file.flush()
                    os.fsync(file.fileno())
            except FileExistsError:
                if path.read_bytes() != content:
                    raise ValueError(f"Paper trade archive conflict at sequence {index}")
            saved += 1
    return {"created": saved, "verified": verified}


def archive_status(state, paper_dir):
    """Audit record coverage, including older trades outside recent-state buffer."""
    root = Path(paper_dir) / "archive"
    total = int(state.get("total_trades", len(state.get("trades", []))))
    if total <= 0:
        return {"status": "empty", "total": 0, "archived": 0, "missing": 0}
    # Only scan file names; never trust them for determining total simulated trades.
    indices = set()
    for file in root.glob("*.json") if root.exists() else ():
        if file.stem.isdigit() and len(file.stem) == 10:
            value = int(file.stem)
            if 1 <= value <= total:
                indices.add(value)
    present = len(indices)
    return {
        "status": "complete" if present == total else "partial",
        "total": total, "archived": present, "missing": total - present,
        "first_sequence": min(indices) if indices else None,
        "last_sequence": max(indices) if indices else None,
        "warning": None if present == total else "Historical paper records are incomplete; reported totals still come from the checkpoint state.",
    }


def page(state, paper_dir, page_num=1, page_size=20):
    if not isinstance(page_num, int) or page_num < 1 or page_num > 100000:
        raise ValueError("Invalid page")
    if page_size not in (10, 20, 50):
        raise ValueError("Invalid page size")
    total = int(state.get("total_trades", len(state.get("trades", []))))
    root = Path(paper_dir) / "archive"
    pages = max(1, (total + page_size - 1)//page_size)
    # Page 1 has the newest trades.
    ids = range(total-(page_num-1)*page_size,
                max(total-page_num*page_size, 0), -1) if page_num <= pages else ()
    entries = []
    missing = []
    for index in ids:
        path = root / f"{index:010d}.json"
        if not path.exists():
            missing.append(index)
            continue
        record = json.loads(path.read_text(encoding="utf-8"))
        if record.get("sequence") != index or not isinstance(record.get("trade"), dict):
            raise ValueError("Corrupted paper trade record")
        entries.append(record)
    return {
        "status": "complete" if not missing else "partial",
        "page": page_num, "per_page": page_size, "total": total,
        "total_pages": pages, "records": entries,
        "missing_sequences": missing,
        "warning": "Read-only simulated trade archive. No real orders or exchange account.",
    }


def source_fingerprint(state):
    """Reproducible identifier for the strategy frozen at account activation."""
    frozen = state.get("frozen") or {}
    return hashlib.sha256(_encode({
        "report_sha256": frozen.get("sha256"),
        "atr": frozen.get("shadow_atr"),
        "control_decision": frozen.get("control_decision"),
        "fee": frozen.get("fee_each_side"),
        "slippage": frozen.get("slippage_each_side"),
    })).hexdigest()
