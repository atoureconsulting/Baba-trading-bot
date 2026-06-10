"""Backtest the live signal engine on MT5 history.

Run on the Windows PC with MT5 open:

    py backtest.py --symbols GOLD GBPUSD USDJPY EURUSD --timeframe M15 --years 3

Outputs per symbol: in-sample stats, per-regime breakdown, walk-forward
out-of-sample stats + recommended confidence threshold, and a trades CSV
(backtest_trades_<SYMBOL>_<TF>.csv) for deeper analysis/calibration.
"""

from __future__ import annotations

import argparse

import pandas as pd

BARS_PER_DAY = {"M5": 288, "M15": 96, "M30": 48, "H1": 24, "H4": 6}


def parse_args():
    p = argparse.ArgumentParser(description="Walk-forward backtest on MT5 history")
    p.add_argument("--symbols", nargs="+", default=["GOLD", "GBPUSD", "USDJPY", "EURUSD"])
    p.add_argument("--timeframe", default="M15", choices=list(BARS_PER_DAY))
    p.add_argument("--years", type=float, default=3.0)
    p.add_argument("--spread-points", type=int, default=None,
                   help="override spread in points (default: current live spread)")
    p.add_argument("--skip-inside-bar", action="store_true")
    return p.parse_args()


def main():
    args = parse_args()
    from baba_bot.backtest import (print_report, run_backtest,
                                   run_inside_bar_backtest, walk_forward_threshold)
    from baba_bot.mt5_client import MT5Client

    client = MT5Client()
    try:
        for symbol in args.symbols:
            spec = client.spec(symbol)
            spread_pts = args.spread_points or client.spread_points(symbol)
            spread_price = spread_pts * spec.point
            n_trig = int(args.years * 365 * BARS_PER_DAY[args.timeframe])
            n_h4 = int(args.years * 365 * BARS_PER_DAY["H4"]) + 400

            print(f"\n############ {symbol} {args.timeframe} | {args.years}y | "
                  f"spread {spread_pts} pts ############")
            try:
                df_trig = client.candles(symbol, args.timeframe, n_trig)
                df_4h = client.candles(symbol, "H4", n_h4)
            except RuntimeError as exc:
                print(f"  data unavailable: {exc}")
                continue
            got_days = (df_trig.index[-1] - df_trig.index[0]).days
            print(f"  got {len(df_trig)} {args.timeframe} bars (~{got_days/365:.1f} years), "
                  f"{len(df_4h)} H4 bars")
            if got_days < 180:
                print("  WARNING: <6 months of history — broker limits bar download; "
                      "results will be statistically weak. In MT5: Tools > Options > "
                      "Charts > Max bars in chart = Unlimited, then reopen charts.")

            report = run_backtest(symbol, args.timeframe, df_trig, df_4h, spec, spread_price)
            wf = walk_forward_threshold(report)
            print_report(report, wf)
            if report.trades:
                out = f"backtest_trades_{symbol}_{args.timeframe}.csv"
                pd.DataFrame([vars(t) for t in report.trades]).to_csv(out, index=False)
                print(f"  trades saved to {out}")

            if not args.skip_inside_bar:
                ib = run_inside_bar_backtest(symbol, df_4h, spec, spread_price)
                print_report(ib)
    finally:
        client.shutdown()


if __name__ == "__main__":
    main()
