"""Simulador de ejecucion con fees, slippage, TP, SL y timeout."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .config import BacktestConfig


@dataclass
class Position:
    token: str
    entry_timestamp: int
    entry_price: float
    quantity: float
    stake: float
    bars_held: int = 0


@dataclass
class Trade:
    token: str
    entry_timestamp: int
    exit_timestamp: int
    entry_price: float
    exit_price: float
    quantity: float
    pnl: float
    return_pct: float
    reason: str

    def as_dict(self) -> dict[str, Any]:
        return self.__dict__.copy()


class BacktestExecutor:
    def __init__(self, config: BacktestConfig | None = None, *, slippage_bps: float | None = None, fee_bps: float | None = None) -> None:
        if config is None:
            config = BacktestConfig(
                slippage_bps=50.0 if slippage_bps is None else slippage_bps,
                fee_bps=30.0 if fee_bps is None else fee_bps,
            )
        self.config = config
        self.position: Position | None = None

    def calculate_fill_price(self, price: float, side: str) -> tuple[float, float]:
        if side == "buy":
            return price * (1 + self.config.slippage_bps / 10000), self.config.slippage_bps
        if side == "sell":
            return price * (1 - self.config.slippage_bps / 10000), self.config.slippage_bps
        raise ValueError("side debe ser 'buy' o 'sell'")

    def open_position(self, token: str, timestamp_or_bar, entry_price: float | None = None, *, capital: float | None = None, position_size: float | None = None) -> Position:
        if self.position is not None:
            raise RuntimeError("ya existe una posicion abierta")
        if isinstance(timestamp_or_bar, dict):
            timestamp = timestamp_or_bar["timestamp"]
            raw = float(timestamp_or_bar["open"])
            stake = self.config.initial_cash * self.config.position_fraction
        else:
            timestamp = timestamp_or_bar
            raw = float(entry_price)
            stake = (self.config.initial_cash if capital is None else capital) * (self.config.position_fraction if position_size is None else position_size)
        entry, _ = self.calculate_fill_price(raw, "buy")
        fee = stake * self.config.fee_bps / 10000
        self.position = Position(token, timestamp, entry, max(stake - fee, 0) / entry, stake)
        return self.position

    def check_exit(self, bar: dict[str, float | int]) -> Trade | None:
        if self.position is None:
            return None
        position = self.position
        position.bars_held += 1
        tp = position.entry_price * (1 + self.config.take_profit)
        sl = position.entry_price * (1 - self.config.stop_loss)
        high, low = float(bar["high"]), float(bar["low"])
        if low <= sl:
            return self.close_position(bar, "stop_loss", sl)
        if high >= tp:
            return self.close_position(bar, "take_profit", tp)
        hold_limit = max(1, int(self.config.max_hold_minutes / 5))
        if position.bars_held >= hold_limit:
            return self.close_position(bar, "timeout")
        return None

    def close_position(self, position_or_bar, timestamp=None, exit_price: float | None = None, reason: str = "manual", price: float | None = None) -> Trade:
        if self.position is None:
            raise RuntimeError("no existe posicion abierta")
        if isinstance(position_or_bar, Position):
            position = position_or_bar
            self.position = position
            exit_timestamp = timestamp
            raw_exit = float(exit_price)
        else:
            position = self.position
            bar = position_or_bar
            if isinstance(timestamp, str):
                reason, exit_timestamp = timestamp, bar["timestamp"]
            else:
                exit_timestamp = bar["timestamp"] if timestamp is None else timestamp
            raw_exit = float(exit_price if exit_price is not None else price if price is not None else bar["close"])
        filled_exit, _ = self.calculate_fill_price(raw_exit, "sell")
        gross = position.quantity * filled_exit
        fee_out = gross * self.config.fee_bps / 10000
        pnl = gross - fee_out - position.stake
        trade = Trade(position.token, position.entry_timestamp, exit_timestamp, position.entry_price, filled_exit, position.quantity, pnl, pnl / position.stake if position.stake else 0.0, reason)
        self.position = None
        return trade
