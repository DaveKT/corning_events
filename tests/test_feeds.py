"""Emission and validation.

Invalid iCal fails silently in calendar clients: the subscription appears to
work and simply shows nothing. These tests are the safety net for that, so
they check the emitted bytes rather than trusting the library.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pytest
from icalendar import Calendar

from corning_events import config, feeds
from corning_events.model import (
    STATUS_CANCELLED,
    STATUS_CONFIRMED,
    Event,
    PublishedEvent,
    all_day_bounds,
)

UTC = timezone.utc
NOW = datetime(2026, 8, 9, 12, 0, tzinfo=UTC)
DTSTAMP = NOW
CORE_FEED, ALL_FEED = config.FEEDS


def make_event(**overrides) -> Event:
    defaults = dict(
        source_id="cmog",
        source_uid="uid-1",
        title="Glassblowing Demo",
        start=NOW + timedelta(days=6),
        end=NOW + timedelta(days=6, hours=1),
        city_tag="Corning",
    )
    defaults.update(overrides)
    return Event(**defaults)


def make_published(uid="a@corning-events", **overrides) -> PublishedEvent:
    status = overrides.pop("status", STATUS_CONFIRMED)
    sequence = overrides.pop("sequence", 0)
    cancelled_at = overrides.pop("cancelled_at", None)
    return PublishedEvent(
        uid=uid,
        event=make_event(**overrides),
        sequence=sequence,
        status=status,
        cancelled_at=cancelled_at,
    )


def vevents(data: bytes):
    return list(Calendar.from_ical(data).walk("VEVENT"))


# ---------------------------------------------------------------------------
# Round trip
# ---------------------------------------------------------------------------


def test_round_trip_preserves_a_timed_event():
    item = make_published(
        description="Watch molten glass being shaped.",
        venue_name="Corning Museum of Glass",
        address="1 Museum Way",
        lat=42.1489,
        lon=-77.0552,
        categories=("Glass", "Family Fun"),
        original_url="https://cmog.test/demo",
    )
    data = feeds.emit(CORE_FEED, [item], DTSTAMP)
    component = vevents(data)[0]

    assert str(component["UID"]) == "a@corning-events"
    assert str(component["SUMMARY"]) == "Glassblowing Demo"
    assert component["DTSTART"].dt == item.event.start
    assert component["DTEND"].dt == item.event.end
    assert str(component["LOCATION"]) == "Corning Museum of Glass, 1 Museum Way"
    assert str(component["URL"]) == "https://cmog.test/demo"
    assert str(component["STATUS"]) == STATUS_CONFIRMED
    assert int(component["SEQUENCE"]) == 0
    assert component["GEO"].to_ical() == "42.1489;-77.0552"
    assert "Watch molten glass" in str(component["DESCRIPTION"])


def test_round_trip_preserves_an_all_day_event():
    start, end = all_day_bounds(date(2026, 8, 15))
    item = make_published(all_day=True, start=start, end=end)
    component = vevents(feeds.emit(CORE_FEED, [item], DTSTAMP))[0]

    # VALUE=DATE means icalendar hands back a date, not a datetime.
    assert component["DTSTART"].dt == date(2026, 8, 15)
    assert component["DTEND"].dt == date(2026, 8, 16)
    assert not isinstance(component["DTSTART"].dt, datetime)


def test_all_day_event_without_an_end_still_emits_an_exclusive_dtend():
    start, _ = all_day_bounds(date(2026, 8, 15))
    item = make_published(all_day=True, start=start, end=None)
    component = vevents(feeds.emit(CORE_FEED, [item], DTSTAMP))[0]
    assert component["DTEND"].dt == date(2026, 8, 16)


def test_round_trip_preserves_a_cancelled_event():
    item = make_published(status=STATUS_CANCELLED, sequence=3)
    component = vevents(feeds.emit(CORE_FEED, [item], DTSTAMP))[0]
    assert str(component["STATUS"]) == STATUS_CANCELLED
    assert int(component["SEQUENCE"]) == 3


def test_timed_events_are_emitted_in_utc():
    data = feeds.emit(CORE_FEED, [make_published()], DTSTAMP)
    assert b"DTSTART:20260815T120000Z" in data


# ---------------------------------------------------------------------------
# Formatting
# ---------------------------------------------------------------------------


def test_long_descriptions_fold_within_the_octet_limit():
    item = make_published(description="x" * 500)
    data = feeds.emit(CORE_FEED, [item], DTSTAMP)
    assert all(len(line) <= 75 for line in data.split(b"\r\n"))
    assert "x" * 500 in str(vevents(data)[0]["DESCRIPTION"])


def test_folding_does_not_split_multibyte_characters():
    # Folding on characters rather than octets corrupts UTF-8 here.
    item = make_published(description="né" * 300)
    data = feeds.emit(CORE_FEED, [item], DTSTAMP)
    assert all(len(line) <= 75 for line in data.split(b"\r\n"))
    data.decode("utf-8")
    assert "né" * 300 in str(vevents(data)[0]["DESCRIPTION"])


def test_text_values_with_delimiters_survive_escaping():
    item = make_published(title="Wine, Cheese; and a Slash \\ Night")
    data = feeds.emit(CORE_FEED, [item], DTSTAMP)
    assert str(vevents(data)[0]["SUMMARY"]) == "Wine, Cheese; and a Slash \\ Night"


def test_line_endings_are_crlf():
    data = feeds.emit(CORE_FEED, [make_published()], DTSTAMP)
    assert data.count(b"\n") == data.count(b"\r\n")


def test_calendar_level_properties_are_present():
    data = feeds.emit(CORE_FEED, [make_published()], DTSTAMP)
    for expected in (
        b"VERSION:2.0",
        b"CALSCALE:GREGORIAN",
        b"METHOD:PUBLISH",
        b"X-WR-CALNAME:Corning Area Events",
        b"X-WR-TIMEZONE:America/New_York",
        b"REFRESH-INTERVAL;VALUE=DURATION:PT12H",
        b"X-PUBLISHED-TTL:PT12H",
        config.PRODID.encode(),
    ):
        assert expected in data


def test_an_empty_feed_is_still_valid_ical():
    data = feeds.emit(CORE_FEED, [], DTSTAMP)
    assert vevents(data) == []
    assert b"BEGIN:VCALENDAR" in data


# ---------------------------------------------------------------------------
# Attribution
# ---------------------------------------------------------------------------


def test_description_carries_source_attribution():
    item = make_published(description="Body text", source_url="https://cmog.test/e/1")
    description = str(vevents(feeds.emit(CORE_FEED, [item], DTSTAMP))[0]["DESCRIPTION"])
    assert description.startswith("Body text")
    assert "Source: Corning Museum of Glass - https://cmog.test/e/1" in description


def test_attribution_appears_even_without_a_description():
    item = make_published(description=None, source_url="https://cmog.test/e/1")
    description = str(vevents(feeds.emit(CORE_FEED, [item], DTSTAMP))[0]["DESCRIPTION"])
    assert description == "Source: Corning Museum of Glass - https://cmog.test/e/1"


def test_url_prefers_the_organizer_over_the_aggregator():
    event = make_event(
        original_url="https://venue.test/e",
        ticket_url="https://tickets.test/e",
        source_url="https://aggregator.test/e",
    )
    assert feeds.event_url(event) == "https://venue.test/e"
    event.original_url = None
    assert feeds.event_url(event) == "https://tickets.test/e"
    event.ticket_url = None
    assert feeds.event_url(event) == "https://aggregator.test/e"


# ---------------------------------------------------------------------------
# Content hash and UID
# ---------------------------------------------------------------------------


def test_content_hash_ignores_fields_that_change_every_run():
    # DTSTAMP is not hashed, and SEQUENCE is derived from the hash, so
    # neither may feed back into it or SEQUENCE would climb forever.
    a = make_published(sequence=0)
    b = make_published(sequence=9)
    assert feeds.content_hash(a) == feeds.content_hash(b)


def test_content_hash_changes_when_a_published_field_changes():
    base = feeds.content_hash(make_published())
    assert feeds.content_hash(make_published(title="Renamed")) != base
    assert feeds.content_hash(make_published(status=STATUS_CANCELLED)) != base
    assert feeds.content_hash(make_published(venue_name="Elsewhere")) != base


def test_minted_uids_are_deterministic_and_source_scoped():
    event = make_event()
    assert feeds.mint_uid(event) == feeds.mint_uid(make_event())
    assert feeds.mint_uid(event).endswith(f"@{config.UID_DOMAIN}")
    assert feeds.mint_uid(event) != feeds.mint_uid(make_event(source_id="chamber"))


# ---------------------------------------------------------------------------
# Selection
# ---------------------------------------------------------------------------


def test_selection_filters_by_ring():
    core = make_published("core@x", city_tag="Corning")
    regional = make_published("reg@x", city_tag="Ithaca")
    both = [core, regional]

    assert [i.uid for i in feeds.select_for_feed(CORE_FEED, both, NOW)] == ["core@x"]
    assert {i.uid for i in feeds.select_for_feed(ALL_FEED, both, NOW)} == {"core@x", "reg@x"}


def test_selection_drops_events_outside_the_time_window():
    past = make_published("past@x", start=NOW - timedelta(days=30), end=None)
    far = make_published("far@x", start=NOW + timedelta(days=400), end=None)
    soon = make_published("soon@x")
    selected = feeds.select_for_feed(CORE_FEED, [past, far, soon], NOW)
    assert [i.uid for i in selected] == ["soon@x"]


def test_selection_keeps_todays_events_for_clients_that_refresh_late():
    earlier_today = make_published("today@x", start=NOW - timedelta(hours=6), end=None)
    assert feeds.select_for_feed(CORE_FEED, [earlier_today], NOW)


def test_selection_drops_placeholder_titles():
    assert feeds.select_for_feed(CORE_FEED, [make_published(title="None")], NOW) == []


def test_selection_keeps_recently_cancelled_events_and_drops_expired_ones():
    recent = make_published("recent@x", status=STATUS_CANCELLED, cancelled_at=NOW - timedelta(days=2))
    expired = make_published("expired@x", status=STATUS_CANCELLED, cancelled_at=NOW - timedelta(days=45))
    selected = feeds.select_for_feed(CORE_FEED, [recent, expired], NOW)
    assert [i.uid for i in selected] == ["recent@x"]


def test_selection_sorts_by_start_time():
    late = make_published("late@x", start=NOW + timedelta(days=9), end=None)
    early = make_published("early@x", start=NOW + timedelta(days=2), end=None)
    selected = feeds.select_for_feed(CORE_FEED, [late, early], NOW)
    assert [i.uid for i in selected] == ["early@x", "late@x"]


def test_selection_applies_category_filters_when_a_feed_declares_them():
    music_feed = config.FeedConfig(
        slug="music",
        calendar_name="Music",
        description="Music only",
        rings=(config.RING_CORE,),
        categories=frozenset({"Music"}),
    )
    music = make_published("m@x", categories=("Music",))
    glass = make_published("g@x", categories=("Glass",))
    selected = feeds.select_for_feed(music_feed, [music, glass], NOW)
    assert [i.uid for i in selected] == ["m@x"]


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


def test_validate_accepts_well_formed_output():
    feeds.validate(feeds.emit(CORE_FEED, [make_published()], DTSTAMP), expected_count=1)


def test_validate_rejects_a_count_mismatch():
    data = feeds.emit(CORE_FEED, [make_published()], DTSTAMP)
    with pytest.raises(feeds.FeedValidationError, match="expected 2"):
        feeds.validate(data, expected_count=2)


def test_validate_rejects_bare_newlines():
    data = feeds.emit(CORE_FEED, [make_published()], DTSTAMP).replace(b"\r\n", b"\n")
    with pytest.raises(feeds.FeedValidationError, match="CRLF"):
        feeds.validate(data, expected_count=1)


def test_validate_rejects_unfolded_long_lines():
    data = feeds.emit(CORE_FEED, [make_published()], DTSTAMP)
    broken = data.replace(b"SUMMARY:Glassblowing Demo", b"SUMMARY:" + b"y" * 200)
    with pytest.raises(feeds.FeedValidationError, match="octets"):
        feeds.validate(broken, expected_count=1)


def test_validate_rejects_duplicate_uids():
    # Two VEVENTs sharing a UID is exactly what a subscriber sees as a
    # duplicate, so emit must refuse rather than ship it.
    with pytest.raises(feeds.FeedValidationError, match="duplicate UID"):
        feeds.emit(CORE_FEED, [make_published("dup@x"), make_published("dup@x")], DTSTAMP)


def test_validate_rejects_a_vevent_missing_a_required_property():
    data = feeds.emit(CORE_FEED, [make_published()], DTSTAMP)
    broken = data.replace(b"SUMMARY:Glassblowing Demo\r\n", b"")
    with pytest.raises(feeds.FeedValidationError, match="SUMMARY"):
        feeds.validate(broken, expected_count=1)


def test_validate_enforces_the_sanity_floor():
    data = feeds.emit(CORE_FEED, [make_published()], DTSTAMP)
    with pytest.raises(feeds.FeedValidationError, match="sanity floor"):
        feeds.validate(data, expected_count=1, min_events=5)


def test_write_preserves_crlf_verbatim(tmp_path):
    data = feeds.emit(CORE_FEED, [make_published()], DTSTAMP)
    path = tmp_path / "out.ics"
    feeds.write(path, data)
    assert path.read_bytes() == data
