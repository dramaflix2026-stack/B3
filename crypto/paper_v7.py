"""BTC/USDT public-price paper observation with immutable frozen V7 candidate.

No trading endpoints, private keys, or real orders. Simulation only.
The official V7 risk gate is CASH (P5); a separate, labeled shadow paper
account can test the historical P4 ATR hypothesis on NEW closed candles.
"""
import datetime as dt
import hashlib
import json
import math
import os
import pathlib
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import deque
from paper_v7_analytics import diagnostics as paper_diagnostics

DATA = pathlib.Path(os.getenv("CRYPTO_DATA_DIR", "/data/crypto"))
PAPER = DATA / "paper-v7"
STATE_FILE = PAPER / "state.json"
SOURCE = "https://data-api.binance.vision/api/v3/klines"
CANDLE_MS = 60_000
FEE = .001
SLIP = .0001
INITIAL_CASH = 1000.
MAX_ALLOC = .25
RISK_BUDGET = .01
BASE_STOP = .015
HOLD_MINUTES = 720
COOLDOWN_MINUTES = 720
MAX_CATCHUP_PAGES = 5
LOCK = threading.RLock()
worker = {"phase": "starting", "last_attempt_utc": None, "last_success_utc": None, "last_error": None}

def now_iso():
    return dt.datetime.now(dt.timezone.utc).isoformat()

def utc_iso(ms):
    return dt.datetime.fromtimestamp(ms / 1000, dt.timezone.utc).isoformat().replace("+00:00", "Z")

def atomic_save(state):
    PAPER.mkdir(parents=True, exist_ok=True)
    tmp = STATE_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, separators=(",", ":"), allow_nan=False))
    os.replace(tmp, STATE_FILE)

def _load():
    return json.loads(STATE_FILE.read_text()) if STATE_FILE.exists() else None

def public_klines(start_ms=None, limit=1000):
    params = {"symbol": "BTCUSDT", "interval": "1m", "limit": str(limit)}
    if start_ms is not None:
        params["startTime"] = str(int(start_ms))
    url = SOURCE + "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"User-Agent": "BTCScalperAI-PaperResearch/1.0"})
    with urllib.request.urlopen(req, timeout=12) as resp:
        rows = json.load(resp)
    if not isinstance(rows, list):
        raise RuntimeError("Resposta inesperada da fonte pública")
    parsed = []
    now_ms = int(time.time() * 1000)
    for row in rows:
        if not isinstance(row, list) or len(row) < 7:
            raise ValueError("Candle público malformado")
        ts = int(row[0])
        o,h,l,c = map(float, row[1:5])
        if ts % CANDLE_MS or min(o,h,l,c) <= 0 or h < max(o,c) or l > min(o,c):
            raise ValueError("OHLC público inválido")
        if ts + CANDLE_MS + 2000 > now_ms:
            continue  # Never use a still-open minute.
        parsed.append({"ms": ts, "o": o, "h": h, "l": l, "c": c})
    for i in range(1, len(parsed)):
        if parsed[i]["ms"] <= parsed[i-1]["ms"]:
            raise ValueError("Fonte pública fora de ordem")
    return parsed

def frozen_source():
    files = sorted((DATA / "v7").glob("*.json"), reverse=True)[:1] if (DATA / "v7").exists() else []
    if not files:
        raise ValueError("Relatório V7 ainda não disponível")
    raw = files[0].read_bytes()
    report = json.loads(raw)
    if report.get("version") != "v7-atr-executable-walkforward-1":
        raise ValueError("Versão V7 inesperada")
    folds = report.get("folds", [])
    by_window = {int(f["test_window"]): f for f in folds}
    last = by_window.get(5)
    study = by_window.get(4)
    if not last or not study:
        raise ValueError("Relatório V7 incompleto")
    if last.get("selected") != "cash":
        raise ValueError("A última decisão V7 mudou; revisar manualmente antes de iniciar")
    rule = study.get("atr_rule")
    if study.get("selected") != "atr_gate" or not rule or rule.get("direction") not in ("low", "high"):
        raise ValueError("Regra exploratória ATR indisponível na janela P4")
    threshold = float(rule["threshold"])
    if not math.isfinite(threshold) or not .001 < threshold < 5:
        raise ValueError("Limite ATR fora da faixa")
    return {
        "source_report": files[0].name,
        "sha256": hashlib.sha256(raw).hexdigest(),
        "frozen_at_utc": now_iso(),
        "control_decision": "cash",
        "control_reason": "P5 da V7 sem filtro elegível; nenhum sinal autorizado na carteira de controle",
        "shadow_label": "ATR P4 congelado, apenas conta virtual exploratória",
        "shadow_atr": {"direction": rule["direction"], "threshold_pct": threshold},
        "signal_conditions": "V6 Robot9 breakout; ATR from 14 completed true ranges; only next-open synthetic fills",
        "fee_each_side": FEE, "slippage_each_side": SLIP,
        "initial_shadow_cash_usdt": INITIAL_CASH,
    }

def new_state(ready_candles, freeze):
    # Warm up 720 completed contiguous candles without generating historical trades.
    if len(ready_candles) < 721:
        raise ValueError("Histórico recente insuficiente para preparar os indicadores")
    candles = ready_candles[-721:]
    for i in range(1, len(candles)):
        if candles[i]["ms"] - candles[i-1]["ms"] != CANDLE_MS:
            raise ValueError("Candles recentes não são consecutivos")
    tranges = []
    for i in range(len(candles)-14, len(candles)):
        r = candles[i]
        prev = candles[i-1]["c"]
        tranges.append(max(r["h"]-r["l"], abs(r["h"]-prev), abs(r["l"]-prev)))
    return {
        "version": 1, "phase": "observing", "created_utc": now_iso(),
        "activation_candle_utc": utc_iso(candles[-1]["ms"]),
        "activation_ms": candles[-1]["ms"],
        "last_ms": candles[-1]["ms"],
        "last_price": candles[-1]["c"],
        "frozen": freeze,
        "closes": [c["c"] for c in candles[-720:]],
        "tranges": tranges,
        "previous_close": candles[-1]["c"],
        "cash": INITIAL_CASH,
        "btc": 0.0, "entry": None, "pending": None,
        "cool": 0, "fees": 0.0, "slippage": 0.0,
        "realized": 0.0, "processed": 0, "signals": 0, "blocked": 0,
        "total_trades": 0,
        "trades": [], "events": [],
        "feed_last_success_utc": now_iso(),
        "error": None,
    }

def _event(state, kind, ms, detail):
    state["events"].append({"time_utc": utc_iso(ms), "type": kind, "detail": detail})
    state["events"] = state["events"][-80:]

def _signal_metrics(state, close):
    prices = state["closes"]
    if len(prices) != 720 or len(state["tranges"]) < 14:
        return None
    fast = sum(prices[-60:]) / 60
    mid = sum(prices[-240:]) / 240
    slow = sum(prices) / 720
    vol = (max(prices[-240:]) - min(prices[-240:])) / close
    prior_high = max(prices[-241:-1])
    atr = sum(state["tranges"]) / 14
    breakout = (fast > mid > slow and vol >= .009 and
                close > slow * 1.006 and close > prior_high * 1.006 and
                close / fast - 1 < .016)
    return {"entry_signal": breakout, "atr": atr,
            "atr_pct": atr / close * 100, "ma60": fast,
            "ma240": mid, "ma720": slow, "vol_pct": vol*100}

def process_candle(state, candle):
    """Advance exactly one CLOSED 1m candle, persisting after each batch."""
    ms,o,h,l,c = (candle[k] for k in ("ms","o","h","l","c"))
    last = state["last_ms"]
    if ms <= last:
        return
    if ms - last != CANDLE_MS:
        # Missing candles would make fills invalid, especially with open exposure.
        state["phase"] = "paused_data_gap"
        state["error"] = "Lacuna de candles; execução interrompida sem inventar preços"
        _event(state, "data_gap", ms, state["error"])
        return

    # Pending order from prior CLOSE: simulate fill at THIS candle OPEN.
    pending = state["pending"]
    state["pending"] = None
    if pending and state["btc"] == 0 and state["cool"] == 0:
        fill = o * (1 + SLIP)
        atr = pending["atr"]
        stop = max(.006, min(.04, atr / o * 2))
        qty = min(state["cash"] * MAX_ALLOC / fill,
                  state["cash"] * RISK_BUDGET / (fill * stop),
                  state["cash"] / (fill * (1 + FEE)))
        if qty * fill >= 5:
            commission = qty * fill * FEE
            state["cash"] -= qty * fill + commission
            state["btc"] = qty
            state["entry"] = {"price": fill, "raw_open": o, "fee": commission,
                              "stop_pct": stop, "open_ms": ms,
                              "signal_ms": pending["signal_ms"],
                              "atr_signal_pct": pending["atr_pct"]}
            state["fees"] += commission
            state["slippage"] += qty * (fill - o)
            _event(state, "paper_buy", ms, "Somente simulação; sem ordem real")

    if state["btc"] > 0:
        p = state["entry"]
        stop_price = p["price"] * (1 - p["stop_pct"])
        reason = None
        if l <= stop_price:
            raw_exit = min(o, stop_price)
            reason = "stop_loss"
        elif ms - p["open_ms"] >= HOLD_MINUTES * CANDLE_MS:
            raw_exit = c
            reason = "max_hold"
        if reason:
            qty = state["btc"]
            fill = raw_exit * (1 - SLIP)
            exit_fee = qty * fill * FEE
            net = qty * (fill - p["price"]) - p["fee"] - exit_fee
            raw_pnl = qty * (raw_exit - p["raw_open"])
            slip = qty * (raw_exit - fill)
            state["cash"] += qty * fill - exit_fee
            state["fees"] += exit_fee
            state["slippage"] += slip
            state["realized"] += net
            state["total_trades"] = int(state.get("total_trades", len(state["trades"]))) + 1
            state["trades"].append({
                "entry_utc": utc_iso(p["open_ms"]), "exit_utc": utc_iso(ms),
                "entry_price_usdt": round(p["price"], 4),
                "exit_price_usdt": round(fill, 4),
                "duration_min": int((ms-p["open_ms"])//CANDLE_MS),
                "reason": reason,
                "net_usdt": round(net, 6),
                "gross_before_costs_usdt": round(raw_pnl, 6),
                "commissions_usdt": round(p["fee"]+exit_fee, 6),
                "slippage_usdt": round(qty*(p["price"]-p["raw_open"]) + slip, 6),
                "atr_signal_pct": round(p["atr_signal_pct"], 6),
            })
            state["trades"] = state["trades"][-500:]
            state["btc"] = 0.0
            state["entry"] = None
            state["cool"] = COOLDOWN_MINUTES
            _event(state, "paper_sell", ms, reason)

    if state["cool"]:
        state["cool"] -= 1

    prev_c = state["previous_close"]
    tr = max(h-l, abs(h-prev_c), abs(l-prev_c))
    state["tranges"].append(tr)
    state["tranges"] = state["tranges"][-14:]
    state["closes"].append(c)
    state["closes"] = state["closes"][-720:]
    state["previous_close"] = c
    metrics = _signal_metrics(state, c)
    if (metrics and metrics["entry_signal"] and state["btc"] == 0
            and state["cool"] == 0):
        state["signals"] += 1
        rule = state["frozen"]["shadow_atr"]
        accepted = (metrics["atr_pct"] < rule["threshold_pct"]
                    if rule["direction"] == "low"
                    else metrics["atr_pct"] >= rule["threshold_pct"])
        if accepted:
            state["pending"] = {"atr": metrics["atr"], "atr_pct": metrics["atr_pct"], "signal_ms": ms}
            _event(state, "paper_signal", ms, "Sinal ATR experimental; entrada virtual no próximo candle")
        else:
            state["blocked"] += 1
    state["last_ms"] = ms
    state["last_price"] = c
    state["processed"] += 1
    state["feed_last_success_utc"] = now_iso()

def snapshot():
    with LOCK:
        state = _load()
        if not state:
            return {"status": "awaiting_feed", "worker": dict(worker),
                    "message": "Acompanhamento ainda não inicializado; nenhuma operação executada."}
        account_value = state["cash"] + state["btc"] * state["last_price"]
        frozen = state["frozen"]
        return {
            "status": state["phase"],
            "worker": dict(worker),
            "created_utc": state["created_utc"],
            "activation_candle_utc": state["activation_candle_utc"],
            "last_candle_utc": utc_iso(state["last_ms"]),
            "last_feed_success_utc": state["feed_last_success_utc"],
            "feed_age_minutes": round(max(0, time.time()*1000-state["last_ms"]-CANDLE_MS)/60000, 1),
            "control": {"decision": frozen["control_decision"],
                        "reason": frozen["control_reason"],
                        "capital_usdt": INITIAL_CASH,
                        "trades": 0},
            "shadow": {
                "mode": "exploratory_paper_only", "frozen_at_utc": frozen["frozen_at_utc"],
                "source_report": frozen["source_report"], "source_sha256": frozen["sha256"],
                "atr_rule": frozen["shadow_atr"],
                "cash_usdt": round(state["cash"], 2),
                "account_value_usdt": round(account_value, 2),
                "pnl_mark_to_market_usdt": round(account_value-INITIAL_CASH, 2),
                "realized_pnl_usdt": round(state["realized"], 2),
                "btc_position": round(state["btc"], 8),
                "commission_usdt": round(state["fees"], 2),
                "slippage_usdt": round(state["slippage"], 2),
                "processed_new_candles": state["processed"],
                "signals": state["signals"], "blocked_by_atr": state["blocked"],
                "open_position": state["entry"],
                "trades": int(state.get("total_trades", len(state["trades"]))),
                "recent_trades": state["trades"][-12:][::-1],
                "recent_events": state["events"][-12:][::-1],
            },
"            "diagnostics": paper_diagnostics(state, worker, int(time.time()*1000)),
            "warning": "Sem ordens reais. Conta virtual experimental não aprovada para negociar. "
                       "Sem reotimização automática. Apenas candles novos e fechados.",
        }

def iteration():
    with LOCK:
        state = _load()
    if state is None:
        freeze = frozen_source()
        recent = public_klines(limit=1000)
        if len(recent) < 721:
            raise ValueError("Fonte pública sem 721 candles fechados consecutivos")
        state = new_state(recent, freeze)
        with LOCK:
            atomic_save(state)
        print('PAPER V7 ACTIVATED control=CASH source='+freeze['source_report']+' first_new_after='+state['activation_candle_utc'], flush=True)
        return

    if state["phase"] != "observing":
        return
    latest_ms = int(time.time()*1000)//CANDLE_MS*CANDLE_MS-CANDLE_MS
    if latest_ms <= state["last_ms"]:
        return

    cursor = state["last_ms"] + CANDLE_MS
    batch = []
    for _ in range(MAX_CATCHUP_PAGES):
        if cursor > latest_ms:
            break
        rows = public_klines(start_ms=cursor, limit=1000)
        if not rows:
            break
        batch.extend(r for r in rows if r["ms"] >= cursor)
        cursor = rows[-1]["ms"] + CANDLE_MS
        if len(rows) < 1000:
            break
    if not batch:
        return

    with LOCK:
        state = _load()
        if state["phase"] != "observing":
            return
        for row in batch:
            process_candle(state, row)
            if state["phase"] != "observing":
                break
        atomic_save(state)
    if state['processed']:
        print('PAPER V7 CANDLE UPDATE last='+utc_iso(state['last_ms'])+' processed='+str(state['processed'])+' shadow_trades='+str(len(state['trades']))+' control=CASH', flush=True)

def loop():
    while True:
        try:
            worker["last_attempt_utc"] = now_iso()
            iteration()
            worker["phase"] = "running"
            worker["last_error"] = None
            worker["last_success_utc"] = now_iso()
            time.sleep(25)
        except Exception as exc:
            worker["phase"] = "feed_error"
            worker["last_error"] = str(exc)[:240]
            print("PAPER V7 FEED ERROR:", str(exc), flush=True)
            time.sleep(60)
