"""Advisor loop — polls MT5 for new closed bars and prints ENTER/EXIT advice."""

from __future__ import annotations

import time
from datetime import datetime, timezone

import pandas as pd

from .journal import SignalJournal
from .levels import prior_day_profile
from .news import NewsCalendar
from .risk import CircuitBreaker
from .signals import evaluate, inside_bar_signal

POLL_SECONDS = 10


class Advisor:
    def __init__(self, client, symbols: list[str], timeframes: list[str],
                 risk_pct: float = 0.01, spread_limits: dict[str, int] | None = None):
        self.client = client
        self.symbols = symbols
        self.timeframes = timeframes
        self.risk_pct = risk_pct
        self.spread_limits = spread_limits or {}
        self.news = NewsCalendar()
        self.journal = SignalJournal()
        self.breaker = CircuitBreaker()
        self._last_bar: dict[tuple[str, str], pd.Timestamp] = {}
        self._last_day: datetime | None = None

    def run(self) -> None:
        print(f"Baba advisor watching {self.symbols} on {self.timeframes} "
              f"(risk {self.risk_pct:.1%}/signal). Ctrl-C to stop.")
        while True:
            try:
                self.tick()
            except KeyboardInterrupt:
                print("Advisor stopped.")
                return
            except Exception as exc:  # keep advising through transient feed errors
                print(f"[warn] {exc}")
            time.sleep(POLL_SECONDS)

    def tick(self) -> None:
        now = datetime.now(timezone.utc)
        balance, equity, _ = self.client.account()
        halt = self.breaker.update(equity, new_day=self._last_day != now.date())
        self._last_day = now.date()
        if halt:
            print(f"[circuit breaker] {halt}")
            return

        for symbol in self.symbols:
            spec = self.client.spec(symbol, self.spread_limits.get(symbol, 0))
            df_4h = self.client.candles(symbol, "H4", 400)
            blackout = self.news.in_blackout(symbol, now)

            # 4H inside-bar pending-order advice (once per new 4H bar)
            if self._is_new_bar(symbol, "H4", df_4h.index[-1]) and not blackout:
                ib = inside_bar_signal(symbol, df_4h, spec, balance, self.risk_pct)
                if ib:
                    print("\n" + ib.describe() + "\n")
                    self.journal.record(ib)

            for tf in self.timeframes:
                df = self.client.candles(symbol, tf, 500)
                if not self._is_new_bar(symbol, tf, df.index[-1]):
                    continue
                for alert in self.journal.resolve_open(symbol, df.iloc[-3:]):
                    print(f"[manage] {alert}")
                profile = prior_day_profile(df, step=10 * spec.point)
                advice, status = evaluate(
                    symbol, tf, df, df_4h, spec, balance, self.risk_pct,
                    news_blackout_reason=blackout, profile=profile, now_utc=now,
                    spread_points=self.client.spread_points(symbol),
                )
                if advice:
                    print("\n" + advice.describe() + "\n")
                    self.journal.record(advice)
                else:
                    print(f"[{now:%H:%M}] {status}")

    def _is_new_bar(self, symbol: str, tf: str, latest: pd.Timestamp) -> bool:
        key = (symbol, tf)
        if self._last_bar.get(key) == latest:
            return False
        self._last_bar[key] = latest
        return True
