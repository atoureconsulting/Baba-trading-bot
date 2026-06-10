"""Baba Trading Bot — MT5 signal advisor entry point.

Usage (on the Windows machine running MetaTrader 5):

    pip install -r requirements.txt
    python main.py --symbols XAUUSD EURUSD --timeframes M15 H1 --risk 0.01

The advisor never places orders. It prints ENTER advice (entry, stop,
target, lot size, confidence, reasons) on new closed bars, and manage/EXIT
alerts when a previously advised trade's stop or target is reached.
"""

from __future__ import annotations

import argparse


def parse_args():
    p = argparse.ArgumentParser(description="MT5 signal advisor")
    # Defaults per KB#16 tournament: GBPUSD dropped (nothing worked in 16y);
    # H1 decision timeframe (spread drag at M15 kills thin edges)
    p.add_argument("--symbols", nargs="+", default=["GOLD", "USDJPY", "EURUSD"],
                   help="MT5 symbol names as shown in your Market Watch "
                        "(gold is GOLD on Admirals, XAUUSD on many others)")
    p.add_argument("--timeframes", nargs="+", default=["H1"],
                   choices=["M1", "M5", "M15", "M30", "H1", "H4", "D1"])
    p.add_argument("--risk", type=float, default=0.01,
                   help="risk per signal as a fraction of balance (default 0.01)")
    p.add_argument("--check-news", action="store_true",
                   help="fetch ForexFactory calendar + FXStreet headlines and exit "
                        "(verifies news feeds work on this machine; no MT5 needed)")
    p.add_argument("--inside-bar", action="store_true",
                   help="re-enable 4H inside-bar advice (off by default pending "
                        "M15-granularity retest — see blueprint KB#13)")
    return p.parse_args()


def check_news() -> None:
    from baba_bot.news import NewsCalendar
    from baba_bot.newsfeed import fetch_fxstreet_headlines

    cal = NewsCalendar()
    print(cal.refresh_auto(force=True))
    for ev in cal.auto_events[:12]:
        print(f"  {ev['time']} UTC  [{ev['currencies'][0]}] {ev['name']} ({ev['impact']} window)")
    if len(cal.auto_events) > 12:
        print(f"  ... and {len(cal.auto_events) - 12} more")
    print()
    try:
        for h in fetch_fxstreet_headlines(limit=10):
            flag = "  !! RISK" if h["risk_flag"] else ""
            print(f"  - {h['title']}{flag}")
    except Exception as exc:
        print(f"FXStreet headlines unavailable: {exc}")


def main():
    args = parse_args()
    if args.check_news:
        check_news()
        return
    if args.risk > 0.02:
        raise SystemExit("Blueprint hard cap: risk per trade must be <= 0.02 (2%)")

    from baba_bot.advisor import Advisor
    from baba_bot.mt5_client import MT5Client

    client = MT5Client()
    try:
        Advisor(client, args.symbols, args.timeframes, args.risk,
                enable_inside_bar=args.inside_bar).run()
    finally:
        client.shutdown()


if __name__ == "__main__":
    main()
