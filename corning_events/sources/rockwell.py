"""The Rockwell Museum, Corning.

WordPress with a custom post type rather than The Events Calendar, so there is
no iCal endpoint and the listing has to be parsed (spec section 5).

Everything needed is on the listing page. The detail pages were checked and
carry no structured event data at all: their JSON-LD describes a WebPage, not
an Event, and the date element present on the listing is absent there. So
fifteen extra requests per run would buy nothing.

The listing prints dates as "Tuesday, Aug 11 @ 10:00 am", with no year. The
year is inferred as the one that places the date inside the publication
horizon, which is unambiguous for a window shorter than a year.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta

from bs4 import BeautifulSoup

from .. import config, normalize
from ..model import UTC, Event

LISTING_URL = "https://rockwellmuseum.org/community-education/events/"

SOURCE_ID = "rockwell"

# The listing gives no location, and the museum programs offsite as well as
# in its own building: "18th Alley Art Project: Public Ribbon Cutting" happens
# in an alley downtown. Stamping the museum on every event asserted a venue
# that is sometimes wrong, and the wrong venue blocked a valid merge with the
# same event in FLXcalendar, which does carry the real location. The city is
# safe to assert, since the museum's programming is Corning based, and it is
# all the ring classifier needs.
VENUE_CITY = "Corning"

# "Tuesday, Aug 11 @ 10:00 am", with the weekday ignored: it is redundant and
# would only be another thing to get wrong.
_EVENT_DATE = re.compile(
    r"(?P<month>[A-Za-z]{3,9})\.?\s+(?P<day>\d{1,2})"
    r"(?:\s*@\s*(?P<hour>\d{1,2})(?::(?P<minute>\d{2}))?\s*(?P<meridiem>am|pm))?",
    re.IGNORECASE,
)

_MONTHS = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}


def fetch(http) -> list[Event]:
    return parse(http.text(LISTING_URL))


def parse(html: str | bytes, now: datetime | None = None) -> list[Event]:
    now = now or datetime.now(UTC)
    earliest = now - timedelta(days=config.PAST_WINDOW_DAYS)
    latest = now + timedelta(days=config.HORIZON_DAYS)

    events: dict[str, Event] = {}
    for card in BeautifulSoup(html, "lxml").select("div.event-card"):
        event = _parse_card(card, now, earliest, latest)
        if event is not None and earliest <= event.start <= latest:
            events[event.source_uid] = event
    return list(events.values())


def _parse_card(card, now, earliest, latest) -> Event | None:
    anchor = card.select_one("p a.event-anchor") or card.select_one("a.event-anchor")
    title_node = card.select_one("p.h4 a") or anchor
    if anchor is None or title_node is None:
        return None

    title = title_node.get_text(" ", strip=True)
    url = anchor.get("href") or None
    if not title or not url:
        return None

    dates = card.select_one("span.event-dates")
    start = _parse_date(dates.get_text(" ", strip=True) if dates else "", now, earliest, latest)
    if start is None:
        return None

    teaser = card.select_one("p.event-teaser-copy")

    return Event(
        source_id=SOURCE_ID,
        source_uid=url.rstrip("/").rsplit("/", 1)[-1],
        title=title,
        start=start,
        description=normalize.strip_html(teaser.get_text(" ", strip=True)) if teaser else None,
        city_tag=VENUE_CITY,
        source_url=url,
        # The museum publishes this itself, so its URL is the organizer's.
        original_url=url,
    )


def _parse_date(text: str, now: datetime, earliest: datetime, latest: datetime):
    """Turn "Tuesday, Aug 11 @ 10:00 am" into an instant.

    The listing omits the year. The correct one is whichever puts the date
    inside the publication window, which is unambiguous because the window is
    shorter than a year.
    """
    match = _EVENT_DATE.search(text)
    if not match:
        return None

    month = _MONTHS.get(match.group("month")[:3].lower())
    if not month:
        return None
    day = int(match.group("day"))

    hour = int(match.group("hour") or 0)
    minute = int(match.group("minute") or 0)
    meridiem = (match.group("meridiem") or "").lower()
    if meridiem == "pm" and hour != 12:
        hour += 12
    elif meridiem == "am" and hour == 12:
        hour = 0

    for year in (now.year, now.year + 1, now.year - 1):
        try:
            candidate = normalize.to_utc(datetime(year, month, day, hour, minute))
        except ValueError:
            continue  # 29 February in a common year
        if earliest <= candidate <= latest:
            return candidate
    return None
