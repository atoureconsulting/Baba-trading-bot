"""Run the bot battle: every auditable pool strategy on YOUR MT5 data.

    py bot_battle.py                          # 4 focus pairs, M15, 3 years
    py bot_battle.py --symbols GOLD --years 2
"""

from __future__ import annotations

import argparse

BARS_PER_DAY = {"M5": 288, "M15": 96, "M30": 48, "H1": 24, "H4": 6}


def main():
    p = argparse.ArgumentParser(description="Head-to-head strategy battle on MT5 history")
    p.add_argument("--symbols", nargs="+", default=["GOLD", "GBPUSD", "USDJPY", "EURUSD"])
    p.add_argument("--timeframe", default="M15", choices=list(BARS_PER_DAY))
    p.add_argument("--years", type=float, default=3.0)
    p.add_argument("--spread-points", type=int, default=None)
    args = p.parse_args()

    from baba_bot.battle import print_battle, run_battle
    from baba_bot.mt5_client import MT5Client

    client = MT5Client()
    try:
        for symbol in args.symbols:
            spec = client.spec(symbol)
            spread_pts = args.spread_points or client.spread_points(symbol)
            n = int(args.years * 365 * BARS_PER_DAY[args.timeframe])
            print(f"\n############ {symbol} {args.timeframe} | {args.years}y | "
                  f"spread {spread_pts} pts ############")
            try:
                df = client.candles(symbol, args.timeframe, n)
            except RuntimeError as exc:
                print(f"  data unavailable: {exc}")
                continue
            print(f"  got {len(df)} bars (~{(df.index[-1]-df.index[0]).days/365:.1f} years)")
            print_battle(symbol, run_battle(symbol, df, spec.point, spread_pts * spec.point))
        print("\nFor comparison, Baba bot (KB#13 walk-forward, M15 OOS): "
              "USDJPY +0.14R/PF1.23, GBPUSD +0.03R, GOLD -0.03R, EURUSD -0.10R.")
        print("Reminder: author-default parameters, no tuning; pips = 10 points; "
              "SL-first on ambiguous bars; news gate not applied to any contender.")
    finally:
        client.shutdown()


if __name__ == "__main__":
    main()
