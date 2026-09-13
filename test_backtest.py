from backtest.config import BacktestConfig
from backtest.engine import WalkForwardEngine
from backtest.report import calculate_metrics


def _bars(count=576):
    return [
        {"timestamp": i * 300_000, "open": 1 + i * 0.001, "high": 1.01 + i * 0.001, "low": 0.99 + i * 0.001, "close": 1 + i * 0.001, "volume": 1000}
        for i in range(count)
    ]


def test_pipeline_five_tokens_two_days():
    config = BacktestConfig(train_bars=288, test_bars=288, step_bars=288, max_hold_bars=12)
    result = WalkForwardEngine(config).run({f"token-{i}": _bars() for i in range(5)})
    values = calculate_metrics(
        [type("TradeLike", (), trade)() for trade in result["trades"]],
        config.initial_cash,
    )
    assert len(result["windows"]) == 5
    assert values["total_trades"] >= 0
    assert result["survivorship_warning"] is True
