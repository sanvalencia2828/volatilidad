"""Descarga y normalizacion de velas historicas desde gmgn-cli.

El comando se ejecuta como proceso externo para reutilizar la autenticacion y
throttling del cliente GMGN instalado en la maquina. Las respuestas se cachean
por token y rango temporal para que una corrida sea reproducible.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


class HistoricalFetcher:
    MIN_CANDLES = 10

    def __init__(self, cache_dir: str | Path = "backtest/data/cache", cli: str = "gmgn-cli") -> None:
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.cli = shutil.which("gmgn-cli.ps1") or shutil.which(cli) or cli

    def fetch(self, token_address: str, start: int, end: int, chain: str = "sol", resolution: str = "5m") -> list[dict[str, float | int]]:
        if start >= end:
            raise ValueError("start debe ser menor que end")
        cache = self.cache_dir / f"{chain}_{token_address}_{resolution}_{start}_{end}.json"
        if cache.exists():
            cached = self._load(cache)
            return cached if len(cached) >= self.MIN_CANDLES else []

        command = [self.cli, "market", "kline", "--chain", chain, "--address", token_address,
                   "--resolution", resolution, "--from", str(start), "--to", str(end), "--raw"]
        if os.name == "nt" and self.cli.lower().endswith(".ps1"):
            command = ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", self.cli, *command[1:]]
        completed = subprocess.run(command, capture_output=True, check=False)
        if completed.returncode != 0:
            error = completed.stderr.decode("utf-8", errors="replace").strip()
            if "429" in error or "RATE_LIMIT" in error.upper():
                match = re.search(r"~([0-9]+)s remaining", error)
                wait = int(match.group(1)) + 2 if match else 60
                time.sleep(wait)
                completed = subprocess.run(command, capture_output=True, check=False)
                if completed.returncode == 0:
                    error = ""
                else:
                    error = completed.stderr.decode("utf-8", errors="replace").strip()
            if completed.returncode != 0:
                raise RuntimeError(f"gmgn-cli fallo ({completed.returncode}): {error}")
        try:
            stdout = completed.stdout.decode("utf-8", errors="replace") if completed.stdout else ""
            rows = self._normalize(json.loads(stdout))
        except (json.JSONDecodeError, TypeError, ValueError) as exc:
            raise ValueError("gmgn-cli no devolvio JSON OHLCV valido") from exc
        if len(rows) < self.MIN_CANDLES:
            return []
        self._write(cache, rows)
        time.sleep(0.5)
        return rows

    def fetch_range(self, token_address: str, start: int, end: int, chain: str = "sol", resolution: str = "5m", chunk_days: int = 7) -> list[dict[str, float | int]]:
        """Descarga por bloques, evitando limites de tamano del CLI/API."""
        if chunk_days < 1:
            raise ValueError("chunk_days debe ser positivo")
        rows: list[dict[str, float | int]] = []
        chunk = chunk_days * 86400
        cursor = start
        while cursor < end:
            stop = min(cursor + chunk, end)
            rows.extend(self.fetch(token_address, cursor, stop, chain, resolution))
            cursor = stop
            if cursor < end:
                time.sleep(0.1)
        return self._dedupe(rows)

    def fetch_token_universe(self, chain: str = "sol", limit: int = 100, universe_type: str = "completed") -> list[str]:
        """Obtiene addresses del universo solicitado usando el CLI autenticado."""
        if universe_type == "trending":
            args = ["market", "hot-searches", "--chain", chain, "--interval", "5m", "--raw"]
        else:
            args = ["market", "trenches", "--chain", chain, "--type", universe_type, "--limit", str(limit), "--raw"]
        command = [self.cli, *args]
        if os.name == "nt" and self.cli.lower().endswith(".ps1"):
            command = ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", self.cli, *args]
        completed = subprocess.run(command, capture_output=True, check=False)
        if completed.returncode != 0:
            error = completed.stderr.decode("utf-8", errors="replace").strip()
            raise RuntimeError(f"gmgn-cli universo fallo: {error}")
        payload = json.loads(completed.stdout.decode("utf-8", errors="replace"))
        if universe_type == "trending":
            groups = payload if isinstance(payload, list) else payload.get("data", [])
            rows = [row for group in groups for row in (group.get("tokens", []) if isinstance(group, dict) else [])]
        else:
            data = payload.get("data", payload) if isinstance(payload, dict) else payload
            rows = data.get(universe_type, []) if isinstance(data, dict) else data
        return list(dict.fromkeys(str(row["address"]) for row in rows if isinstance(row, dict) and row.get("address")))[:limit]

    @staticmethod
    def _normalize(payload: Any) -> list[dict[str, float | int]]:
        raw = payload
        if isinstance(raw, dict):
            for key in ("list", "data", "klines", "candles", "result"):
                value = raw.get(key)
                if isinstance(value, (list, dict)):
                    raw = value
                    break
        if isinstance(raw, dict):
            raw = raw.get("list", [])
        if not isinstance(raw, list):
            raise ValueError("formato de velas desconocido")

        normalized = []
        for item in raw:
            if isinstance(item, dict):
                get = lambda *keys: next((item[k] for k in keys if k in item), None)
                values = [get("timestamp", "time", "startTime"), get("open"), get("high"), get("low"), get("close"), get("volume", "vol")]
            elif isinstance(item, (list, tuple)) and len(item) >= 6:
                values = list(item[:6])
            else:
                continue
            if values[0] is None:
                continue
            timestamp = int(float(values[0]))
            if timestamp < 10_000_000_000:
                timestamp *= 1000
            open_, high, low, close, volume = (float(value or 0) for value in values[1:])
            if min(open_, high, low, close) <= 0 or high < low:
                continue
            normalized.append({"timestamp": timestamp, "open": open_, "high": high, "low": low, "close": close, "volume": volume})
        return sorted(HistoricalFetcher._dedupe(normalized), key=lambda row: row["timestamp"])

    @staticmethod
    def _dedupe(rows: list[dict[str, float | int]]) -> list[dict[str, float | int]]:
        return list({int(row["timestamp"]): row for row in rows}.values())

    @staticmethod
    def _load(path: Path) -> list[dict[str, float | int]]:
        return HistoricalFetcher._normalize(json.loads(path.read_text(encoding="utf-8")))

    @staticmethod
    def _write(path: Path, rows: list[dict[str, float | int]]) -> None:
        path.write_text(json.dumps(rows, separators=(",", ":")), encoding="utf-8")
