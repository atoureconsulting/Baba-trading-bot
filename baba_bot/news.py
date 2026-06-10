"""News blackout windows (blueprint §4).

Events are loaded from a user-maintained JSON file until a calendar API is
wired in (the MT5 Python API does not expose the terminal's economic
calendar). Format, one object per event, times in UTC:

    [{"time": "2026-06-12 12:30", "name": "US CPI", "impact": "CPI",
      "currencies": ["USD"]}]
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

# minutes (before, after) per blueprint §4
BLACKOUTS = {
    "FOMC": (60, 60),
    "NFP": (30, 30),
    "CPI": (30, 30),
    "ECB": (15, 30),
    "BOJ": (15, 30),
    "DEFAULT": (30, 30),
}


class NewsCalendar:
    def __init__(self, path: str | Path = "news_events.json"):
        self.path = Path(path)
        self.events: list[dict] = []
        self.reload()

    def reload(self) -> None:
        if self.path.exists():
            self.events = json.loads(self.path.read_text())
        else:
            self.events = []

    def in_blackout(self, symbol: str, now_utc: datetime | None = None) -> str | None:
        """Return the blocking event name if `symbol` is inside a window."""
        now_utc = now_utc or datetime.now(timezone.utc)
        sym_ccys = {symbol[:3].upper(), symbol[3:6].upper()}
        if symbol.upper().startswith("XAU"):
            sym_ccys.add("USD")  # gold reacts to all USD events
        for ev in self.events:
            if not sym_ccys & {c.upper() for c in ev.get("currencies", [])}:
                continue
            before, after = BLACKOUTS.get(ev.get("impact", ""), BLACKOUTS["DEFAULT"])
            ev_time = datetime.strptime(ev["time"], "%Y-%m-%d %H:%M").replace(tzinfo=timezone.utc)
            if ev_time - timedelta(minutes=before) <= now_utc <= ev_time + timedelta(minutes=after):
                return ev.get("name", "scheduled event")
        return None
