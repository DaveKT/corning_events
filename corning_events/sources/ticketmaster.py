"""Ticketmaster Discovery API.

The only source in the registry with a documented geo-radius search, so it is
queried directly around the anchor rather than filtered afterwards. Covers
ticketed concerts and touring shows only. Expect low volume for this market
but high data quality (spec section 3.4).

Requires a free API key in ``TICKETMASTER_API_KEY``. Without one the source
skips with a warning rather than failing, so the rest of the pipeline runs
normally.

Unverified against a live response. No API key was available when this was
written, so the parser follows the documented Discovery API v2 schema and is
tested against a fixture built from that schema rather than from a real
capture. Re-check it against real output the first time a key is present:
every field access is defensive, so the likely failure is silently empty
results rather than an exception.
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta

from .. import config, normalize
from ..model import UTC, Event

API_URL = "https://app.ticketmaster.com/discovery/v2/events.json"

SOURCE_ID = "ticketmaster"
API_KEY_ENV = "TICKETMASTER_API_KEY"

# The API caps page size at 200 and refuses deep paging beyond 1000 results.
PAGE_SIZE = 200
MAX_PAGES = 5


class MissingApiKey(Exception):
    """Raised when no key is configured, so main.py can skip rather than fail."""


def fetch(http) -> list[Event]:
    key = os.environ.get(API_KEY_ENV, "").strip()
    if not key:
        raise MissingApiKey(
            f"{API_KEY_ENV} is not set, so the Ticketmaster source is skipped. "
            "A free key is issued immediately at developer.ticketmaster.com."
        )

    payloads = []
    for page in range(MAX_PAGES):
        payload = http.json(
            API_URL,
            params={
                "apikey": key,
                "latlong": f"{config.ANCHOR_LAT},{config.ANCHOR_LON}",
                "radius": int(config.MAX_RADIUS_MILES),
                "unit": "miles",
                "size": PAGE_SIZE,
                "page": page,
                "sort": "date,asc",
            },
        )
        payloads.append(payload)
        if page + 1 >= (payload.get("page", {}).get("totalPages") or 1):
            break

    events: list[Event] = []
    for payload in payloads:
        events.extend(parse(payload))
    return events


def parse(payload: dict, now: datetime | None = None) -> list[Event]:
    """Map one Discovery API page onto Events."""
    now = now or datetime.now(UTC)
    earliest = now - timedelta(days=config.PAST_WINDOW_DAYS)
    latest = now + timedelta(days=config.HORIZON_DAYS)

    records = (payload.get("_embedded") or {}).get("events") or []
    events = []
    for record in records:
        event = _parse_record(record)
        if event is not None and earliest <= event.start <= latest:
            events.append(event)
    return events


def _parse_record(record: dict) -> Event | None:
    uid = record.get("id")
    title = (record.get("name") or "").strip()
    if not uid or not title:
        return None

    start, all_day = _start(record)
    if start is None:
        return None

    venue = (record.get("_embedded") or {}).get("venues") or [{}]
    venue = venue[0] if venue else {}
    latitude, longitude = _coordinates(venue)

    return Event(
        source_id=SOURCE_ID,
        source_uid=str(uid),
        title=title,
        start=start,
        all_day=all_day,
        description=normalize.strip_html((record.get("info") or "").strip() or None),
        venue_name=(venue.get("name") or "").strip() or None,
        address=_address(venue),
        lat=latitude,
        lon=longitude,
        city_tag=((venue.get("city") or {}).get("name") or "").strip() or None,
        categories=normalize.canonical_categories(_categories(record)),
        cost=_price(record),
        # Ticketmaster is both the ticket vendor and the listing, so its URL
        # serves as the ticket link. It is not the organizer's own page, so
        # original_url stays empty.
        ticket_url=record.get("url"),
        source_url=record.get("url"),
    )


def _start(record: dict) -> tuple[datetime | None, bool]:
    dates = (record.get("dates") or {}).get("start") or {}

    absolute = dates.get("dateTime")
    if absolute:
        try:
            return datetime.fromisoformat(absolute.replace("Z", "+00:00")).astimezone(UTC), False
        except ValueError:
            pass

    # An event with a date but no announced time. Publishing it as all-day is
    # honest: a made up start time would be worse than none.
    local_date = dates.get("localDate")
    if local_date:
        try:
            parsed = datetime.fromisoformat(local_date)
        except ValueError:
            return None, False
        return parsed.replace(tzinfo=UTC), True

    return None, False


def _coordinates(venue: dict) -> tuple[float | None, float | None]:
    location = venue.get("location") or {}
    try:
        return float(location["latitude"]), float(location["longitude"])
    except (KeyError, TypeError, ValueError):
        return None, None


def _address(venue: dict) -> str | None:
    parts = [
        (venue.get("address") or {}).get("line1"),
        (venue.get("city") or {}).get("name"),
        (venue.get("state") or {}).get("stateCode"),
        (venue.get("postalCode") or ""),
    ]
    joined = ", ".join(str(part).strip() for part in parts if str(part or "").strip())
    return joined or None


def _categories(record: dict) -> list[str]:
    """Flatten the classification tree into candidate category labels.

    Ticketmaster's own vocabulary is coarse (segment, genre, subgenre), so the
    labels are handed to the normalizer, which keeps whatever maps onto the
    canonical set and drops the rest.
    """
    labels = []
    for classification in record.get("classifications") or []:
        for level in ("segment", "genre", "subGenre"):
            name = (classification.get(level) or {}).get("name")
            if name and name not in ("Undefined", "Other"):
                labels.append(name)
    return labels


def _price(record: dict) -> str | None:
    ranges = record.get("priceRanges") or []
    if not ranges:
        return None
    band = ranges[0]
    low, high = band.get("min"), band.get("max")
    currency = band.get("currency") or "USD"
    symbol = "$" if currency == "USD" else f"{currency} "
    if low is None and high is None:
        return None
    if low == high or high is None:
        return f"{symbol}{low:g}"
    if low is None:
        return f"up to {symbol}{high:g}"
    return f"{symbol}{low:g} to {symbol}{high:g}"
