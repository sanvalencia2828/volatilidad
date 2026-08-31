from fastapi.testclient import TestClient

from backend import make_app


class FakeBridge:
    def radar_migrated(self, chain="sol", **kwargs):
        return [{
            "symbol": "TEST",
            "name": "Test Token",
            "address": "abc123",
            "market_cap": 120000,
            "liquidity": 20000,
            "top_10_holder_rate": 0.12,
            "fresh_wallet_rate": 0.08,
            "bundler_trader_amount_rate": 0.15,
            "rug_ratio": 0.1,
            "launchpad_status": 1,
            "smart_degen_count": 2,
            "price_change_percent1h": 3.5,
            "price_change_percent5m": 1.2,
            "holder_count": 300,
            "price": 0.00042,
            "volume_24h": 50000,
        }]

    def get_dossier_data(self, chain: str, address: str):
        return {
            "address": address,
            "chain": chain,
            "symbol": "TEST",
            "name": "Test Token",
            "price": 0.00042,
            "market_cap": 120000,
            "liquidity": 20000,
            "top_10_holders": 0.12,
            "dev_hold": 0.0,
            "bundler_rate": 0.15,
            "fresh_wallet_rate": 0.08,
            "smart_money_count": 15,
            "kol_count": 3,
            "rug_ratio": 0.05,
            "renounced_mint": True,
            "renounced_freeze": True,
            "creator_status": "creator_close",
            "klines": [
                {"time": 1700000000000 + i * 300000, "open": "0.00040", "high": "0.00045", "low": "0.00039", "close": str(0.00040 + i * 0.000002), "volume": "1000"}
                for i in range(10)
            ],
            "kline_closes": [0.00040 + i * 0.000002 for i in range(10)],
        }


def test_health():
    app = make_app(FakeBridge())
    client = TestClient(app)
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


def test_run_endpoint_returns_tokens():
    app = make_app(FakeBridge())
    client = TestClient(app)
    resp = client.post("/api/run", json={"chain": "sol"})
    assert resp.status_code == 200
    payload = resp.json()
    assert payload["chain"] == "sol"
    assert payload["count"] == 1
    assert payload["tokens"][0]["symbol"] == "TEST"


def test_dossier_endpoint_returns_dossier():
    app = make_app(FakeBridge())
    client = TestClient(app)
    resp = client.get("/api/dossier?chain=sol&address=abc123")
    assert resp.status_code == 200
    payload = resp.json()
    assert payload["symbol"] == "TEST"
    assert payload["address"] == "abc123"
    assert payload["market_cap"] == 120000
    assert payload["liquidity"] == 20000
    assert payload["top_10_holders"] == 0.12
    assert payload["dev_hold"] == 0.0
    assert payload["smart_money_count"] == 15
    assert payload["kol_count"] == 3
    assert len(payload["klines"]) == 10
    assert len(payload["kline_closes"]) == 10


def test_dossier_endpoint_requires_address():
    app = make_app(FakeBridge())
    client = TestClient(app)
    resp = client.get("/api/dossier?chain=sol")
    assert resp.status_code == 422  # FastAPI validation error for missing required query param


def test_run_endpoint_handles_rate_limit():
    class RateLimitBridge:
        def radar_migrated(self, **kwargs):
            raise RuntimeError("429 Too Many Requests")

    app = make_app(RateLimitBridge())
    client = TestClient(app)
    resp = client.post("/api/run", json={"chain": "sol"})
    assert resp.status_code == 429
    assert "Rate limit de GMGN activo" in resp.json()["detail"]


def test_dossier_endpoint_handles_timeout():
    class TimeoutBridge:
        def get_dossier_data(self, chain: str, address: str):
            raise TimeoutError("GMGN API Timeout")

    app = make_app(TimeoutBridge())
    client = TestClient(app)
    resp = client.get("/api/dossier?chain=sol&address=abc123")
    assert resp.status_code == 504
    assert "Timeout" in resp.json()["detail"]


def test_run_endpoint_preserves_zero_value_filters():
    capture = {}

    class CaptureBridge:
        def radar_migrated(self, **kwargs):
            capture.update(kwargs)
            return []

    app = make_app(CaptureBridge())
    client = TestClient(app)
    resp = client.post(
        "/api/run",
        json={
            "chain": "sol",
            "min_marketcap": 0,
            "max_marketcap": 0,
            "min_liquidity": 0,
            "max_top_holder_rate": 0,
            "max_bundler_rate": 0,
            "max_fresh_wallet_rate": 0,
        },
    )

    assert resp.status_code == 200
    assert capture["min_mc"] == 0
    assert capture["max_mc"] == 0
    assert capture["min_liq"] == 0
    assert capture["max_top10"] == 0
    assert capture["max_bundler"] == 0
    assert capture["max_fresh"] == 0


def test_gmgn_cli_retries_once_after_429(monkeypatch):
    import subprocess

    import gmgn_bridge

    calls = []

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        if len(calls) == 1:
            return subprocess.CompletedProcess(cmd, returncode=1, stdout="", stderr="429 RATE_LIMIT_BANNED")
        return subprocess.CompletedProcess(cmd, returncode=0, stdout='{"ok": true}', stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.setattr(gmgn_bridge, "GMGN_BAN_WAIT", 0)
    monkeypatch.setattr(gmgn_bridge.time, "sleep", lambda *_args, **_kwargs: None)

    bridge = gmgn_bridge.GMGNBridge()
    assert bridge.cli("info", ["token", "info", "--chain", "sol", "--address", "abc123"]) == {"ok": True}
    assert len(calls) == 2
