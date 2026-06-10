"""Indicator suite (blueprint §2). All functions take/return pandas objects.

Wilder smoothing is used for RSI/ATR/ADX to match MT5's built-in values, so
the advisor's numbers agree with what the user sees on their charts.
"""

from __future__ import annotations

import numpy as np
import pandas as pd


def ema(series: pd.Series, period: int) -> pd.Series:
    return series.ewm(span=period, adjust=False).mean()


def sma(series: pd.Series, period: int) -> pd.Series:
    return series.rolling(period).mean()


def _wilder(series: pd.Series, period: int) -> pd.Series:
    return series.ewm(alpha=1.0 / period, adjust=False).mean()


def rsi(close: pd.Series, period: int = 14) -> pd.Series:
    delta = close.diff()
    gain = _wilder(delta.clip(lower=0.0), period)
    loss = _wilder((-delta).clip(lower=0.0), period)
    rs = gain / loss.replace(0.0, np.nan)
    return (100 - 100 / (1 + rs)).fillna(50.0)


def true_range(df: pd.DataFrame) -> pd.Series:
    prev_close = df["close"].shift(1)
    return pd.concat(
        [
            df["high"] - df["low"],
            (df["high"] - prev_close).abs(),
            (df["low"] - prev_close).abs(),
        ],
        axis=1,
    ).max(axis=1)


def atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    return _wilder(true_range(df), period)


def adx(df: pd.DataFrame, period: int = 14) -> pd.Series:
    up = df["high"].diff()
    down = -df["low"].diff()
    plus_dm = pd.Series(np.where((up > down) & (up > 0), up, 0.0), index=df.index)
    minus_dm = pd.Series(np.where((down > up) & (down > 0), down, 0.0), index=df.index)
    tr = _wilder(true_range(df), period)
    plus_di = 100 * _wilder(plus_dm, period) / tr
    minus_di = 100 * _wilder(minus_dm, period) / tr
    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0.0, np.nan)
    return _wilder(dx.fillna(0.0), period)


def macd(close: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9):
    line = ema(close, fast) - ema(close, slow)
    sig = ema(line, signal)
    return line, sig, line - sig


def bollinger(close: pd.Series, period: int = 20, dev: float = 2.0):
    mid = sma(close, period)
    sd = close.rolling(period).std(ddof=0)
    return mid + dev * sd, mid, mid - dev * sd


def ema_cross(close: pd.Series, fast: int = 9, slow: int = 21) -> int:
    """+1 if fast crossed above slow on the last closed bar, -1 if below, 0 none."""
    f, s = ema(close, fast), ema(close, slow)
    now, prev = f.iloc[-1] - s.iloc[-1], f.iloc[-2] - s.iloc[-2]
    if prev <= 0 < now:
        return 1
    if prev >= 0 > now:
        return -1
    return 0


def rsi_divergence(close: pd.Series, rsi_series: pd.Series, lookback: int = 5) -> int:
    """Blueprint §2 divergence probe on the last `lookback` closed bars.

    +1 bullish (price lower low, RSI higher low), -1 bearish, 0 none.
    """
    price, osc = close.iloc[-lookback:], rsi_series.iloc[-lookback:]
    half = lookback // 2
    if price.iloc[-1] <= price.iloc[:half].min() and osc.iloc[-1] > osc.iloc[:half].min():
        return 1
    if price.iloc[-1] >= price.iloc[:half].max() and osc.iloc[-1] < osc.iloc[:half].max():
        return -1
    return 0


def inside_bar(df: pd.DataFrame) -> bool:
    """Last closed bar fully inside the prior (mother) bar's range (KB#4)."""
    mother, last = df.iloc[-2], df.iloc[-1]
    return last["high"] < mother["high"] and last["low"] > mother["low"]


def breakout_strength(df: pd.DataFrame, avg_window: int = 20) -> float:
    """Candle range vs average range (KB#1 order-flow imitation)."""
    rng = df["high"] - df["low"]
    avg = rng.iloc[-avg_window - 1 : -1].mean()
    return float(rng.iloc[-1] / avg) if avg > 0 else 0.0


def swing_low(df: pd.DataFrame, lookback: int = 7) -> float:
    return float(df["low"].iloc[-lookback:].min())


def swing_high(df: pd.DataFrame, lookback: int = 7) -> float:
    return float(df["high"].iloc[-lookback:].max())
