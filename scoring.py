"""Fallback minimo para ejecutar el backtester en este checkout.

En produccion este modulo debe sustituirse por el ScoringEngine del bot.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class TokenFeatures:
    token_address: str
    price: float
    high: float
    low: float
    volume: float
    momentum: float
    timestamp: int


class ScoringEngine:
    def fit(self, _features: list[TokenFeatures]) -> None:
        return None

    def score(self, features: TokenFeatures) -> str:
        return "ENTER" if features.momentum > 0 else "HOLD"
