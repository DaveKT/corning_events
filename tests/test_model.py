"""Event invariants.

The UTC and exclusive-DTEND rules are enforced in the dataclass because
getting either wrong produces calendar data that is silently wrong on a
subscriber's phone rather than loudly wrong in CI.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from corning_events.model import Event, PublishedEvent, all_day_bounds

UTC = timezone.utc
START = datetime(2026, 8, 15, 22, 0, tzinfo=UTC)


def make_event(**overrides) -> Event:
    defaults = dict(source_id="flxcalendar", source_uid="uid-1", title="Concert", start=START)
    defaults.update(overrides)
    return Event(**defaults)


def test_naive_datetimes_are_rejected():
    with pytest.raises(ValueError, match="UTC"):
        make_event(start=datetime(2026, 8, 15, 18, 0))


def test_non_utc_datetimes_are_rejected():
    # Catches a source that forgot to call normalize.to_utc.
    with pytest.raises(ValueError, match="UTC"):
        make_event(start=datetime(2026, 8, 15, 18, 0, tzinfo=ZoneInfo("America/New_York")))


def test_required_fields_must_be_non_empty():
    for field in ("source_id", "source_uid", "title"):
        with pytest.raises(ValueError, match=field):
            make_event(**{field: "   "})


def test_coordinates_must_be_supplied_as_a_pair():
    with pytest.raises(ValueError, match="lat and lon"):
        make_event(lat=42.1)


def test_an_end_at_or_before_the_start_is_discarded():
    # Usually means a source passed an inclusive end. Emitting DTEND <=
    # DTSTART would be invalid iCal, and a missing DTEND is recoverable.
    assert make_event(end=START).end is None
    assert make_event(end=START - timedelta(hours=1)).end is None


def test_a_valid_end_is_kept():
    end = START + timedelta(hours=2)
    assert make_event(end=end).end == end


def test_all_day_events_must_start_at_midnight_utc():
    with pytest.raises(ValueError, match="midnight"):
        make_event(all_day=True, start=START)


def test_all_day_bounds_makes_dtend_exclusive():
    # A single all-day event on the 5th ends on the 6th (spec section 9.2).
    start, end = all_day_bounds(date(2026, 8, 5))
    assert start == datetime(2026, 8, 5, tzinfo=UTC)
    assert end == datetime(2026, 8, 6, tzinfo=UTC)


def test_all_day_bounds_treats_last_day_as_inclusive():
    start, end = all_day_bounds(date(2026, 8, 5), date(2026, 8, 7))
    assert (start.day, end.day) == (5, 8)


def test_all_day_bounds_rejects_a_reversed_span():
    with pytest.raises(ValueError):
        all_day_bounds(date(2026, 8, 7), date(2026, 8, 5))


def test_placeholder_titles_are_detectable():
    # FLXcalendar emits the literal string None on 52 records (spec 4.6).
    assert make_event(title="None").is_placeholder
    assert not make_event(title="A Concert").is_placeholder


def test_key_combines_source_and_uid():
    assert make_event().key == "flxcalendar:uid-1"


def test_round_trip_through_dict_preserves_every_field():
    event = make_event(
        end=START + timedelta(hours=2),
        description="Text",
        venue_name="Venue",
        address="1 Market St",
        lat=42.1,
        lon=-77.0,
        city_tag="Corning",
        county_tag="Steuben County",
        categories=("Music", "Comedy"),
        cost="Free",
        ticket_url="https://example.test/tickets",
        source_url="https://example.test/event",
        original_url="https://venue.test/event",
        recurrence_parent_id="parent-uid",
    )
    assert Event.from_dict(event.to_dict()) == event


def test_round_trip_handles_all_day_and_absent_optional_fields():
    start, end = all_day_bounds(date(2026, 8, 5))
    event = make_event(all_day=True, start=start, end=end)
    assert Event.from_dict(event.to_dict()) == event


def test_published_event_rejects_an_unknown_status():
    with pytest.raises(ValueError, match="status"):
        PublishedEvent(uid="u@x", event=make_event(), status="MAYBE")
