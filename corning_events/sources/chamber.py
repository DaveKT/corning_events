"""Corning Area Chamber of Commerce.

ChamberMaster / GrowthZone. The broadest single Corning source, and the one
that most often lists a small business event no aggregator picked up.

The print rendering is the clean parse target, as spec section 5 says. Its
cards carry schema.org style `content` attributes holding ISO local
timestamps, so no date text has to be parsed out of prose.

Results are paginated ten to a page regardless of the date range requested,
which the spec does not mention. Pages are walked until one comes back empty
or repeats what has already been seen.

Only listing pages are fetched, never the sixty detail pages behind them.
The cards already carry a title, description, start, end and permalink, and
the detail pages would multiply this source's request count by sixty for a
venue name. The city is instead recovered from the card text where it names a
place the registry knows, which is enough to keep ring classification honest.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta

from bs4 import BeautifulSoup

from .. import config, normalize
from ..model import UTC, Event

LISTING_URL = "https://www.corningny.com/events"

SOURCE_ID = "chamber"

# Ten per page is the server's choice, not ours. This bounds the walk in case
# the pagination ever stops terminating.
MAX_PAGES = 20

# How far ahead to ask for. The chamber publishes a long tail of business
# events, and the horizon filter trims anything beyond the feed's range.
REQUEST_DAYS = 180


def fetch(http) -> list[Event]:
    today = date.today()
    until = today + timedelta(days=REQUEST_DAYS)

    pages: list[str] = []
    seen_links: set[str] = set()

    for page in range(1, MAX_PAGES + 1):
        html = http.text(
            LISTING_URL,
            params={
                "rendermode": "print",
                "from": f"{today:%m/%d/%Y}",
                "to": f"{until:%m/%d/%Y}",
                "page": page,
            },
        )
        links = _card_links(html)
        if not links or links <= seen_links:
            break
        seen_links |= links
        pages.append(html)

    return parse(pages)


def _card_links(html: str) -> set[str]:
    soup = BeautifulSoup(html, "lxml")
    return {
        anchor["href"]
        for card in soup.select("div.gz-events-card")
        if (anchor := card.select_one("h5 a")) is not None and anchor.get("href")
    }


def parse(pages: str | list[str], now: datetime | None = None) -> list[Event]:
    """Parse one or more listing pages into events."""
    if isinstance(pages, (str, bytes)):
        pages = [pages]

    now = now or datetime.now(UTC)
    earliest = now - timedelta(days=config.PAST_WINDOW_DAYS)
    latest = now + timedelta(days=config.HORIZON_DAYS)

    events: dict[str, Event] = {}
    for html in pages:
        for card in BeautifulSoup(html, "lxml").select("div.gz-events-card"):
            event = _parse_card(card)
            if event is None or not earliest <= event.start <= latest:
                continue
            # A page boundary can repeat a card; last one wins, they are equal.
            events[event.source_uid] = event
    return list(events.values())


def _parse_card(card) -> Event | None:
    anchor = card.select_one("h5 a")
    if anchor is None:
        return None

    title = anchor.get_text(strip=True)
    url = anchor.get("href") or None
    if not title or not url:
        return None

    start = _timestamp(card.select_one("li.gz-card-date span[content]"), "content")
    if start is None:
        return None
    end = _timestamp(card.select_one("li.gz-card-date meta[content]"), "content")

    description = card.select_one("p.gz-events-description")
    description = normalize.strip_html(description.get_text(" ", strip=True)) if description else None

    return Event(
        source_id=SOURCE_ID,
        # The permalink slug carries the chamber's own event id and date, so
        # it is stable across runs and unique per occurrence.
        source_uid=url.rstrip("/").rsplit("/", 1)[-1],
        title=title,
        start=start,
        end=end if end and end > start else None,
        description=description,
        city_tag=normalize.detect_city(f"{title} {description or ''}"),
        source_url=url,
        original_url=None,
    )


def _timestamp(element, attribute: str) -> datetime | None:
    """Read an ISO local timestamp out of a schema.org content attribute."""
    if element is None:
        return None
    raw = (element.get(attribute) or "").strip()
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        return None
    return normalize.to_utc(parsed)
