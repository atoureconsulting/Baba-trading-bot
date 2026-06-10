"""Market regime filter (blueprint §3) and session gating (§4)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time, timezone
from enum import Enum

import pandas as pd

from . import indicators as ind


class Regime(Enum):
    TRENDING = "trending"
    RANGING = "ranging"
    HIGH_VOL = "high_vol"
    TRANSITIONAL = "transitional"
    EVENT = "event"


def detect_regime(df: pd.DataFrame, news_blackout: bool) -> Regime:
    if news_blackout:
        return Regime.EVENT
    adx_now = float(ind.adx(df).iloc[-1])
    if adx_now > 25:
        return Regime.TRENDING
    atr14 = float(ind.atr(df, 14).iloc[-1])
    atr50 = float(ind.atr(df, 50).iloc[-1])
    if atr50 > 0 and atr14 > 1.5 * atr50:
        return Regime.HIGH_VOL
    if adx_now < 20:
        return Regime.RANGING
    return Regime.TRANSITIONAL


@dataclass(frozen=True)
class Session:
    name: str
    start: time          # GMT
    end: time            # GMT
    allowed: frozenset   # regimes allowed to fire in this session


# Blueprint §4 session table. USD/JPY trend exception handled in allowed_now().
SESSIONS = [
    Session("asia", time(0, 0), time(8, 0), frozenset({Regime.RANGING})),
    Session("london_open", time(8, 0), time(12, 0), frozenset({Regime.TRENDING, Regime.HIGH_VOL})),
    Session("london_ny", time(12, 0), time(16, 0), frozenset({Regime.TRENDING, Regime.RANGING, Regime.HIGH_VOL})),
    Session("ny", time(16, 0), time(20, 0), frozenset({Regime.TRENDING, Regime.RANGING, Regime.HIGH_VOL})),
    Session("ny_close", time(20, 0), time(23, 59, 59), frozenset({Regime.TRENDING})),
]


def current_session(now_utc: datetime | None = None) -> Session:
    now_utc = now_utc or datetime.now(timezone.utc)
    t = now_utc.time()
    for s in SESSIONS:
        if s.start <= t < s.end:
            return s
    return SESSIONS[0]


def session_allows(symbol: str, regime: Regime, now_utc: datetime | None = None) -> bool:
    s = current_session(now_utc)
    if regime in s.allowed:
        return True
    # Blueprint §4: USD/JPY may trend-trade during Asia.
    return s.name == "asia" and symbol.upper().startswith("USDJPY") and regime == Regime.TRENDING
