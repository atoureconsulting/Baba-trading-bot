"""Walk-forward backtester (blueprint §9, §12b).

Replays the LIVE signal engine (signals.evaluate — the same code the advisor
runs) over historical MT5 bars, simulates each advice against subsequent
bars, then walk-forward-optimizes the confidence threshold on rolling
train/test windows so reported numbers are out-of-sample.

Honest limitations (printed with every report):
- No historical news calendar exists in our feed, so the news blackout gate
  is OFF in backtest → live results should be slightly BETTER than backtest
  on news-spike losses, slightly worse on missed news moves.
- MT5 candles are bid-based; spread is charged per trade as an R-cost.
- Conservative ambiguity rule: if one bar spans both SL and TP, count SL.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from .risk import SymbolSpec
from .signals import evaluate, inside_bar_signal

WARMUP_BARS = 450          # indicator warmup on trigger TF
H4_WINDOW = 320            # bars of H4 context per evaluation (200EMA + slack)
MAX_HOLD_BARS = 300        # time-stop: force exit after N trigger bars
COLLECT_THRESHOLD = 0.20   # collect low-confidence candidates; thresholds applied later
THRESHOLDS = (0.20, 0.35, 0.50, 0.65, 0.80)


@dataclass
class BTrade:
    time: pd.Timestamp
    symbol: str
    timeframe: str
    direction: int
    entry: float
    sl: float
    tp: float
    confidence: float
    regime: str
    outcome: str = ""      # take_profit / stop_loss / timeout
    r: float = 0.0         # net R multiple after spread cost
    bars_held: int = 0


@dataclass
class Report:
    symbol: str
    timeframe: str
    trades: list[BTrade] = field(default_factory=list)

    def stats(self, trades: list[BTrade] | None = None) -> dict:
        t = self.trades if trades is None else trades
        if not t:
            return {"trades": 0}
        rs = np.array([x.r for x in t])
        wins = rs[rs > 0]
        losses = rs[rs <= 0]
        cum = np.cumsum(rs)
        peak = np.maximum.accumulate(cum)
        return {
            "trades": len(t),
            "win_rate": round(float((rs > 0).mean()), 3),
            "expectancy_R": round(float(rs.mean()), 3),
            "profit_factor": round(float(wins.sum() / -losses.sum()), 2) if losses.sum() < 0 else float("inf"),
            "max_drawdown_R": round(float((peak - cum).max()), 1),
            "total_R": round(float(rs.sum()), 1),
        }

    def by_regime(self) -> dict[str, dict]:
        out = {}
        for regime in sorted({t.regime for t in self.trades}):
            out[regime] = self.stats([t for t in self.trades if t.regime == regime])
        return out


def _simulate(df: pd.DataFrame, start: int, direction: int, entry: float,
              sl: float, tp: float) -> tuple[str, int, float]:
    """Scan bars from `start` until SL/TP/timeout. SL wins ambiguous bars."""
    end = min(start + MAX_HOLD_BARS, len(df))
    for j in range(start, end):
        bar = df.iloc[j]
        hit_sl = bar["low"] <= sl if direction > 0 else bar["high"] >= sl
        hit_tp = bar["high"] >= tp if direction > 0 else bar["low"] <= tp
        if hit_sl:
            return "stop_loss", j, sl
        if hit_tp:
            return "take_profit", j, tp
    j = end - 1
    return "timeout", j, float(df.iloc[j]["close"])


def run_backtest(
    symbol: str,
    timeframe: str,
    df_trig: pd.DataFrame,
    df_4h: pd.DataFrame,
    spec: SymbolSpec,
    spread_price: float,
    progress: bool = True,
) -> Report:
    """Sequential single-position replay of the live signal engine."""
    report = Report(symbol, timeframe)
    h4_index = df_4h.index
    i = WARMUP_BARS
    n = len(df_trig)
    next_pct = 5

    while i < n - 1:
        if progress and (100 * i) // n >= next_pct:
            print(f"  ... {next_pct}% ({len(report.trades)} trades so far)")
            next_pct += 5
        bar_time = df_trig.index[i]
        h4_pos = h4_index.searchsorted(bar_time, side="right")
        if h4_pos < 210:
            i += 1
            continue
        window = df_trig.iloc[i - WARMUP_BARS : i + 1]
        df4 = df_4h.iloc[max(0, h4_pos - H4_WINDOW) : h4_pos]

        advice, _ = evaluate(
            symbol, timeframe, window, df4, spec,
            balance=10_000, risk_pct=0.01,
            now_utc=bar_time.to_pydatetime(),
            confidence_threshold=COLLECT_THRESHOLD,
        )
        if advice is None:
            i += 1
            continue

        sl_dist = abs(advice.entry - advice.risk.stop_loss)
        outcome, exit_i, exit_px = _simulate(
            df_trig, i + 1, advice.direction, advice.entry,
            advice.risk.stop_loss, advice.risk.take_profit,
        )
        gross_r = (exit_px - advice.entry) / sl_dist * advice.direction
        net_r = gross_r - (spread_price / sl_dist)
        report.trades.append(BTrade(
            time=bar_time, symbol=symbol, timeframe=timeframe,
            direction=advice.direction, entry=advice.entry,
            sl=advice.risk.stop_loss, tp=advice.risk.take_profit,
            confidence=advice.confidence, regime=advice.regime,
            outcome=outcome, r=round(float(net_r), 3), bars_held=exit_i - i,
        ))
        i = exit_i + 1  # one position at a time, like the advisor's bloc rule
    return report


def run_inside_bar_backtest(
    symbol: str, df_4h: pd.DataFrame, spec: SymbolSpec, spread_price: float,
    trigger_window: int = 6,
) -> Report:
    """KB#4 4H inside-bar pending-order strategy, reported separately."""
    report = Report(symbol, "H4-insidebar")
    i = 210
    n = len(df_4h)
    while i < n - 2:
        window = df_4h.iloc[: i + 1]
        sig = inside_bar_signal(symbol, window, spec, balance=10_000, risk_pct=0.01)
        if sig is None:
            i += 1
            continue
        # pending stop order: must trigger within `trigger_window` bars
        triggered = None
        for j in range(i + 1, min(i + 1 + trigger_window, n)):
            bar = df_4h.iloc[j]
            if (sig.direction > 0 and bar["high"] >= sig.entry) or \
               (sig.direction < 0 and bar["low"] <= sig.entry):
                triggered = j
                break
        if triggered is None:
            i += 1
            continue
        sl_dist = abs(sig.entry - sig.risk.stop_loss)
        outcome, exit_i, exit_px = _simulate(
            df_4h, triggered, sig.direction, sig.entry,
            sig.risk.stop_loss, sig.risk.take_profit,
        )
        gross_r = (exit_px - sig.entry) / sl_dist * sig.direction
        report.trades.append(BTrade(
            time=df_4h.index[triggered], symbol=symbol, timeframe="H4-insidebar",
            direction=sig.direction, entry=sig.entry, sl=sig.risk.stop_loss,
            tp=sig.risk.take_profit, confidence=sig.confidence,
            regime="inside_bar_breakout", outcome=outcome,
            r=round(float(gross_r - spread_price / sl_dist), 3),
            bars_held=exit_i - triggered,
        ))
        i = exit_i + 1
    return report


def walk_forward_threshold(report: Report, train_months: int = 12,
                           test_months: int = 3, min_train_trades: int = 30) -> dict:
    """Pick the best confidence threshold on each train window (by expectancy),
    apply it to the following unseen test window, aggregate test results."""
    trades = sorted(report.trades, key=lambda t: t.time)
    if len(trades) < min_train_trades * 2:
        return {"error": f"only {len(trades)} trades — not enough for walk-forward "
                         f"(need ≥{min_train_trades * 2}); showing in-sample stats only"}
    times = pd.DatetimeIndex([t.time for t in trades])
    start, end = times[0], times[-1]
    oos: list[BTrade] = []
    chosen: list[tuple[str, float]] = []
    cursor = start
    while cursor + pd.DateOffset(months=train_months + test_months) <= end + pd.DateOffset(days=1):
        train_end = cursor + pd.DateOffset(months=train_months)
        test_end = train_end + pd.DateOffset(months=test_months)
        train = [t for t in trades if cursor <= t.time < train_end]
        test = [t for t in trades if train_end <= t.time < test_end]
        cursor = cursor + pd.DateOffset(months=test_months)
        if len(train) < min_train_trades or not test:
            continue
        best_thr, best_exp = None, -1e9
        for thr in THRESHOLDS:
            sel = [t for t in train if t.confidence >= thr]
            if len(sel) < 10:
                continue
            exp = float(np.mean([t.r for t in sel]))
            if exp > best_exp:
                best_thr, best_exp = thr, exp
        if best_thr is None:
            continue
        chosen.append((str(train_end.date()), best_thr))
        oos.extend(t for t in test if t.confidence >= best_thr)
    if not oos:
        return {"error": "walk-forward produced no out-of-sample trades"}
    return {
        "oos_stats": report.stats(oos),
        "thresholds_chosen": chosen,
        "recommended_threshold": chosen[-1][1] if chosen else None,
    }


def print_report(report: Report, wf: dict | None = None) -> None:
    s = report.stats()
    print(f"\n=== {report.symbol} {report.timeframe} — IN-SAMPLE (all candidates) ===")
    for k, v in s.items():
        print(f"  {k:16} {v}")
    if s.get("trades", 0) == 0:
        return
    print("  by regime:")
    for regime, rs in report.by_regime().items():
        print(f"    {regime:22} trades={rs['trades']:4}  win={rs.get('win_rate', '-')}"
              f"  expectancy={rs.get('expectancy_R', '-')}R")
    if wf:
        print(f"--- WALK-FORWARD (out-of-sample — the number that counts) ---")
        if "error" in wf:
            print(f"  {wf['error']}")
        else:
            for k, v in wf["oos_stats"].items():
                print(f"  {k:16} {v}")
            print(f"  recommended confidence threshold: {wf['recommended_threshold']}")
    print("  NOTE: news gate OFF in backtest (no historical calendar); "
          "ambiguous bars counted as losses; spread charged per trade.")
