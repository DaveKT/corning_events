"""Feed filtering, iCal emission and output validation.

Emission goes through the ``icalendar`` library rather than string building,
because the RFC 5545 details that must be right here (folding at 75 octets
without splitting a multi-byte character, escaping in TEXT values, CRLF
endings) fail silently when they are wrong: the subscription appears to work
and simply shows nothing.

Validation therefore runs on every emitted file before anything is written.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Sequence
from datetime import datetime, timedelta
from pathlib import Path

from icalendar import Calendar
from icalendar import Event as VEvent

from . import config, normalize
from .model import STATUS_CANCELLED, Event, PublishedEvent

REQUIRED_EVENT_PROPERTIES = ("UID", "DTSTAMP", "DTSTART", "SUMMARY")
MAX_LINE_OCTETS = 75


class FeedValidationError(Exception):
    """Raised when emitted iCal fails a structural check."""


# ---------------------------------------------------------------------------
# Field assembly
# ---------------------------------------------------------------------------


def event_url(event: Event) -> str | None:
    """Best link for a subscriber who wants details or tickets.

    The organizer's own page beats the aggregator's, because a subscriber
    looking at this on a phone wants the venue, not the middleman.
    """
    return event.original_url or event.ticket_url or event.source_url


def event_location(event: Event) -> str | None:
    parts = [part for part in (event.venue_name, event.address) if part]
    return ", ".join(parts) if parts else None


def attribution_line(event: Event) -> str:
    """Credit the originating source and link back to it.

    Required because the feeds redistribute other organizations' listings
    (spec section 9.4). Also practical: it is how a subscriber gets from a
    calendar entry to ticket details.
    """
    source = config.SOURCES.get(event.source_id)
    name = source.name if source else event.source_id
    link = event.source_url or event.original_url or (source.homepage if source else None)
    return f"Source: {name} - {link}" if link else f"Source: {name}"


def event_description(event: Event) -> str:
    body = (event.description or "").strip()
    attribution = attribution_line(event)
    return f"{body}\n\n{attribution}" if body else attribution


def content_hash(published: PublishedEvent) -> str:
    """Hash of everything that appears in the emitted VEVENT.

    Deliberately excludes DTSTAMP, which changes every run, and SEQUENCE,
    which is derived from this hash. Including either would make every event
    look modified on every run and bump SEQUENCE forever.
    """
    event = published.event
    payload = {
        "uid": published.uid,
        "status": published.status,
        "summary": event.title,
        "description": event_description(event),
        "start": event.start.isoformat(),
        "end": event.end.isoformat() if event.end else None,
        "all_day": event.all_day,
        "location": event_location(event),
        "url": event_url(event),
        "geo": [event.lat, event.lon] if event.has_coordinates else None,
        "categories": list(event.categories),
    }
    encoded = json.dumps(payload, sort_keys=True, ensure_ascii=False)
    return hashlib.sha1(encoded.encode("utf-8")).hexdigest()


def mint_uid(event: Event) -> str:
    """Derive a published UID from the record that first formed a cluster.

    Only ever called once per cluster. The result is persisted and reused for
    the life of the cluster, because a UID that changes between runs makes
    subscribers accumulate duplicates (build plan, Part 1 adjustment 6).
    """
    digest = hashlib.sha1(event.key.encode("utf-8")).hexdigest()
    return f"{digest}@{config.UID_DOMAIN}"


# ---------------------------------------------------------------------------
# Filtering
# ---------------------------------------------------------------------------


def select_for_feed(
    feed: config.FeedConfig,
    published: Iterable[PublishedEvent],
    now: datetime,
) -> list[PublishedEvent]:
    """Pick the events belonging in one feed, in start order.

    Volume control is mandatory rather than cosmetic: a subscribed calendar
    carrying every record from every source renders a phone calendar unusable
    (spec section 9.1).
    """
    earliest = now - timedelta(days=config.PAST_WINDOW_DAYS)
    latest = now + timedelta(days=config.HORIZON_DAYS)
    retention_cutoff = now - timedelta(days=config.CANCELLED_RETENTION_DAYS)

    selected = []
    for item in published:
        event = item.event
        if event.is_placeholder:
            continue
        if not earliest <= event.start <= latest:
            continue
        if item.status == STATUS_CANCELLED:
            # A cancelled event stays visible for a while so subscribers see
            # that it was cancelled rather than watching it silently vanish.
            if item.cancelled_at is not None and item.cancelled_at < retention_cutoff:
                continue
        if normalize.classify_ring(event) not in feed.rings:
            continue
        if feed.categories is not None and not (
            set(event.categories) & set(feed.categories)
        ):
            continue
        selected.append(item)

    selected.sort(key=lambda item: (item.event.start, item.uid))
    return selected


# ---------------------------------------------------------------------------
# Emission
# ---------------------------------------------------------------------------


def build_vevent(published: PublishedEvent, dtstamp: datetime) -> VEvent:
    event = published.event
    component = VEvent()
    component.add("uid", published.uid)
    component.add("dtstamp", dtstamp)

    if event.all_day:
        # VALUE=DATE with an exclusive DTEND. A single all-day event on the
        # 5th has DTEND of the 6th (spec section 9.2).
        component.add("dtstart", event.start.date())
        end = event.end or (event.start + timedelta(days=1))
        component.add("dtend", end.date())
    else:
        component.add("dtstart", event.start)
        if event.end is not None:
            component.add("dtend", event.end)

    component.add("summary", event.title)
    component.add("description", event_description(event))

    location = event_location(event)
    if location:
        component.add("location", location)

    url = event_url(event)
    if url:
        component.add("url", url)

    if event.has_coordinates:
        component.add("geo", (event.lat, event.lon))

    if event.categories:
        component.add("categories", list(event.categories))

    component.add("sequence", published.sequence)
    component.add("status", published.status)
    return component


def build_calendar(
    feed: config.FeedConfig,
    published: Sequence[PublishedEvent],
    dtstamp: datetime,
) -> Calendar:
    calendar = Calendar()
    calendar.add("version", "2.0")
    calendar.add("prodid", config.PRODID)
    calendar.add("calscale", "GREGORIAN")
    calendar.add("method", "PUBLISH")
    calendar.add("x-wr-calname", feed.calendar_name)
    calendar.add("x-wr-caldesc", feed.description)
    calendar.add("x-wr-timezone", config.LOCAL_TZ)
    # Apple Calendar honours REFRESH-INTERVAL and Microsoft clients honour
    # X-PUBLISHED-TTL. Emit both and trust neither: refresh cadence is the
    # client's decision, not the publisher's (spec section 9.3).
    calendar.add("refresh-interval", config.REFRESH_INTERVAL, parameters={"VALUE": "DURATION"})
    calendar.add("x-published-ttl", config.REFRESH_INTERVAL_ISO)

    for item in published:
        calendar.add_component(build_vevent(item, dtstamp))
    return calendar


def emit(
    feed: config.FeedConfig,
    published: Sequence[PublishedEvent],
    dtstamp: datetime,
) -> bytes:
    """Render a feed and validate it before it can reach a subscriber."""
    data = build_calendar(feed, published, dtstamp).to_ical()
    validate(data, expected_count=len(published))
    return data


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


def validate(
    data: bytes, expected_count: int, min_events: int | None = None
) -> None:
    """Structural checks on emitted iCal.

    Invalid iCal fails silently in calendar clients, so this runs on every
    file every time rather than only in tests.
    """
    if not isinstance(data, bytes):
        raise FeedValidationError("feed data must be bytes")

    if data.count(b"\n") != data.count(b"\r\n"):
        raise FeedValidationError("line endings must be CRLF throughout")

    for index, line in enumerate(data.split(b"\r\n"), start=1):
        if len(line) > MAX_LINE_OCTETS:
            raise FeedValidationError(
                f"line {index} is {len(line)} octets, over the {MAX_LINE_OCTETS} "
                "octet limit, so folding is broken"
            )

    try:
        calendar = Calendar.from_ical(data)
    except Exception as exc:
        raise FeedValidationError(f"emitted feed does not parse: {exc}") from exc

    if not calendar.get("prodid") or not calendar.get("version"):
        raise FeedValidationError("calendar is missing PRODID or VERSION")

    events = list(calendar.walk("VEVENT"))
    if len(events) != expected_count:
        raise FeedValidationError(
            f"expected {expected_count} events, reparsed {len(events)}"
        )

    seen: set[str] = set()
    for component in events:
        for prop in REQUIRED_EVENT_PROPERTIES:
            if prop not in component:
                raise FeedValidationError(f"a VEVENT is missing {prop}")
        uid = str(component["UID"])
        if uid in seen:
            raise FeedValidationError(f"duplicate UID in feed: {uid}")
        seen.add(uid)

    if min_events is not None and len(events) < min_events:
        raise FeedValidationError(
            f"only {len(events)} events, below the sanity floor of {min_events}. "
            "Refusing to overwrite a good feed with a broken one."
        )


def write(path: Path, data: bytes) -> None:
    """Write bytes verbatim, without newline translation."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
