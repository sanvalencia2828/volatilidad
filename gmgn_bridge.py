"""gmgn_bridge.py — Orquestador del Sistema de Volatilidad.

Usa gmgn_client compartido (aitrader + sistema_volatilidad comparten throttler,
cache, 429-handler). Aqui solo vive la logica de workflow: radar -> dossier ->
payload deterministico (sin LLM).

Uso:
  python gmgn_bridge.py radar
  python gmgn_bridge.py dossier <addr> [chain]
  python gmgn_bridge.py hot [intervalo]
"""

import json
import logging
import os
import re
import subprocess
import sys
import time

import requests

# gmgn_client vive en aitrader/ (proyecto hermano en ~/gmgn-demos/aitrader)
_AITRADER_DIR = os.path.join(os.path.expanduser("~"), "gmgn-demos", "aitrader")
if os.path.isdir(_AITRADER_DIR) and _AITRADER_DIR not in sys.path:
    sys.path.insert(0, _AITRADER_DIR)

from gmgn_client import gmgn, RateLimitError

GMGN_BAN_WAIT = 180

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    encoding="utf-8",
)


class VolatilityOrchestrator:
    def __init__(self, local_url="http://127.0.0.1:8000"):
        self.local_url = local_url.rstrip("/")
        self.api_endpoint = f"{self.local_url}/api/run"
        self.session = requests.Session()

    # ----------------------------------------------------------- extraccion
    def token_info(self, chain, address):
        return gmgn.cli("info", ["token", "info", "--chain", chain, "--address", address])

    def token_security(self, chain, address):
        return gmgn.cli("security", ["token", "security", "--chain", chain, "--address", address])

    def token_holders(self, chain, address, limit=20, tag=None):
        args = ["token", "holders", "--chain", chain, "--address", address, "--limit", str(limit)]
        if tag:
            args += ["--tag", tag]
        return gmgn.cli("holders", args)

    def token_traders(self, chain, address, limit=20):
        args = ["token", "traders", "--chain", chain, "--address", address, "--limit", str(limit)]
        return gmgn.cli("traders", args)

    def token_kline(self, chain, address, resolution="5m", limit=60):
        now = int(__import__("time").time())
        frm = now - limit * 60
        return gmgn.cli("kline", ["market", "kline", "--chain", chain, "--address", address,
                                  "--resolution", resolution, "--from", str(frm), "--to", str(now)])

    def market_signals(self, chain, signal_type=None):
        args = ["market", "signal", "--chain", chain]
        if signal_type is not None:
            args += ["--signal-type", str(signal_type)]
        return gmgn.cli("signal", args)

    # ----------------------------------------------------------- workflow
    def radar_migrated(self, chain="sol", min_mc=50000, max_mc=200000,
                       min_liq=10000, max_top10=0.2, max_bundler=0.2,
                       max_fresh=0.2, limit=80):
        """Fase 1: tokens migrados al DEX con filtros duros."""
        args = ["market", "trenches", "--chain", chain, "--type", "completed",
                "--min-marketcap", str(min_mc), "--max-marketcap", str(max_mc),
                "--min-liquidity", str(min_liq),
                "--max-top-holder-rate", str(max_top10),
                "--max-bundler-rate", str(max_bundler),
                "--max-fresh-wallet-rate", str(max_fresh),
                "--limit", str(limit)]
        data = gmgn.cli("trenches", args)
        if not data:
            return []
        inner = data.get("data", data) if isinstance(data, dict) else data
        toks = inner.get("completed", []) if isinstance(inner, dict) else (inner or [])
        return [t for t in toks if _f(t.get("rug_ratio"), 0.0) <= 0.7]

    def radar_new_creations(self, chain="sol", platform="Pump.fun", limit=80):
        """Fase 1 alternativa: tokens recien creados en un launchpad."""
        args = ["market", "trenches", "--chain", chain, "--type", "new_creation",
                "--launchpad-platform", platform, "--limit", str(limit)]
        data = gmgn.cli("trenches", args)
        if not data:
            return []
        inner = data.get("data", data) if isinstance(data, dict) else data
        return inner.get("new_creation", []) if isinstance(inner, dict) else (inner or [])

    def token_dossier(self, chain, address):
        """Extraccion completa: info + security + holders + traders + kline + signals."""
        info = self.token_info(chain, address) or {}
        security = self.token_security(chain, address) or {}
        holders = self.token_holders(chain, address) or {}
        kline = self.token_kline(chain, address, resolution="5m", limit=60) or []
        if isinstance(kline, dict):
            kline = kline.get("list") or kline.get("data") or []
        return {"chain": chain, "address": address, "info": info,
                "security": security, "holders": holders, "kline": kline}

    # ------------------------------------------------------- backend local
    def fetch_sol_decisions(self, chain="sol"):
        try:
            r = self.session.post(self.api_endpoint, json={"chain": chain}, timeout=10)
            r.raise_for_status()
            return r.json()
        except requests.exceptions.Timeout:
            logging.error("Timeout consultando backend local")
            return None
        except requests.exceptions.RequestException as e:
            logging.error("Error backend local: %s", e)
            return None

    def fetch_hot_searches(self, interval="5m", chains=None):
        args = ["market", "hot-searches", "--interval", interval]
        if chains:
            for c in chains:
                args += ["--chain", c]
        return gmgn.cli("hot-searches", args)


# ------------------------------------------------------------ payload
def _f(x, default=0.0):
    try:
        v = float(x)
        return v if v == v else default
    except (TypeError, ValueError):
        return default


def sanitize_symbol(sym):
    m = re.match(r"[A-Za-z0-9]{1,12}", str(sym or ""))
    return m.group(0) if m else "UNKNOWN"


def _kline_volatility(klines):
    """Desviacion estandar de returns (%) desde velas de cierre."""
    if not klines or len(klines) < 2:
        return 0.0
    closes = [_f(k.get("close")) for k in klines]
    returns = [(closes[i] / closes[i - 1] - 1) * 100 for i in range(1, len(closes)) if closes[i - 1] > 0]
    if not returns:
        return 0.0
    mean = sum(returns) / len(returns)
    var = sum((r - mean) ** 2 for r in returns) / len(returns)
    return round(var ** 0.5, 3)


def _kline_pattern(klines):
    """Clasifica patron basico de precio."""
    if not klines or len(klines) < 3:
        return "insufficient"
    closes = [_f(k.get("close")) for k in klines]
    first, last = closes[0], closes[-1]
    if first <= 0:
        return "insufficient"
    chg = (last / first - 1) * 100
    highs = [_f(k.get("high")) for k in klines]
    lows = [_f(k.get("low")) for k in klines]
    mx, mn = max(highs), min(lows)
    rng = (mx / mn - 1) * 100 if mn > 0 else 0
    if chg > 20 and rng < chg * 2:
        return "uptrend"
    if chg < -20:
        return "breakdown"
    if rng < 10:
        return "basing"
    if chg < -5 and rng > 30:
        return "distribution"
    return "chop"


def _holder_pnl_ratio(holders_raw):
    """Ratio de holders con P&L positivo (todos, no solo smart)."""
    items = holders_raw.get("list") or holders_raw.get("holders") or []
    if not items:
        return 0.0, 0.0
    pnls = [_f(h.get("profit")) for h in items]
    if not pnls:
        return 0.0, 0.0
    pos = sum(1 for p in pnls if p > 0)
    return round(pos / len(pnls), 3), round(sum(pnls) / len(pnls), 2)


def build_payload(dossier: dict) -> dict:
    """Payload 100% numerico + symbol saneado. Motor deterministico decide entrada/TP/SL."""
    info = dossier.get("info", {})
    sec = dossier.get("security", {})
    price = info.get("price", {}) if isinstance(info.get("price"), dict) else {}
    stat = info.get("stat", {}) if isinstance(info.get("stat"), dict) else {}
    dev = info.get("dev", {}) if isinstance(info.get("dev"), dict) else {}
    tags = info.get("wallet_tags_stat", {}) if isinstance(info.get("wallet_tags_stat"), dict) else {}

    p = _f(price.get("price"))
    supply = _f(info.get("circulating_supply")) or _f(info.get("total_supply"))
    ath = _f(info.get("ath_price"))
    p1h, p24h = _f(price.get("price_1h")), _f(price.get("price_24h"))
    buys1h, sells1h = _f(price.get("buys_1h")), _f(price.get("sells_1h"))
    buys24, sells24 = _f(price.get("buys_24h")), _f(price.get("sells_24h"))
    mc = _f(info.get("market_cap")) or (round(p * supply, 2) if supply else 0)

    klines = dossier.get("kline") or []
    holder_pnl_ratio, holder_avg_pnl = _holder_pnl_ratio(sec if sec.get("list") else {})

    ren_mint = sec.get("renounced_mint") if sec.get("renounced_mint") is not None else info.get("renounced_mint")
    ren_freeze = sec.get("renounced_freeze_account") if sec.get("renounced_freeze_account") is not None else info.get("renounced_freeze_account")

    payload = {
        "task": "deterministic entry(yes/no) + TP ladder + SL for a micro-cap memecoin",
        "token": {
            "symbol": sanitize_symbol(info.get("symbol")),
            "chain": dossier.get("chain"),
            "address": dossier.get("address"),
            "price_usd": p,
            "market_cap_usd": mc,
            "liquidity_usd": _f(info.get("liquidity")),
            "ath_price_usd": ath,
            "ath_drawdown_pct": round((1 - p / ath) * 100, 1) if ath and p else None,
            "chg_1h_pct": round((p / p1h - 1) * 100, 2) if p1h else None,
            "chg_24h_pct": round((p / p24h - 1) * 100, 2) if p24h else None,
            "volume_1h_usd": _f(price.get("volume_1h")),
            "volume_24h_usd": _f(price.get("volume_24h")),
            "buy_ratio_1h": round(buys1h / (buys1h + sells1h), 3) if buys1h + sells1h else None,
            "buy_ratio_24h": round(buys24 / (buys24 + sells24), 3) if buys24 + sells24 else None,
            "holders": _f(info.get("holder_count")),
            "on_bonding_curve": _f(info.get("launchpad_status")) == 0,
            "launchpad_progress": round(_f(info.get("launchpad_progress")), 3),
        },
        "security": {
            "renounced_mint": ren_mint in (True, 1, "1") if ren_mint is not None else None,
            "renounced_freeze": ren_freeze in (True, 1, "1") if ren_freeze is not None else None,
            "owner_renounced": str(sec.get("owner_renounced", "")).lower(),
            "open_source": str(sec.get("open_source", "")).lower(),
            "rug_ratio": _f(sec.get("rug_ratio")),
            "top10_holder_rate": _f(sec.get("top_10_holder_rate")) or _f(stat.get("top_10_holder_rate")),
            "dev_hold_rate": _f(sec.get("dev_team_hold_rate")) or _f(stat.get("dev_team_hold_rate")),
            "creator_status": dev.get("creator_token_status", ""),
            "buy_tax": _f(sec.get("buy_tax")),
            "sell_tax": _f(sec.get("sell_tax")),
            "sniper_count": _f(sec.get("sniper_count")),
            "bot_degen_rate": _f(stat.get("bot_degen_rate")),
            "bundler_vol_rate": _f(stat.get("top_bundler_trader_percentage")),
            "fresh_wallet_rate": _f(stat.get("fresh_wallet_rate")),
        },
        "smart_money": {
            "smart_wallets": _f(tags.get("smart_wallets")),
            "kol_wallets": _f(tags.get("renowned_wallets")),
            "whale_wallets": _f(tags.get("whale_wallets")),
        },
        "on_chain_signals": {
            "kline_volatility": _kline_volatility(klines),
            "kline_pattern": _kline_pattern(klines),
            "holder_pnl_ratio": holder_pnl_ratio,
            "holder_avg_pnl_usd": holder_avg_pnl,
        },
        "risk_flags": [],
    }

    s, t, oc = payload["security"], payload["token"], payload["on_chain_signals"]
    if s["rug_ratio"] > 0.3:
        payload["risk_flags"].append("rug_ratio_high")
    if s["renounced_mint"] is False:
        payload["risk_flags"].append("mint_not_renounced")
    if s["creator_status"] == "creator_close":
        payload["risk_flags"].append("dev_exited")
    if t["on_bonding_curve"]:
        payload["risk_flags"].append("pre_graduation")
    if s["bot_degen_rate"] > 0.3 or s["bundler_vol_rate"] > 0.3:
        payload["risk_flags"].append("bot_heavy")
    if (t["chg_1h_pct"] or 0) < 0 and (t["chg_24h_pct"] or 0) < 0:
        payload["risk_flags"].append("fading")
    if t["liquidity_usd"] and t["liquidity_usd"] < 15000:
        payload["risk_flags"].append("thin_liquidity")
    if payload["smart_money"]["smart_wallets"] >= 3:
        payload["risk_flags"].append("smart_money_present")
    if oc["kline_pattern"] == "uptrend":
        payload["risk_flags"].append("uptrend_confirmed")
    if oc["kline_pattern"] in ("breakdown", "distribution"):
        payload["risk_flags"].append("bearish_pattern")
    if oc["holder_pnl_ratio"] < 0.3:
        payload["risk_flags"].append("holders_in_loss")
    return payload


class GMGNClient(VolatilityOrchestrator):
    """Adaptador de compatibilidad para la API usada por el backend antiguo."""

    def cli(self, operation, args):
        command = ["gmgn-cli", *args]
        for attempt in range(2):
            completed = subprocess.run(command, capture_output=True, text=True)
            if completed.returncode == 0:
                return json.loads(completed.stdout) if completed.stdout else {}
            if "429" not in completed.stderr and "RATE_LIMIT" not in completed.stderr.upper():
                raise RuntimeError(completed.stderr.strip() or "gmgn-cli fallo")
            if attempt == 0:
                time.sleep(GMGN_BAN_WAIT)
        raise RateLimitError("GMGN rate limit after retry")

    def get_dossier_data(self, chain, address):
        return self.token_dossier(chain, address)


GMGNBridge = GMGNClient


if __name__ == "__main__":
    orch = VolatilityOrchestrator()
    cmd = sys.argv[1] if len(sys.argv) > 1 else "radar"

    if cmd == "radar":
        cands = orch.radar_migrated()
        cands.sort(key=lambda t: (-_f(t.get("smart_degen_count")), _f(t.get("rug_ratio"))))
        logging.info("Radar: %s candidatos", len(cands))
        for i, tk in enumerate(cands[:10], 1):
            sym = sanitize_symbol(tk.get("symbol"))
            print(f"{i}. {sym} mc=${_f(tk.get('usd_market_cap') or tk.get('market_cap')):,.0f} "
                  f"sm={tk.get('smart_degen_count', 0)} rug={_f(tk.get('rug_ratio')):.2f} "
                  f"{tk.get('address')}")
        if cands:
            print(json.dumps(build_payload(
                {"chain": "sol", "address": cands[0].get("address"),
                 "info": cands[0], "security": {}, "holders": {}, "kline": []}),
                ensure_ascii=False, indent=2))

    elif cmd == "dossier" and len(sys.argv) > 2:
        addr = sys.argv[2]
        chain = sys.argv[3] if len(sys.argv) > 3 else "sol"
        d = orch.token_dossier(chain, addr)
        print(json.dumps(build_payload(d), ensure_ascii=False, indent=2))

    elif cmd == "hot":
        interval = sys.argv[2] if len(sys.argv) > 2 else "5m"
        print(json.dumps(orch.fetch_hot_searches(interval), ensure_ascii=False))

    else:
        print("Uso: gmgn_bridge.py radar | dossier <addr> [chain] | hot [interval]")
