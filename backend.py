import os
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from gmgn_bridge import GMGNBridge, RateLimitError


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
    def get_dossier(
        address: str = Query(..., description="Token contract address"),
        chain: str = Query("sol", description="Blockchain network"),
    ) -> Dict[str, Any]:
        if not address or not address.strip():
            raise HTTPException(status_code=400, detail="Address is required")
        try:
            dossier = bridge.get_dossier_data(chain=chain.strip(), address=address.strip())
            return dossier
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
