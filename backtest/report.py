"""Metricas del backtest y reporte reproducible por consola."""

from __future__ import annotations

from typing import Iterable
import sys

from .executor import Trade


def calculate_metrics(trades: Iterable[Trade], initial_cash: float = 1_000.0) -> dict[str, float | int]:
    closed = list(trades)
    pnls = [float(trade.pnl) for trade in closed]
    gains = sum(pnl for pnl in pnls if pnl > 0)
    losses = abs(sum(pnl for pnl in pnls if pnl < 0))
    equity = initial_cash
    peak = initial_cash
    max_drawdown = 0.0
    for pnl in pnls:
        equity += pnl
        peak = max(peak, equity)
        max_drawdown = max(max_drawdown, (peak - equity) / peak if peak else 0.0)
    return {
        "net_pnl": sum(pnls),
        "win_rate": (sum(pnl > 0 for pnl in pnls) / len(pnls) * 100) if pnls else 0.0,
        "profit_factor": gains / losses if losses else (float("inf") if gains else 0.0),
        "max_drawdown": max_drawdown * 100,
        "total_trades": len(closed),
        "final_equity": equity,
    }


def print_report(trades: Iterable[Trade], initial_cash: float = 1_000.0) -> dict[str, float | int]:
    """Imprime metricas y devuelve el mismo payload para tests/automatizacion."""
    closed = list(trades)
    warning = "⚠️ ADVERTENCIA: Los resultados pueden estar afectados por Survivorship Bias, ya que se backtestean tokens que aún existen en la blockchain."
    try:
        print(warning)
    except UnicodeEncodeError:
        # Consolas Windows antiguas pueden usar cp1252 aunque el proceso sea UTF-8.
        if hasattr(sys.stdout, "reconfigure"):
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        print(warning)
    values = calculate_metrics(closed, initial_cash)
    print(f"Total PnL: {values['net_pnl']:.2f}")
    print(f"Win Rate: {values['win_rate']:.2f}%")
    print(f"Profit Factor: {values['profit_factor']:.4f}")
    print(f"Max Drawdown: {values['max_drawdown']:.2f}%")
    print(f"Total de Trades: {values['total_trades']}")
    reasons: dict[str, int] = {}
    for trade in closed:
        reasons[trade.reason] = reasons.get(trade.reason, 0) + 1
    print(f"Exit Reasons: {reasons}")
    return values
