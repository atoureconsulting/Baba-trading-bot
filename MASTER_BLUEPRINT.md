# Baba Trading Bot — Master Blueprint

**Version:** 1.0
**Scope:** Hybrid trading signal bot for Forex majors (EUR/USD, GBP/USD, USD/JPY, USD/CHF, AUD/USD, USD/CAD, NZD/USD) and Gold (XAU/USD)
**Goal:** Maximize signal accuracy by fusing technical analysis, market sentiment, macroeconomic filters, and validated open-source bot logic.

> This document is the single source of truth. Every new piece of information
> is appended to the Knowledge Base log and reflected in the sections below.
> Copy this file at any time to get the full current system design.

---

## 1. Data Sources Required

| Category | Source | Status |
|---|---|---|
| Price/OHLCV multi-TF (15M, 1H, 4H, D) with **bid AND ask** | TBD (broker API / MT5 / OANDA / Dukascopy) | ❌ not chosen |
| Tick volume | Same feed (caveat: forex tick volume ≠ real volume; confirmation-only) | ⏳ |
| Macro calendar (NFP, FOMC, CPI, ECB/BOJ) | ForexFactory / DailyFX calendar (scrape or JSON) | ⏳ candidate chosen |
| Retail positioning | IG Client Sentiment or OANDA Order Book | ⏳ candidate chosen |
| COT report (weekly) | CFTC (free, published Fridays, data as of Tuesday) | ⏳ |
| Gold macro drivers | DXY price feed, 10Y TIPS real yield (FRED), CME FedWatch | ⏳ |
| Risk sentiment proxy | S&P 500 futures (ES) | ⏳ |

**Microstructure rules (from KB#1):**
- Use **mid price** = (bid+ask)/2 for all indicator computation — never raw close of one side.
- Signal price ≠ execution price: model slippage explicitly (see §6, §8).

## 2. Indicator Suite & Weights

Composite score in [-1, +1] via weighted voting. Initial weights below reflect the
evidence ranking in KB#1; **all weights are priors to be recalibrated by walk-forward
backtest**, never hand-tuned to in-sample data.

| Indicator | TF | Settings | Role | Weight | Notes |
|---|---|---|---|---|---|
| 200 EMA | 4H/D | 200 | **Directional bias gate** (not a vote) | gate | Price above → longs only; below → shorts only |
| ATR | all | 14 | Stops, vol filter, breakout strength | n/a | Measure, not a signal |
| MACD | 1H | 12,26,9 | Momentum vote; histogram divergence | 0.25 | Down-weight in ranging regime |
| RSI | 15M | 14 | Range extremes + divergence only | 0.20 | **Never** sole trigger in trends (can pin <30 for weeks) |
| EMA cross | 15M | 9/21 | Trend-regime entry trigger | 0.25 | Only active when regime = TRENDING |
| Bollinger Bands | 15M | 20,2 | Range-regime entry trigger; squeeze = pending breakout | 0.20 | Only active when regime = RANGING |
| Tick volume | 15M | n/a | Breakout confirmation only | 0.10 | `breakout_strength = candle_range / avg_range(20)`; low-range breakout = suspected fakeout |
| Divergence detector | 15M/1H | 5-bar swing compare | High-accuracy reversal overlay | bonus +0.15 | `min(price[-5:])` vs `min(RSI[-5:])` (and max for bearish) |

**Excluded** (per KB#1, unless future evidence reverses): Stochastic standalone,
Ichimoku, pivot points, Fibonacci retracements (subjective; only ever as confluence,
never as trigger).

## 3. Market Regime Filter

Four regimes (KB#1's three + an event regime that takes precedence):

```python
def detect_regime(pair):
    if in_news_blackout(pair):           return EVENT       # hard no-trade
    if ADX(14) > 25:                     return TRENDING
    if ATR(14) > 1.5 * ATR(50):          return HIGH_VOL    # structural vol → breakout logic
    if ADX(14) < 20:                     return RANGING
    return TRANSITIONAL                  # 20 ≤ ADX ≤ 25 → no new entries
```

| Regime | Strategy enabled | Strategy disabled |
|---|---|---|
| TRENDING | EMA crossover, breakout continuation | Mean reversion, RSI extremes |
| RANGING | Bollinger mean reversion, range fades | Trend following, breakouts |
| HIGH_VOL | Breakout with widened stops (×1.3) | Scalping, tight stops |
| EVENT / TRANSITIONAL | nothing | everything |

**Resolution of v0 conflict:** v0 suppressed all trades in any volatile state. KB#1
distinguishes *event-driven* vol (unpredictable → blackout) from *structural* vol
(tradeable via breakouts). Adopted: EVENT suppresses; HIGH_VOL trades breakouts.

## 4. Session & News Filters

### Session gating (GMT)
| Session | Allowed strategies |
|---|---|
| Asia 00–09 | Range strategies only (exception: USD/JPY trend OK) |
| London open 08–10 | Breakout + trend |
| London–NY overlap 12–16 | All strategies (**prime Gold window 12:00–16:00**) |
| NY close 20–00 | Trend continuation only |

### News blackout windows
| Event | Blackout (before/after) |
|---|---|
| FOMC rate decision | 60 / 60 min |
| NFP | 30 / 30 min |
| CPI | 30 / 30 min |
| ECB/BOJ statement | 15 / 30 min |
| Sudden geopolitical shock | Not predictable — drawdown breaker + vol-adjusted stops are the defense |

## 5. Macro & Sentiment Layer

### Gold (XAU/USD) macro gate
- 10Y TIPS real yield **rising** → block new longs.
- DXY above its 200 EMA → block new longs; DXY falling → long bias confirmation.
- FedWatch pricing a cut → bullish tilt (weight into composite, not a gate).
- Correlation prior: gold↔DXY ≈ −0.85 (recheck rolling 90-day; regimes shift).

### Forex macro tilt
- Interest-rate differential direction (wider USD differential → USD bias).
- Risk sentiment via ES futures: risk-on → AUD/JPY long bias, USD/JPY short bias.
- PMI surprises (esp. German manufacturing for EUR) as bias tilt, not trigger.

### COT (weekly, lagged ~3 days)
- ⚠️ **Correction to KB#1 source:** in gold, commercials (producers/hedgers) are
  *structurally net short* — "commercials net long > 150k" essentially never prints
  and would mute the signal forever. Adopted instead: **COT Index** (percentile of
  net positioning over trailing 156 weeks). Commercials' net-short *extreme
  narrowing* (COT index > 80) = bullish; large-spec net-long crowding
  (index > 90) = exhaustion warning.

### Retail positioning (contrarian)
```python
if retail_long_pct > 75 and price_near_resistance: veto_longs(); tilt_short()
if retail_short_pct > 75 and price_near_support:   veto_shorts(); tilt_long()
```
Role: **veto/tilt**, never a standalone trigger.

## 6. Entry / Exit Logic

Hierarchy: **regime → time/news → HTF bias → LTF trigger → sentiment veto → risk model**.

- **HTF bias (4H):** price vs 200 EMA, plus macro gate for gold.
- **LTF trigger (15M):**
  - TRENDING + long bias: 9 EMA crosses above 21 EMA AND RSI(14) > 40
  - TRENDING + short bias: 9 EMA crosses below 21 EMA AND RSI(14) < 60
  - RANGING + long bias: price < lower BB AND RSI(14) < 30
  - RANGING + short bias: price > upper BB AND RSI(14) > 70
  - HIGH_VOL: breakout of session high/low with `breakout_strength ≥ 1.5` and rising tick volume
- **Divergence overlay:** confirmed divergence adds +0.15 confidence; opposing divergence vetoes.
- **Take profit:** trending → 2.0 R; ranging → mid-band (≈1.2–1.5 R typical); HIGH_VOL breakout → trail at 1×ATR after 1 R.
- **Time stop:** breakout trades exit if not in profit within N bars (N = backtest-derived, prior 8×15M).

## 7. Risk & Position Sizing

```python
risk_per_trade = min(0.02, 0.25 * kelly_fraction) * equity   # Kelly floor-capped at 2%
position_size  = risk_per_trade / (stop_distance_pips * pip_value)

stop_loss = entry ± 1.5 * ATR(14)
if vol_ratio > 1.5:                      # current vol 50% above average (gold esp.)
    stop_loss *= 1.3

# Circuit breakers (both active)
if daily_loss > 0.03 * equity:           halt_until_next_session()
if drawdown_from_peak > 0.15:            close_all(); disable_trading(24h); alert()
```

- Structure stops (beyond swing high/low) preferred in TRENDING; ATR stops in RANGING/HIGH_VOL.
- Max 1 position per currency bloc (USD-cluster counts as one).
- No martingale, no grid averaging.
- Kelly inputs (win rate, payoff) only from walk-forward stats with ≥500 trades; until then fixed 1%.

## 8. Execution

- **Limit orders for ~80% of entries** (at structure levels); stop-limit for breakouts; market orders only for emergency exits.
- Compute signals on **mid price**; require confirmation candle close to defeat bid-ask bounce.
- Breakouts through key levels on low volume/small range → treat as stop-hunt fakeout, no entry (consider fade in RANGING regime).
- Slippage model in backtest: +0.5 pip EUR/USD, +2 pips gold; news periods excluded by blackout anyway.

## 9. Backtesting & Validation Requirements

- ≥2 years data spanning multiple regimes; ≥500 trades per pair.
- Walk-forward: optimize 12 mo → test 3 mo unseen → roll. **Degradation >30% in-sample→out-of-sample = overfit, reject.**
- Final 6-month holdout never touched during tuning.
- Costs: pair-specific spread + slippage (§8) + swap.
- Monte Carlo trade-order reshuffle for drawdown confidence intervals.

| Metric | Acceptable | Good | Excellent |
|---|---|---|---|
| Win rate | >45% | >55% | >60% |
| Profit factor | >1.2 | >1.5 | >2.0 |
| Max drawdown | <20% | <10% | <5% |
| Sharpe | >0.5 | >1.0 | >1.5 |
| Avg trade (pips) | >5 | >15 | >30 |

---

## 10. Current Signal Logic (Master Decision Tree)

```text
on_new_bar(pair):
    # 1. Regime & time gates
    if in_news_blackout(pair) or session_disallows(strategy):     return NO_TRADE
    regime = detect_regime(pair)                                  # §3
    if regime in (EVENT, TRANSITIONAL):                            return NO_TRADE

    # 2. Higher-timeframe bias (4H)
    bias = LONG  if mid_4H > EMA200_4H else SHORT if mid_4H < EMA200_4H else NEUTRAL
    if pair == XAUUSD: bias = apply_gold_macro_gate(bias)          # TIPS, DXY §5
    if bias == NEUTRAL:                                            return NO_TRADE

    # 3. Lower-timeframe trigger (15M) per regime                  # §6
    candidate = ltf_trigger(regime, bias)
    if candidate is None:                                          return NO_TRADE

    # 4. Confidence & vetoes
    score  = weighted_vote(MACD, RSI, volume_confirm)              # §2
    score += divergence_overlay(candidate)                         # +0.15 / veto
    if retail_crowding_opposes(candidate):                         return NO_TRADE
    if score < CONFIDENCE_THRESHOLD:                               return NO_TRADE

    # 5. Risk & execution
    sl   = atr_or_structure_stop(regime)                           # §7
    tp   = regime_target(regime)                                   # 2R / mid-band / trail
    size = min(0.02, 0.25*kelly) * equity / (sl_pips * pip_value)
    return LimitOrderSignal(pair, candidate, entry, sl, tp, size, confidence=score)

continuous_monitor():
    if daily_loss > 3% or dd_from_peak > 15%: circuit_break()      # §7
```

## 11. Accuracy Estimate

**Directional win-rate prior: 52–58%** at blended ~1.6 R average payoff (profit
factor ≈ 1.4–1.8) — *unvalidated, pre-backtest*. Reasoning: each filter layer
(regime gating, MTF alignment, news blackout, session gating, sentiment veto)
independently removes documented false-signal classes rather than adding
predictive claims; the trigger set itself (EMA cross, BB fade) is roughly
break-even standalone in published tests, so the edge thesis rests entirely on
the filters. Expect live results at the low end of the range; treat anything
above 60% in backtest as an overfitting red flag, not a success.

## 12. Missing Piece (highest-value next input)

**Broker/platform + historical data source decision.** Every section above is
now specified enough to implement, but nothing can be validated until we lock:
(1) execution venue (MT5? OANDA? cTrader?) — determines API, spreads, whether
bid/ask history and tick volume are available; (2) backtest data source
(Dukascopy ticks are free and bid/ask-true). Second priority: confirm retail
sentiment source (IG vs OANDA) since the veto layer depends on it.

---

## Knowledge Base (append-only log)

| # | Date | Input | Incorporated as | Conflicts noted |
|---|---|---|---|---|
| 0 | 2026-06-10 | Framework definition | Blueprint skeleton v0.1 | — |
| 1 | 2026-06-10 | Deep market-structure & strategy reference (Parts 1–7: microstructure, regime taxonomy, ranked indicators, macro/sentiment filters, Kelly sizing, execution, validation) | Blueprint v1.0 — populated §§1–10 | (a) v0 "suppress all vol" vs KB#1 "trade structural vol breakouts" → resolved: EVENT suppresses, HIGH_VOL trades; (b) KB#1 gold COT "commercials net long >150k = buy" is structurally impossible (gold commercials are net short hedgers) → replaced with COT Index percentile logic; (c) fixed 2R TP vs mean-reversion logic → TP made regime-specific |
