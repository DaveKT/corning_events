"""Cross-source deduplication.

Spec section 10 calls this the primary engineering problem, and it is right:
a duplicate in a database is a data quality issue, but a duplicate in a
subscribed calendar shows up on every phone that subscribed.

The cascade below is the spec's, adjusted after measuring what the sources
actually publish. Four changes were needed, each recorded against the evidence
that forced it.

**Only records from different sources are compared.** A source has already
deduplicated itself, so two of its records are two different things. Without
this rule the four separate showtimes FLXcalendar lists for one theatre run
would collapse into a single event, losing three of them.

**Candidates are bucketed by local date, not UTC date.** An 8pm local event is
already tomorrow in UTC, which split 17 of 58 evening events in the 2026-08-09
capture and would have hidden them from each other entirely.

**A missing venue is treated as unknown, not as a mismatch.** The spec's rules
2 and 3 require the venues to be equal. The Clemens Center feed publishes no
LOCATION at all, so under that reading none of its events could ever match
the same event in FLXcalendar. Absence of evidence is not evidence of
difference, so a comparison where either side lacks a venue stays eligible,
and city tags are checked the same way to keep the looser rule honest.

**A containment rule was added.** Character similarity punishes an added
suffix: "Wise Crackers All Stars" against "Wise Crackers All Stars Comedy
Show" scores 0.79 against a threshold of 0.85, despite being the same show at
the same instant in the same room. Word containment scores it 1.0.

Measured against the 2026-08-09 capture, the cascade as specified caught
neither of the two genuine duplicates present. With these adjustments it
catches both, and the 6 same-title same-day groups in that data all sit at a
single venue, so nothing is merged that should not be.
"""

from __future__ import annotations

import difflib
from collections import defaultdict
from dataclasses import replace
from urllib.parse import urlsplit, urlunsplit

from . import config, normalize
from .model import Event

# Fields taken as a set from one record rather than assembled from several,
# because mixing them would describe an event that never happened.
_TEMPORAL_FIELDS = ("start", "end", "all_day")


def normalize_url(url: str | None) -> str | None:
    """Reduce a URL to its identity, dropping query strings and fragments.

    Aggregators append tracking parameters to the same organizer link.
    """
    if not url:
        return None
    parts = urlsplit(url.strip())
    if not parts.netloc:
        return None
    path = parts.path.rstrip("/")
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), path, "", "")) or None


# ---------------------------------------------------------------------------
# Matching
# ---------------------------------------------------------------------------


def _compatible(left: str | None, right: str | None, fold=lambda v: (v or "").strip().lower()) -> bool:
    """Whether two optional values agree, treating absent as unknown."""
    a, b = fold(left), fold(right)
    if not a or not b:
        return True
    return a == b


def _location_compatible(a: Event, b: Event) -> bool:
    return _compatible(a.venue_name, b.venue_name, normalize.normalize_venue) and _compatible(
        a.city_tag, b.city_tag
    )


def _similar(a: Event, b: Event) -> float:
    return difflib.SequenceMatcher(
        None, normalize.normalize_title(a.title, a.venue_name), normalize.normalize_title(b.title, b.venue_name)
    ).ratio()


def _same_named_location(a: Event, b: Event) -> bool:
    """Both sides positively name the same place, absence not counting.

    Stricter than :func:`_location_compatible`, which treats a missing value
    as unknown. Used where the other evidence is too thin to extend the
    benefit of the doubt.
    """
    left_venue = normalize.normalize_venue(a.venue_name)
    right_venue = normalize.normalize_venue(b.venue_name)
    if left_venue and right_venue and left_venue == right_venue:
        return True
    left_city = (a.city_tag or "").strip().lower()
    right_city = (b.city_tag or "").strip().lower()
    return bool(left_city and right_city and left_city == right_city)


def _containment_match(a: Event, b: Event) -> bool:
    """Whether the containment rule accepts this pair.

    Containment exists for a title that gained a suffix: "Wise Crackers All
    Stars" inside "Wise Crackers All Stars Comedy Show". At four words and
    up that is a strong signal. At the two word minimum it is barely one:
    "Open House" sits inside half the chamber's calendar, so a same-instant
    pair of different open houses would merge if either side happened to
    lack location data. Titles at the minimum length therefore need both
    sides to positively name the same place, absence not counting.
    """
    left = normalize.title_tokens(a.title, a.venue_name)
    right = normalize.title_tokens(b.title, b.venue_name)
    shortest = min(len(left), len(right))
    if shortest < config.MIN_TITLE_TOKENS:
        # A one word title matches far too much to be trusted at all.
        return False
    if normalize.containment(left, right) < config.TITLE_CONTAINMENT_THRESHOLD:
        return False
    if shortest == config.MIN_TITLE_TOKENS:
        return _same_named_location(a, b)
    return True


def match_reason(a: Event, b: Event) -> str | None:
    """Return which cascade rule matches this pair, strongest first, or None."""
    if a.source_id == b.source_id:
        return None

    left_url, right_url = normalize_url(a.original_url), normalize_url(b.original_url)
    if left_url and right_url and left_url == right_url:
        return "original_url"

    same_day = normalize.local_date(a) == normalize.local_date(b)
    same_instant = a.start == b.start

    if (
        same_day
        and _location_compatible(a, b)
        and normalize.normalize_title(a.title, a.venue_name)
        == normalize.normalize_title(b.title, b.venue_name)
    ):
        return "title+day+venue"

    if same_instant and _location_compatible(a, b) and _similar(a, b) >= config.TITLE_SIMILARITY_THRESHOLD:
        return "instant+venue+title"

    if (
        same_instant
        and a.has_coordinates
        and b.has_coordinates
        and normalize.haversine_metres(a.lat, a.lon, b.lat, b.lon) <= config.GEO_MATCH_METRES
        and _similar(a, b) >= config.TITLE_SIMILARITY_THRESHOLD
    ):
        return "instant+geo+title"

    if same_instant and _location_compatible(a, b) and _containment_match(a, b):
        return "instant+venue+containment"

    return None


# ---------------------------------------------------------------------------
# Clustering
# ---------------------------------------------------------------------------


class _UnionFind:
    def __init__(self, size: int) -> None:
        self._parent = list(range(size))

    def find(self, item: int) -> int:
        while self._parent[item] != item:
            self._parent[item] = self._parent[self._parent[item]]
            item = self._parent[item]
        return item

    def union(self, a: int, b: int) -> None:
        root_a, root_b = self.find(a), self.find(b)
        if root_a != root_b:
            self._parent[max(root_a, root_b)] = min(root_a, root_b)


def cluster(events: list[Event]) -> list[list[Event]]:
    """Group events that describe the same real world occurrence.

    Comparison is confined to events sharing a local date, which keeps this
    linear in practice: the whole registry averages a couple of events a day.
    """
    union = _UnionFind(len(events))

    buckets: dict[object, list[int]] = defaultdict(list)
    for index, event in enumerate(events):
        buckets[normalize.local_date(event)].append(index)

    # An event whose local date sits either side of midnight in another source
    # is still reachable, because same-instant rules bucket identically.
    for indexes in buckets.values():
        for position, left in enumerate(indexes):
            for right in indexes[position + 1 :]:
                if match_reason(events[left], events[right]):
                    union.union(left, right)

    grouped: dict[int, list[Event]] = defaultdict(list)
    for index, event in enumerate(events):
        grouped[union.find(index)].append(event)
    return list(grouped.values())


# ---------------------------------------------------------------------------
# Field resolution
# ---------------------------------------------------------------------------


def _rank(event: Event) -> tuple[int, int, str]:
    """Sort key placing the most trustworthy, most complete record first."""
    source = config.SOURCES.get(event.source_id)
    trust = source.trust if source else config.TRUST_OTHER
    populated = sum(
        1 for value in vars(event).values() if value not in (None, "", ())
    )
    return (trust, -populated, event.source_id)


def resolve(members: list[Event]) -> Event:
    """Merge a cluster into the record to publish.

    Venue sites are authoritative on times and cancellations and aggregators
    lag, so the highest trust member sets the event's identity and its timing
    (spec section 10). Every other field falls back through the remaining
    members in trust order, which is how a venue feed that publishes no
    location still ends up with the venue name an aggregator supplied.
    """
    if len(members) == 1:
        return members[0]

    ordered = sorted(members, key=_rank)
    winner = ordered[0]

    def first(field: str):
        for member in ordered:
            value = getattr(member, field)
            if value not in (None, "", ()):
                return value
        return None

    # Venue and address travel together, as do the coordinates: taking half a
    # location from one source and half from another invents a place.
    venue_source = next((m for m in ordered if m.venue_name), None)
    geo_source = next((m for m in ordered if m.has_coordinates), None)

    categories: list[str] = []
    for member in ordered:
        for category in member.categories:
            if category not in categories:
                categories.append(category)

    return replace(
        winner,
        title=winner.title,
        description=first("description"),
        venue_name=venue_source.venue_name if venue_source else None,
        address=venue_source.address if venue_source else first("address"),
        lat=geo_source.lat if geo_source else None,
        lon=geo_source.lon if geo_source else None,
        city_tag=first("city_tag"),
        county_tag=first("county_tag"),
        categories=tuple(categories),
        cost=first("cost"),
        ticket_url=first("ticket_url"),
        source_url=first("source_url"),
        original_url=first("original_url"),
    )


def deduplicate(events: list[Event]) -> list[tuple[Event, list[Event]]]:
    """Cluster and resolve in one step.

    Returns each published event alongside the members it came from, since the
    caller needs the member keys to pin the cluster's UID in the state store.
    """
    return [(resolve(members), members) for members in cluster(events)]
