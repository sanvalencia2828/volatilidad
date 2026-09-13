import os
import json
import logging
import asyncio
import httpx
import re
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from gmgn_bridge import GMGNBridge, RateLimitError

logger = logging.getLogger(__name__)


def _number(value: Any, default: float = 0.0) -> float:
    try:
        result = float(value)
        return result if result == result else default
    except (TypeError, ValueError):
        return default


def _first_number(*values: Any) -> float:
    for value in values:
        number = _number(value)
        if number != 0:
            return number
    return 0.0


def _extract_price(info: Dict[str, Any]) -> float:
    price = info.get("price")
    if isinstance(price, dict):
        return _first_number(price.get("price"), price.get("price_usd"), price.get("usd"))
    return _first_number(price, info.get("price_usd"), info.get("priceUsd"))


def _extract_liquidity(info: Dict[str, Any]) -> float:
    pool = info.get("pool") if isinstance(info.get("pool"), dict) else {}
    return _first_number(info.get("liquidity"), info.get("liquidity_usd"), info.get("liquidityUsd"), pool.get("liquidity"), pool.get("liquidity_usd"))


def _extract_market_cap(info: Dict[str, Any], price: float) -> float:
    supply = _first_number(info.get("circulating_supply"), info.get("total_supply"), info.get("max_supply"))
    return _first_number(info.get("market_cap"), info.get("market_cap_usd"), info.get("usd_market_cap"), info.get("migration_market_cap"), price * supply)


def _flatten_dossier(dossier: Dict[str, Any], chain: str, address: str) -> Dict[str, Any]:
    """Expose the flat contract expected by the UI from GMGN's nested payload."""
    info = dossier.get("info", dossier) if isinstance(dossier, dict) else {}
    info = info if isinstance(info, dict) else {}
    stat = info.get("stat", {}) if isinstance(info.get("stat"), dict) else {}
    tags = info.get("wallet_tags_stat", {}) if isinstance(info.get("wallet_tags_stat"), dict) else {}
    dev = info.get("dev", {}) if isinstance(info.get("dev"), dict) else {}
    price = _extract_price(info)
    market_cap = _extract_market_cap(info, price)
    liquidity = _extract_liquidity(info)
    klines = dossier.get("kline", dossier.get("klines", [])) if isinstance(dossier, dict) else []
    return {
        "address": address,
        "chain": chain,
        "symbol": info.get("symbol") or "UNKNOWN",
        "name": info.get("name") or info.get("symbol") or "Unknown",
        "price": price,
        "market_cap": market_cap,
        "liquidity": liquidity,
        "dev_hold": _first_number(stat.get("dev_team_hold_rate"), dev.get("dev_team_hold_rate")),
        "top_10_holders": _first_number(stat.get("top_10_holder_rate"), dev.get("top_10_holder_rate"), info.get("top_10_holders"), info.get("top_10_holder_rate")),
        "bundler_rate": _first_number(stat.get("top_bundler_trader_percentage"), info.get("bundler_rate")),
        "fresh_wallet_rate": _first_number(stat.get("fresh_wallet_rate"), info.get("fresh_wallet_rate")),
        "rug_ratio": _first_number(info.get("rug_ratio")),
        "renounced_mint": info.get("renounced_mint"),
        "smart_money_count": _first_number(tags.get("smart_wallets"), info.get("smart_money_count")),
        "kol_count": _first_number(tags.get("renowned_wallets"), info.get("kol_count")),
        "klines": klines,
        "kline_closes": [
            _number(candle.get("close")) for candle in klines
            if isinstance(candle, dict) and candle.get("close") is not None
        ],
        "source": "GMGN",
    }


async def _fetch_dexscreener(address: str, chain: str) -> Dict[str, Any] | None:
    url = f"https://api.dexscreener.com/latest/dex/tokens/{address}"
    async with httpx.AsyncClient(timeout=20) as client:
        response = await client.get(url)
        response.raise_for_status()
        payload = response.json()
    raw_pairs = payload.get("pairs") or []
    pairs = [pair for pair in raw_pairs if pair.get("chainId") in (chain, "solana" if chain == "sol" else chain)]
    if not pairs:
        pairs = raw_pairs
    if not pairs:
        return None
    pair = max(pairs, key=lambda item: _number((item.get("liquidity") or {}).get("usd")))
    base = pair.get("baseToken") or {}
    return {
        "address": address,
        "chain": chain,
        "symbol": base.get("symbol") or "UNKNOWN",
        "name": base.get("name") or base.get("symbol") or "Unknown",
        "price": _number(pair.get("priceUsd")),
        "market_cap": _first_number(pair.get("marketCap"), pair.get("fdv")),
        "liquidity": _number((pair.get("liquidity") or {}).get("usd")),
        "dev_hold": 0.0,
        "top_10_holders": 0.0,
        "bundler_rate": 0.0,
        "fresh_wallet_rate": 0.0,
        "rug_ratio": 0.0,
        "renounced_mint": None,
        "smart_money_count": 0,
        "kol_count": 0,
        "klines": [],
        "source": "DexScreener",
    }


def _raise_gmgn_http_exception(exc: Exception, context: str = "GMGN") -> None:
    msg = str(exc)
    low = msg.lower()

    if isinstance(exc, TimeoutError) or "timeout" in low:
        raise HTTPException(status_code=504, detail="GMGN API Timeout")

    if isinstance(exc, RateLimitError) or "429" in msg or "rate limit" in low or "too many requests" in low:
        raise HTTPException(status_code=429, detail="Rate limit de GMGN activo. Intenta en 3 mins.")

    raise HTTPException(status_code=500, detail=f"Error en {context}: {msg}")


class RunRequest(BaseModel):
    chain: str = "sol"
    min_marketcap: Optional[float] = None
    max_marketcap: Optional[float] = None
    min_liquidity: Optional[float] = None
    max_top_holder_rate: Optional[float] = None
    max_bundler_rate: Optional[float] = None
    max_fresh_wallet_rate: Optional[float] = None
    limit: int = 80


class RunResponse(BaseModel):
    chain: str
    count: int
    tokens: List[Dict[str, Any]]


def make_app(bridge_init: Optional[Any] = None) -> FastAPI:
    app = FastAPI(title="GMGN Bridge Backend")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Explicitly ensure bridge is not None for the routes
    bridge: GMGNBridge = bridge_init if bridge_init is not None else GMGNBridge()

    @app.get("/health")
    def health() -> Dict[str, str]:
        return {"status": "ok"}

    @app.post("/api/run", response_model=RunResponse)
    def run_backend(payload: RunRequest):
        try:
            filters = {
                "chain": payload.chain,
                "min_mc": payload.min_marketcap if payload.min_marketcap is not None else 50000,
                "max_mc": payload.max_marketcap if payload.max_marketcap is not None else 200000,
                "min_liq": payload.min_liquidity if payload.min_liquidity is not None else 10000,
                "max_top10": payload.max_top_holder_rate if payload.max_top_holder_rate is not None else 0.2,
                "max_bundler": payload.max_bundler_rate if payload.max_bundler_rate is not None else 0.2,
                "max_fresh": payload.max_fresh_wallet_rate if payload.max_fresh_wallet_rate is not None else 0.2,
                "limit": payload.limit,
            }
            tokens = bridge.radar_migrated(**filters)
            return RunResponse(
                chain=payload.chain,
                count=len(tokens),
                tokens=tokens,
            )
        except (RateLimitError, TimeoutError) as e:
            _raise_gmgn_http_exception(e, "radar")
        except Exception as e:
            msg = str(e)
            if "429" in msg or "rate limit" in msg.lower() or "too many requests" in msg.lower():
                raise HTTPException(status_code=429, detail="Rate limit de GMGN activo. Intenta en 3 mins.")
            if "timeout" in msg.lower():
                raise HTTPException(status_code=504, detail="GMGN API Timeout")
            raise HTTPException(status_code=500, detail=f"Error en el radar: {msg}")

    @app.get("/api/dossier")
    async def get_dossier(
        address: str = Query(..., description="Token contract address"),
        chain: str = Query("sol", description="Blockchain network"),
    ) -> Dict[str, Any]:
        address = address.strip() if address else ""
        chain = chain.strip().lower() if chain else ""
        if not address or not re.fullmatch(r"[A-Za-z0-9]{4,64}", address):
            raise HTTPException(status_code=400, detail="Address is required")
        if chain not in {"sol", "solana"}:
            raise HTTPException(status_code=400, detail="Unsupported chain")
        try:
            dossier = await asyncio.to_thread(bridge.get_dossier_data, chain=chain, address=address)
            normalized = _flatten_dossier(dossier, chain, address)
            logger.info("Dossier chain=%s address=%s source=%s price=%s market_cap=%s liquidity=%s klines=%s", chain, address, normalized.get("source"), normalized.get("price"), normalized.get("market_cap"), normalized.get("liquidity"), len(normalized.get("klines") or []))
            if normalized["price"] <= 0:
                fallback = await _fetch_dexscreener(address, chain)
                if fallback:
                    logger.info("DexScreener fallback usado para %s", address)
                    return fallback
            return normalized
        except (RateLimitError, TimeoutError) as e:
            _raise_gmgn_http_exception(e, "dossier")
        except Exception as e:
            msg = str(e)
            if "429" in msg or "rate limit" in msg.lower() or "too many requests" in msg.lower():
                raise HTTPException(status_code=429, detail="Rate limit de GMGN activo. Intenta en 3 mins.")
            if "timeout" in msg.lower():
                raise HTTPException(status_code=504, detail="GMGN API Timeout")
            raise HTTPException(status_code=500, detail=msg)

    return app


app = make_app()
app.mount("/", StaticFiles(directory="static", html=True), name="static")

if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "backend:app",
        host=os.getenv("HOST", "0.0.0.0"),
        port=int(os.getenv("PORT", "8000")),
        reload=False,
    )
