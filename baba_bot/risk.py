"""Risk model and position sizing (blueprint §7, KB#5 edge cases)."""

from __future__ import annotations

import math
from dataclasses import dataclass


@dataclass(frozen=True)
class SymbolSpec:
    """Contract specs pulled from MT5 symbol_info (or config in dry-run)."""
    name: str
    point: float            # smallest price increment of quotes
    tick_size: float        # trade tick size
    tick_value: float       # account-currency value of one tick per 1.0 lot
    volume_min: float
    volume_max: float
    volume_step: float
    spread_points_limit: int = 0  # 0 = no limit configured


@dataclass(frozen=True)
class RiskAdvice:
    stop_loss: float
    take_profit: float
    lots: float
    risk_money: float
    rr: float


def position_size(
    spec: SymbolSpec,
    balance: float,
    risk_pct: float,
    entry: float,
    stop_loss: float,
    commission_per_lot: float = 0.0,
) -> float:
    """KB#5 formula: lots = risk / (sl_ticks * tick_value + round-trip commission).

    Always rounds DOWN to volume_step and clamps to broker min/max.
    """
    if spec.tick_size <= 0 or spec.tick_value <= 0:
        return 0.0
    sl_distance = abs(entry - stop_loss)
    if sl_distance <= 0:
        return 0.0
    risk_money = balance * risk_pct
    loss_per_lot = (sl_distance / spec.tick_size) * spec.tick_value + 2 * commission_per_lot
    lots = risk_money / loss_per_lot
    # epsilon guards float error (0.2/0.01 -> 19.999...); never round UP past risk
    lots = math.floor(lots / spec.volume_step + 1e-9) * spec.volume_step
    lots = round(lots, 8)
    return max(min(lots, spec.volume_max), 0.0) if lots >= spec.volume_min else 0.0


def build_advice(
    spec: SymbolSpec,
    balance: float,
    risk_pct: float,
    direction: int,             # +1 long / -1 short
    entry: float,
    atr_value: float,
    structure_stop: float | None,
    regime: str,
    vol_ratio: float = 1.0,
    commission_per_lot: float = 0.0,
) -> RiskAdvice:
    """ATR or structure stop, regime-specific TP (blueprint §6/§7)."""
    atr_stop_dist = 1.5 * atr_value * (1.3 if vol_ratio > 1.5 else 1.0)
    if regime == "trending" and structure_stop is not None:
        # structure stop beyond swing, with KB#7 anti-stop-hunt buffer
        buffer = 6 * 10 * spec.point  # ~6 pips
        sl = structure_stop - buffer if direction > 0 else structure_stop + buffer
        # never tighter than half the ATR stop, never wider than 2x
        dist = abs(entry - sl)
        dist = min(max(dist, atr_stop_dist * 0.5), atr_stop_dist * 2.0)
        sl = entry - dist if direction > 0 else entry + dist
    else:
        sl = entry - atr_stop_dist if direction > 0 else entry + atr_stop_dist

    sl_dist = abs(entry - sl)
    rr = {"trending": 2.0, "high_vol": 1.5, "ranging": 1.3}.get(regime, 1.5)
    tp = entry + rr * sl_dist if direction > 0 else entry - rr * sl_dist

    lots = position_size(spec, balance, risk_pct, entry, sl, commission_per_lot)
    return RiskAdvice(
        stop_loss=round(sl, 6),
        take_profit=round(tp, 6),
        lots=lots,
        risk_money=round(balance * risk_pct, 2),
        rr=rr,
    )


class CircuitBreaker:
    """Daily -3% halt and 15% peak-drawdown shutdown with hysteresis (§7, KB#3)."""

    def __init__(self, daily_loss_pct: float = 0.03, max_dd_pct: float = 0.15,
                 recovery_pct: float = 0.05):
        self.daily_loss_pct = daily_loss_pct
        self.max_dd_pct = max_dd_pct
        self.recovery_pct = recovery_pct
        self.peak_equity: float | None = None
        self.day_start_equity: float | None = None
        self.throttled = False

    def update(self, equity: float, new_day: bool) -> str | None:
        """Return a halt reason if signals must be suppressed."""
        if new_day or self.day_start_equity is None:
            self.day_start_equity = equity
        self.peak_equity = max(self.peak_equity or equity, equity)

        if equity <= self.day_start_equity * (1 - self.daily_loss_pct):
            return f"daily loss limit hit ({self.daily_loss_pct:.0%}) — stand down until next session"

        dd = 1 - equity / self.peak_equity
        if dd > self.max_dd_pct:
            self.throttled = True
        elif self.throttled and dd < self.max_dd_pct - self.recovery_pct:
            self.throttled = False  # hysteresis: resume only after real recovery
        if self.throttled:
            return f"drawdown {dd:.1%} exceeds {self.max_dd_pct:.0%} cap — trading suspended"
        return None
