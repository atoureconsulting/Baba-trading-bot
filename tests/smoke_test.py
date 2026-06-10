"""End-to-end pipeline smoke test on synthetic data (no MT5 needed).

Run:  python tests/smoke_test.py
"""

from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from baba_bot import indicators as ind
from baba_bot.levels import market_profile, prior_day_profile
from baba_bot.regime import Regime, detect_regime
from baba_bot.risk import CircuitBreaker, SymbolSpec, build_advice, position_size
from baba_bot.signals import evaluate, inside_bar_signal


def synth(n=600, trend=0.0, vol=0.0008, seed=7, freq="15min", start_price=1.10):
    rng = np.random.default_rng(seed)
    rets = rng.normal(trend, vol, n)
    close = start_price * np.exp(np.cumsum(rets))
    high = close * (1 + np.abs(rng.normal(0, vol, n)))
    low = close * (1 - np.abs(rng.normal(0, vol, n)))
    open_ = np.r_[close[0], close[:-1]]
    idx = pd.date_range("2026-06-01", periods=n, freq=freq, tz="UTC")
    return pd.DataFrame(
        {"open": open_, "high": np.maximum.reduce([open_, close, high]),
         "low": np.minimum.reduce([open_, close, low]), "close": close,
         "tick_volume": rng.integers(80, 300, n)},
        index=idx,
    )


SPEC = SymbolSpec("EURUSD", point=0.00001, tick_size=0.00001, tick_value=1.0,
                  volume_min=0.01, volume_max=100.0, volume_step=0.01)


def check(name, cond):
    print(f"  {'OK ' if cond else 'FAIL'} {name}")
    assert cond, name


print("indicators:")
df = synth()
check("RSI in [0,100]", ind.rsi(df["close"]).iloc[-1] in pd.Interval(0, 100, closed="both")
      or 0 <= ind.rsi(df["close"]).iloc[-1] <= 100)
check("ATR positive", ind.atr(df).iloc[-1] > 0)
check("ADX in [0,100]", 0 <= ind.adx(df).iloc[-1] <= 100)

print("regime:")
trending = synth(trend=0.0006, vol=0.0004, seed=3)
check("strong drift detected as trending/high_vol",
      detect_regime(trending, False) in (Regime.TRENDING, Regime.HIGH_VOL))
check("news blackout -> EVENT", detect_regime(df, True) is Regime.EVENT)

print("market profile:")
prof = market_profile(df.iloc[-96:], step=0.0005)
check("VAL <= POC <= VAH", prof.val <= prof.poc <= prof.vah)
check("prior-day profile computes", prior_day_profile(df, 0.0005) is not None)

print("position sizing (KB#5 rules):")
lots = position_size(SPEC, balance=10_000, risk_pct=0.01, entry=1.1000, stop_loss=1.0950)
# risk 100 USD, SL 50 pips = 500 points -> per-lot loss 500 USD -> 0.2 lots
check("formula matches hand calc (0.2 lots)", abs(lots - 0.20) < 1e-9)
check("rounds DOWN to step", position_size(SPEC, 10_000, 0.01, 1.1000, 1.09543) <= 0.22)
check("zero when below min lot", position_size(SPEC, 100, 0.001, 1.1000, 1.0900) == 0.0)

print("risk advice:")
adv = build_advice(SPEC, 10_000, 0.01, +1, 1.1000, atr_value=0.0012,
                   structure_stop=1.0980, regime="trending")
check("SL below entry for long", adv.stop_loss < 1.1000)
check("TP gives 2R in trend", abs((adv.take_profit - 1.1000) / (1.1000 - adv.stop_loss) - 2.0) < 0.01)

print("circuit breaker:")
cb = CircuitBreaker()
check("normal equity passes", cb.update(10_000, True) is None)
check("-4% day halts", cb.update(9_600, False) is not None)
cb2 = CircuitBreaker()
cb2.update(10_000, True)
check("-20% drawdown suspends", cb2.update(8_000, True) is not None)
check("hysteresis: -12% still suspended", cb2.update(8_800, True) is not None)
check("recovers above threshold", cb2.update(9_200, True) is None)

print("signal engine:")
df4h = synth(n=400, trend=0.0008, vol=0.001, seed=11, freq="4h")
advice, status = evaluate("EURUSD", "M15", trending, df4h, SPEC, 10_000,
                          now_utc=datetime(2026, 6, 10, 13, 0, tzinfo=timezone.utc))
print(f"    -> {status}")
check("evaluate returns clean status", "EURUSD" in status)
blocked, status2 = evaluate("EURUSD", "M15", trending, df4h, SPEC, 10_000,
                            news_blackout_reason="US CPI",
                            now_utc=datetime(2026, 6, 10, 13, 0, tzinfo=timezone.utc))
check("news blackout blocks", blocked is None and "blackout" in status2)

print("inside bar:")
ib_df = df4h.copy()
mother_high, mother_low = ib_df.iloc[-2]["high"], ib_df.iloc[-2]["low"]
ib_df.iloc[-1, ib_df.columns.get_loc("high")] = mother_high - (mother_high - mother_low) * 0.2
ib_df.iloc[-1, ib_df.columns.get_loc("low")] = mother_low + (mother_high - mother_low) * 0.2
sig = inside_bar_signal("EURUSD", ib_df, SPEC, 10_000)
check("inside-bar signal emitted with 1:2 RR", sig is not None and sig.risk.rr == 2.0)
if sig:
    print()
    print(sig.describe())

print("news feed parsing (offline):")
from baba_bot.newsfeed import _RISK_WORDS, classify_impact
check("NFP title classified", classify_impact("Non-Farm Employment Change", "High") == "NFP")
check("FOMC title classified", classify_impact("FOMC Statement & Federal Funds Rate", "High") == "FOMC")
check("CPI title classified", classify_impact("CPI y/y", "High") == "CPI")
check("generic high-impact gates", classify_impact("Retail Sales m/m", "High") == "DEFAULT")
check("low impact ignored", classify_impact("Tertiary Industry Activity", "Low") is None)
check("risk keywords flag", bool(_RISK_WORDS.search("Surprise hike: BoJ emergency intervention")))
check("calm headline not flagged", not _RISK_WORDS.search("EUR/USD steadies ahead of session open"))

print("calendar merge (manual + auto):")
from datetime import datetime as _dt, timezone as _tz
from baba_bot.news import NewsCalendar
cal = NewsCalendar(path="/tmp/_none.json", auto_path="/tmp/_none_auto.json")
cal.auto_events = [{"time": "2026-06-12 12:30", "name": "US CPI", "impact": "CPI", "currencies": ["USD"]}]
inside = _dt(2026, 6, 12, 12, 15, tzinfo=_tz.utc)
outside = _dt(2026, 6, 12, 14, 0, tzinfo=_tz.utc)
check("auto event blocks XAUUSD inside window", cal.in_blackout("XAUUSD", inside) is not None)
check("GOLD symbol name also gated (Admirals etc.)", cal.in_blackout("GOLD", inside) is not None)
check("GOLDm suffix also gated", cal.in_blackout("GOLDm", inside) is not None)
check("auto event blocks EURUSD (USD leg)", cal.in_blackout("EURUSD", inside) is not None)
check("clear outside window", cal.in_blackout("XAUUSD", outside) is None)
check("GBPJPY unaffected by USD event", cal.in_blackout("GBPJPY", inside) is None)

print("\nALL CHECKS PASSED")
