"""gmgn_bridge.py — Orquestador del Sistema de Volatilidad sobre gmgn-cli.

Mejoras v2 (tras los banes 429 reales del 29/08):
  1. Leaky bucket con PESOS reales por endpoint (holders=5, trenches=3, info=1...)
     y recarga conservadora (10 peso/s) => no vuelve a reventar el limite.
  2. Cache TTL por endpoint => dossier de N fases no repite llamadas.
  3. Deteccion de 429/RATE_LIMIT_* con espera hasta el reset y 1 reintento.
  4. Pipeline completo: radar_migrated() -> token_dossier() -> build_llm_payload()
     (solo numericos + symbol ASCII saneado; texto on-chain nunca entra al LLM).
  5. CLI Windows-safe (node + dist/index.js) y UTF-8 forzado.

Uso:
  python gmgn_bridge.py radar                     # Fase 1: migrados con filtros
  python gmgn_bridge.py dossier <addr> [chain]    # Fase 1-3 de un token + payload LLM
  python gmgn_bridge.py hot [intervalo]           # hot-searches 5m por defecto
"""

import json
import logging
import os
import pathlib
import re
import shutil
import subprocess
import sys
import threading
import time

import requests

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    encoding="utf-8",
)

GMGN_BAN_WAIT = float(os.environ.get("GMGN_BAN_WAIT", "210"))
_GMGN_PREFIX = None


def _gmgn_prefix():
    global _GMGN_PREFIX
    if _GMGN_PREFIX is not None:
        return _GMGN_PREFIX
    if os.name == "nt":
        npm = pathlib.Path(os.environ.get("APPDATA", "")) / "npm"
        js = npm / "node_modules" / "gmgn-cli" / "dist" / "index.js"
        node = shutil.which("node")
        if node and js.exists():
            _GMGN_PREFIX = [node, str(js)]
            return _GMGN_PREFIX
    found = shutil.which("gmgn-cli")
    _GMGN_PREFIX = [found] if found else ["gmgn-cli"]
    return _GMGN_PREFIX


class RateLimitError(RuntimeError):
    pass


class LeakyBucket:
    """Capacidad 20, recarga 10 peso/s (mitad del limite real => margen)."""

    CAPACITY = 20.0
    REFILL = 10.0

    def __init__(self):
        self.tokens = self.CAPACITY
        self.ts = time.monotonic()
        self.lock = threading.Lock()
        self.last_call = 0.0

    def acquire(self, weight: float, min_gap: float = 0.35):
        while True:
            with self.lock:
                now = time.monotonic()
                self.tokens = min(self.CAPACITY, self.tokens + (now - self.ts) * self.REFILL)
                self.ts = now
                gap_ok = (now - self.last_call) >= min_gap
                if self.tokens >= weight and gap_ok:
                    self.tokens -= weight
                    self.last_call = now
                    return
                need = max(weight - self.tokens, 0.0)
                wait = max(need / self.REFILL, (min_gap - (now - self.last_call)) if not gap_ok else 0.0)
            time.sleep(min(wait + 0.05, 2.0))


WEIGHTS = {
    "info": 1, "security": 1, "pool": 1, "trending": 1, "search": 1,
    "kline": 2, "hot-searches": 3, "trenches": 3, "signal": 3,
    "holders": 5, "traders": 5,
}
CACHE_TTL = {
    "info": 60, "security": 60, "pool": 60,
    "holders": 120, "traders": 120,
    "hot-searches": 45, "trenches": 60, "trending": 30,
    "kline": 60,
}

_429_RE = re.compile(r"429|RATE_LIMIT_(BANNED|EXCEEDED)", re.I)


class GMGNBridge:
    def __init__(self, local_url="http://127.0.0.1:8000"):
        self.local_url = local_url.rstrip("/")
        self.api_endpoint = f"{self.local_url}/api/run"
        self.session = requests.Session()
        self.bucket = LeakyBucket()
        self._cache = {}
        self._retries = 0

    # ------------------------------------------------------------------ CLI
    def cli(self, subcommand: str, args: list, timeout: int = 90):
        """Ejecuta gmgn-cli con throttle+peso, cache TTL y reintento tras ban 429."""
        key = (subcommand, tuple(args))
        ttl = CACHE_TTL.get(subcommand)
        if ttl:
            hit = self._cache.get(key)
            if hit and time.monotonic() - hit[0] < ttl:
                logging.info("cache hit: %s %s", subcommand, " ".join(args[:4]))
                return hit[1]

        cmd = _gmgn_prefix() + list(args) + ["--raw"]
        self.bucket.acquire(WEIGHTS.get(subcommand, 3))

        attempts = 2  # 429 => un cooldown breve y 1 reintento antes de fallar
        last_err = None
        for i in range(attempts):
            try:
                r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                                   errors="replace", timeout=timeout)
            except FileNotFoundError:
                logging.error("gmgn-cli/node no encontrado en PATH.")
                return None
            except subprocess.TimeoutExpired:
                last_err = f"timeout {timeout}s"
                logging.warning("CLI timeout (%s/%s): %s", i + 1, attempts, last_err)
                continue

            out = (r.stdout or "").strip()
            err = (r.stderr or "").strip()
            if r.returncode == 0 and out:
                try:
                    data = json.loads(out)
                except json.JSONDecodeError:
                    last_err = "JSON invalido"
                    logging.error("Salida no-JSON: %s", out[:200])
                    continue
                if ttl:
                    self._cache[key] = (time.monotonic(), data)
                return data
            blob = f"{err} {out}"
            if _429_RE.search(blob):
                reset = GMGN_BAN_WAIT
                m = re.search(r"resets at ([0-9\-: ]+GMT[^\)]*)", blob)
                reset_msg = m.group(1) if m else f"~{reset:.0f}s"
                if i < attempts - 1:
                    cooldown = max(float(reset), 0.1)
                    logging.warning("429 RATE LIMIT. Reintentando tras cooldown de %.1fs", cooldown)
                    time.sleep(cooldown)
                    self.bucket.acquire(WEIGHTS.get(subcommand, 3))
                    continue
                logging.error("429 RATE LIMIT. Sin reintento automatico adicional: %s", reset_msg)
                raise RateLimitError(reset_msg)
            last_err = err or f"exit {r.returncode}"
            logging.error("CLI fallo: %s", last_err[:300])
        logging.error("Agotados reintentos: %s", last_err)
        return None

    # ------------------------------------------------------------- workflow
    def token_info(self, chain, address):
        return self.cli("info", ["token", "info", "--chain", chain, "--address", address])

    def token_security(self, chain, address):
        return self.cli("security", ["token", "security", "--chain", chain, "--address", address])

    def token_holders(self, chain, address, limit=20, tag=None):
        args = ["token", "holders", "--chain", chain, "--address", address, "--limit", str(limit)]
        if tag:
            args += ["--tag", tag]
        return self.cli("holders", args)

    def radar_migrated(self, chain="sol", min_mc=50000, max_mc=200000,
                       min_liq=10000, max_top10=0.2, max_bundler=0.2,
                       max_fresh=0.2, limit=80):
        """Fase 1 del Sistema de Volatilidad: migrados al DEX con filtros duros."""
        args = ["market", "trenches", "--chain", chain, "--type", "completed",
                "--min-marketcap", str(min_mc), "--max-marketcap", str(max_mc),
                "--min-liquidity", str(min_liq),
                "--max-top-holder-rate", str(max_top10),
                "--max-bundler-rate", str(max_bundler),
                "--max-fresh-wallet-rate", str(max_fresh),
                "--limit", str(limit)]
        data = self.cli("trenches", args)
        if not data:
            return []
        inner = data.get("data", data) if isinstance(data, dict) else data
        toks = inner.get("completed", []) if isinstance(inner, dict) else (inner or [])
        # Use a safer filter for rug_ratio to avoid filtering out everything
        return [t for t in toks if _f(t.get("rug_ratio"), 0.0) <= 0.7]

    def token_kline(self, chain, address, resolution="5m"):
        return self.cli("kline", ["market", "kline", "--chain", chain, "--address", address, "--resolution", resolution])

    def get_dossier_data(self, chain: str, address: str) -> dict:
        """Extrae info, security y klines respetando LeakyBucket throttle y cache."""
        info = self.token_info(chain, address) or {}
        security = self.token_security(chain, address) or {}
        kline_res = self.token_kline(chain, address, resolution="5m") or []

        # Parse klines: handle dict with list/data or direct list
        if isinstance(kline_res, dict):
            raw_klines = kline_res.get("list") or kline_res.get("data") or []
        elif isinstance(kline_res, list):
            raw_klines = kline_res
        else:
            raw_klines = []

        sorted_klines = sorted(raw_klines, key=lambda k: _f(k.get("time", 0)))[-10:] if raw_klines else []
        closes = [_f(k.get("close")) for k in sorted_klines]

        price_obj = info.get("price", {}) if isinstance(info.get("price"), dict) else {}
        stat = info.get("stat", {}) if isinstance(info.get("stat"), dict) else {}
        tags = info.get("wallet_tags_stat", {}) if isinstance(info.get("wallet_tags_stat"), dict) else {}
        dev = info.get("dev", {}) if isinstance(info.get("dev"), dict) else {}

        current_price = _f(price_obj.get("price")) or _f(info.get("price")) or (closes[-1] if closes else 0.0)
        supply = _f(info.get("circulating_supply")) or _f(info.get("total_supply"))
        mc = _f(info.get("market_cap_usd")) or _f(info.get("usd_market_cap")) or _f(info.get("market_cap"))
        if not mc and supply and current_price:
            mc = round(current_price * supply, 2)

        liq = _f(info.get("liquidity")) or _f(info.get("liquidity_usd"))
        top10 = _f(security.get("top_10_holder_rate")) or _f(stat.get("top_10_holder_rate")) or _f(info.get("top_10_holder_rate"))
        dev_hold = _f(security.get("dev_team_hold_rate")) or _f(stat.get("dev_team_hold_rate")) or _f(info.get("dev_team_hold_rate"))
        bundler = _f(security.get("bundler_rate")) or _f(stat.get("top_bundler_trader_percentage")) or _f(info.get("bundler_trader_amount_rate")) or _f(info.get("bundler_rate"))
        fresh = _f(security.get("fresh_wallet_rate")) or _f(stat.get("fresh_wallet_rate")) or _f(info.get("fresh_wallet_rate"))
        rug = _f(security.get("rug_ratio")) or _f(info.get("rug_ratio"))

        smart_cnt = _f(tags.get("smart_wallets")) or _f(info.get("smart_degen_count")) or _f(stat.get("smart_degen_count"))
        kol_cnt = _f(tags.get("renowned_wallets")) or _f(info.get("renowned_count")) or _f(stat.get("renowned_count"))

        ren_mint = security.get("renounced_mint")
        if ren_mint is None:
            ren_mint = info.get("renounced_mint")
        ren_mint_bool = ren_mint in (True, 1, "1")

        ren_freeze = security.get("renounced_freeze_account")
        if ren_freeze is None:
            ren_freeze = info.get("renounced_freeze_account")
        ren_freeze_bool = ren_freeze in (True, 1, "1")

        return {
            "address": address,
            "chain": chain,
            "symbol": sanitize_symbol(info.get("symbol") or ""),
            "name": info.get("name") or info.get("symbol") or "Unknown",
            "price": current_price,
            "market_cap": mc,
            "liquidity": liq,
            "top_10_holders": top10,
            "dev_hold": dev_hold,
            "bundler_rate": bundler,
            "fresh_wallet_rate": fresh,
            "smart_money_count": int(smart_cnt),
            "kol_count": int(kol_cnt),
            "rug_ratio": rug,
            "renounced_mint": ren_mint_bool,
            "renounced_freeze": ren_freeze_bool,
            "creator_status": dev.get("creator_token_status") or info.get("creator_token_status") or "",
            "klines": sorted_klines,
            "kline_closes": closes,
        }

    def token_dossier(self, chain, address):
        """Fase 1 de extraccion: info + security + holders, fusionados."""
        info = self.token_info(chain, address) or {}
        security = self.token_security(chain, address) or {}
        holders = self.token_holders(chain, address) or {}
        return {"chain": chain, "address": address, "info": info,
                "security": security, "holders": holders}

    # ------------------------------------------------------- backend local
    def fetch_sol_decisions(self, chain="sol"):
        try:
            r = self.session.post(self.api_endpoint, json={"chain": chain}, timeout=10)
            r.raise_for_status()
            return r.json()
        except requests.exceptions.Timeout:
            logging.error("Timeout al consultar el backend local de GMGN en 10s")
            return None
        except requests.exceptions.RequestException as e:
            if "429" in str(e):
                logging.warning("Rate limit visible al consultar el backend local: %s", e)
            else:
                logging.error("Error de conexion con el backend local: %s", e)
            return None

    def fetch_hot_searches(self, interval="5m", chains=None):
        args = ["market", "hot-searches", "--interval", interval]
        if chains:
            for c in chains:
                args += ["--chain", c]
        return self.cli("hot-searches", args)


# ------------------------------------------------------------ payload LLM
def _f(x, default=0.0):
    try:
        v = float(x)
        return v if v == v else default
    except (TypeError, ValueError):
        return default


def sanitize_symbol(sym):
    m = re.match(r"[A-Za-z0-9]{1,12}", str(sym or ""))
    return m.group(0) if m else "UNKNOWN"


def _row_payload(row: dict, chain: str) -> dict:
    """Payload desde una fila plana de trending/trenches (otra forma que token info)."""
    p = _f(row.get("price"))
    mc = _f(row.get("usd_market_cap")) or _f(row.get("market_cap"))
    hmc = _f(row.get("history_highest_market_cap"))
    b24, s24 = _f(row.get("buys_24h")), _f(row.get("sells_24h"))
    ren = row.get("renounced_mint")
    ren_known = ren is not None and ren != ""
    smart = _f(row.get("smart_degen_count"))
    kol = _f(row.get("renowned_count"))
    rug = _f(row.get("rug_ratio"))
    bundler = _f(row.get("bundler_rate")) or _f(row.get("bundler_trader_amount_rate"))
    fresh = _f(row.get("fresh_wallet_rate"))
    top10 = _f(row.get("top_10_holder_rate"))
    dev_hold = _f(row.get("dev_team_hold_rate"))
    status = row.get("creator_token_status", "")
    hp = str(row.get("is_honeypot", "")).lower() in ("1", "true", "yes")
    payload = {
        "task": "decide entry(yes/no) + TP ladder + SL for a micro-cap memecoin",
        "token": {
            "symbol": sanitize_symbol(row.get("symbol")),
            "chain": chain,
            "address": row.get("address"),
            "price_usd": p,
            "market_cap_usd": mc,
            "liquidity_usd": _f(row.get("liquidity")),
            "ath_drawdown_pct": round((1 - mc / hmc) * 100, 1) if mc and hmc else None,
            "chg_5m_pct": _f(row.get("price_change_percent5m"), None),
            "chg_1h_pct": _f(row.get("price_change_percent1h"), None),
            "volume_24h_usd": _f(row.get("volume_24h")) or _f(row.get("volume")),
            "buy_ratio_24h": round(b24 / (b24 + s24), 3) if b24 + s24 else None,
            "holders": _f(row.get("holder_count")),
            "on_bonding_curve": str(row.get("launchpad_status", "")) == "0",
        },
        "security": {
            "renounced_mint": None if not ren_known else (ren in (1, True, "1")),
            "honeypot": hp,
            "rug_ratio": rug,
            "top10_holder_rate": top10,
            "dev_hold_rate": dev_hold,
            "creator_status": status,
            "bundler_vol_rate": bundler,
            "fresh_wallet_rate": fresh,
        },
        "smart_money": {"smart_wallets": smart, "kol_wallets": kol},
        "risk_flags": [],
    }
    s, t = payload["security"], payload["token"]
    if rug > 0.3:
        payload["risk_flags"].append("rug_ratio_high")
    if s["renounced_mint"] is False:
        payload["risk_flags"].append("mint_not_renounced")
    if hp:
        payload["risk_flags"].append("HONEYPOT")
    if status == "creator_close":
        payload["risk_flags"].append("dev_exited")
    if t["on_bonding_curve"]:
        payload["risk_flags"].append("pre_graduation")
    if bundler > 0.3:
        payload["risk_flags"].append("bot_heavy")
    if (t["chg_1h_pct"] or 0) < 0 and (t["chg_5m_pct"] or 0) < 0:
        payload["risk_flags"].append("fading")
    if t["liquidity_usd"] and t["liquidity_usd"] < 15000:
        payload["risk_flags"].append("thin_liquidity")
    if smart >= 3:
        payload["risk_flags"].append("smart_money_present")
    return payload


def build_llm_payload(dossier: dict) -> dict:
    """Payload 100% numerico + symbol saneado. El LLM decide entrada/TP/SL."""
    info = dossier.get("info", {})
    sec = dossier.get("security", {})
    # Fila plana (trenches/trending) sin security => mapear por la otra ruta
    if isinstance(info, dict) and isinstance(sec, dict) and not sec and \
            ("usd_market_cap" in info or "smart_degen_count" in info or
             "price_change_percent1h" in info):
        return _row_payload(info, dossier.get("chain", "sol"))

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

    payload = {
        "task": "decide entry(yes/no) + TP ladder + SL for a micro-cap memecoin",
        "token": {
            "symbol": sanitize_symbol(info.get("symbol")),
            "chain": dossier.get("chain"),
            "address": dossier.get("address"),
            "price_usd": p,
            "market_cap_usd": round(p * supply, 2) if supply else 0,
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
            "renounced_mint": sec.get("renounced_mint") in (True, 1, "1"),
            "renounced_freeze": sec.get("renounced_freeze_account") in (True, 1, "1"),
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
        "risk_flags": [],
    }
    s = payload["security"]
    t = payload["token"]
    if s["rug_ratio"] > 0.3:
        payload["risk_flags"].append("rug_ratio_high")
    if not s["renounced_mint"]:
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
    return payload


if __name__ == "__main__":
    bridge = GMGNBridge()
    cmd = sys.argv[1] if len(sys.argv) > 1 else "radar"

    if cmd == "radar":
        cands = bridge.radar_migrated()
        cands.sort(key=lambda t: (-_f(t.get("smart_degen_count")), _f(t.get("rug_ratio"))))
        logging.info("Radar: %s candidatos tras filtro rug_ratio<=0.3", len(cands))
        for i, tk in enumerate(cands[:10], 1):
            sym = sanitize_symbol(tk.get("symbol"))
            print(f"{i}. {sym} mc=${_f(tk.get('usd_market_cap') or tk.get('market_cap')):,.0f} "
                  f"sm={tk.get('smart_degen_count', 0)} rug={_f(tk.get('rug_ratio')):.2f} "
                  f"{tk.get('address')}")
        if cands:
            print(json.dumps(build_llm_payload(
                {"chain": "sol", "address": cands[0].get("address"),
                 "info": cands[0], "security": {}, "holders": {}}),
                ensure_ascii=False, indent=2))

    elif cmd == "dossier" and len(sys.argv) > 2:
        addr = sys.argv[2]
        chain = sys.argv[3] if len(sys.argv) > 3 else "sol"
        d = bridge.token_dossier(chain, addr)
        payload = build_llm_payload(d)
        print(json.dumps(payload, ensure_ascii=False, indent=2))

    elif cmd == "hot":
        interval = sys.argv[2] if len(sys.argv) > 2 else "5m"
        print(json.dumps(bridge.fetch_hot_searches(interval), ensure_ascii=False))

    else:
        print("Uso: gmgn_bridge.py radar | dossier <addr> [chain] | hot [interval]")
