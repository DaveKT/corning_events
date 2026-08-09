"""FLXcalendar, the highest volume source in the registry.

A Timely calendar exported as RFC 6321 xCal. The XML form is preferred over
the ``.ics`` form because it parses with the standard library (spec section
4.6). The export is around 9.5 MB and growing, so it is streamed with
``iterparse`` and each record is cleared as it is consumed.

Property values are wrapped in a type element, so ``dtstart`` contains a
``date-time`` or ``date`` child, ``summary`` contains ``text``, ``geo``
contains ``latitude`` and ``longitude``, and X- properties wrap their value in
``unknown``.

Three things differ from what the spec recorded on 2026-07-24 and were
measured against a fresh export on 2026-08-09:

``x-original-url`` is empty on every one of the 1503 records. The spec called
it the best dedupe signal, at 100 percent coverage. It is present as an empty
element and carries nothing, so it is parsed to None and the dedupe cascade
must lean on its title, venue and time rules instead.

``rrule`` is structured XML, not an RRULE string. Its children are ``freq``,
``until``, ``byday`` and ``wkst``, which have to be reassembled before
dateutil can expand them.

Roughly 43 records are all-day, carrying ``date`` rather than ``date-time``.
The spec's property table did not mention all-day events at all.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from dateutil.rrule import rrulestr

from .. import config, normalize
from ..model import UTC, Event

EXPORT_URL = "https://timelyapp.time.ly/api/calendars/48240494/export?format=xml"

NS = "urn:ietf:params:xml:ns:icalendar-2.0"
Q = f"{{{NS}}}"

SOURCE_ID = "flxcalendar"

# County tags read "Steuben County"; everything else is treated as a city.
_COUNTY_SUFFIX = "county"


def fetch(http) -> list[Event]:
    """Download and parse the export into one Event per occurrence."""
    raw = http.bytes(EXPORT_URL)
    return parse(raw)


def parse(raw: bytes, now: datetime | None = None) -> list[Event]:
    """Parse an xCal export.

    Occurrences outside the window from ``PAST_WINDOW_DAYS`` ago to
    ``HORIZON_DAYS`` ahead are discarded here rather than stored. This settles
    spec section 15 question 1 in favour of discarding: the service publishes
    a calendar, there is no analytics use for the roughly 1,200 past records
    in each export, and keeping them would bloat a database that is committed
    to the repository on every run.
    """
    now = now or datetime.now(UTC)
    earliest = now - timedelta(days=config.PAST_WINDOW_DAYS)
    latest = now + timedelta(days=config.HORIZON_DAYS)

    events: list[Event] = []
    source = _ByteSource(raw)
    for _, element in ET.iterparse(source, events=("end",)):
        if element.tag != f"{Q}vevent":
            continue
        events.extend(_parse_vevent(element, earliest, latest))
        element.clear()
    return events


class _ByteSource:
    """Minimal file-like wrapper so iterparse can stream from bytes."""

    def __init__(self, data: bytes) -> None:
        self._data = data
        self._pos = 0

    def read(self, size: int = -1) -> bytes:
        if size < 0:
            chunk = self._data[self._pos :]
            self._pos = len(self._data)
            return chunk
        chunk = self._data[self._pos : self._pos + size]
        self._pos += len(chunk)
        return chunk


# ---------------------------------------------------------------------------
# Element helpers
# ---------------------------------------------------------------------------


def _text(properties: ET.Element, name: str) -> str | None:
    """Value of a property, whichever type element wraps it.

    X- properties use ``unknown``, ``url`` uses ``uri``, everything else uses
    ``text``. Empty elements are returned as None rather than an empty string,
    which matters because several properties are present but blank.
    """
    element = properties.find(f"{Q}{name}")
    if element is None:
        return None
    for child in element:
        if child.tag == f"{Q}parameters":
            continue
        value = (child.text or "").strip()
        if value:
            return value
    return None


def _tzid(element: ET.Element) -> str | None:
    parameters = element.find(f"{Q}parameters")
    if parameters is None:
        return None
    return parameters.findtext(f"{Q}tzid/{Q}text")


def _moment(element: ET.Element) -> tuple[datetime, bool] | None:
    """Read a date-time or date element into UTC, flagging all-day values."""
    if element is None:
        return None

    raw_datetime = element.findtext(f"{Q}date-time")
    if raw_datetime:
        parsed = datetime.fromisoformat(raw_datetime.replace("Z", "+00:00"))
        if parsed.tzinfo is not None:
            return parsed.astimezone(UTC), False
        zone = _tzid(element) or config.LOCAL_TZ
        return normalize.to_utc(parsed, zone), False

    raw_date = element.findtext(f"{Q}date")
    if raw_date:
        parsed = date.fromisoformat(raw_date)
        return datetime(parsed.year, parsed.month, parsed.day, tzinfo=UTC), True

    return None


def _rrule_string(element: ET.Element) -> str | None:
    """Reassemble an RRULE string from the structured children.

    The export writes ``<freq>WEEKLY</freq><until>...</until><byday>SA</byday>``
    rather than a single RRULE value, so dateutil cannot read it directly.
    """
    parts = []
    for child in element:
        if child.tag == f"{Q}parameters":
            continue
        name = child.tag.replace(Q, "").upper()
        value = (child.text or "").strip()
        if not value:
            continue
        if name == "UNTIL":
            # dateutil wants the compact form, not the ISO one.
            value = value.replace("-", "").replace(":", "")
        parts.append(f"{name}={value}")
    return "RRULE:" + ";".join(parts) if parts else None


# ---------------------------------------------------------------------------
# Record parsing
# ---------------------------------------------------------------------------


def _parse_vevent(
    vevent: ET.Element, earliest: datetime, latest: datetime
) -> list[Event]:
    properties = vevent.find(f"{Q}properties")
    if properties is None:
        return []

    parent_uid = _text(properties, "uid")
    title = _text(properties, "summary")
    if not parent_uid or not title:
        return []

    start_element = properties.find(f"{Q}dtstart")
    first = _moment(start_element)
    if first is None:
        return []
    start, all_day = first

    end_pair = _moment(properties.find(f"{Q}dtend"))
    duration = end_pair[0] - start if end_pair else None
    if duration is not None and duration <= timedelta(0):
        duration = None

    shared = _shared_fields(properties)
    starts = _occurrences(properties, start, earliest, latest)

    recurring = len(starts) > 1 or properties.find(f"{Q}rdate") is not None
    events: list[Event] = []
    for occurrence in sorted(starts):
        if not earliest <= occurrence <= latest:
            continue
        events.append(
            Event(
                source_id=SOURCE_ID,
                # The occurrence timestamp rather than only its date, because
                # a series can legitimately run twice in one day. Timely's own
                # permalinks are keyed the same way.
                source_uid=f"{parent_uid}:{occurrence:%Y%m%dT%H%M%S}",
                title=title,
                start=occurrence,
                end=occurrence + duration if duration else None,
                all_day=all_day,
                recurrence_parent_id=parent_uid if recurring else None,
                **shared,
            )
        )
    return events


def _occurrences(
    properties: ET.Element, start: datetime, earliest: datetime, latest: datetime
) -> set[datetime]:
    """Expand DTSTART, RDATE and RRULE, then remove EXDATE.

    Ignoring RDATE would lose roughly a fifth of the calendar: 105 records
    carry 345 additional dates between them (spec section 4.6).
    """
    starts = {start}

    for rdate in properties.findall(f"{Q}rdate"):
        moment = _moment(rdate)
        if moment:
            starts.add(moment[0])

    rrule_element = properties.find(f"{Q}rrule")
    if rrule_element is not None:
        rule_text = _rrule_string(rrule_element)
        if rule_text:
            try:
                rule = rrulestr(rule_text, dtstart=start)
                starts.update(rule.between(earliest, latest, inc=True))
            except (ValueError, TypeError):
                # A malformed rule costs us the extra occurrences, not the
                # base event, so keep what we already have and move on.
                pass

    for exdate in properties.findall(f"{Q}exdate"):
        moment = _moment(exdate)
        if moment:
            starts.discard(moment[0])

    return starts


def _shared_fields(properties: ET.Element) -> dict:
    """Fields that are identical across every occurrence of one record."""
    venue, address = normalize.split_location(_text(properties, "location"))
    city_tag, county_tag = _split_tags(_text(properties, "x-tags"))

    latitude = longitude = None
    geo = properties.find(f"{Q}geo")
    if geo is not None:
        raw_lat = geo.findtext(f"{Q}latitude")
        raw_lon = geo.findtext(f"{Q}longitude")
        if raw_lat and raw_lon:
            try:
                latitude, longitude = float(raw_lat), float(raw_lon)
            except ValueError:
                latitude = longitude = None

    return dict(
        description=normalize.strip_html(_text(properties, "description")),
        venue_name=venue,
        address=address,
        lat=latitude,
        lon=longitude,
        city_tag=city_tag,
        county_tag=county_tag,
        categories=normalize.canonical_categories(_text(properties, "categories")),
        cost=_text(properties, "x-cost") or _text(properties, "x-cost-type"),
        ticket_url=_text(properties, "x-tickets-url"),
        source_url=_text(properties, "url"),
        # Present on every record but empty on every record, as of the
        # 2026-08-09 export. Parsed anyway in case the curator starts
        # populating it, since it would be the strongest dedupe signal.
        original_url=_text(properties, "x-original-url"),
    )


def _split_tags(raw: str | None) -> tuple[str | None, str | None]:
    """Split the geographic tag list into a city and a county.

    Records carry both, for example ``Ithaca,Tompkins County``.
    """
    if not raw:
        return None, None
    city = county = None
    for part in raw.split(","):
        tag = part.strip()
        if not tag:
            continue
        if tag.lower().endswith(_COUNTY_SUFFIX):
            county = county or tag
        else:
            city = city or tag
    return city, county
