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
    p.add_argument("--symbols", nargs="+", default=["XAUUSD", "EURUSD"],
                   help="MT5 symbol names as shown in your Market Watch")
    p.add_argument("--timeframes", nargs="+", default=["M15", "H1"],
                   choices=["M1", "M5", "M15", "M30", "H1", "H4", "D1"])
    p.add_argument("--risk", type=float, default=0.01,
                   help="risk per signal as a fraction of balance (default 0.01)")
    return p.parse_args()


def main():
    args = parse_args()
    if args.risk > 0.02:
        raise SystemExit("Blueprint hard cap: risk per trade must be <= 0.02 (2%)")

    from baba_bot.advisor import Advisor
    from baba_bot.mt5_client import MT5Client

    client = MT5Client()
    try:
        Advisor(client, args.symbols, args.timeframes, args.risk).run()
    finally:
        client.shutdown()


if __name__ == "__main__":
    main()
