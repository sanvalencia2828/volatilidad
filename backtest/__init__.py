"""Herramientas para validacion historica sin look-ahead."""

from .config import BacktestConfig
from .executor import BacktestExecutor

__all__ = ["BacktestConfig", "BacktestExecutor", "WalkForwardEngine"]


def __getattr__(name):
    if name == "WalkForwardEngine":
        from .engine import WalkForwardEngine
        return WalkForwardEngine
    raise AttributeError(name)
