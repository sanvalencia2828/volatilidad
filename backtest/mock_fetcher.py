"""Fuente OHLCV determinista para validar el pipeline sin depender de GMGN."""

from __future__ import annotations

import hashlib


class MockFetcher:
    def fetch_range(self, token_address: str, start: int, end: int, chain: str = "sol", resolution: str = "5m") -> list[dict[str, float | int]]:
        del chain, resolution
        bars = []
        count = max(0, (end - start) // 300)
        token_seed = int(hashlib.sha256(token_address.encode()).hexdigest()[:8], 16)
        losing = token_seed % 5 == 0
        price = 1.0 + (token_seed % 100) / 1000
        for index in range(count):
            phase = index % 24
            if not losing:
                drift = 0.006
            else:
                # Mantiene momentum positivo y provoca un crash despues de
                # la senal, cubriendo el camino de stop-loss del executor.
                drift = -0.004 if phase == 5 else 0.002
            open_price = price
            close = max(0.000001, open_price * (1 + drift))
            high = max(open_price, close) * (1.72 if phase == 5 and not losing else 1.01)
            low = min(open_price, close) * (0.36 if phase == 5 and losing else 0.99)
            bars.append({
                "timestamp": (start + index * 300) * 1000,
                "open": open_price,
                "high": high,
                "low": low,
                "close": close,
                "volume": 1_000.0 + (token_seed % 500),
            })
            price = close
        return bars
