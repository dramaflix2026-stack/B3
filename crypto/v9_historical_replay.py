"""V9 historical replay with genuine, previously archived BTCUSDT M1 OHLC.

No signal forcing; the frozen V7 experimental rule is copied from persistent
state and applied on every completed candle. All trades stay in RAM and never
modify V7. Known historical data: NOT an untouched validation set.

Designed as one-click diagnostic. Uses only the newest verified monthly CSV,
not exchange order APIs or synthetic prices.
"""
import csv
import datetime as dt
import json
import math
import pathlib
from collections import deque

import paper_v7

MAX_REPLAY_CANDLES = 20000
WARMUP_CANDLES = 721
MAX_SNAPSHOT_TRADES = 6


def _utc_millis(value):
    parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("UTC timestamp without timezone")
    return int(parsed.timestamp() * 1000)


def _last_verified_month(data_dir):
    """Trust only data already downloaded and validated by the historical collector."""
    manifest = pathlib.Path(data_dir) / "BTCUSDT-manifest.json"
    entries = json.loads(manifest.read_text(encoding="utf-8"))
    if not isinstance(entries, list):
        raise ValueError("Manifest format invalid")
    eligible = [row for row in entries if isinstance(row, dict) and row.get("month")]
    if not eligible:
        raise ValueError("Nenhum mês BTCUSDT verificado")
    row = max(eligible, key=lambda item:item["month"])
    path = pathlib.Path(data_dir) / ("BTCUSDT-1m-" + row["month"] + ".csv")
    if not path.is_file():
        raise ValueError("Arquivo mensal verificado indisponível")
    if path.stat().st_size < 100:
        raise ValueError("Arquivo mensal BTCUSDT vazio")
    if row.get("rows",0) < WARMUP_CANDLES + 1:
        raise ValueError("Mês verificado sem candles suficientes")
    return row, path


def run(data_dir=None):
    """Replay real M1 candle OHLC. No network, volume writes or simulated buys forced."""
    root = pathlib.Path(data_dir if data_dir is not None else paper_v7.DATA)
    manifest_row, csv_path = _last_verified_month(root)
    # Only read the *frozen V7 hypothesis*; do not write into the V7 paper account.
    with paper_v7.LOCK:
        real_account = paper_v7._load()
        if real_account is None:
            raise ValueError("Conta V7 ainda não inicializada")
        frozen = json.loads(json.dumps(real_account["frozen"]))
    if frozen.get("control_decision") != "cash":
        raise ValueError("Controle V7 não está em caixa; replay abortado")
    if frozen.get("shadow_atr",{}).get("direction") not in ("low","high"):
        raise ValueError("Filtro ATR congelado indisponível")

    last = deque(maxlen=WARMUP_CANDLES+MAX_REPLAY_CANDLES)
    original_count = 0
    with csv_path.open("r",encoding="utf-8",newline="") as stream:
        reader = csv.DictReader(stream)
        expected = {"timestamp","open","high","low","close"}
        if not expected.issubset(reader.fieldnames or []):
            raise ValueError("CSV mensal sem campos OHLC obrigatórios")
        for record in reader:
            candle = {"ms":_utc_millis(record["timestamp"]),
                      "o":float(record["open"]),"h":float(record["high"]),
                      "l":float(record["low"]),"c":float(record["close"])}
            if (not all(math.isfinite(candle[k]) for k in ("o","h","l","c"))
                    or min(candle[k] for k in ("o","h","l","c"))<=0
                    or candle["h"]<max(candle["o"],candle["c"])
                    or candle["l"]>min(candle["o"],candle["c"])):
                raise ValueError("Candle histórico inválido")
            last.append(candle)
            original_count += 1
    if original_count != manifest_row["rows"]:
        raise ValueError("CSV mensal não corresponde ao total verificado no manifesto")
    candles = list(last)
    if len(candles) < WARMUP_CANDLES + 1:
        raise ValueError("Amostra não permite aquecimento e teste")
    for previous, following in zip(candles,candles[1:]):
        if following["ms"]-previous["ms"]!=paper_v7.CANDLE_MS:
            raise ValueError("Lacuna ou duplicação nos últimos candles verificados")
    test_account = paper_v7.new_state(candles[:WARMUP_CANDLES], frozen)
    for candle in candles[WARMUP_CANDLES:]:
        paper_v7.process_candle(test_account,candle)
        if test_account["phase"]!="observing":
            raise ValueError("Replay interrompido em candle de dados históricos")
    count = test_account["processed"]
    if count!=len(candles)-WARMUP_CANDLES or count<1:
        raise ValueError("Total de candles executados divergente")
    equity = test_account["cash"]+test_account["btc"]*test_account["last_price"]
    total_trades = int(test_account["total_trades"])
    trades = test_account["trades"]
    realized = float(test_account["realized"])
    fee = float(test_account["fees"])
    slip = float(test_account["slippage"])
    checks = [
        ("verified_source",original_count==int(manifest_row["rows"])),
        ("replay_contiguous",test_account["phase"]=="observing" and count==len(candles)-WARMUP_CANDLES),
        ("control_cash",frozen["control_decision"]=="cash"),
        ("no_forced_signals",total_trades <= count and test_account["blocked"] <= test_account["signals"]),
        ("no_negative_exposure",test_account["btc"]>=0 and test_account["cash"]>=0),
        ("finite_account",all(math.isfinite(value) for value in (equity,fee,slip,realized))),
        ("fee_accounting",fee>=0 and slip>=0),
        ("trade_counter",total_trades==len(trades)),
        ("consistent_position",bool(test_account["btc"])==bool(test_account["entry"])),
        ("initial_cash_preserved",abs(equity-(paper_v7.INITIAL_CASH+realized+
          (test_account["btc"]*(test_account["last_price"]-test_account["entry"]["price"])-test_account["entry"]["fee"]
           if test_account["btc"] and test_account["entry"] else 0)))<.01),
    ]
    passed = sum(ok for _,ok in checks)
    recent = [{key:trade.get(key) for key in
        ("entry_utc","exit_utc","reason","net_usdt","commissions_usdt","slippage_usdt")}
        for trade in trades[-MAX_SNAPSHOT_TRADES:]][::-1]
    return {
        "version":"V9",
        "status":"passed" if len(checks)==passed else "failed",
        "test_type":"historical_real_candle_replay",
        "source":"Binance Vision historical BTCUSDT M1 verified CSV",
        "source_month":manifest_row["month"],
        "historical_candles_in_month":original_count,
        "replayed_candles":count,
        "from_utc":paper_v7.utc_iso(candles[WARMUP_CANDLES]["ms"]),
        "to_utc":paper_v7.utc_iso(candles[-1]["ms"]),
        "atr_rule":frozen["shadow_atr"],
        "signals":int(test_account["signals"]),
        "atr_blocked":int(test_account["blocked"]),
        "trades_closed":total_trades,
        "open_position":test_account["btc"]>0,
        "realized_net_usdt":round(realized,4),
        "marked_pnl_usdt":round(equity-paper_v7.INITIAL_CASH,4),
        "marked_equity_usdt":round(equity,4),
        "fees_usdt":round(fee,4),
        "slippage_usdt":round(slip,4),
        "recent_virtual_trades":recent,
        "checks_passed":passed,
        "checks_total":len(checks),
        "failed_checks":[key for key,ok in checks if not ok],
        "uses_real_historical_prices":True,
        "signals_forced":False,
        "executes_exchange_orders":False,
        "writes_v7_state":False,
        "sample_is_new_prospective":False,
        "warning":"Preços históricos reais já disponíveis na pesquisa. Sem sinais forçados, sem ordens reais e sem gravação na V7. Histórico previamente analisado; não prova rentabilidade futura.",
    }
