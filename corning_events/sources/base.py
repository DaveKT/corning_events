"""The contract every source module implements.

A source module exposes a single function:

    def fetch(session) -> list[Event]

It receives the shared requests session from http.py, which already carries the
User-Agent, timeout and retry policy, and returns a list of Event records. It
does no persistence, no deduplication and no filtering; those belong to later
stages of the pipeline.

Four rules apply to every source:

1. source_uid must be stable across runs. Prefer the identifier the source
   supplies. Where a source supplies none, derive one from the event detail
   URL slug, adding the occurrence date for anything recurring. An unstable
   source_uid breaks cluster pinning and makes subscribers see duplicates.
2. Recurring events are expanded into one Event per occurrence at parse time.
3. Raise on failure rather than returning an empty list. main.py distinguishes
   the two, and an empty list from a broken source would look like every event
   being cancelled at once (build plan, Part 1 adjustment 3).
4. Times are converted to UTC before returning, using normalize.to_utc.
5. Descriptions are passed through normalize.strip_html before returning.
   Nothing downstream can safely do this for you: only the source knows
   whether its description field holds HTML, and running the stripper over
   text that merely contains a "<" would silently eat the rest of the
   sentence. Unstripped HTML reaches subscribers as visible markup.

Shared helpers land here as the parsers reveal what they have in common.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta

from dateutil.rrule import rrulestr
from icalendar import Calendar

from .. import config, normalize
from ..model import UTC, Event


def parse_ics(
    raw: bytes,
    source_id: str,
    now: datetime | None = None,
    url_is_canonical: bool = False,
) -> list[Event]:
    """Parse an iCalendar feed into one Event per occurrence.

    Written against The Events Calendar, the WordPress plugin behind both
    ssclibrary and clemenscenter, but it assumes nothing plugin specific and
    should serve any well formed .ics feed.

    ``url_is_canonical`` marks a feed published by the venue itself, where the
    event URL is the organizer's own page rather than an aggregator's. That
    populates ``original_url``, which is the strongest dedupe signal available
    when another source lists the same event and links back to the venue.
    """
    now = now or datetime.now(UTC)
    earliest = now - timedelta(days=config.PAST_WINDOW_DAYS)
    latest = now + timedelta(days=config.HORIZON_DAYS)

    calendar = Calendar.from_ical(raw)
    events: list[Event] = []

    for component in calendar.walk("VEVENT"):
        events.extend(
            _parse_component(component, source_id, earliest, latest, url_is_canonical)
        )
    return events


def _parse_component(
    component,
    source_id: str,
    earliest: datetime,
    latest: datetime,
    url_is_canonical: bool,
) -> list[Event]:
    uid = _string(component, "UID")
    title = _string(component, "SUMMARY")
    if not uid or not title:
        return []

    start_property = component.get("DTSTART")
    if start_property is None:
        return []
    start, all_day = _moment(start_property.dt)

    end_property = component.get("DTEND")
    duration = None
    if end_property is not None:
        end, _ = _moment(end_property.dt)
        if end > start:
            duration = end - start

    url = _string(component, "URL")
    venue, address = normalize.split_location(_string(component, "LOCATION"))
    latitude, longitude = _geo(component)

    shared = dict(
        description=_truncate(normalize.strip_html(_string(component, "DESCRIPTION"))),
        venue_name=venue,
        address=address,
        lat=latitude,
        lon=longitude,
        categories=normalize.canonical_categories(_categories(component)),
        source_url=url,
        original_url=url if url_is_canonical else None,
    )

    starts = _occurrences(component, start, earliest, latest)
    recurring = len(starts) > 1
    events: list[Event] = []
    for occurrence in sorted(starts):
        if not earliest <= occurrence <= latest:
            continue
        events.append(
            Event(
                source_id=source_id,
                source_uid=f"{uid}:{occurrence:%Y%m%dT%H%M%S}" if recurring else uid,
                title=title,
                start=occurrence,
                end=occurrence + duration if duration else None,
                all_day=all_day,
                recurrence_parent_id=uid if recurring else None,
                **shared,
            )
        )
    return events


def _occurrences(component, start, earliest, latest) -> set[datetime]:
    starts = {start}

    rule = component.get("RRULE")
    if rule is not None:
        try:
            expanded = rrulestr(
                "RRULE:" + rule.to_ical().decode("utf-8"), dtstart=start
            )
            starts.update(expanded.between(earliest, latest, inc=True))
        except (ValueError, TypeError):
            pass

    for name, action in (("RDATE", starts.add), ("EXDATE", starts.discard)):
        entries = component.get(name)
        if entries is None:
            continue
        for entry in entries if isinstance(entries, list) else [entries]:
            for item in getattr(entry, "dts", []):
                action(_moment(item.dt)[0])

    return starts


def _moment(value) -> tuple[datetime, bool]:
    """Normalize a date or datetime to UTC, flagging all-day values."""
    if isinstance(value, datetime):
        return normalize.to_utc(value), False
    if isinstance(value, date):
        return datetime(value.year, value.month, value.day, tzinfo=UTC), True
    raise TypeError(f"unsupported temporal value {value!r}")


def _string(component, name: str) -> str | None:
    value = component.get(name)
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _categories(component):
    value = component.get("CATEGORIES")
    if value is None:
        return None
    items = getattr(value, "cats", None)
    return [str(item) for item in items] if items else str(value)


def _geo(component) -> tuple[float | None, float | None]:
    geo = component.get("GEO")
    if geo is None:
        return None, None
    try:
        return float(geo.latitude), float(geo.longitude)
    except (AttributeError, TypeError, ValueError):
        return None, None


def _truncate(text: str | None) -> str | None:
    """Cap a description at a word boundary.

    Venue feeds pad every event with box office hours, share links and
    visitor information. The event URL is always emitted, so a subscriber who
    wants the rest is one tap away.
    """
    if text is None or len(text) <= config.MAX_DESCRIPTION_CHARS:
        return text
    clipped = text[: config.MAX_DESCRIPTION_CHARS]
    boundary = clipped.rfind(" ")
    if boundary > config.MAX_DESCRIPTION_CHARS // 2:
        clipped = clipped[:boundary]
    return clipped.rstrip(" ,.;:-") + " ..."
