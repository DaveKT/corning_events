"""Text, time and geography normalization.

Everything here is pure and side effect free, which is what lets the dedupe
cascade and the ring classifier be tested without a network or a database.
"""

from __future__ import annotations

import math
import re
import unicodedata
from datetime import datetime
from zoneinfo import ZoneInfo

from bs4 import BeautifulSoup

from . import config
from .model import UTC, Event

_LOCAL_ZONE = ZoneInfo(config.LOCAL_TZ)

_WHITESPACE = re.compile(r"\s+")
_PUNCTUATION = re.compile(r"[^\w\s]", re.UNICODE)

# FLXcalendar LOCATION format is "Venue Name @ Street Address" (spec 4.6).
_LOCATION_SEPARATOR = " @ "

# A trailing "@ Venue" fragment that aggregators append to titles.
_TRAILING_VENUE = re.compile(r"\s+@\s+.+$")

# Elements that imply a line break when HTML is flattened to text.
_BLOCK_TAGS = (
    "address article aside blockquote br div dl dd dt figure footer h1 h2 h3 "
    "h4 h5 h6 header hr li main nav ol p pre section table tbody td th tr ul"
).split()

EARTH_RADIUS_MILES = 3958.7613
METRES_PER_MILE = 1609.344


# ---------------------------------------------------------------------------
# Text
# ---------------------------------------------------------------------------


def strip_html(value: str | None) -> str | None:
    """Reduce HTML to plain text.

    FLXcalendar descriptions are frequently pasted out of Facebook and carry
    inline styles, generated class names and the occasional script block (spec
    section 4.6). None of that belongs in a calendar entry.

    Only block level elements become line breaks. Breaking at every tag would
    split a sentence wherever someone had bolded a word.
    """
    if value is None:
        return None
    if "<" not in value and "&" not in value:
        # Already plain text. Tidy it the same way as the HTML path rather
        # than flattening: feeds that convert HTML themselves hand back runs
        # of tabs and blank lines, but the paragraph breaks are still real.
        return _tidy_lines(value.splitlines())

    soup = BeautifulSoup(value, "lxml")
    for tag in soup(["script", "style"]):
        tag.decompose()

    for tag in soup.find_all(_BLOCK_TAGS):
        if tag.name == "br":
            tag.replace_with("\n")
        else:
            tag.insert_after("\n")

    return _tidy_lines(soup.get_text().splitlines())


def _tidy_lines(lines: list[str]) -> str | None:
    cleaned = [_collapse(line) for line in lines]
    text = "\n".join(line for line in cleaned if line)
    return text or None


def _collapse(value: str) -> str:
    """Collapse runs of whitespace, including non-breaking spaces."""
    return _WHITESPACE.sub(" ", value.replace("\xa0", " ")).strip()


def normalize_title(title: str, venue: str | None = None) -> str:
    """Fold a title down to something comparable across sources.

    Aggregators decorate the same event differently, so this lowercases,
    strips punctuation and whitespace, and removes both a leading venue name
    prefix and a trailing ``@ Venue`` fragment (build plan, dedupe cascade).
    """
    text = _TRAILING_VENUE.sub("", title)
    text = _fold(text)

    if venue:
        prefix = _fold(venue)
        if prefix and text.startswith(prefix) and text != prefix:
            text = text[len(prefix) :].strip()

    return text


def normalize_venue(venue: str | None) -> str:
    """Fold a venue name for comparison. Empty string when absent."""
    return _fold(venue) if venue else ""


def _fold(value: str) -> str:
    text = unicodedata.normalize("NFKD", value)
    text = _PUNCTUATION.sub(" ", text.lower())
    return _collapse(text)


def split_location(location: str | None) -> tuple[str | None, str | None]:
    """Split the ``Venue Name @ Street Address`` LOCATION format.

    Returns ``(venue, address)``. A location with no separator is treated as a
    venue name, which is the common case outside FLXcalendar.
    """
    if not location:
        return None, None
    cleaned = _collapse(location)
    if not cleaned:
        return None, None
    if _LOCATION_SEPARATOR in cleaned:
        venue, _, address = cleaned.partition(_LOCATION_SEPARATOR)
        return _collapse(venue) or None, _collapse(address) or None
    return cleaned, None


def canonical_categories(raw: object) -> tuple[str, ...]:
    """Map inbound category labels onto the canonical vocabulary.

    Unrecognised labels are dropped rather than passed through, so that
    category filtered feeds cannot be defeated by an upstream rename.
    """
    if raw is None:
        return ()
    if isinstance(raw, str):
        items = [part for part in re.split(r"[,;|]", raw)]
    else:
        items = list(raw)

    resolved: list[str] = []
    for item in items:
        label = _collapse(str(item))
        if not label:
            continue
        if label in config.CANONICAL_CATEGORIES:
            canonical = label
        else:
            canonical = config.CATEGORY_ALIASES.get(label.lower())
        if canonical and canonical not in resolved:
            resolved.append(canonical)
    return tuple(resolved)


# ---------------------------------------------------------------------------
# Time
# ---------------------------------------------------------------------------


def to_utc(value: datetime, tz: str | ZoneInfo | None = None) -> datetime:
    """Convert a datetime to UTC.

    A naive datetime is assumed to be in ``tz``, defaulting to the local zone
    of the region, which is what every source in the registry publishes in.
    """
    zone = _LOCAL_ZONE if tz is None else (ZoneInfo(tz) if isinstance(tz, str) else tz)
    if value.tzinfo is None:
        value = value.replace(tzinfo=zone)
    return value.astimezone(UTC)


# ---------------------------------------------------------------------------
# Geography
# ---------------------------------------------------------------------------


def haversine_miles(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great circle distance in miles."""
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    d_phi = phi2 - phi1
    d_lambda = math.radians(lon2 - lon1)
    a = (
        math.sin(d_phi / 2) ** 2
        + math.cos(phi1) * math.cos(phi2) * math.sin(d_lambda / 2) ** 2
    )
    return 2 * EARTH_RADIUS_MILES * math.asin(math.sqrt(a))


def haversine_metres(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    return haversine_miles(lat1, lon1, lat2, lon2) * METRES_PER_MILE


def distance_from_anchor_miles(lat: float, lon: float) -> float:
    return haversine_miles(config.ANCHOR_LAT, config.ANCHOR_LON, lat, lon)


def ring_for_distance(miles: float) -> str:
    """Bucket a distance in miles into a ring."""
    for ring in (config.RING_CORE, config.RING_NEAR, config.RING_REGIONAL):
        if miles <= config.RING_MAX_MILES[ring]:
            return ring
    return config.RING_OUT


def classify_ring(event: Event) -> str:
    """Decide which ring an event belongs to.

    The cascade stops at the first test that resolves:

    1. The city tag, which FLXcalendar carries on 99.4 percent of records
       against GEO on 48.6 percent, making it the most reliable signal
       available (spec section 4.4).
    2. Coordinates, when present.
    3. The source default, which only single venue and city scoped sources
       declare. Regional aggregators leave it None so the cascade continues.
    4. The county tag, which is coarse but better than a guess.
    5. The fallback, which keeps an unclassifiable event out of the default
       feed while leaving it in the firehose.
    """
    city = (event.city_tag or "").strip().lower()
    if city in config.CITY_RINGS:
        return config.CITY_RINGS[city]

    if event.has_coordinates:
        return ring_for_distance(distance_from_anchor_miles(event.lat, event.lon))

    source = config.SOURCES.get(event.source_id)
    if source is not None and source.default_ring is not None:
        return source.default_ring

    county = (event.county_tag or "").strip().lower()
    if county in config.COUNTY_RINGS:
        return config.COUNTY_RINGS[county]

    return config.FALLBACK_RING
