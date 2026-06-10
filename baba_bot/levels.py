"""Market Profile level engine (blueprint §2/§13-KB#6).

Algorithm ported from EarnForex MarketProfile (Apache-2.0, earnforex.com):
TPO counts per price step over a session; POC = max-count price with
closest-to-midpoint tiebreak; Value Area expands from POC, taking the larger
neighboring side each step, until `value_area_pct` of TPOs are enclosed.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd


@dataclass(frozen=True)
class ProfileLevels:
    poc: float
    vah: float
    val: float

    def near(self, price: float, tolerance: float) -> str | None:
        """Which level (if any) `price` is within `tolerance` of."""
        for name, level in (("POC", self.poc), ("VAH", self.vah), ("VAL", self.val)):
            if abs(price - level) <= tolerance:
                return name
        return None


def market_profile(df: pd.DataFrame, step: float, value_area_pct: float = 0.70) -> ProfileLevels:
    """Compute POC/VAH/VAL over the bars in `df` at price granularity `step`."""
    lo = float(np.floor(df["low"].min() / step) * step)
    hi = float(np.ceil(df["high"].max() / step) * step)
    prices = np.arange(lo, hi + step / 2, step)
    # TPO count: number of bars whose range includes each price level
    lows = df["low"].to_numpy()[:, None]
    highs = df["high"].to_numpy()[:, None]
    counts = ((prices >= lows) & (prices <= highs)).sum(axis=0)

    total = int(counts.sum())
    if total == 0:
        mid = (lo + hi) / 2
        return ProfileLevels(mid, hi, lo)

    midpoint = (lo + hi) / 2
    max_count = counts.max()
    candidates = np.flatnonzero(counts == max_count)
    poc_idx = candidates[np.argmin(np.abs(prices[candidates] - midpoint))]

    target = total * value_area_pct
    enclosed = int(counts[poc_idx])
    up, down = poc_idx + 1, poc_idx - 1
    while enclosed < target and (up < len(prices) or down >= 0):
        above = counts[up] if up < len(prices) else -1
        below = counts[down] if down >= 0 else -1
        if above >= below:
            enclosed += int(above)
            up += 1
        else:
            enclosed += int(below)
            down -= 1
    return ProfileLevels(
        poc=float(prices[poc_idx]),
        vah=float(prices[min(up - 1, len(prices) - 1)]),
        val=float(prices[max(down + 1, 0)]),
    )


def prior_day_profile(df: pd.DataFrame, step: float) -> ProfileLevels | None:
    """Profile of the most recent completed UTC day in `df` (intraday bars)."""
    days = df.index.normalize().unique()
    if len(days) < 2:
        return None
    prior = days[-2]
    day_df = df[df.index.normalize() == prior]
    if len(day_df) < 4:
        return None
    return market_profile(day_df, step)
