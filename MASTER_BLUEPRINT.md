# Baba Trading Bot — Master Blueprint

**Version:** 0.1 (skeleton — no strategy inputs incorporated yet)
**Scope:** Hybrid trading signal bot for Forex majors (EUR/USD, GBP/USD, USD/JPY, USD/CHF, AUD/USD, USD/CAD, NZD/USD) and Gold (XAU/USD)
**Goal:** Maximize signal accuracy by fusing technical analysis, market sentiment, macroeconomic filters, and validated open-source bot logic.

> This document is the single source of truth. Every new piece of information
> (links, screenshots, repos, papers, broker insights, indicator settings, risk
> rules) is appended to the Knowledge Base and reflected in the sections below.
> Copy this file at any time to get the full current system design.

---

## 1. Data Sources Required

| Category | Source | Status |
|---|---|---|
| Price/OHLCV (multi-timeframe) | TBD (broker API / MT5 / OANDA / Polygon / Dukascopy ticks) | ❌ not chosen |
| Market sentiment | TBD (retail positioning, COT report, news NLP) | ❌ not chosen |
| Macroeconomic calendar | TBD (high-impact event filter: NFP, CPI, FOMC, ECB) | ❌ not chosen |
| Volatility context | Derivable (ATR, realized vol) once price feed chosen | ⏳ |
| Gold-specific drivers | DXY, real yields (10Y TIPS), risk-on/off proxy | ❌ not chosen |

## 2. Indicator Suite & Weights

*Empty — populated as indicators are validated and added.*

| Indicator | Timeframe | Settings | Weight | Rationale / Source |
|---|---|---|---|---|
| — | — | — | — | — |

Weighting model (default until evidence suggests otherwise): weighted voting
ensemble producing a composite score in [-1, +1]; signal fires only above a
confidence threshold. Weights to be calibrated by walk-forward backtest, not
hand-tuned.

## 3. Market Regime Filter

Three regimes gate which sub-strategies are allowed to fire:

- **Trending** — trend-following logic enabled, mean-reversion disabled.
- **Ranging** — mean-reversion enabled, trend-following disabled.
- **Volatile/Event** — all entries suppressed (macro event window, vol spike).

Regime detection method: TBD (candidates: ADX threshold, Hurst exponent,
ATR percentile, moving-average slope). Awaiting first inputs.

## 4. Entry / Exit Logic

*Empty — defined once the first strategy components arrive.*

Structural rule already fixed: **no signal is emitted unless (a) regime filter
permits, (b) composite indicator score exceeds threshold, (c) no high-impact
macro event inside the blackout window, (d) sentiment does not strongly
contradict the technical signal.**

## 5. Risk & Position Sizing Rules

Defaults (to be overridden by your rules when provided):

- Risk per trade: 1% of equity (hard cap 2%).
- Position size = (equity × risk%) / (stop distance in pips × pip value).
- Max concurrent correlated exposure: 1 position per currency bloc (USD-long
  cluster counts as one).
- Daily stop: -3% equity → halt signals until next session.
- Stop-loss mandatory on every signal; no martingale, no grid averaging-down
  unless a provided strategy proves otherwise in backtest.

## 6. Backtesting Requirements

- Walk-forward analysis (rolling train/validate windows), not single in-sample fit.
- Out-of-sample holdout ≥ 20% of data, never touched during tuning.
- Realistic costs: spread (pair-specific), slippage, swap.
- Minimum 500 trades per pair before trusting a win-rate estimate.
- Metrics: win rate, profit factor, max drawdown, Sharpe/Sortino, expectancy
  per trade, and stability of equity curve across regimes.
- Monte Carlo reshuffling of trade order for drawdown confidence intervals.

---

## 7. Current Signal Logic (Decision Tree)

```text
on_new_bar(pair, timeframes):
    if macro_event_within_blackout(pair):        return NO_SIGNAL
    regime = detect_regime(pair)                  # TBD method
    if regime == VOLATILE:                        return NO_SIGNAL

    score = weighted_vote(indicator_suite)       # suite empty — v0
    if regime == TRENDING  and score >= +T:  candidate = LONG
    if regime == TRENDING  and score <= -T:  candidate = SHORT
    if regime == RANGING   and at_range_extreme: candidate = fade_extreme()
    else:                                         return NO_SIGNAL

    if sentiment_strongly_opposes(candidate):     return NO_SIGNAL
    sl, tp = risk_model(ATR, structure_levels)
    size   = position_size(equity, risk_pct, sl_distance)
    return Signal(pair, candidate, entry, sl, tp, size, confidence=|score|)
```

## 8. Accuracy Estimate

**Not yet estimable** — no validated components in the suite. The architecture
(regime gating + ensemble voting + event blackout) is consistent with
literature showing regime-conditional strategies outperform unconditional
ones, but a number would be fabrication at this stage.

## 9. Missing Piece (highest-value next input)

**A price data source + your core strategy seed** — a GitHub repo, indicator
set, or rule screenshot. The single most valuable first input is whichever
strategy you already trust most, so the ensemble has a backbone to weight
everything else against.

---

## Knowledge Base (append-only log)

| # | Date | Input | Incorporated as | Conflicts noted |
|---|---|---|---|---|
| 0 | 2026-06-10 | Framework definition (this session) | Blueprint skeleton v0.1 | — |
