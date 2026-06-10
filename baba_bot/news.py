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

# NOTE: docstring above describes the manual file; live ForexFactory events
# are merged in automatically via refresh_auto() (see newsfeed.py).

# minutes (before, after) per blueprint §4
BLACKOUTS = {
    "FOMC": (60, 60),
    "NFP": (30, 30),
    "CPI": (30, 30),
    "ECB": (15, 30),
    "BOJ": (15, 30),
    "DEFAULT": (30, 30),
}


REFRESH_HOURS = 4


class NewsCalendar:
    """Blackout calendar = manual events (news_events.json) + live
    ForexFactory events (auto-fetched, cached in news_events_auto.json)."""

    def __init__(self, path: str | Path = "news_events.json",
                 auto_path: str | Path = "news_events_auto.json"):
        self.path = Path(path)
        self.auto_path = Path(auto_path)
        self.events: list[dict] = []
        self.auto_events: list[dict] = []
        self.last_fetch: datetime | None = None
        self.reload()

    def reload(self) -> None:
        self.events = json.loads(self.path.read_text()) if self.path.exists() else []
        if self.auto_path.exists():
            cached = json.loads(self.auto_path.read_text())
            self.auto_events = cached.get("events", [])
            ts = cached.get("fetched_at")
            self.last_fetch = datetime.fromisoformat(ts) if ts else None

    def refresh_auto(self, force: bool = False) -> str:
        """Pull the live ForexFactory calendar if the cache is stale."""
        now = datetime.now(timezone.utc)
        if not force and self.last_fetch and (now - self.last_fetch) < timedelta(hours=REFRESH_HOURS):
            return f"calendar fresh ({len(self.auto_events)} gating events cached)"
        from .newsfeed import fetch_forexfactory_calendar
        try:
            self.auto_events = fetch_forexfactory_calendar()
            self.last_fetch = now
            self.auto_path.write_text(json.dumps(
                {"fetched_at": now.isoformat(), "events": self.auto_events}, indent=2))
            return f"ForexFactory calendar updated: {len(self.auto_events)} gating events this+next week"
        except Exception as exc:
            stale = " (using stale cache)" if self.auto_events else " (NO calendar data — gate is manual-only!)"
            return f"calendar fetch failed: {exc}{stale}"

    def in_blackout(self, symbol: str, now_utc: datetime | None = None) -> str | None:
        """Return the blocking event name if `symbol` is inside a window."""
        now_utc = now_utc or datetime.now(timezone.utc)
        sym_ccys = {symbol[:3].upper(), symbol[3:6].upper()}
        if symbol.upper().startswith("XAU"):
            sym_ccys.add("USD")  # gold reacts to all USD events
        for ev in self.events + self.auto_events:
            if not sym_ccys & {c.upper() for c in ev.get("currencies", [])}:
                continue
            before, after = BLACKOUTS.get(ev.get("impact", ""), BLACKOUTS["DEFAULT"])
            ev_time = datetime.strptime(ev["time"], "%Y-%m-%d %H:%M").replace(tzinfo=timezone.utc)
            if ev_time - timedelta(minutes=before) <= now_utc <= ev_time + timedelta(minutes=after):
                return ev.get("name", "scheduled event")
        return None
