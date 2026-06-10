"""Live news feeds (blueprint §4 news gate, KB#11).

Two sources, both fetched with stdlib only (no extra dependencies):

1. ForexFactory economic calendar — official JSON feed
   (nfs.faireconomy.media, the feed ForexFactory publishes for apps/EAs;
   the forexfactory.com website itself is Cloudflare-protected and not
   reliably scrapable). Gives scheduled events with impact + currency →
   feeds the blackout windows automatically.

2. FXStreet news RSS — headline stream for situational awareness. Headlines
   are NOT a trading signal here; they are displayed, and scanned for
   sudden-risk keywords to warn the user (blueprint: we avoid news, we
   don't trade it).
"""

from __future__ import annotations

import json
import re
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone

FF_CALENDAR_URLS = [
    "https://nfs.faireconomy.media/ff_calendar_thisweek.json",
    "https://nfs.faireconomy.media/ff_calendar_nextweek.json",
]
FXSTREET_RSS_URL = "https://www.fxstreet.com/rss/news"
USER_AGENT = "Mozilla/5.0 (BabaTradingBot advisor; personal use)"

# Map ForexFactory event titles onto our blackout classes (news.BLACKOUTS)
_TITLE_CLASSES = [
    (re.compile(r"non.?farm|nfp", re.I), "NFP"),
    (re.compile(r"fomc|federal funds rate|fed (interest )?rate", re.I), "FOMC"),
    (re.compile(r"\bcpi\b|consumer price", re.I), "CPI"),
    (re.compile(r"\becb\b|main refinancing", re.I), "ECB"),
    (re.compile(r"\bboj\b|bank of japan", re.I), "BOJ"),
]

# Headline keywords that suggest unscheduled risk (geopolitics, intervention)
_RISK_WORDS = re.compile(
    r"emergency|intervention|war|strike[s]?\b|missile|escalat|sanction|"
    r"unscheduled|surprise (cut|hike)|flash crash|halt", re.I
)


def _get(url: str, timeout: int = 20) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


def classify_impact(title: str, ff_impact: str) -> str | None:
    """Return our blackout class for an event, or None if it shouldn't gate."""
    for pattern, cls in _TITLE_CLASSES:
        if pattern.search(title):
            return cls
    if ff_impact.lower() == "high":
        return "DEFAULT"
    return None  # medium/low impact events don't trigger blackouts


def fetch_forexfactory_calendar() -> list[dict]:
    """Fetch this + next week's calendar, normalized to news.py event format."""
    events: list[dict] = []
    errors: list[str] = []
    for url in FF_CALENDAR_URLS:
        try:
            raw = json.loads(_get(url))
        except Exception as exc:
            errors.append(f"{url}: {exc}")
            continue
        for item in raw:
            impact_class = classify_impact(item.get("title", ""), item.get("impact", ""))
            if impact_class is None:
                continue
            try:
                when = datetime.fromisoformat(item["date"])
            except (KeyError, ValueError):
                continue
            events.append({
                "time": when.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M"),
                "name": item.get("title", "event"),
                "impact": impact_class,
                "currencies": [item.get("country", "").upper()],
                "source": "forexfactory",
            })
    if not events and errors:
        raise ConnectionError("ForexFactory calendar unreachable: " + "; ".join(errors))
    return events


def fetch_fxstreet_headlines(limit: int = 15) -> list[dict]:
    """Latest FXStreet headlines: [{title, link, published, risk_flag}]."""
    root = ET.fromstring(_get(FXSTREET_RSS_URL))
    out = []
    for item in root.iter("item"):
        title = (item.findtext("title") or "").strip()
        if not title:
            continue
        out.append({
            "title": title,
            "link": (item.findtext("link") or "").strip(),
            "published": (item.findtext("pubDate") or "").strip(),
            "risk_flag": bool(_RISK_WORDS.search(title)),
        })
        if len(out) >= limit:
            break
    return out
