"""Signal engine — blueprint §10 master decision tree.

Inputs: multi-timeframe candles (trigger TF + 4H bias TF), symbol spec,
account state. Output: SignalAdvice or None, with human-readable reasons
for every gate passed/failed (the user must be able to audit each call).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone

import pandas as pd

from . import indicators as ind
from .levels import ProfileLevels, prior_day_profile
from .regime import Regime, detect_regime, session_allows
from .risk import RiskAdvice, SymbolSpec, build_advice

CONFIDENCE_THRESHOLD = 0.5  # §12b lever: raise to trade accuracy for frequency

# §2 voting weights
W_MACD, W_RSI, W_VOLUME, W_DIVERGENCE = 0.25, 0.20, 0.10, 0.15


@dataclass
class SignalAdvice:
    symbol: str
    timeframe: str
    direction: int                  # +1 long, -1 short
    action: str                     # "ENTER"
    entry: float
    risk: RiskAdvice
    confidence: float
    regime: str
    reasons: list[str] = field(default_factory=list)
    time: str = ""

    def describe(self) -> str:
        side = "BUY (long)" if self.direction > 0 else "SELL (short)"
        lines = [
            f"{self.symbol} {self.timeframe} — {self.action} {side}",
            f"  entry  ~{self.entry}",
            f"  stop   {self.risk.stop_loss}",
            f"  target {self.risk.take_profit}  (R:R 1:{self.risk.rr})",
            f"  size   {self.risk.lots} lots  (risking {self.risk.risk_money})",
            f"  confidence {self.confidence:.2f} | regime {self.regime}",
            "  why: " + "; ".join(self.reasons),
        ]
        return "\n".join(lines)


def _htf_bias(df_4h: pd.DataFrame) -> tuple[int, str]:
    close = df_4h["close"]
    ema200 = ind.ema(close, 200).iloc[-1]
    price = close.iloc[-1]
    if price > ema200:
        return 1, f"4H close {price:.5f} above 200EMA {ema200:.5f}"
    if price < ema200:
        return -1, f"4H close {price:.5f} below 200EMA {ema200:.5f}"
    return 0, "4H at 200EMA — neutral"


def _trigger(regime: Regime, bias: int, df: pd.DataFrame) -> tuple[int, list[str]]:
    """Regime-specific LTF trigger (§6). Returns (direction, reasons)."""
    close = df["close"]
    rsi_now = float(ind.rsi(close).iloc[-1])
    reasons: list[str] = []

    if regime == Regime.TRENDING:
        cross = ind.ema_cross(close)
        if bias > 0 and cross > 0 and rsi_now > 40:
            reasons.append(f"9/21 EMA bull cross, RSI {rsi_now:.0f} > 40")
            return 1, reasons
        if bias < 0 and cross < 0 and rsi_now < 60:
            reasons.append(f"9/21 EMA bear cross, RSI {rsi_now:.0f} < 60")
            return -1, reasons

    elif regime == Regime.RANGING:
        upper, _, lower = ind.bollinger(close)
        price = close.iloc[-1]
        if bias > 0 and price < lower.iloc[-1] and rsi_now < 30:
            reasons.append(f"price below lower BB, RSI {rsi_now:.0f} < 30")
            return 1, reasons
        if bias < 0 and price > upper.iloc[-1] and rsi_now > 70:
            reasons.append(f"price above upper BB, RSI {rsi_now:.0f} > 70")
            return -1, reasons

    elif regime == Regime.HIGH_VOL:
        strength = ind.breakout_strength(df)
        if strength >= 1.5:
            vol_rising = df["tick_volume"].iloc[-1] > df["tick_volume"].iloc[-21:-1].mean()
            last = df.iloc[-1]
            prev_high = float(df["high"].iloc[-21:-1].max())
            prev_low = float(df["low"].iloc[-21:-1].min())
            if last["close"] > prev_high and vol_rising and bias >= 0:
                reasons.append(f"breakout above 20-bar high, strength {strength:.1f}, volume rising")
                return 1, reasons
            if last["close"] < prev_low and vol_rising and bias <= 0:
                reasons.append(f"breakdown below 20-bar low, strength {strength:.1f}, volume rising")
                return -1, reasons
    return 0, reasons


def _confluence(df: pd.DataFrame, direction: int, reasons: list[str]) -> float:
    """Weighted vote (§2): MACD momentum, RSI placement, volume, divergence."""
    close = df["close"]
    score = 0.0

    line, sig, hist = ind.macd(close)
    macd_dir = 1 if hist.iloc[-1] > 0 else -1
    if macd_dir == direction:
        score += W_MACD
        reasons.append("MACD histogram agrees")

    rsi_series = ind.rsi(close)
    rsi_now = float(rsi_series.iloc[-1])
    if (direction > 0 and 40 <= rsi_now <= 70) or (direction < 0 and 30 <= rsi_now <= 60):
        score += W_RSI
        reasons.append(f"RSI {rsi_now:.0f} in healthy zone")

    if df["tick_volume"].iloc[-1] > df["tick_volume"].iloc[-21:-1].mean():
        score += W_VOLUME
        reasons.append("tick volume above 20-bar average")

    div = ind.rsi_divergence(close, rsi_series)
    if div == direction:
        score += W_DIVERGENCE
        reasons.append("RSI divergence supports entry")
    elif div == -direction:
        return -1.0  # opposing divergence vetoes (§6)
    return score


def evaluate(
    symbol: str,
    timeframe: str,
    df_trigger: pd.DataFrame,
    df_4h: pd.DataFrame,
    spec: SymbolSpec,
    balance: float,
    risk_pct: float = 0.01,
    news_blackout_reason: str | None = None,
    profile: ProfileLevels | None = None,
    now_utc: datetime | None = None,
    spread_points: int | None = None,
) -> tuple[SignalAdvice | None, str]:
    """Run the full §10 tree. Returns (advice_or_none, status_line)."""
    now_utc = now_utc or datetime.now(timezone.utc)

    # 1. event / regime / session gates
    if news_blackout_reason:
        return None, f"{symbol} {timeframe}: NO TRADE — news blackout ({news_blackout_reason})"
    regime = detect_regime(df_trigger, news_blackout=False)
    if regime in (Regime.EVENT, Regime.TRANSITIONAL):
        return None, f"{symbol} {timeframe}: NO TRADE — regime {regime.value}"
    if not session_allows(symbol, regime, now_utc):
        return None, f"{symbol} {timeframe}: NO TRADE — session disallows {regime.value} strategies"
    if spread_points is not None and spec.spread_points_limit and spread_points > spec.spread_points_limit:
        return None, f"{symbol} {timeframe}: NO TRADE — spread {spread_points} > limit (KB#5 pre-trade check)"

    # 2. higher-timeframe bias
    bias, bias_reason = _htf_bias(df_4h)
    if bias == 0:
        return None, f"{symbol} {timeframe}: NO TRADE — neutral 4H bias"

    # 3. regime-specific trigger (+ inside-bar handled by advisor on 4H bars)
    direction, reasons = _trigger(regime, bias, df_trigger)
    if direction == 0:
        return None, f"{symbol} {timeframe}: no trigger ({regime.value}, bias {'long' if bias>0 else 'short'})"
    reasons.insert(0, bias_reason)

    # 4. confluence score + vetoes
    score = _confluence(df_trigger, direction, reasons)
    if score < 0:
        return None, f"{symbol} {timeframe}: VETO — opposing divergence"
    if score < CONFIDENCE_THRESHOLD:
        return None, f"{symbol} {timeframe}: confluence {score:.2f} below threshold {CONFIDENCE_THRESHOLD}"

    entry = float(df_trigger["close"].iloc[-1])
    if profile is not None:
        near = profile.near(entry, tolerance=1.5 * float(ind.atr(df_trigger).iloc[-1]))
        if near:
            reasons.append(f"near prior-day {near} (market profile level)")
            score = min(score + 0.05, 1.0)

    # 5. risk model
    atr_now = float(ind.atr(df_trigger).iloc[-1])
    atr50 = float(ind.atr(df_trigger, 50).iloc[-1])
    vol_ratio = atr_now / atr50 if atr50 > 0 else 1.0
    structure = ind.swing_low(df_trigger) if direction > 0 else ind.swing_high(df_trigger)
    risk = build_advice(spec, balance, risk_pct, direction, entry, atr_now,
                        structure, regime.value, vol_ratio)
    if risk.lots <= 0:
        return None, f"{symbol} {timeframe}: NO TRADE — stop too wide for risk budget"

    advice = SignalAdvice(
        symbol=symbol, timeframe=timeframe, direction=direction, action="ENTER",
        entry=entry, risk=risk, confidence=round(score, 2), regime=regime.value,
        reasons=reasons, time=now_utc.strftime("%Y-%m-%d %H:%M UTC"),
    )
    return advice, f"{symbol} {timeframe}: SIGNAL {advice.confidence}"


def inside_bar_signal(
    symbol: str,
    df_4h: pd.DataFrame,
    spec: SymbolSpec,
    balance: float,
    risk_pct: float = 0.01,
) -> SignalAdvice | None:
    """KB#4 4H inside-bar breakout: pending-order style advice.

    Entry ±10% of mother-bar range beyond its extreme, SL 0.4x range,
    TP 0.8x range (1:2). Direction follows the 200EMA bias.
    """
    if len(df_4h) < 210 or not ind.inside_bar(df_4h):
        return None
    bias, bias_reason = _htf_bias(df_4h)
    if bias == 0:
        return None
    mother = df_4h.iloc[-2]
    rng = float(mother["high"] - mother["low"])
    if rng <= 0:
        return None
    if bias > 0:
        entry, sl, tp = mother["high"] + 0.1 * rng, None, None
        sl = entry - 0.4 * rng
        tp = entry + 0.8 * rng
    else:
        entry = mother["low"] - 0.1 * rng
        sl = entry + 0.4 * rng
        tp = entry - 0.8 * rng
    from .risk import position_size
    lots = position_size(spec, balance, risk_pct, entry, sl)
    if lots <= 0:
        return None
    risk = RiskAdvice(stop_loss=round(sl, 6), take_profit=round(tp, 6),
                      lots=lots, risk_money=round(balance * risk_pct, 2), rr=2.0)
    return SignalAdvice(
        symbol=symbol, timeframe="H4", direction=bias, action="ENTER",
        entry=round(float(entry), 6), risk=risk, confidence=0.55,
        regime="inside_bar_breakout",
        reasons=[bias_reason, "4H inside bar — place stop order 10% of range beyond mother bar (KB#4)"],
        time=datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
    )
