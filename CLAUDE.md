# Baba Trading Bot — Claude Code Memory File

This file is read at the start of every Claude Code session. It captures
the full project context so work can continue without re-deriving history.

---

## Project Identity

**Owner:** Baba Toure (atoureconsulting@gmail.com)
**Broker:** Admirals Group — demo account 58675012 (hedge mode)
**Gold symbol name:** `GOLD` (not XAUUSD — Admirals-specific alias)
**Purpose:** MT5 signal advisor — Python process alongside MetaTrader 5
terminal; human executes trades, bot prints ENTER/EXIT advice per closed bar.
**Platform locked:** MetaTrader 5 + `MetaTrader5` pip package (Windows).
Bot never places orders; it only prints advice.

---

## Repository Layout

```
main.py              # entry point: --symbols GOLD USDJPY EURUSD --timeframes H1
backtest.py          # CLI: walk-forward OOS backtest on MT5 history
bot_battle.py        # CLI: 11 pool strategies head-to-head on MT5 history
baba_bot/
  indicators.py      # all indicators (Wilder smoothing to match MT5)
  regime.py          # detect_regime(), session_allows() — 4-state filter
  signals.py         # evaluate(), inside_bar_signal() — §10 decision tree
  risk.py            # position_size(), CircuitBreaker, RiskAdvice, SymbolSpec
  levels.py          # market_profile(), prior_day_profile() — POC/VAH/VAL
  news.py            # NewsCalendar — ForexFactory auto-refresh (4h)
  newsfeed.py        # fetch_forexfactory_calendar(), fetch_fxstreet_headlines()
  advisor.py         # Advisor loop (enable_inside_bar=False by default)
  mt5_client.py      # MT5Client: candles(), spec(), spread_points()
  journal.py         # SignalJournal: records signals, resolves outcomes
  backtest.py        # run_backtest(), walk_forward_threshold(), print_report()
  battle.py          # 11 strategy implementations + print_battle()
tests/smoke_test.py  # 32+ offline checks (no MT5 required)
MASTER_BLUEPRINT.md  # full system design, append-only KB log
news_events.example.json
requirements.txt
```

---

## Active Branch

Development branch: `claude/dreamy-hopper-b1xr5b`
Always commit and push to this branch — never push to main without permission.

---

## Critical Broker / Symbol Facts

| Fact | Value |
|---|---|
| Gold symbol | `GOLD` (not XAUUSD) |
| Account | Admirals demo 58675012, hedge mode |
| Spread source | Use `df["spread"].median()` from historical bars — NOT live snapshot (off-hours rollover poisons live readings; USDJPY shows 162pts live at rollover, correct value is ~9pts) |

`_is_gold()` utility in `news.py` recognizes: GOLD, GOLDm, XAUUSD, XAU*

---

## Architecture in One Paragraph

`Advisor.run()` loops on new closed bars. For each (symbol, timeframe) pair
it calls `signals.evaluate()` which runs the §10 decision tree:
1. News blackout gate (ForexFactory events)
2. Regime detection (ADX/ATR — TRENDING / RANGING / HIGH_VOL / EVENT / TRANSITIONAL)
3. Session gate (5 GMT windows, each with allowed regime set)
4. Spread pre-check vs `spec.spread_points_limit`
5. Higher-TF bias (4H 200 EMA; suspended in RANGING — fades are symmetric)
6. Regime-specific trigger (returns direction + optional SL/TP override)
7. Confidence scoring (MACD + RSI + volume + divergence weighted votes)
8. Risk model → `RiskAdvice` (lot size, SL, TP, R:R)
9. Returns `SignalAdvice` or None with a status line explaining why

---

## Knowledge Base Summary (KB#0 – KB#16)

These are the key conclusions — append-only in `MASTER_BLUEPRINT.md`.

| # | Finding |
|---|---|
| KB#0 | Blueprint created; initial architecture |
| KB#1 | Reference deep-dive: mid-price only; Wilder RSI/ATR; EMA cross is regime-gated |
| KB#2 | FXBot (trentstauff): OANDA pattern useful; strategies need regime gate |
| KB#3 | Circuit breakers: −3% daily halt, −15% DD with 10% recovery hysteresis |
| KB#4 | EarnForex PositionSizer: round DOWN lots; include commission in denominator; asymmetric tick values for metals; 630-combo sweep confirmed MA cross is near-zero across H1 |
| KB#5 | MarketProfile POC/VAH/VAL ported; TPO center-biased tiebreak |
| KB#6 | geraked MT5 patterns: swing_low/high, inside_bar, 3MACD strategy noted |
| KB#7 | Trading-Bot (raidastauras): COT1 approach; swing stop = extreme ± fixed buffer |
| KB#8 | Trading_Pal: raw OHLCV ML ≈ no edge after costs; use engineered features + 3-class labeling |
| KB#9 | Polygon.io websocket pattern (backup feed) |
| KB#10 | Signal advisor mode locked (MT5 + human executes) |
| KB#11 | Spread pre-check added (KB#5 pre-trade rule) |
| KB#12 | Market profile level proximity boosts confidence +0.05 |
| KB#13 | **First real backtest (M15, 4 pairs, 4y OOS):** GOLD −0.03R, GBPUSD +0.03R, USDJPY +0.14R, EURUSD −0.10R. Win rates 32–40%. HIGH_VOL regime was positive on all pairs (small sample). TRENDING EMA-cross ≈ 0R across ~1,100 trades → trigger is dead. Inside-bar disabled by default pending M15-granularity retest. |
| KB#14 | Engine integrity verified on random synthetic data (≈0 / negative — no phantom edge) |
| KB#15 | **16-year bot battle (H1, 11 strategies):** No strategy wins everywhere. EURUSD = mean reversion (band fade green in all 4 test runs). USDJPY = slow trend (32/64 SMA cross only positive trend logic). GOLD = HIGH_VOL breakout only. GBPUSD = nothing positive in 16 years → dropped from defaults. |
| KB#16 | **Triggers v2 implemented** (see below). Main defaults changed to `GOLD USDJPY EURUSD` on `H1`. |

---

## Triggers v2 (current — as of KB#16)

Replaced the dead 9/21 EMA cross with evidence-backed alternatives:

**TRENDING regime:** 32/64 SMA bull/bear cross (+ RSI > 40 / < 60 filter)
— KB#16 evidence: only trend logic positive across 16y; best on USDJPY 10/17 years

**RANGING regime:** Symmetric band re-entry fade — BOTH directions, no HTF bias gate
- Long: prev bar below lower BB, current bar re-enters between lower and mid → SL 0.9×half-band below entry, TP = mid-band
- Short: prev bar above upper BB, current bar re-enters between upper and mid → SL 0.9×half-band above entry, TP = mid-band
- HTF bias gate SUSPENDED for RANGING (fades are symmetric; 200EMA side is arbitrary inside a range)
- Scoring: `_confluence_range()` instead of `_confluence()` (RSI turning not momentum; quiet volume is good)

**HIGH_VOL regime:** unchanged — Donchian breakout above/below 20-bar high/low with `breakout_strength ≥ 1.5` and rising volume + HTF bias filter

---

## Risk Model

```python
# baba_bot/risk.py
def position_size(spec, balance, risk_pct, entry, stop_loss, commission_per_lot=0.0):
    sl_distance = abs(entry - stop_loss)
    loss_per_lot = (sl_distance / spec.tick_size) * spec.tick_value + 2 * commission_per_lot
    lots = risk_money / loss_per_lot
    lots = math.floor(lots / spec.volume_step + 1e-9) * spec.volume_step  # epsilon guards float error
    lots = round(lots, 8)
    return max(min(lots, spec.volume_max), 0.0) if lots >= spec.volume_min else 0.0
```

**Circuit breakers:** −3% daily halt; −15% peak DD with hysteresis (resume only after 10% recovery)

**Hard cap:** `--risk` flag is capped at 0.02 (2%) by `main.py`

---

## Validated Strategies (what has evidence)

| Strategy | Evidence | Status |
|---|---|---|
| HIGH_VOL breakout (Donchian + volume) | Positive on all pairs in both backtest and battle | ✅ Active |
| RANGING band re-entry fade (symmetric) | EURUSD fade green all 4 battle runs / 16y | ✅ Active (v2) |
| TRENDING 32/64 SMA cross | USDJPY +R in battle, only positive trend logic | ✅ Active (v2) |
| Inside-bar 4H breakout | +583 pips USDJPY but thin edge; disabled pending M15 retest | ⏳ Off by default |
| 9/21 EMA cross | Dead across ~1,100 trades (backtest) + 630-combo sweep | ❌ Replaced |
| GBPUSD (any strategy) | Nothing positive in 16 years | ❌ Dropped |

---

## Pending / Next Steps (in priority order)

1. **Re-validate v2 triggers with walk-forward backtest** (most critical):
   ```
   py backtest.py --symbols GOLD USDJPY EURUSD --timeframe H1 --years 12
   ```
   Numbers to beat: USDJPY +0.14R/PF 1.23 (v1 M15 baseline).
   The v2 components won the tournament individually — this tests them inside
   the full filter stack (regime gates, session gates, news gates, confidence
   threshold) over 12 years, out-of-sample.

2. **Add remaining unimplemented pool strategies to battle:**
   - geraked: CEZLSMA, 3MACD, DHLAOS, LRCMACD
   - ForexSmartBot: mean-reversion, scalping-MA, momentum-breakout
   - (COT1 and Fear Index need external data — skip for now)

3. **Inside-bar retest at M15 granularity** (the H4 simulation was unfair;
   signals should be evaluated against M15 trigger data).

4. **Confidence score enrichment** — walk-forward consistently picked the
   lowest threshold (score discrimination is weak). Candidates: session-quality
   vote, Market Profile proximity, spread-to-ATR ratio, regime strength (ADX value).

5. **Journal-driven recalibration** — once live signals accumulate (≥50 per pair),
   run monthly ablation to verify each filter contributes.

---

## How to Run

```bash
# Signal advisor (needs MT5 running on Windows)
py main.py --symbols GOLD USDJPY EURUSD --timeframes H1 --risk 0.01

# Verify news feeds (no MT5 needed)
py main.py --check-news

# Walk-forward backtest (needs MT5)
py backtest.py --symbols GOLD USDJPY EURUSD --timeframe H1 --years 12

# Bot battle: 11 strategies head-to-head (needs MT5)
py bot_battle.py --symbols GOLD USDJPY EURUSD --timeframe H1 --years 12

# Offline tests (no MT5)
py -m pytest tests/smoke_test.py -v
```

---

## Design Decisions That Must NOT Be Reversed

- **Signal advisor only** — never auto-place orders without explicit user sign-off
- **Risk cap ≤ 2%** — hard-coded in `main.py`
- **No GBPUSD** — 16 years of data, nothing positive; removed from defaults
- **Spread from historical bars** — never from live snapshot for backtests
- **HTF bias suspended in RANGING** — fades are symmetric; applying 200EMA side arbitrarily kills half the trades
- **Inside-bar off by default** (`--inside-bar` flag required) — pending M15 retest
- **Wilder smoothing** for RSI and ATR (matches MT5; standard EWM gives different numbers)
- **SL-first on ambiguous bars** in backtest (conservative; avoids look-ahead bias)
