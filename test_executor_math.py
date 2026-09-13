import pytest
from backtest.executor import BacktestExecutor
from datetime import datetime, timedelta

EX = BacktestExecutor(slippage_bps=50.0, fee_bps=30.0)
T0 = datetime(2024, 1, 1, 12, 0)
T1 = T0 + timedelta(minutes=30)

def _round_trip(entry_price, exit_price):
    t = EX.open_position("TOK", T0, entry_price, capital=10_000, position_size=0.1)
    return EX.close_position(t, T1, exit_price, reason="tp")

def test_precio_plano_debe_perder_dinero():
    t = _round_trip(100.0, 100.0)
    assert t.pnl < 0, f"❌ Precio plano dio PnL={t.pnl:.2f}. Costes ausentes."
    assert -20 < t.pnl < -12, f"PnL fuera de rango esperado: {t.pnl:.2f}"

def test_perdida_real_es_negativa():
    t = _round_trip(100.0, 90.0)
    assert t.pnl < 0, f"❌ Caída del 10% dio PnL={t.pnl:.2f}. SIGNO INVERTIDO."
    assert -125 < t.pnl < -105, f"PnL fuera de rango: {t.pnl:.2f}"

def test_ganancia_real_es_positiva():
    t = _round_trip(100.0, 110.0)
    assert 70 < t.pnl < 95, f"PnL fuera de rango: {t.pnl:.2f}"
    assert t.pnl < 100, "❌ Ganancia bruta sin descontar costes"

def test_rug_pull_pierde_casi_todo():
    t = _round_trip(100.0, 5.0)
    assert t.pnl < -900, f"❌ Rug pull dio PnL={t.pnl:.2f}"

def test_slippage_empeora_ambos_lados():
    buy, _ = EX.calculate_fill_price(100.0, "buy")
    sell, _ = EX.calculate_fill_price(100.0, "sell")
    assert buy > 100.0, "❌ Slippage de compra debe SUBIR el precio"
    assert sell < 100.0, "❌ Slippage de venta debe BAJAR el precio"
