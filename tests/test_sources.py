"""Parser tests, run offline against saved captures.

Every fixture is pinned to CAPTURED_AT rather than the real clock, so these
tests give the same answer next year as they do today.

The FLXcalendar and Clemens Center fixtures are real captures taken on
2026-08-09. The Ticketmaster fixture is not: it was built from the documented
Discovery API v2 schema because no API key was available. See the note in
corning_events/sources/ticketmaster.py.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from corning_events import config, normalize
from corning_events.model import Event
from corning_events.sources import clemenscenter, flxcalendar, ssclibrary, ticketmaster

UTC = timezone.utc
FIXTURES = Path(__file__).parent / "fixtures"

# The moment both real fixtures were captured. Pinning it keeps the horizon
# and past windows fixed, so counts do not drift as the calendar ages.
CAPTURED_AT = datetime(2026, 8, 9, 18, 0, tzinfo=UTC)


@pytest.fixture(scope="module")
def flx_events():
    return flxcalendar.parse((FIXTURES / "flxcalendar.xml").read_bytes(), now=CAPTURED_AT)


@pytest.fixture(scope="module")
def clemens_events():
    return clemenscenter.parse((FIXTURES / "clemenscenter.ics").read_bytes(), now=CAPTURED_AT)


@pytest.fixture(scope="module")
def tm_events():
    payload = json.loads((FIXTURES / "ticketmaster.json").read_text())
    return ticketmaster.parse(payload, now=CAPTURED_AT)


def by_title(events, fragment):
    matches = [e for e in events if fragment.lower() in e.title.lower()]
    assert matches, f"no event matching {fragment!r}"
    return matches


# ---------------------------------------------------------------------------
# Shared guarantees, asserted for every parser
# ---------------------------------------------------------------------------


def test_every_parser_returns_events(flx_events, clemens_events, tm_events):
    assert flx_events and clemens_events and tm_events


@pytest.mark.parametrize("name", ["flx_events", "clemens_events", "tm_events"])
def test_source_uids_are_unique_within_a_source(name, request):
    events = request.getfixturevalue(name)
    uids = [e.source_uid for e in events]
    assert len(uids) == len(set(uids))


@pytest.mark.parametrize("name", ["flx_events", "clemens_events", "tm_events"])
def test_all_times_are_utc(name, request):
    for event in request.getfixturevalue(name):
        assert event.start.utcoffset() == timedelta(0)
        assert event.end is None or event.end.utcoffset() == timedelta(0)


@pytest.mark.parametrize("name", ["flx_events", "clemens_events", "tm_events"])
def test_descriptions_carry_no_markup(name, request):
    for event in request.getfixturevalue(name):
        assert "<div" not in (event.description or "")
        assert "<p>" not in (event.description or "")


@pytest.mark.parametrize("name", ["flx_events", "clemens_events", "tm_events"])
def test_categories_are_canonical(name, request):
    for event in request.getfixturevalue(name):
        for category in event.categories:
            assert category in config.CANONICAL_CATEGORIES


@pytest.mark.parametrize("name", ["flx_events", "clemens_events", "tm_events"])
def test_every_event_classifies_into_a_ring(name, request):
    for event in request.getfixturevalue(name):
        assert normalize.classify_ring(event) in config.RING_ORDER


# ---------------------------------------------------------------------------
# FLXcalendar
# ---------------------------------------------------------------------------


def test_flx_parses_the_expected_shape(flx_events):
    # 19 source records expanding to more occurrences than records, since
    # several are weekly series.
    assert len(flx_events) > 19
    assert all(e.source_id == "flxcalendar" for e in flx_events)


def test_flx_applies_the_tzid_rather_than_assuming_utc(flx_events):
    # Naive local times carrying TZID=America/New_York must shift by four
    # hours in August. A parser that ignored TZID would put evening events in
    # the afternoon.
    timed = [e for e in flx_events if not e.all_day]
    assert timed
    assert any(e.start.hour >= 20 or e.start.hour <= 3 for e in timed)


def test_flx_expands_weekly_series_into_separate_occurrences(flx_events):
    series = {}
    for event in flx_events:
        if event.recurrence_parent_id:
            series.setdefault(event.recurrence_parent_id, []).append(event)
    assert series, "fixture should contain at least one recurring record"

    parent, occurrences = max(series.items(), key=lambda kv: len(kv[1]))
    occurrences.sort(key=lambda e: e.start)
    assert len(occurrences) > 1
    # Occurrences of one series share a title and differ only in time.
    assert len({e.title for e in occurrences}) == 1
    assert len({e.source_uid for e in occurrences}) == len(occurrences)


def test_flx_occurrence_uids_encode_the_full_timestamp(flx_events):
    # Date alone would collide for a series that runs twice in one day.
    recurring = [e for e in flx_events if e.recurrence_parent_id]
    assert recurring
    for event in recurring:
        assert event.source_uid == f"{event.recurrence_parent_id}:{event.start:%Y%m%dT%H%M%S}"


def test_flx_parses_all_day_events_with_an_exclusive_end(flx_events):
    all_day = [e for e in flx_events if e.all_day]
    assert all_day
    for event in all_day:
        assert event.start.time() == datetime.min.time()
        if event.end:
            assert event.end > event.start


def test_flx_splits_location_into_venue_and_address(flx_events):
    located = [e for e in flx_events if e.venue_name]
    assert located
    for event in located:
        assert " @ " not in event.venue_name


def test_flx_splits_tags_into_city_and_county(flx_events):
    assert any(e.city_tag for e in flx_events)
    counties = {e.county_tag for e in flx_events if e.county_tag}
    assert counties
    for county in counties:
        assert county.lower().endswith("county")


def test_flx_original_url_is_empty_upstream(flx_events):
    # Documented as 100 percent populated in spec section 4.2 and the best
    # dedupe signal. It is present but empty on every record, so the dedupe
    # cascade cannot rely on it. If this ever starts failing, the curator has
    # begun populating it and dedupe rule 1 becomes available.
    assert all(e.original_url is None for e in flx_events)


def test_flx_keeps_the_timely_permalink_as_the_source_url(flx_events):
    assert all(e.source_url and "time.ly" in e.source_url for e in flx_events)


def test_flx_honours_exdate(flx_events):
    # The fixture deliberately includes the one record carrying an EXDATE.
    raw = (FIXTURES / "flxcalendar.xml").read_bytes()
    assert b"exdate" in raw
    # Parse with a window wide enough to reach that past record.
    old = datetime(2026, 7, 1, tzinfo=UTC)
    events = flxcalendar.parse(raw, now=old)
    excluded = datetime(2026, 8, 1, 12, 0, tzinfo=UTC)
    assert all(e.start != excluded for e in events)


def test_flx_discards_occurrences_outside_the_window(flx_events):
    earliest = CAPTURED_AT - timedelta(days=config.PAST_WINDOW_DAYS)
    latest = CAPTURED_AT + timedelta(days=config.HORIZON_DAYS)
    for event in flx_events:
        assert earliest <= event.start <= latest


# ---------------------------------------------------------------------------
# The Events Calendar feeds
# ---------------------------------------------------------------------------


def test_clemens_parses_the_expected_shape(clemens_events):
    assert len(clemens_events) >= 10
    assert all(e.source_id == "clemenscenter" for e in clemens_events)


def test_clemens_applies_the_tzid(clemens_events):
    # DTSTART;TZID=America/New_York:20260818T120000 is noon EDT, 16:00 UTC.
    blood_drive = by_title(clemens_events, "Blood Drive")[0]
    assert blood_drive.start == datetime(2026, 8, 18, 16, 0, tzinfo=UTC)
    assert blood_drive.end == datetime(2026, 8, 18, 21, 0, tzinfo=UTC)


def test_clemens_treats_a_multi_day_run_as_all_day(clemens_events):
    # A three night run published as DATE values, ending exclusively.
    show = by_title(clemens_events, "YOUNG FRANKENSTEIN")[0]
    assert show.all_day
    assert show.start == datetime(2026, 8, 14, tzinfo=UTC)
    assert show.end == datetime(2026, 8, 17, tzinfo=UTC)


def test_clemens_drops_an_end_equal_to_its_start(clemens_events):
    # This feed publishes DTEND == DTSTART for single performances.
    zero_length = [e for e in clemens_events if e.end is None]
    assert zero_length


def test_clemens_marks_its_own_urls_as_canonical(clemens_events):
    # A venue publishing its own feed is the organizer, so its URL is the
    # strongest dedupe signal when an aggregator lists the same show.
    for event in clemens_events:
        assert event.original_url == event.source_url
        assert event.original_url.startswith("https://clemenscenter.org/")


def test_clemens_truncates_boilerplate_descriptions(clemens_events):
    # Every event is padded with box office hours and ticketing notices.
    for event in clemens_events:
        assert len(event.description or "") <= config.MAX_DESCRIPTION_CHARS + 8
    assert any((e.description or "").endswith("...") for e in clemens_events)


def test_clemens_ignores_vtimezone_components(clemens_events):
    # The feed carries DAYLIGHT and STANDARD blocks whose DTSTART values look
    # like events. Walking VEVENT only is what keeps them out.
    assert all(e.start.year >= 2026 for e in clemens_events)


def test_library_uses_the_same_parser(clemens_events):
    # ssclibrary is blocked by a bot challenge upstream, so it has no capture
    # of its own. It shares the Tribe parser, so exercising it here confirms
    # the module is wired correctly and ready if access is arranged.
    events = ssclibrary.parse((FIXTURES / "clemenscenter.ics").read_bytes(), now=CAPTURED_AT)
    assert [e.source_id for e in events] == ["ssclibrary"] * len(events)
    assert len(events) == len(clemens_events)


def test_library_source_is_disabled_pending_access():
    assert config.SOURCES["ssclibrary"].enabled is False


# ---------------------------------------------------------------------------
# Ticketmaster
# ---------------------------------------------------------------------------


def test_ticketmaster_parses_the_documented_schema(tm_events):
    assert len(tm_events) == 3
    assert all(e.source_id == "ticketmaster" for e in tm_events)


def test_ticketmaster_prefers_the_absolute_start_time(tm_events):
    band = by_title(tm_events, "Touring Band")[0]
    assert band.start == datetime(2026, 8, 22, 23, 30, tzinfo=UTC)
    assert not band.all_day


def test_ticketmaster_publishes_a_time_tba_event_as_all_day(tm_events):
    # Inventing a start time would be worse than admitting there isn't one.
    comedy = by_title(tm_events, "Comedy Night")[0]
    assert comedy.all_day
    assert comedy.start == datetime(2026, 9, 5, tzinfo=UTC)


def test_ticketmaster_extracts_venue_and_coordinates(tm_events):
    band = by_title(tm_events, "Touring Band")[0]
    assert band.venue_name == "Tag's Summer Stage"
    assert band.city_tag == "Big Flats"
    assert (round(band.lat, 4), round(band.lon, 4)) == (42.1428, -76.9319)
    assert "3037 Watkins Road" in band.address


def test_ticketmaster_formats_price_ranges(tm_events):
    assert by_title(tm_events, "Touring Band")[0].cost == "$25 to $75"
    # A single price collapses rather than repeating itself.
    assert by_title(tm_events, "Comedy Night")[0].cost == "$30"


def test_ticketmaster_maps_classifications_onto_canonical_categories(tm_events):
    assert "Music" in by_title(tm_events, "Touring Band")[0].categories
    assert "Comedy" in by_title(tm_events, "Comedy Night")[0].categories


def test_ticketmaster_survives_a_record_with_no_venue(tm_events):
    sparse = by_title(tm_events, "Almost No Metadata")[0]
    assert sparse.venue_name is None
    assert sparse.lat is None and sparse.lon is None


def test_ticketmaster_needs_a_key(monkeypatch):
    monkeypatch.delenv(ticketmaster.API_KEY_ENV, raising=False)
    with pytest.raises(ticketmaster.MissingApiKey):
        ticketmaster.fetch(http=None)


# ---------------------------------------------------------------------------
# Fetch orchestration
# ---------------------------------------------------------------------------


def test_one_failing_source_does_not_stop_the_others(monkeypatch):
    # A broken parser must not take the run down: the other sources still have
    # events worth publishing.
    from corning_events import main as main_module

    def boom(http):
        raise RuntimeError("upstream changed its markup")

    def fine(http):
        return [
            Event(
                source_id="clemenscenter",
                source_uid="x",
                title="Still here",
                start=CAPTURED_AT,
            )
        ]

    monkeypatch.setitem(main_module.FETCHERS, "flxcalendar", boom)
    monkeypatch.setitem(main_module.FETCHERS, "clemenscenter", fine)

    results = main_module.fetch_all(
        [config.SOURCES["flxcalendar"], config.SOURCES["clemenscenter"]]
    )
    outcomes = {r.source_id: r for r in results}

    assert outcomes["flxcalendar"].ok is False
    assert "upstream changed its markup" in outcomes["flxcalendar"].note
    assert outcomes["clemenscenter"].ok is True
    assert outcomes["clemenscenter"].count == 1


def test_a_missing_api_key_is_a_skip_not_a_failure(monkeypatch):
    from corning_events import main as main_module

    monkeypatch.delenv(ticketmaster.API_KEY_ENV, raising=False)
    results = main_module.fetch_all([config.SOURCES["ticketmaster"]])

    assert results[0].ok is False
    assert "developer.ticketmaster.com" in results[0].note


def test_a_failed_source_keeps_its_stored_events(tmp_path):
    """The core of the outage guard.

    A source that fails must leave its previous events in place. If they were
    dropped, the next stage would read the absence as every one of them being
    cancelled, and two phones would lose a source's worth of calendar.
    """
    from corning_events import main as main_module
    from corning_events import state

    db = tmp_path / "state.db"
    conn = state.connect(db)
    run_one = datetime(2026, 8, 9, 9, 0, tzinfo=UTC)
    run_two = datetime(2026, 8, 10, 9, 0, tzinfo=UTC)

    stored = Event(
        source_id="flxcalendar",
        source_uid="keep-me",
        title="Still Scheduled",
        start=CAPTURED_AT + timedelta(days=30),
    )
    main_module.persist(
        conn, [main_module.FetchResult("flxcalendar", ok=True, events=[stored])], run_one
    )

    # The next run fails outright.
    main_module.persist(
        conn,
        [main_module.FetchResult("flxcalendar", ok=False, note="timeout")],
        run_two,
    )

    assert state.get_raw_event(conn, "flxcalendar", "keep-me") is not None
    assert state.consecutive_failures(conn, "flxcalendar") == 1
    # last_seen must not have advanced, or the event would look re-confirmed.
    row = conn.execute("SELECT last_seen FROM raw_events").fetchone()
    assert row["last_seen"] == run_one.isoformat()
    conn.close()


def test_a_sustained_outage_of_the_main_source_fails_the_run(tmp_path):
    from corning_events import main as main_module
    from corning_events import state

    conn = state.connect(tmp_path / "state.db")
    failure = main_module.FetchResult("flxcalendar", ok=False, note="timeout")

    for day in range(config.FLX_FAILURE_LIMIT):
        main_module.persist(conn, [failure], datetime(2026, 8, 9 + day, 9, 0, tzinfo=UTC))

    message = main_module.critical_outage(conn, [failure])
    assert message and "failed" in message
    conn.close()


def test_a_single_failure_does_not_fail_the_run(tmp_path):
    from corning_events import main as main_module
    from corning_events import state

    conn = state.connect(tmp_path / "state.db")
    failure = main_module.FetchResult("flxcalendar", ok=False, note="timeout")
    main_module.persist(conn, [failure], datetime(2026, 8, 9, 9, 0, tzinfo=UTC))

    assert main_module.critical_outage(conn, [failure]) is None
    conn.close()
