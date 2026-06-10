"""Bot battle (KB#14): every auditable strategy from the KB#2–#9 repo pool,
faithfully reimplemented from their audited source and run head-to-head on
the SAME data, costs, and simulation rules.

Excluded with reasons (printed in the report):
- Trading_Pal (KB#9): contains no strategy logic to test.
- EarnForex PositionSizer/MarketProfile (KB#5/#6): tools, not strategies.
- ForexSmartBot ML strategies (KB#3): data-leaky targets — nothing real to test.
- geraked grid overlay (KB#7): martingale, banned by blueprint §7. Pure
  strategies are tested, which their own READMEs admit were unprofitable —
  we verify that claim on our data.

Two simulation styles, matching how each bot actually trades:
- ALWAYS-IN (FXBot, KB#8 ML, KB#4 MA-cross): position ±1 every bar, flip on
  signal; per-unit position change costs half the spread.
- SL/TP EVENT (ForexSmartBot technicals, geraked pure entries): enter on
  closed-bar signal, walk forward to SL/TP; SL wins ambiguous bars; spread
  charged per trade.

Author-published default parameters are used (NOT tuned on this data), so
the comparison is out-of-the-box honest for every contender.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from . import indicators as ind
from .backtest import _simulate

MAX_HOLD = 300


# --------------------------------------------------------------------------
# metrics
# --------------------------------------------------------------------------

@dataclass
class BattleResult:
    name: str
    source: str
    style: str
    trades: int
    win_rate: float
    expectancy_pips: float
    profit_factor: float
    total_pips: float
    max_dd_pips: float
    note: str = ""
    pnl_by_year: dict | None = None


def _metrics(name: str, source: str, style: str,
             pnl: list[tuple[pd.Timestamp, float]], note: str = "") -> BattleResult:
    if not pnl:
        return BattleResult(name, source, style, 0, 0.0, 0.0, 0.0, 0.0, 0.0, note or "no trades")
    arr = np.array([p for _, p in pnl])
    wins, losses = arr[arr > 0], arr[arr <= 0]
    cum = np.cumsum(arr)
    dd = float((np.maximum.accumulate(cum) - cum).max())
    pf = float(wins.sum() / -losses.sum()) if losses.sum() < 0 else float("inf")
    by_year: dict[int, float] = {}
    for ts, p in pnl:
        by_year[ts.year] = by_year.get(ts.year, 0.0) + p
    return BattleResult(name, source, style, len(arr),
                        round(float((arr > 0).mean()), 3),
                        round(float(arr.mean()), 2), round(pf, 2),
                        round(float(arr.sum()), 1), round(dd, 1), note,
                        {y: round(v, 0) for y, v in sorted(by_year.items())})


# --------------------------------------------------------------------------
# ALWAYS-IN runner: position series -> per-episode pips
# --------------------------------------------------------------------------

def _run_always_in(df: pd.DataFrame, pos: pd.Series, spread_price: float,
                   pip_size: float) -> list[tuple[pd.Timestamp, float]]:
    pos = pos.fillna(0.0)
    held = pos.shift(1).fillna(0.0)                    # act on next bar (no lookahead)
    bar_pnl = df["close"].diff().fillna(0.0) * held
    cost = pos.diff().abs().fillna(0.0) * (spread_price / 2.0)
    net = (bar_pnl - cost) / pip_size
    episode_id = (held != held.shift(1)).cumsum()
    pnl = [(g.index[0], float(g.sum())) for eid, g in net.groupby(episode_id)
           if held[g.index[0]] != 0]
    return pnl


# --------------------------------------------------------------------------
# SL/TP EVENT runner
# --------------------------------------------------------------------------

def _run_events(df: pd.DataFrame, entries: list[tuple[int, int, float, float]],
                spread_price: float, pip_size: float) -> list[tuple[pd.Timestamp, float]]:
    """entries: list of (bar_index, direction, sl, tp); sequential, one at a time."""
    pnl, busy_until = [], -1
    for i, direction, sl, tp in entries:
        if i <= busy_until or i + 1 >= len(df):
            continue
        entry = float(df["close"].iloc[i])
        if (direction > 0 and sl >= entry) or (direction < 0 and sl <= entry):
            continue
        _, exit_i, exit_px = _simulate(df, i + 1, direction, entry, sl, tp)
        pnl.append((df.index[i], ((exit_px - entry) * direction - spread_price) / pip_size))
        busy_until = exit_i
    return pnl


# --------------------------------------------------------------------------
# strategies — ALWAYS-IN family
# --------------------------------------------------------------------------

def fxbot_sma(df):       # FXBot main.py example params (9/20)
    return np.sign(ind.sma(df["close"], 9) - ind.sma(df["close"], 20))


def fxbot_bollinger(df):  # FXBot BollingerBandsBacktest (20,2), flat on mid-cross
    upper, mid, lower = ind.bollinger(df["close"], 20, 2.0)
    dist = df["close"] - mid
    pos = pd.Series(np.nan, index=df.index)
    pos[df["close"] < lower] = 1.0
    pos[df["close"] > upper] = -1.0
    pos[dist * dist.shift(1) < 0] = 0.0
    return pos.ffill().fillna(0.0)


def fxbot_momentum(df):   # window=3 (their example)
    return np.sign(df["close"].pct_change().rolling(3).mean())


def fxbot_contrarian(df):
    return -fxbot_momentum(df)


def kb4_ma_cross(df):     # KB#4 best-of-630 combo (32/64) — least-bad in their sweep
    return np.sign(ind.sma(df["close"], 32) - ind.sma(df["close"], 64))


def kb8_logistic(df):     # KB#8/FXBot ml_classification family: logistic on 5 lag returns
    rets = df["close"].pct_change().fillna(0.0).to_numpy()
    lags = 5
    X = np.column_stack([np.roll(rets, k) for k in range(1, lags + 1)])[lags:-1]
    y = (rets[lags + 1:] > 0).astype(float)
    split = int(len(X) * 0.7)
    Xtr = np.c_[np.ones(split), X[:split]]
    w = np.zeros(lags + 1)
    for _ in range(300):
        p = 1.0 / (1.0 + np.exp(-Xtr @ w))
        w -= 0.1 * Xtr.T @ (p - y[:split]) / split
    p_all = 1.0 / (1.0 + np.exp(-np.c_[np.ones(len(X)), X] @ w))
    pos = pd.Series(0.0, index=df.index)
    pos.iloc[lags : lags + len(X)] = np.where(p_all > 0.5, 1.0, -1.0)
    pos.iloc[: lags + split] = 0.0   # trade only the out-of-sample 30%
    return pos


# --------------------------------------------------------------------------
# strategies — SL/TP EVENT family
# --------------------------------------------------------------------------

def fsb_sma_atr(df):      # ForexSmartBot sma_crossover.py: 20/50 cross, 2/3 ATR
    f, s = ind.sma(df["close"], 20), ind.sma(df["close"], 50)
    atr = ind.atr(df, 14)
    cross = np.sign(f - s)
    out = []
    for i in range(60, len(df) - 1):
        if cross.iloc[i - 1] <= 0 < cross.iloc[i]:
            c, a = df["close"].iloc[i], atr.iloc[i]
            out.append((i, 1, c - 2 * a, c + 3 * a))
        elif cross.iloc[i - 1] >= 0 > cross.iloc[i]:
            c, a = df["close"].iloc[i], atr.iloc[i]
            out.append((i, -1, c + 2 * a, c - 3 * a))
    return out


def fsb_rsi_reversion(df):  # ForexSmartBot rsi_reversion.py (RSI30/70 + SMA50 trend)
    rsi = ind.rsi(df["close"], 14)
    sma50 = ind.sma(df["close"], 50)
    atr = ind.atr(df, 14)
    out = []
    for i in range(60, len(df) - 1):
        c, a = df["close"].iloc[i], atr.iloc[i]
        if rsi.iloc[i - 1] < 30 <= rsi.iloc[i] and c > sma50.iloc[i]:
            out.append((i, 1, c - 1.5 * a, c + 2.25 * a))
        elif rsi.iloc[i - 1] > 70 >= rsi.iloc[i] and c < sma50.iloc[i]:
            out.append((i, -1, c + 1.5 * a, c - 2.25 * a))
    return out


def fsb_breakout_atr(df):  # ForexSmartBot breakout_atr.py: Donchian-20 + ATR
    atr = ind.atr(df, 14)
    hi = df["high"].rolling(20).max()
    lo = df["low"].rolling(20).min()
    out = []
    for i in range(60, len(df) - 1):
        c, a = df["close"].iloc[i], atr.iloc[i]
        mid = (hi.iloc[i - 1] + lo.iloc[i - 1]) / 2
        if c > hi.iloc[i - 1]:
            out.append((i, 1, mid, c + 2 * a))
        elif c < lo.iloc[i - 1]:
            out.append((i, -1, mid, c - 2 * a))
    return out


def geraked_bbrsi(df, point):  # geraked BBRSI.mq5 PURE (no grid): BB(500,2)+RSI(7)
    upper, mid, lower = ind.bollinger(df["close"], 500, 2.0)
    rsi = ind.rsi(df["close"], 7)
    out = []
    for i in range(510, len(df) - 1):
        c1, c2 = df["close"].iloc[i], df["close"].iloc[i - 1]
        if (rsi.iloc[i - 1] < 30 and c2 < lower.iloc[i - 1]
                and 30 < rsi.iloc[i] < 50 and c1 > lower.iloc[i] and c1 < mid.iloc[i]):
            sl = lower.iloc[i] - 0.9 * (mid.iloc[i] - lower.iloc[i])
            out.append((i, 1, sl, c1 + (c1 - sl)))
        elif (rsi.iloc[i - 1] > 70 and c2 > upper.iloc[i - 1]
                and 50 < rsi.iloc[i] < 70 and c1 < upper.iloc[i] and c1 > mid.iloc[i]):
            sl = upper.iloc[i] + 0.9 * (upper.iloc[i] - mid.iloc[i])
            out.append((i, -1, sl, c1 - (sl - c1)))
    return out


def geraked_2macdsto(df, point):  # geraked 2MACDSTO.mq5 PURE: MACD13/21+34/144, Stoch(7,3,3)
    m1, _, _ = ind.macd(df["close"], 13, 21, 9)
    m2, _, _ = ind.macd(df["close"], 34, 144, 9)
    low_min = df["low"].rolling(7).min()
    high_max = df["high"].rolling(7).max()
    k = 100 * (df["close"] - low_min) / (high_max - low_min).replace(0, np.nan)
    k = k.rolling(3).mean()
    d = k.rolling(3).mean()
    buffer = 60 * point
    out = []
    for i in range(160, len(df) - 1):
        c = df["close"].iloc[i]
        if (m2.iloc[i - 1] > 0 and m1.iloc[i - 1] < 0 and k.iloc[i - 1] < 20
                and k.iloc[i - 1] <= d.iloc[i - 1] and k.iloc[i] > d.iloc[i]):
            sl = df["low"].iloc[max(0, i - 7):i].min() - buffer
            out.append((i, 1, sl, c + (c - sl)))
        elif (m2.iloc[i - 1] < 0 and m1.iloc[i - 1] > 0 and k.iloc[i - 1] > 80
                and k.iloc[i - 1] >= d.iloc[i - 1] and k.iloc[i] < d.iloc[i]):
            sl = df["high"].iloc[max(0, i - 7):i].max() + buffer
            out.append((i, -1, sl, c - (sl - c)))
    return out


# --------------------------------------------------------------------------
# battle orchestration
# --------------------------------------------------------------------------

def run_battle(symbol: str, df: pd.DataFrame, point: float,
               spread_price: float) -> list[BattleResult]:
    pip = 10 * point
    results = [
        _metrics("SMA cross 9/20", "FXBot (KB#2)", "always-in",
                 _run_always_in(df, fxbot_sma(df), spread_price, pip)),
        _metrics("Bollinger 20,2 flat@mid", "FXBot (KB#2)", "always-in",
                 _run_always_in(df, fxbot_bollinger(df), spread_price, pip)),
        _metrics("Momentum w3", "FXBot (KB#2)", "always-in",
                 _run_always_in(df, fxbot_momentum(df), spread_price, pip)),
        _metrics("Contrarian w3", "FXBot (KB#2)", "always-in",
                 _run_always_in(df, fxbot_contrarian(df), spread_price, pip)),
        _metrics("MA cross 32/64", "Py-FxTB (KB#4)", "always-in",
                 _run_always_in(df, kb4_ma_cross(df), spread_price, pip),
                 note="best of their 630-combo sweep"),
        _metrics("Logistic 5-lag", "Trading-Bot (KB#8) / FXBot ML", "always-in",
                 _run_always_in(df, kb8_logistic(df), spread_price, pip),
                 note="OOS 30% only"),
        _metrics("SMA 20/50 + ATR stops", "ForexSmartBot (KB#3)", "sl/tp",
                 _run_events(df, fsb_sma_atr(df), spread_price, pip)),
        _metrics("RSI-30/70 reversion", "ForexSmartBot (KB#3)", "sl/tp",
                 _run_events(df, fsb_rsi_reversion(df), spread_price, pip)),
        _metrics("Donchian-20 breakout", "ForexSmartBot (KB#3)", "sl/tp",
                 _run_events(df, fsb_breakout_atr(df), spread_price, pip)),
        _metrics("BBRSI pure (no grid)", "geraked (KB#7)", "sl/tp",
                 _run_events(df, geraked_bbrsi(df, point), spread_price, pip),
                 note="grid stripped per blueprint"),
        _metrics("2MACDSTO pure (no grid)", "geraked (KB#7)", "sl/tp",
                 _run_events(df, geraked_2macdsto(df, point), spread_price, pip),
                 note="grid stripped per blueprint"),
    ]
    return results


def print_battle(symbol: str, results: list[BattleResult]) -> None:
    print(f"\n=== BOT BATTLE — {symbol} (sorted by expectancy/trade) ===")
    header = (f"{'strategy':26} {'from':28} {'trades':>6} {'win%':>6} "
              f"{'exp(pips)':>9} {'PF':>6} {'total':>9} {'maxDD':>8}  note")
    print(header)
    print("-" * len(header))
    ranked = sorted(results, key=lambda r: r.expectancy_pips, reverse=True)
    for r in ranked:
        print(f"{r.name:26} {r.source:28} {r.trades:>6} {r.win_rate:>6.1%} "
              f"{r.expectancy_pips:>9.2f} {r.profit_factor:>6.2f} "
              f"{r.total_pips:>9.1f} {r.max_dd_pips:>8.1f}  {r.note}")
    print("\nyear-by-year (positive strategies with >100 trades — consistency check):")
    for r in ranked:
        if r.expectancy_pips > 0 and r.trades > 100 and r.pnl_by_year:
            years = "  ".join(f"{y}:{v:+.0f}" for y, v in r.pnl_by_year.items())
            pos_years = sum(1 for v in r.pnl_by_year.values() if v > 0)
            print(f"  {r.name} [{pos_years}/{len(r.pnl_by_year)} years positive]: {years}")
    print("excluded: Trading_Pal (no strategy), EarnForex tools (not bots), "
          "ForexSmartBot ML (data leakage), geraked grid overlay (martingale).")
