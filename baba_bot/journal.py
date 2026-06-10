"""Signal journal (§12/§12b) — every advice is recorded, then resolved
against later prices. This file is the calibration dataset that turns the
accuracy prior into a measured number.
"""

from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path

import pandas as pd

from .signals import SignalAdvice


class SignalJournal:
    def __init__(self, path: str | Path = "signals_journal.json"):
        self.path = Path(path)
        self.entries: list[dict] = []
        if self.path.exists():
            self.entries = json.loads(self.path.read_text())

    def _save(self) -> None:
        self.path.write_text(json.dumps(self.entries, indent=2, default=str))

    def record(self, advice: SignalAdvice) -> None:
        entry = asdict(advice)
        entry["status"] = "open"
        entry["outcome"] = None
        self.entries.append(entry)
        self._save()

    def resolve_open(self, symbol: str, df: pd.DataFrame) -> list[str]:
        """Check open signals against recent bars; mark TP/SL hits.

        Conservative rule: if one bar spans both SL and TP, count it as SL
        (we can't know intra-bar order without ticks).
        """
        alerts = []
        for e in self.entries:
            if e["symbol"] != symbol or e["status"] != "open":
                continue
            after = df[df.index > pd.Timestamp(e["time"].replace(" UTC", ""), tz="UTC")] \
                if e.get("time") else df
            if after.empty:
                continue
            sl, tp, direction = e["risk"]["stop_loss"], e["risk"]["take_profit"], e["direction"]
            for ts, bar in after.iterrows():
                hit_sl = bar["low"] <= sl if direction > 0 else bar["high"] >= sl
                hit_tp = bar["high"] >= tp if direction > 0 else bar["low"] <= tp
                if hit_sl:  # conservative: SL first on ambiguous bars
                    e["status"], e["outcome"] = "closed", "stop_loss"
                    alerts.append(f"{symbol}: earlier {e['timeframe']} signal STOPPED OUT at {sl}")
                    break
                if hit_tp:
                    e["status"], e["outcome"] = "closed", "take_profit"
                    alerts.append(f"{symbol}: earlier {e['timeframe']} signal HIT TARGET at {tp}")
                    break
        if alerts:
            self._save()
        return alerts

    def stats(self) -> dict:
        closed = [e for e in self.entries if e["status"] == "closed"]
        wins = sum(1 for e in closed if e["outcome"] == "take_profit")
        return {
            "signals_emitted": len(self.entries),
            "closed": len(closed),
            "wins": wins,
            "measured_win_rate": round(wins / len(closed), 3) if closed else None,
        }
