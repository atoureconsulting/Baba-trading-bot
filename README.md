# Baba Trading Bot — MT5 Signal Advisor

A signal advisor for Forex majors and Gold (XAU/USD) that runs **alongside your
MetaTrader 5 terminal**. It reads your charts live, runs the pipeline defined in
[MASTER_BLUEPRINT.md](MASTER_BLUEPRINT.md) (regime filter → session/news gates →
4H bias → timeframe trigger → confluence score → risk model), and tells you
**when to enter, where to stop, where to target, and how many lots** — you stay
in control of the actual trade.

```
XAUUSD M15 — ENTER BUY (long)
  entry  ~2381.45
  stop   2377.10
  target 2390.15  (R:R 1:2.0)
  size   0.23 lots  (risking 100.00)
  confidence 0.65 | regime trending
  why: 4H close above 200EMA; 9/21 EMA bull cross, RSI 54 > 40; MACD histogram
       agrees; tick volume above 20-bar average; near prior-day POC
```

## Setup (on the Windows PC running MT5)

1. Install Python 3.10+ and your MT5 terminal; log into your account
   (demo recommended until the journal proves the edge).
2. In MT5: Tools → Options → Expert Advisors → enable **Algo Trading**
   (required for the Python API to attach — the advisor still never trades).
3. ```
   pip install -r requirements.txt
   python main.py --symbols XAUUSD EURUSD --timeframes M15 H1 --risk 0.01
   ```
4. Optional: maintain `news_events.json` with upcoming high-impact events
   (see `news_events.example.json`) so the advisor enforces blackout windows.

Symbols must match your broker's Market Watch names exactly (some brokers use
`XAUUSD.a`, `GOLD`, etc.).

## What it does every new closed bar

- Detects the regime (trending / ranging / high-vol / event) and only allows
  the strategy class that historically works in it.
- Refuses to signal during news blackouts, dead sessions, wide spreads, after
  a -3% day, or in a >15% drawdown (circuit breakers with recovery hysteresis).
- Scores confluence (MACD, RSI zone, volume, divergence, market-profile
  levels) and only speaks above the confidence threshold.
- Sizes positions with broker-accurate math (tick value, lot step rounding
  down, commission-aware) at your chosen risk %, hard-capped at 2%.
- **Journals every signal** to `signals_journal.json` and reports when an
  advised trade hits its stop or target — this journal is the dataset that
  turns the accuracy estimate into a measured number (blueprint §12b).

## Repo layout

| Path | Purpose |
|---|---|
| `MASTER_BLUEPRINT.md` | The living system design + knowledge base (KB#0–10) |
| `baba_bot/` | The advisor package (indicators, regime, levels, signals, risk, journal, MT5 client) |
| `main.py` | Entry point |
| `tests/smoke_test.py` | Offline pipeline check — `python tests/smoke_test.py` |

## Status / honesty notes

- The strategy logic is implemented per blueprint but **not yet validated by
  walk-forward backtest on real broker data** — run it in advise-only mode on
  demo and let the journal accumulate ≥100 closed signals before trusting it.
- Accuracy prior: 52–58% at blended ~1.6R (see blueprint §11/§12b for the
  improvement roadmap). Anything claiming more without out-of-sample evidence
  is overfit.
