# Baba Trading Bot — Master Blueprint

**Version:** 1.3
**Platform (LOCKED by user decision):** MetaTrader 5 terminal + Python `MetaTrader5` package, running as a **signal advisor** alongside the user's own MT5 — human executes, bot analyzes and alerts (enter/exit/stand-aside per symbol & timeframe).
**Scope:** Hybrid trading signal bot for Forex majors (EUR/USD, GBP/USD, USD/JPY, USD/CHF, AUD/USD, USD/CAD, NZD/USD) and Gold (XAU/USD)
**Goal:** Maximize signal accuracy by fusing technical analysis, market sentiment, macroeconomic filters, and validated open-source bot logic.

> This document is the single source of truth. Every new piece of information
> is appended to the Knowledge Base log and reflected in the sections below.
> Copy this file at any time to get the full current system design.

---

## 1. Data Sources Required

| Category | Source | Status |
|---|---|---|
| Price/OHLCV multi-TF (15M, 1H, 4H, D) with **bid AND ask** | **OANDA v20 API via `tpqoa`** (candidate promoted by KB#2 — free practice account, historical mid/bid/ask candles, tick streaming, order API, has XAU/USD) | ⏳ awaiting confirmation |
| Tick volume | Same feed (caveat: forex tick volume ≠ real volume; confirmation-only) | ⏳ |
| Macro calendar (NFP, FOMC, CPI, ECB/BOJ) | ForexFactory / DailyFX calendar (scrape or JSON) | ⏳ candidate chosen |
| Retail positioning | IG Client Sentiment or OANDA Order Book | ⏳ candidate chosen |
| COT report (weekly) | CFTC (free, published Fridays, data as of Tuesday) | ⏳ |
| Gold macro drivers | DXY price feed, 10Y TIPS real yield (FRED), CME FedWatch | ⏳ |
| Risk sentiment proxy | S&P 500 futures (ES) | ⏳ |
| Backup/secondary tick feed | Polygon.io websocket (pattern from KB#9; useful for feed cross-validation) | optional |

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
| **Market Profile POC/VAH/VAL** (KB#6) | session/daily | TPO, 70% value area | **Level engine** — defines "near support/resistance" for limit entries, range fades, retail-sentiment veto | levels, not a vote | Port from EarnForex MarketProfile (Apache-2.0). POC = max-TPO price (center-biased tiebreak); VA = expand from POC until 70% TPOs enclosed |
| **Inside-bar breakout** (KB#4) | 4H | entry = ±10% of mother-bar range beyond extreme; SL 0.4×range; TP 0.8×range | Breakout-regime entry trigger | 0.20 (HIGH_VOL regime) | Evidence: +583 pips USD/JPY 2020, 746 trades, spread-adjusted — but only ~0.8 pips/trade avg → thin; needs our filter stack on top |

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

Two COT philosophies now in the pool — **both kept as separate candidate features,
backtest decides**: (a) our contrarian COT-Index percentile extremes (above), and
(b) KB#7's COT1 momentum approach: rank symbols by week-over-week *positioning
growth*, trade the top movers, entries Mon–Tue only (right after Friday release).

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
  - HIGH_VOL: breakout of session high/low with `breakout_strength ≥ 1.5` and rising tick volume; **or 4H inside-bar breakout** (KB#4: entry ±10% of mother-bar range, SL 0.4×range, TP 0.8×range)
  - ⚠️ Evidence caveat (KB#4, KB#8): naked MA-crossover entries tested net-negative across 630 parameter combos / 21 pairs on H1; OHLCV-only ML direction prediction ≈ no edge after costs. The 9/21 EMA trigger survives **only** because it is regime- and bias-gated; if walk-forward shows it lagging the inside-bar/structure triggers, replace it.
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

- Structure stops (beyond swing high/low) preferred in TRENDING; ATR stops in RANGING/HIGH_VOL. KB#7 pattern adopted: swing stop = swing extreme ± fixed buffer (e.g. 6 pips) to survive stop-hunts.
- Max 1 position per currency bloc (USD-cluster counts as one).
- No martingale, no grid averaging.
- Kelly inputs (win rate, payoff) only from walk-forward stats with ≥500 trades; until then fixed 1%.

**Sizing edge cases adopted from KB#5 (EarnForex PositionSizer, Apache-2.0 — portable):**
- Include **round-trip commission** in the risk denominator: `lots = risk_money / (sl_points × unit_cost/tick_size + 2×commission_per_lot)`.
- Always **round lot size DOWN** to broker lot step; clamp to min/max lot; split orders above max lot.
- Convert risk across currencies via USD as intermediate when no direct pair exists.
- Use **asymmetric tick values** (loss vs profit) — matters for XAU/USD.
- Guard `tick_size == 0` division; treat positions without SL as infinite risk in portfolio totals.
- Pre-trade checks: max spread, max entry-to-SL distance, post-trade margin utilization cap.
- Portfolio-level total risk = Σ per-position (SL distance × unit cost), currency-converted.
- Breakeven move (entry + round-trip cost, not raw entry) and trailing stop as managed exits.

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

## 12. Operating Mode (decided)

**MT5 signal-advisor mode.** ~~Platform decision~~ → resolved: MetaTrader 5.
- Python process attaches to the user's running MT5 terminal (`MetaTrader5` pip
  package, Windows) — reads candles for configured symbols/timeframes, account
  balance/currency, live spread, symbol contract specs.
- Per closed bar it runs the §10 decision tree and emits **advice**, not orders:
  `ENTER long/short @ price, SL, TP, lots, confidence, reasons` or
  `EXIT/manage` for previously advised trades, or stays silent.
- Every emitted signal is journaled (`signals_journal.json`) with its later
  outcome → this journal is the calibration dataset for §11.
- EarnForex tools (KB#5, KB#6) can run natively on the same charts as visual
  cross-checks since we're MT5-side anyway.
- Upgrade path: same signal engine can later auto-execute via `mt5.order_send()`
  once trust is earned (flip `execution_mode: advise → auto`).

## 12b. Accuracy Improvement Roadmap (path from prior to measured edge)

The 52–58% prior improves through measurement and selectivity, in this order:
1. **Backtest on real MT5 history** (your broker's actual spreads on XAU/USD!)
   under the §9 protocol → replaces the prior with measured per-pair, per-TF
   win rates. This is the single biggest accuracy lever and is now unblocked.
2. **Confidence-threshold tuning:** the composite score is a dial — raising the
   threshold trades frequency for accuracy. Target: emit only top-confluence
   signals (≈2–5/day across pairs), measured precision > raw win rate.
3. **Filter stack verification:** session gate, news blackout, MTF alignment,
   Market Profile level proximity — each gets an ablation test (accuracy with
   vs without) so weights reflect *our* data, not priors.
4. **Journal-driven recalibration:** monthly walk-forward refit of indicator
   weights + threshold from the live signal journal; drop any component whose
   ablation shows no contribution.
5. **Meta-model (later):** logistic regression over the engineered filter
   features (NOT raw OHLCV — KB#8 lesson) to learn interaction effects, with
   the 3-class dead-zone labeling from KB#8.

---

## 13. External Component Pool (vetted open-source logic)

### KB#2 — FXBot (github.com/trentstauff/FXBot) — Python, OANDA v20 + tpqoa

**What it is:** interactive CLI bot offering vectorized backtesting + tick-stream live
trading on OANDA practice/live accounts. Five strategies: SMA crossover,
Bollinger mean reversion, momentum (sign of rolling mean return), contrarian
(inverse momentum), logistic-regression classification on lagged returns
(+ a linear-regression forward-test module, backtest only).

**ADOPT (architecture, not verbatim — repo has NO license file):**
- ✅ OANDA + `tpqoa` integration pattern (historical mid/bid/ask candles, tick stream, orders) → promotes OANDA to lead broker candidate (§1).
- ✅ Live bar-builder: stream ticks → resample mid price to bar → act only on **completed** bars. Matches our §8 mid-price + confirmation-candle rules.
- ✅ Vectorized backtest skeleton: `position.shift(1) × log returns` (no lookahead), per-trade cost via `|Δposition| × tc`, benchmark vs buy-and-hold.
- ✅ Class template: `Backtester` / `LiveTrader` bases with `define_strategy()` override per strategy → clean fit for our regime-switched ensemble.
- ✅ Momentum & contrarian rolling-window probes as cheap auxiliary regime confirmers (momentum profits ↔ trending; contrarian profits ↔ ranging).

**REJECT (conflicts with blueprint):**
- ❌ Always-in-market ±1 positions (never flat) → violates our flat-by-default confluence gating (§6).
- ❌ No per-trade SL/TP, no position sizing (fixed units); "stop loss" is only a session-P&L halt → violates §7 (mandatory stops).
- ❌ Market orders for all entries (reversals at 2× units) → violates §8 limit-order rule.
- ❌ Optimizer = brute-force grid search maximizing return **on the same interval** (no walk-forward, no out-of-sample) → textbook overfitting; exactly what §9 forbids.
- ❌ ML layer uses only lagged returns as features (≈ coin-flip edge documented across literature); hit-ratio bookkeeping also miscounts zero-return bars.
- ⚠️ Code is stale: pandas `.append()` (removed in pandas 2.0), buggy market-hours check, double "−100" in some optimizer printouts. Treat as reference, not dependency.

### KB#3 — ForexSmartBot (github.com/VoxHash/ForexSmartBot) — Python, MIT(+terms)

**What it is:** large PyQt6 desktop bot, 17 strategies (10 technical + 7 ML), multi-provider data (YFinance/OANDA/AlphaVantage/CSV/MT4-ZeroMQ bridge), paper/MT4/REST/IB brokers, GA/Optuna/Monte-Carlo optimization modules. Elaborate docs that **overstate the code**.

**ADOPT:**
- ✅ Risk engine design (`core/risk_engine.py`): layered sizing = base risk% × symbol multiplier × strategy multiplier, then `min(…, 0.25-Kelly, volatility-target size)`, **drawdown throttle with hysteresis** (halve size at 25% DD, resume only after 10% recovery). The hysteresis idea improves our circuit breaker (§7).
- ✅ Fear Index composite regime score (z-scored VIX 0.30 + FX vol 0.25 + DXY 0.15 + news 0.20 + policy 0.10, SMA-smoothed, ±0.5 thresholds) → clean template for our macro/sentiment layer.
- ✅ Multi-provider fallback chain pattern for data resilience.
- ✅ Donchian-breakout + ATR strategy structure; SMA/RSI implementations are sound.

**REJECT:**
- ❌ **All 7 ML strategies have look-ahead data leakage** (`target = Close.shift(-1)` — trained on the future). Every ML backtest result from this repo is invalid.
- ❌ **Walk-forward is fake**: training windows are computed then ignored; no reoptimization — sequential in-sample tests labeled "walk-forward". Violates §9.
- ❌ Docs claim v3.3 cloud/REST/WebSocket features that don't exist in code; no slippage/commission in backtests; no cross-symbol correlation in sizing.
- **Meta-lesson:** impressive READMEs ≠ valid logic. Every external claim gets code-level verification before entering this blueprint.

### KB#4 — Python-ForexTradingBot (github.com/mathewqpmiller) — Python, NO LICENSE

**What it is:** OANDA research notebooks + incomplete starter bot. Honest, data-driven exploration.

**ADOPT (patterns/evidence, not code — no license):**
- ✅ **Inside-bar 4H breakout** (now in §2/§6): entry ±10% mother-bar range, SL 0.4×, TP 0.8× (1:2 RR). Spread-adjusted sim: +583 pips, 746 trades, USD/JPY 2020. Caveat: ~0.8 pips/trade — too thin raw; viable only with our session/regime/news filters and only if walk-forward confirms on more pairs/years.
- ✅ **Negative result worth gold:** 630 MA-crossover combos × 21 pairs on H1 → ALL net-negative. Hard evidence behind §6's caveat.
- ✅ Bid/ask dual-stream simulation (buy at ask-derived levels, sell at bid) and pip-location normalization (`10^pipLocation`) — JPY pairs & gold handled correctly.
- ⚠️ Their own spread experiment: spread cost ≈ 3% of gross pips on H4 USD/JPY — on 15M signals it will be several times larger; our §9 cost model is mandatory, not optional.

### KB#5 — EarnForex PositionSizer — MQL5, Apache-2.0

**What it is:** mature, widely-used MT5 position-sizing panel/EA. The sizing math is now adopted wholesale into §7 (see "Sizing edge cases"). Also adopted: multiple-TP volume splitting with remainder distribution, pending-order expiry, OCO-style entry handling, magic-number scoping. License permits porting to Python with attribution.

### KB#6 — EarnForex MarketProfile — MQL5/C#, Apache-2.0

**What it is:** Market Profile (TPO) indicator: POC / VAH / VAL / single prints / developing POC, with session segmentation (intraday/daily/weekly/monthly) and level-cross alerts.

**ADOPT:**
- ✅ Port POC/VAH/VAL into Python as the **level engine** (§2): per session, count TPOs per price tick; POC = max count (tie → closest to session midpoint); expand symmetrically from POC, adding the larger neighbor side, until 70% of TPOs enclosed → VAH/VAL.
- ✅ Prior-session level rays = our limit-order placement anchors and "price near S/R" predicate (needed by retail-sentiment veto §5 and range fades §6).
- ✅ Their three cross definitions (price break / candle close / gap cross) formalize our "confirmation candle" rule — we adopt **candle-close-through** as the standard confirmation.
- ✅ Single prints = low-acceptance zones → expect fast revisits; useful for TP placement on breakouts.

### KB#7 — geraked/metatrader5 — MQL5, MIT

**What it is:** 10+ MQL5 EAs (BBRSI, 3MACD, 2MACDSTO, CEZLSMA, DHLAOS, LRCMACD, COT1…) on a shared `EAUtils.mqh` framework, with published MT5 tester reports.

**ADOPT:**
- ✅ `EAUtils` framework features: risk% sizing off min(balance, free margin), swing-stop ± buffer, margin-level floor (refuse trades below 300%), spread limit, calendar-based news pause, order retry with backoff — all consistent with §§4,7,8.
- ✅ **COT1 architecture** (§5): weekly CFTC data in SQLite, rank 8 currency contracts by positioning growth, trade top long/short movers, Mon–Tue entries only, max 1 deal/symbol/week. MIT — portable.

**REJECT:**
- ❌ **The published profitability is a grid/martingale artifact.** Their own READMEs admit every pure strategy was unprofitable; profits appear only after grid averaging (vol multipliers 1.0–1.5×, up to 50 levels) at 1:100–1:500 leverage. That's tail-risk laundering: smooth equity curve until one trend wipes the account. Violates our hard no-grid rule (§7). None of the entry strategies earn an indicator-suite slot on this evidence.

### KB#8 — raidastauras/Trading-Bot — Python, NO LICENSE (2018, TF1.x)

**What it is:** honest ML research: logistic regression / LSTM / CNN on 15y EUR/USD H1, 25–250 ta-lib features, three-class direction labels (up/flat/down with dead-zone delta), plus a live OANDA loop.

**ADOPT (evidence + patterns):**
- ✅ **Author's own conclusions** (validating our §2/§11 stance): OHLCV-only direction prediction "not really accurate"; 250 features no better than 25; direct return-maximization objective fails/overfits; transaction costs flip marginal models negative. → Our rule stands: ML enters only as a meta-layer over **our** filter-stack features (regime, sentiment, macro), never on raw price alone.
- ✅ Three-class labeling with flat dead-zone (≈ ±2.75 pips H1) — better than binary labels for "no-trade" learning; adopt if/when we train a meta-model.
- ✅ Session dummy features (London/NY/Sydney/Tokyo) and multi-period ta-lib feature template.

**REJECT:** ❌ market orders, all-margin sizing, no SL/TP in live loop; deprecated OANDA v1 + TF1.x code.

### KB#9 — Trading_Pal-main (United-Visions) — Python/Flask, **AGPL-3.0**

**What it is:** Gemini-LLM chat web app wrapping OANDA/Alpaca order endpoints + Polygon.io websocket feed. **No strategy or signal logic at all** — order CRUD via chatbot.

- ⚠️ **License clash: AGPL-3.0 is copyleft.** Copying any of its code into this bot would force open-sourcing the whole system if ever deployed as a service. **Do not vendor code from this repo.** Ideas only.
- ✅ Idea worth keeping: Polygon.io websocket + REST-backfill + reconnect-with-backoff as an independent secondary feed (§1) for data sanity checks.
- ❌ Anti-patterns catalogued: committed SQLite DB with user data, plaintext broker keys in DB, no input validation on order routes, committed logs. Our repo: secrets via env/config outside git, period.
- ❌ "LLM decides trades from chat" is the opposite of our deterministic, backtestable pipeline.

---

## 14. Clash Matrix (cross-source conflicts & resolutions)

| # | Clash | Sources | Resolution |
|---|---|---|---|
| 1 | **Grid/martingale "profitability"** vs hard no-grid rule | KB#7 (all EAs profitable only with grid) vs KB#1/§7, KB#5 (fixed-risk philosophy) | Grid rejected. Tail risk hidden by averaging-down; KB#7 entry strategies admitted unprofitable without it. §7 unchanged. |
| 2 | **Always-in-market ±1** vs flat-by-default | KB#2 (FXBot), KB#8 (live loop) vs §6 confluence gating | Rejected. Always-in doubles cost exposure and forces trades in EVENT/TRANSITIONAL regimes. |
| 3 | **Market orders** vs limit-order rule | KB#2, KB#8, KB#9 vs KB#1/§8 | Limit orders stand; Market Profile levels (KB#6) now give us the concrete prices to rest limits at. |
| 4 | **MA-crossover entries** | KB#2/KB#3 ship them; KB#4 proves 630 combos net-negative; §6 uses 9/21 EMA | Kept *only* as regime-gated trigger, on notice: §6 caveat says replace with structure/inside-bar triggers if walk-forward agrees with KB#4. |
| 5 | **ML claims** | KB#3 (7 leaky models, fake walk-forward) vs KB#8 (honest negative results) vs KB#2 (lag-features ≈ coin flip) | Converging evidence: OHLCV-only ML has no deployable edge. ML restricted to future meta-layer over engineered filter features. KB#3's numbers quarantined entirely. |
| 6 | **COT philosophy: contrarian extremes vs positioning momentum** | §5 COT-Index percentile vs KB#7 COT1 growth-ranking | Genuine methodological conflict — both retained as separate candidate features; walk-forward backtest arbitrates. They must not both gate the same trade (double-counting one dataset). |
| 7 | **Licenses** | KB#9 AGPL (viral) vs KB#2/#4/#8 no-license (all rights reserved) vs KB#5/#6 Apache, KB#7 MIT, KB#3 MIT+terms | Port code only from Apache/MIT sources (KB#5, #6, #7, cautiously #3). No-license & AGPL repos: patterns and evidence only, zero copied code. |
| 8 | **Platform split: MQL5 vs Python** | KB#5/#6/#7 are MT5-side; our stack is Python (OANDA candidate) | Not adopted as dependencies — algorithms get **ported** to Python. If user later prefers MT5 execution, EarnForex tools run natively alongside. |
| 9 | **Validation quality** | KB#3 fake walk-forward & KB#7 in-sample grid reports vs §9 standards | §9 unchanged and now battle-tested: every external performance claim so far has failed our checklist — none enter the accuracy estimate. |

---

## Knowledge Base (append-only log)

| # | Date | Input | Incorporated as | Conflicts noted |
|---|---|---|---|---|
| 0 | 2026-06-10 | Framework definition | Blueprint skeleton v0.1 | — |
| 1 | 2026-06-10 | Deep market-structure & strategy reference (Parts 1–7: microstructure, regime taxonomy, ranked indicators, macro/sentiment filters, Kelly sizing, execution, validation) | Blueprint v1.0 — populated §§1–10 | (a) v0 "suppress all vol" vs KB#1 "trade structural vol breakouts" → resolved: EVENT suppresses, HIGH_VOL trades; (b) KB#1 gold COT "commercials net long >150k = buy" is structurally impossible (gold commercials are net short hedgers) → replaced with COT Index percentile logic; (c) fixed 2R TP vs mean-reversion logic → TP made regime-specific |
| 2 | 2026-06-10 | FXBot repo (trentstauff) | §13 KB#2; OANDA/tpqoa promoted in §1 | Always-in-market, market orders, in-sample optimizer → all rejected (clash matrix #2,#3,#9) |
| 3 | 2026-06-10 | ForexSmartBot repo (VoxHash) | §13 KB#3; DD-throttle hysteresis + Fear Index template adopted (§7,§5) | ML data leakage + fake walk-forward → quarantined (clash #5,#9) |
| 4 | 2026-06-10 | Python-ForexTradingBot repo (mathewqpmiller) | §13 KB#4; inside-bar trigger added (§2,§6); MA-cross negative evidence (§6 caveat) | Challenges our own EMA-cross trigger (clash #4) |
| 5 | 2026-06-10 | EarnForex PositionSizer | §13 KB#5; sizing edge cases merged into §7 | None — pure upgrade |
| 6 | 2026-06-10 | EarnForex MarketProfile | §13 KB#6; POC/VAH/VAL level engine added (§2); close-through confirmation standard (§8 refinement) | None — fills the "where to rest limit orders" gap |
| 7 | 2026-06-10 | geraked/metatrader5 EA collection | §13 KB#7; EAUtils risk patterns + COT1 momentum approach (§5,§7) | Grid-dependent profits rejected (clash #1); COT philosophy conflict logged (clash #6) |
| 8 | 2026-06-10 | raidastauras/Trading-Bot ML research | §13 KB#8; 3-class labeling + session dummies + negative-evidence (§2,§6,§11) | Reinforces ML-as-meta-layer-only rule (clash #5) |
| 9 | 2026-06-10 | Trading_Pal-main (United-Visions) | §13 KB#9; Polygon backup-feed idea (§1) | AGPL license wall — ideas only, no code (clash #7) |
| 10 | 2026-06-10 | User requirement: run alongside MT5 as per-timeframe enter/exit advisor; improve accuracy | Platform locked (header, §12); advisor mode + signal journal (§12); accuracy roadmap (§12b); initial Python implementation in `baba_bot/` | Resolves clash #8 (MQL5-vs-Python) in favor of MT5-side Python; OANDA demoted to backtest-data fallback |
