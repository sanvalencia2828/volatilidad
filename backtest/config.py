"""Configuracion reproducible del backtest."""

from dataclasses import dataclass
from pathlib import Path


@dataclass
class BacktestConfig:
    chain: str = "sol"
    resolution: str = "5m"
    train_bars: int = 1152  # 4 dias de velas de 5 minutos
    test_bars: int = 288  # 1 dia
    step_bars: int = 288
    initial_cash: float = 1_000.0
    position_fraction: float = 1.0
    fee_bps: float = 100.0
    slippage_bps: float = 100.0
    take_profit: float = 0.60
    stop_loss: float = 0.35
    max_hold_bars: int = 12
    max_hold_minutes: int = 60
    cache_dir: Path = Path("backtest/data/cache")
    n_tokens: int = 100
    days_back: int = 7
    train_window_days: int = 2
    test_window_days: int = 1
    universe_type: str = "completed"

    def __post_init__(self) -> None:
        if self.train_bars < 2 or self.test_bars < 1 or self.step_bars < 1:
            raise ValueError("train_bars, test_bars y step_bars deben ser positivos")
        if not 0 < self.position_fraction <= 1:
            raise ValueError("position_fraction debe estar entre 0 y 1")
        if self.initial_cash <= 0 or self.fee_bps < 0 or self.slippage_bps < 0:
            raise ValueError("capital, comision y slippage deben ser validos")
        if self.take_profit <= 0 or not 0 < self.stop_loss < 1:
            raise ValueError("TP y SL deben ser porcentajes positivos validos")
        if self.max_hold_bars < 1:
            raise ValueError("max_hold_bars debe ser positivo")
        if self.max_hold_minutes < 5:
            raise ValueError("max_hold_minutes debe ser al menos 5")
        if self.n_tokens < 1 or self.days_back < 1:
            raise ValueError("n_tokens y days_back deben ser positivos")
        if self.train_window_days < 1 or self.test_window_days < 1:
            raise ValueError("train_window_days y test_window_days deben ser positivos")
        if self.universe_type not in {"completed", "trending", "new_creation"}:
            raise ValueError("universe_type debe ser completed, trending o new_creation")
