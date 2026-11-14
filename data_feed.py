"""Data-feed contract for B3 paper trading. No live provider is connected."""
from __future__ import annotations
import csv
import io
import math
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Iterable

TICK = {"WIN": 5.0, "WDO": 0.5}

@dataclass(frozen=True)
class Candle:
    timestamp: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float

@dataclass(frozen=True)
class FeedStatus:
    source: str
    symbol: str
    live: bool
    connected: bool
    delayed: bool

def parse_candles(csv_text: str, symbol: str) -> list[Candle]:
    """Parse historical candles in timestamp,open,high,low,close,volume format."""
    if symbol not in TICK:
        raise ValueError("Unsupported symbol")
    reader = csv.DictReader(io.StringIO(csv_text.lstrip("\ufeff")))
    expected = ["timestamp", "open", "high", "low", "close", "volume"]
    if reader.fieldnames != expected:
        raise ValueError("Expected CSV columns: " + ",".join(expected))
    result: list[Candle] = []
    previous: datetime | None = None
    for line, row in enumerate(reader, start=2):
        try:
            if None in row or any(row.get(k) is None for k in expected):
                raise ValueError("Missing column")
            timestamp = datetime.fromisoformat(row["timestamp"].replace("Z", "+00:00"))
            if timestamp.tzinfo is None:
                raise ValueError("Timestamp must include timezone")
            timestamp = timestamp.astimezone(timezone.utc)
            values = [float(row[k]) for k in expected[1:]]
            o, h, l, c, v = values
            tick = TICK[symbol]
            if not all(math.isfinite(x) for x in values):
                raise ValueError("Non-finite value")
            if min(o, h, l, c) <= 0 or v < 0 or h < max(o, c) or l > min(o, c):
                raise ValueError("Invalid OHLCV")
            if any(abs(x / tick - round(x / tick)) > 1e-6 for x in (o, h, l, c)):
                raise ValueError("Price not aligned to tick")
            if previous is not None and timestamp <= previous:
                raise ValueError("Timestamps must be strictly increasing")
            result.append(Candle(timestamp, o, h, l, c, v))
            previous = timestamp
        except (TypeError, ValueError, OverflowError) as exc:
            raise ValueError(f"Invalid candle at CSV line {line}: {exc}") from exc
    if not result:
        raise ValueError("CSV contains no candles")
    return result

class HistoricalFeed:
    """Deterministic iterator; next candle is inaccessible until next() is called."""
    def __init__(self, candles: Iterable[Candle], symbol: str):
        if symbol not in TICK:
            raise ValueError("Unsupported symbol")
        self._candles = iter(candles)
        self.status = FeedStatus("imported_csv", symbol, live=False, connected=True, delayed=False)

    def next(self) -> Candle | None:
        return next(self._candles, None)

class LiveFeedUnavailable(RuntimeError):
    pass

class LiveFeed:
    """Fail closed until a licensed market-data provider is implemented."""
    def __init__(self, symbol: str):
        if symbol not in TICK:
            raise ValueError("Unsupported symbol")
        self.status = FeedStatus("not_configured", symbol, live=True, connected=False, delayed=False)

    def next(self) -> Candle:
        raise LiveFeedUnavailable(
            "Live B3 feed is not connected. Configure a licensed provider before paper trading."
        )
