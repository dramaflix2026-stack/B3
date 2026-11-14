from dataclasses import dataclass
from datetime import datetime
import math
from typing import Optional

SPECS = {"WIN": (5.0, 1.0), "WDO": (0.5, 5.0)}

@dataclass
class Risk:
    max_contracts: int = 1
    max_daily_loss: float = 100.0
    max_trades: int = 5
    stop_brl: float = 30.0
    fee_per_side_per_contract: float = 0.0
    slippage_ticks: int = 1

class Simulator:
    def __init__(self, symbol: str, risk: Risk):
        if symbol not in SPECS: raise ValueError("Unsupported symbol")
        if (not isinstance(risk.max_contracts, int) or risk.max_contracts < 1 or
            not isinstance(risk.max_trades, int) or risk.max_trades < 1 or
            not isinstance(risk.slippage_ticks, int) or risk.slippage_ticks < 0 or
            any(not math.isfinite(v) or v <= 0 for v in (risk.max_daily_loss, risk.stop_brl)) or
            not math.isfinite(risk.fee_per_side_per_contract) or risk.fee_per_side_per_contract < 0):
            raise ValueError('Invalid risk configuration')
        self.symbol, self.risk = symbol, risk
        self.position: Optional[dict] = None
        self.realized = 0.0
        self.trades = 0
        self.locked = False
        self.history = []
        self.session_date = None

    def _session(self, timestamp: datetime):
        day = timestamp.date()
        if self.session_date is None: self.session_date = day
        if day != self.session_date:
            if self.position is not None: raise RuntimeError("Close position before changing session")
            self.session_date, self.realized, self.trades, self.locked = day, 0.0, 0, False

    def _validate_price(self, price: float):
        tick, _ = SPECS[self.symbol]
        if not math.isfinite(price) or price <= 0 or abs(price / tick - round(price / tick)) > 1e-6:
            raise ValueError('Invalid price or tick alignment')

    def _fill(self, price: float, direction: int):
        tick, _ = SPECS[self.symbol]
        return price + direction * tick * self.risk.slippage_ticks

    def open(self, side: str, contracts: int, price: float, timestamp: datetime):
        self._session(timestamp)
        self._validate_price(price)
        if self.locked or self.realized <= -self.risk.max_daily_loss: raise RuntimeError("Daily risk lock")
        if self.position: raise RuntimeError("Position already open")
        if self.trades >= self.risk.max_trades: raise RuntimeError("Daily trade limit")
        if side not in ("buy", "sell") or not 1 <= contracts <= self.risk.max_contracts: raise ValueError("Invalid order")
        self._validate_price(price)
        direction = 1 if side == "buy" else -1
        entry = self._fill(price, direction)
        self.position = dict(side=side, contracts=contracts, entry=entry, timestamp=timestamp)
        self.trades += 1
        return entry

    def unrealized(self, price: float):
        if not self.position: return 0.0
        p = self.position
        tick, value = SPECS[self.symbol]
        direction = 1 if p["side"] == "buy" else -1
        exit_price = self._fill(price, -direction)
        fees = 2*self.risk.fee_per_side_per_contract*p['contracts']
        return ((exit_price-p["entry"])/tick)*value*p["contracts"]*direction-fees

    def mark(self, price: float, timestamp: datetime):
        self._session(timestamp)
        self._validate_price(price)
        if self.position and (self.unrealized(price) <= -self.risk.stop_brl or self.realized + self.unrealized(price) <= -self.risk.max_daily_loss):
            return self.close(price, timestamp, "stop")
        return None

    def close(self, price: float, timestamp: datetime, reason="manual"):
        self._session(timestamp)
        if not self.position: raise RuntimeError("No position")
        self._validate_price(price)
        p = self.position
        tick, value = SPECS[self.symbol]
        direction = 1 if p["side"] == "buy" else -1
        exit_price = self._fill(price, -direction)
        gross = (exit_price-p["entry"])/tick*value*p["contracts"]*direction
        fees = 2*self.risk.fee_per_side_per_contract*p["contracts"]
        net = gross-fees
        self.realized += net
        record = dict(**p, exit=exit_price, gross=gross, fees=fees, net=net, reason=reason)
        self.history.append(record)
        self.position = None
        if self.realized <= -self.risk.max_daily_loss: self.locked = True
        return record

    def emergency_stop(self, price: float, timestamp: datetime):
        record = self.close(price, timestamp, "emergency") if self.position else None
        self.locked = True
        return record
