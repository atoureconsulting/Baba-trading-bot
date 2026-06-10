"""MT5 terminal connection (Windows only — requires a running MT5 terminal).

The official `MetaTrader5` package is imported lazily so the rest of the
package (backtests, dry-run mode, unit tests) works on any OS.
"""

from __future__ import annotations

import pandas as pd

from .risk import SymbolSpec

TIMEFRAME_MAP = {
    "M1": "TIMEFRAME_M1", "M5": "TIMEFRAME_M5", "M15": "TIMEFRAME_M15",
    "M30": "TIMEFRAME_M30", "H1": "TIMEFRAME_H1", "H4": "TIMEFRAME_H4",
    "D1": "TIMEFRAME_D1",
}


class MT5Client:
    def __init__(self):
        import MetaTrader5 as mt5  # noqa: N816 — official package name
        self.mt5 = mt5
        if not mt5.initialize():
            raise ConnectionError(
                f"Could not attach to MT5 terminal: {mt5.last_error()}. "
                "Make sure MetaTrader 5 is running and 'Algo Trading' is enabled."
            )

    def shutdown(self) -> None:
        self.mt5.shutdown()

    def candles(self, symbol: str, timeframe: str, count: int = 500) -> pd.DataFrame:
        tf = getattr(self.mt5, TIMEFRAME_MAP[timeframe])
        rates = self.mt5.copy_rates_from_pos(symbol, tf, 0, count)
        if rates is None or len(rates) == 0:
            raise RuntimeError(f"No data for {symbol} {timeframe}: {self.mt5.last_error()}")
        df = pd.DataFrame(rates)
        df["time"] = pd.to_datetime(df["time"], unit="s", utc=True)
        df = df.set_index("time")
        # Drop the still-forming bar: we act on closed bars only (§8).
        return df.iloc[:-1]

    def spec(self, symbol: str, spread_points_limit: int = 0) -> SymbolSpec:
        info = self.mt5.symbol_info(symbol)
        if info is None:
            raise RuntimeError(f"Unknown symbol {symbol}")
        if not info.visible:
            self.mt5.symbol_select(symbol, True)
            info = self.mt5.symbol_info(symbol)
        return SymbolSpec(
            name=symbol,
            point=info.point,
            tick_size=info.trade_tick_size,
            tick_value=info.trade_tick_value,
            volume_min=info.volume_min,
            volume_max=info.volume_max,
            volume_step=info.volume_step,
            spread_points_limit=spread_points_limit,
        )

    def spread_points(self, symbol: str) -> int:
        info = self.mt5.symbol_info(symbol)
        return int(info.spread) if info else 0

    def account(self) -> tuple[float, float, str]:
        acc = self.mt5.account_info()
        if acc is None:
            raise RuntimeError("No account info — is the terminal logged in?")
        return acc.balance, acc.equity, acc.currency
