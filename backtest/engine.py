"""Motor walk-forward diario para evaluar el ScoringEngine sin look-ahead."""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from scoring import ScoringEngine, TokenFeatures

from .config import BacktestConfig
from .executor import BacktestExecutor, Trade
from .fetcher import HistoricalFetcher
from .report import print_report


@dataclass(frozen=True)
class WalkForwardWindow:
    token: str
    train_start: int
    train_end: int
    test_start: int
    test_end: int


class WalkForwardEngine:
    """Usa 4 dias para ajuste y el dia siguiente exclusivamente para test."""

    BARS_PER_DAY = 288

    def __init__(self, config: BacktestConfig, scorer: ScoringEngine | None = None) -> None:
        self.config = config
        self.scoring = scorer or ScoringEngine()
        self.executor = BacktestExecutor(config)

    def run(self, datasets: dict[str, list[dict[str, float | int]]] | None = None) -> dict[str, Any]:
        if datasets is None:
            end = int(time.time())
            start = end - self.config.days_back * 86400
            fetcher = HistoricalFetcher(self.config.cache_dir)
            tokens = fetcher.fetch_token_universe(self.config.chain, self.config.n_tokens, self.config.universe_type)
            datasets = {token: fetcher.fetch_range(token, start, end, self.config.chain, self.config.resolution) for token in tokens}
        trades: list[Trade] = []
        windows: list[WalkForwardWindow] = []
        for token, source in datasets.items():
            bars = sorted(source, key=lambda row: int(row["timestamp"]))
            requested_train = self.config.train_window_days * self.BARS_PER_DAY
            requested_test = self.config.test_window_days * self.BARS_PER_DAY
            test_bars = min(requested_test, max(2, len(bars) // 3))
            train_bars = min(requested_train, len(bars) - test_bars)
            if train_bars < 2 or test_bars < 2:
                continue
            for train_start in range(0, len(bars) - train_bars - test_bars + 1, self.config.step_bars):
                train_end = train_start + train_bars
                test_end = train_end + test_bars
                window = WalkForwardWindow(token, train_start, train_end, train_end, test_end)
                windows.append(window)
                self._fit(bars[train_start:train_end])
                trades.extend(self._test_window(token, bars, train_end, test_end))
        return {"trades": [trade.as_dict() for trade in trades], "windows": [asdict(window) for window in windows], "survivorship_warning": True}

    def _test_window(self, token: str, bars: list[dict[str, float | int]], test_start: int, test_end: int) -> list[Trade]:
        """Evalua una ventana y registra cada cierre exactamente una vez."""
        open_positions: dict[str, BacktestExecutor] = {}
        window_trades: list[Trade] = []
        for index in range(test_start, test_end):
            bar = bars[index]
            executor = open_positions.get(token)
            if executor is not None:
                closed = executor.check_exit(bar)
                if closed is not None:
                    window_trades.append(closed)
                    del open_positions[token]
            if token not in open_positions:
                try:
                    features = self._features(token, bars, index)
                    verdict = self.scoring.score(features)
                except (KeyError, IndexError, TypeError, ValueError, ZeroDivisionError):
                    continue
                if self._is_enter(verdict):
                    self.executor.open_position(token, bar)
                    open_positions[token] = self.executor

        # Any remaining position is closed once at the final observed price.
        for token_addr, executor in list(open_positions.items()):
            window_trades.append(executor.close_position(bars[test_end - 1], "timeout"))
            del open_positions[token_addr]
        return window_trades

    def _fit(self, train_bars: list[dict[str, float | int]]) -> None:
        fit = getattr(self.scoring, "fit", None)
        if callable(fit):
            fit([self._features("train", train_bars, i) for i in range(len(train_bars))])

    @staticmethod
    def _features(token: str, bars: list[dict[str, float | int]], index: int) -> TokenFeatures:
        # La vela de entrada aun no es conocida al generar la senal. Se usa
        # la ultima vela cerrada y se ejecuta sobre la apertura de `index`.
        observed = bars[max(0, index - 1)]
        previous = bars[max(0, index - 13):index - 1]
        closes = [float(row["close"]) for row in previous if float(row["close"]) > 0]
        first = closes[0] if closes else float(observed["close"])
        return TokenFeatures(token_address=token, price=float(observed["close"]), high=float(observed["high"]), low=float(observed["low"]), volume=float(observed.get("volume", 0)), momentum=(float(observed["close"]) / first - 1) if first else 0.0, timestamp=int(observed["timestamp"]))

    @staticmethod
    def _is_enter(verdict: Any) -> bool:
        value = verdict.get("verdict") if isinstance(verdict, dict) else getattr(verdict, "verdict", verdict)
        return str(value).upper() == "ENTER"


def _load_tokens(limit: int, chain: str = "sol") -> list[str]:
    cli = shutil.which("gmgn-cli.ps1") or shutil.which("gmgn-cli")
    if not cli:
        raise RuntimeError("gmgn-cli no esta disponible en PATH")
    addresses: list[str] = []
    # `trenches --type trending` puede devolver una lista vacia; hot-searches
    # expone el mismo universo activo con addresses y datos de mercado.
    hot_command = [cli, "market", "hot-searches", "--chain", chain, "--interval", "5m", "--raw"]
    if os.name == "nt" and cli.lower().endswith(".ps1"):
        hot_command = ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", cli, *hot_command[1:]]
    hot_result = subprocess.run(hot_command, capture_output=True, check=False)
    if hot_result.returncode == 0:
        try:
            hot_payload = json.loads(hot_result.stdout.decode("utf-8", errors="replace"))
            groups = hot_payload if isinstance(hot_payload, list) else hot_payload.get("data", [])
            for group in groups:
                rows = group.get("tokens", []) if isinstance(group, dict) else []
                addresses.extend(str(row["address"]) for row in rows if isinstance(row, dict) and row.get("address"))
        except (json.JSONDecodeError, TypeError):
            pass
    addresses = list(dict.fromkeys(addresses))
    if len(addresses) >= limit:
        return addresses[:limit]

    for token_type in ("completed", "new_creation"):
        command = [cli, "market", "trenches", "--chain", chain, "--type", token_type, "--limit", str(limit), "--raw"]
        if os.name == "nt" and cli.lower().endswith(".ps1"):
            command = ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", cli, *command[1:]]
        completed = subprocess.run(command, capture_output=True, check=False)
        if completed.returncode != 0:
            error = completed.stderr.decode("utf-8", errors="replace").strip()
            if "429" in error or "RATE_LIMIT" in error.upper():
                match = re.search(r"~([0-9]+)s remaining", error)
                time.sleep((int(match.group(1)) + 2) if match else 60)
                completed = subprocess.run(command, capture_output=True, check=False)
        if completed.returncode != 0:
            error = completed.stderr.decode("utf-8", errors="replace").strip()
            raise RuntimeError(f"gmgn-cli no pudo obtener el universo {token_type}: {error}")
        stdout = completed.stdout.decode("utf-8", errors="replace") if completed.stdout else ""
        try:
            payload = json.loads(stdout)
        except json.JSONDecodeError as exc:
            raise RuntimeError("gmgn-cli devolvio una respuesta no JSON") from exc
        data = payload.get("data", payload) if isinstance(payload, dict) else payload
        rows = data.get(token_type, []) if isinstance(data, dict) else data
        addresses.extend(str(row["address"]) for row in rows if isinstance(row, dict) and row.get("address"))
    return list(dict.fromkeys(addresses))[:limit]


def main() -> None:
    parser = argparse.ArgumentParser(description="Backtest walk-forward de AI TRADER")
    parser.add_argument("--tokens", type=int, default=100)
    parser.add_argument("--days", type=int, default=7)
    parser.add_argument("--chain", default="sol")
    args = parser.parse_args()
    if args.tokens < 1 or args.days < 5:
        parser.error("Se requieren tokens positivos y al menos 5 dias")
    tokens = _load_tokens(args.tokens)
    if len(tokens) < args.tokens:
        raise RuntimeError(f"Se solicitaron {args.tokens} tokens, pero GMGN solo devolvio {len(tokens)} tokens")
    end = int(time.time())
    start = end - args.days * 86400
    config = BacktestConfig(chain=args.chain, train_bars=1152, test_bars=288, step_bars=288, n_tokens=args.tokens, days_back=args.days, train_window_days=2, test_window_days=1, universe_type="trending")
    result = WalkForwardEngine(config).run()
    trades = [Trade(**trade) for trade in result["trades"]]
    # Cada token se simula con una posicion independiente; el capital de
    # referencia del portfolio debe incluir todas las asignaciones.
    values = print_report(trades, config.initial_cash * len(datasets))
    if values["profit_factor"] < 1.0:
        reasons = {}
        for trade in trades:
            reasons[trade.reason] = reasons.get(trade.reason, 0) + 1
        if reasons:
            print(f"Exit reason mas comun: {max(reasons, key=reasons.get)}")


if __name__ == "__main__":
    main()
