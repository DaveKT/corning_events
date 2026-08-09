"""The canonical Event record.

Mirrors the canonical data model in spec section 8, with one deliberate
omission: the spec lists an ``event_id`` surrogate key, but ``(source_id,
source_uid)`` is already the primary key in the state store, so a second
identifier would be dead weight. Use :attr:`Event.key` where a single string
identifier is wanted.

Two conventions matter and are enforced in ``__post_init__`` because getting
either wrong produces calendar data that is silently broken on a subscriber's
phone rather than loudly broken in CI.

**Times are UTC.** ``start`` and ``end`` are timezone aware and in UTC. Source
modules convert before returning, using :func:`normalize.to_utc`.

**DTEND is exclusive.** ``end`` is the first instant *after* the event, which
is what RFC 5545 means by DTEND. A single all-day event on the 5th therefore
has ``end`` at midnight on the 6th. This trips people up, so build all-day
spans with :func:`all_day_bounds` rather than by hand.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import date, datetime, time, timedelta, timezone

UTC = timezone.utc

# The literal title FLXcalendar emits for 52 of its records (spec section 4.6).
PLACEHOLDER_TITLE = "None"

STATUS_CONFIRMED = "CONFIRMED"
STATUS_CANCELLED = "CANCELLED"


def _check_utc(value: datetime, field_name: str, all_day: bool) -> None:
    if not isinstance(value, datetime):
        raise TypeError(f"{field_name} must be a datetime, got {type(value).__name__}")
    if value.tzinfo is None or value.utcoffset() != timedelta(0):
        raise ValueError(
            f"{field_name} must be timezone aware and in UTC, got {value!r}. "
            "Source modules convert with normalize.to_utc before returning."
        )
    if all_day and value.time() != time(0, 0):
        raise ValueError(
            f"{field_name} must fall at midnight UTC on an all-day event, got "
            f"{value!r}. Build all-day spans with model.all_day_bounds."
        )


@dataclass
class Event:
    """One occurrence of one event, as reported by one source.

    Recurring events are expanded into one Event per occurrence at parse time,
    so this never carries a recurrence rule. ``recurrence_parent_id`` records
    which upstream record an occurrence came from.
    """

    source_id: str
    source_uid: str
    title: str
    start: datetime
    end: datetime | None = None
    all_day: bool = False
    description: str | None = None
    venue_name: str | None = None
    address: str | None = None
    lat: float | None = None
    lon: float | None = None
    city_tag: str | None = None
    county_tag: str | None = None
    categories: tuple[str, ...] = ()
    cost: str | None = None
    ticket_url: str | None = None
    source_url: str | None = None
    original_url: str | None = None
    recurrence_parent_id: str | None = None

    def __post_init__(self) -> None:
        for name in ("source_id", "source_uid", "title"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{name} is required and must be a non-empty string")

        _check_utc(self.start, "start", self.all_day)
        if self.end is not None:
            _check_utc(self.end, "end", self.all_day)
            # An end at or before its start is meaningless and would emit
            # invalid iCal. It usually means a source passed an inclusive end
            # date. Drop it: a missing DTEND reads as a one day all-day event
            # or a zero length timed event, both of which are recoverable.
            if self.end <= self.start:
                self.end = None

        if (self.lat is None) != (self.lon is None):
            raise ValueError("lat and lon must be supplied together or not at all")

        self.categories = tuple(self.categories)

    @property
    def key(self) -> str:
        """Stable identifier for this record within the state store."""
        return f"{self.source_id}:{self.source_uid}"

    @property
    def is_placeholder(self) -> bool:
        """Whether the title is FLXcalendar's literal ``None`` placeholder."""
        return self.title.strip() == PLACEHOLDER_TITLE

    @property
    def has_coordinates(self) -> bool:
        return self.lat is not None and self.lon is not None

    def to_dict(self) -> dict:
        """JSON ready representation, used for the state store payload."""
        data = asdict(self)
        data["start"] = self.start.isoformat()
        data["end"] = self.end.isoformat() if self.end else None
        data["categories"] = list(self.categories)
        return data

    @classmethod
    def from_dict(cls, data: dict) -> "Event":
        data = dict(data)
        data["start"] = datetime.fromisoformat(data["start"])
        if data.get("end"):
            data["end"] = datetime.fromisoformat(data["end"])
        else:
            data["end"] = None
        data["categories"] = tuple(data.get("categories") or ())
        return cls(**data)


@dataclass
class PublishedEvent:
    """An event as it appears in a feed, carrying its published identity.

    ``uid`` is minted once per cluster and never regenerated, because a UID
    that changes between runs makes subscribers accumulate duplicates (build
    plan, Part 1 adjustment 6).
    """

    uid: str
    event: Event
    sequence: int = 0
    status: str = STATUS_CONFIRMED
    cancelled_at: datetime | None = None

    def __post_init__(self) -> None:
        if self.status not in (STATUS_CONFIRMED, STATUS_CANCELLED):
            raise ValueError(f"unknown status {self.status!r}")


def all_day_bounds(
    first_day: date, last_day: date | None = None
) -> tuple[datetime, datetime]:
    """Return the UTC midnight start and *exclusive* end of an all-day span.

    ``last_day`` is inclusive, the way humans and most sources describe it, so
    a single day event passes one date and gets an end on the following day.

    >>> all_day_bounds(date(2026, 8, 5))[1].day
    6
    """
    if last_day is None:
        last_day = first_day
    if last_day < first_day:
        raise ValueError("last_day cannot precede first_day")
    start = datetime.combine(first_day, time(0, 0), tzinfo=UTC)
    end = datetime.combine(last_day + timedelta(days=1), time(0, 0), tzinfo=UTC)
    return start, end
