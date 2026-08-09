"""Deduplication: the match cascade, field resolution and UID pinning.

A duplicate here is not a data quality nuisance. It is an extra entry on two
phones, which is the failure the whole state store exists to prevent.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from corning_events import config, dedupe, state
from corning_events.main import pin_clusters
from corning_events.model import Event

UTC = timezone.utc
EVENING = datetime(2026, 8, 20, 23, 0, tzinfo=UTC)  # 7pm local, same UTC day
LATE = datetime(2026, 8, 21, 0, 0, tzinfo=UTC)  # 8pm local on 20 August


def event(source_id="flxcalendar", uid=None, title="Glassblowing Demo", start=EVENING, **kw):
    return Event(
        source_id=source_id,
        source_uid=uid or f"{source_id}-1",
        title=title,
        start=start,
        **kw,
    )


def reasons(a, b):
    return dedupe.match_reason(a, b)


# ---------------------------------------------------------------------------
# The cascade, rule by rule
# ---------------------------------------------------------------------------


def test_rule_1_matches_on_the_organizer_url():
    a = event("cmog", original_url="https://cmog.test/e/1?utm_source=news")
    b = event("chamber", title="Totally Different Name", original_url="https://cmog.test/e/1/")
    # Tracking parameters and a trailing slash are noise, not identity.
    assert reasons(a, b) == "original_url"


def test_rule_2_matches_on_title_day_and_venue():
    a = event("cmog", venue_name="Corning Museum of Glass")
    b = event("chamber", venue_name="Corning Museum of Glass", start=EVENING + timedelta(hours=2))
    assert reasons(a, b) == "title+day+venue"


def test_rule_3_matches_similar_titles_at_the_same_instant():
    a = event("cmog", title="Glassblowing Demonstration", venue_name="CMoG")
    b = event("chamber", title="Glassblowing Demonstrations", venue_name="CMoG")
    assert reasons(a, b) == "instant+venue+title"


def test_rule_4_matches_on_coordinates_when_venue_names_differ():
    a = event("cmog", title="Glassblowing Demonstration", venue_name="CMoG", lat=42.1489, lon=-77.0552)
    b = event(
        "chamber",
        title="Glassblowing Demonstrations",
        venue_name="Museum of Glass",
        lat=42.1490,
        lon=-77.0553,
    )
    assert reasons(a, b) == "instant+geo+title"


def test_rule_5_matches_a_title_that_gained_a_suffix():
    # The real case: character similarity scores this 0.79 against a 0.85
    # threshold, but it is plainly the same show.
    a = event("clemenscenter", title="CANCELLED - Wise Crackers All Stars")
    b = event("flxcalendar", title="Wise Crackers All Stars Comedy Show", venue_name="Clemens Center")
    assert reasons(a, b) == "instant+venue+containment"


def test_containment_ignores_very_short_titles():
    # "Yoga" sits inside far too many things to merge on.
    a = event("cmog", title="Yoga")
    b = event("chamber", title="Yoga in the Park with Live Music")
    assert reasons(a, b) is None


# ---------------------------------------------------------------------------
# What must never match
# ---------------------------------------------------------------------------


def test_two_records_from_one_source_never_merge():
    # FLXcalendar lists four separate showtimes for one theatre run. Merging
    # them would silently delete three performances.
    matinee = event("flxcalendar", uid="a", start=datetime(2026, 8, 15, 18, 0, tzinfo=UTC), venue_name="Clemens Center")
    evening = event("flxcalendar", uid="b", start=datetime(2026, 8, 15, 23, 30, tzinfo=UTC), venue_name="Clemens Center")
    assert reasons(matinee, evening) is None
    assert len(dedupe.cluster([matinee, evening])) == 2


def test_same_title_at_different_venues_stays_separate():
    a = event("cmog", title="Farmers Market", venue_name="Riverfront Centennial Park", city_tag="Corning")
    b = event("chamber", title="Farmers Market", venue_name="Dewitt Park", city_tag="Ithaca")
    assert reasons(a, b) is None


def test_same_title_in_different_cities_stays_separate():
    a = event("cmog", title="Farmers Market", city_tag="Corning")
    b = event("chamber", title="Farmers Market", city_tag="Ithaca")
    assert reasons(a, b) is None


def test_different_events_at_one_venue_stay_separate():
    a = event("cmog", title="Glassblowing Demo", venue_name="CMoG")
    b = event("chamber", title="Watercolour Workshop", venue_name="CMoG")
    assert reasons(a, b) is None


# ---------------------------------------------------------------------------
# Missing data and local dates
# ---------------------------------------------------------------------------


def test_a_missing_venue_does_not_block_a_match():
    # The Clemens Center feed publishes no LOCATION at all. Requiring venue
    # equality would make every one of its events a permanent duplicate.
    a = event("clemenscenter", venue_name=None)
    b = event("flxcalendar", venue_name="Clemens Center")
    assert reasons(a, b) == "title+day+venue"


def test_bucketing_uses_local_dates_not_utc_dates():
    # 8pm local on 20 August is already 21 August in UTC. Bucketing by UTC
    # date would put these on different days and never compare them.
    late = event("flxcalendar", start=LATE, venue_name="Clemens Center")
    all_day = event("clemenscenter", start=datetime(2026, 8, 20, tzinfo=UTC), all_day=True)
    assert len(dedupe.cluster([late, all_day])) == 1


def test_an_all_day_record_matches_a_timed_one_on_the_same_day():
    # One source publishes a multi-night run as an all-day span, another as
    # individual showtimes.
    span = event("clemenscenter", start=datetime(2026, 8, 14, tzinfo=UTC), all_day=True)
    showtime = event("flxcalendar", start=datetime(2026, 8, 14, 23, 30, tzinfo=UTC), venue_name="Clemens Center")
    assert len(dedupe.cluster([span, showtime])) == 1


# ---------------------------------------------------------------------------
# Clustering
# ---------------------------------------------------------------------------


def test_one_event_reported_by_three_sources_collapses_to_one():
    members = [
        event("cmog", title="Glassblowing Demonstration", venue_name="Corning Museum of Glass"),
        event("chamber", title="Glassblowing Demonstration", venue_name="Corning Museum of Glass"),
        event("flxcalendar", title="Glassblowing Demonstration @ Corning Museum of Glass"),
    ]
    clusters = dedupe.cluster(members)
    assert len(clusters) == 1
    assert len(clusters[0]) == 3


def test_clustering_is_transitive():
    # A matches B on url, B matches C on title. All three are one event.
    a = event("cmog", title="Quite Different", original_url="https://v.test/e")
    b = event("chamber", title="Glassblowing Demo", original_url="https://v.test/e")
    c = event("flxcalendar", title="Glassblowing Demo")
    assert len(dedupe.cluster([a, b, c])) == 1


def test_unrelated_events_are_left_alone():
    events = [
        event("cmog", title="Glassblowing Demo"),
        event("chamber", title="Poetry Reading"),
        event("flxcalendar", title="Farmers Market", start=EVENING + timedelta(days=3)),
    ]
    assert len(dedupe.cluster(events)) == 3


# ---------------------------------------------------------------------------
# Field resolution
# ---------------------------------------------------------------------------


def test_the_most_trusted_source_sets_the_title_and_timing():
    # Venue sites are authoritative on times and cancellations; aggregators
    # lag. Here that preserves a cancellation the aggregator had not caught.
    venue = event("clemenscenter", title="CANCELLED - Wise Crackers All Stars")
    aggregator = event("flxcalendar", title="Wise Crackers All Stars Comedy Show", venue_name="Clemens Center")
    resolved = dedupe.resolve([aggregator, venue])
    assert resolved.title == "CANCELLED - Wise Crackers All Stars"
    assert resolved.source_id == "clemenscenter"


def test_gaps_in_the_winning_record_are_filled_from_the_others():
    # The venue feed publishes no location; the aggregator does.
    venue = event("clemenscenter", title="Show", description=None)
    aggregator = event(
        "flxcalendar",
        title="Show",
        venue_name="Clemens Center",
        address="207 Clemens Center Parkway",
        city_tag="Elmira",
        description="Doors at seven.",
    )
    resolved = dedupe.resolve([venue, aggregator])
    assert resolved.source_id == "clemenscenter"
    assert resolved.venue_name == "Clemens Center"
    assert resolved.address == "207 Clemens Center Parkway"
    assert resolved.city_tag == "Elmira"
    assert resolved.description == "Doors at seven."


def test_venue_and_address_are_taken_from_one_record():
    # Half a location from each source would invent a place that is not real.
    a = event("clemenscenter", venue_name="Powers Theater", address=None)
    b = event("flxcalendar", venue_name="Mandeville Hall", address="207 Clemens Center Parkway")
    resolved = dedupe.resolve([a, b])
    assert (resolved.venue_name, resolved.address) == ("Powers Theater", None)


def test_coordinates_are_taken_as_a_pair():
    a = event("clemenscenter", lat=None, lon=None)
    b = event("flxcalendar", lat=42.0898, lon=-76.8077)
    resolved = dedupe.resolve([a, b])
    assert (resolved.lat, resolved.lon) == (42.0898, -76.8077)


def test_categories_are_pooled_across_sources():
    a = event("clemenscenter", categories=("Comedy",))
    b = event("flxcalendar", categories=("Performing Arts", "Comedy"), venue_name="Clemens Center")
    resolved = dedupe.resolve([a, b])
    assert set(resolved.categories) == {"Comedy", "Performing Arts"}


def test_a_lone_event_resolves_to_itself():
    solo = event("flxcalendar")
    assert dedupe.resolve([solo]) is solo


def test_completeness_breaks_a_trust_tie():
    sparse = event("cmog", uid="sparse")
    rich = event("rockwell", uid="rich", description="Full details", venue_name="V", city_tag="Corning")
    # Both are venue sites at the same trust level, so the fuller record wins.
    assert dedupe.resolve([sparse, rich]).source_uid == "rich"


# ---------------------------------------------------------------------------
# UID pinning across runs
# ---------------------------------------------------------------------------


@pytest.fixture
def conn():
    connection = state.connect(":memory:")
    yield connection
    connection.close()


def test_a_second_run_mints_no_new_clusters(conn):
    events = [
        event("cmog", title="Glassblowing Demo", venue_name="CMoG"),
        event("chamber", title="Glassblowing Demo", venue_name="CMoG"),
        event("flxcalendar", title="Poetry Reading"),
    ]
    first, _ = pin_clusters(conn, dedupe.deduplicate(events))
    before = {row["cluster_id"] for row in state.all_clusters(conn)}

    second, retired = pin_clusters(conn, dedupe.deduplicate(events))
    after = {row["cluster_id"] for row in state.all_clusters(conn)}

    assert before == after
    assert retired == []
    assert {uid for uid, _, _ in first} == {uid for uid, _, _ in second}


def test_a_uid_survives_a_change_of_canonical_source(conn):
    # Run one: only the aggregator knows about the event.
    aggregator = event("flxcalendar", title="Glassblowing Demo", venue_name="CMoG")
    (first_uid, _, _), = pin_clusters(conn, dedupe.deduplicate([aggregator]))[0]

    # Run two: the museum publishes it too and outranks the aggregator, so the
    # resolved record changes source. The published UID must not.
    museum = event("cmog", title="Glassblowing Demo", venue_name="CMoG")
    pinned, retired = pin_clusters(conn, dedupe.deduplicate([aggregator, museum]))

    uid, resolved, members = pinned[0]
    assert resolved.source_id == "cmog", "the museum should now win resolution"
    assert uid == first_uid, "a changed UID would duplicate the event on every phone"
    assert len(members) == 2
    assert retired == []


def test_converging_clusters_keep_the_older_uid_and_retire_the_other(conn):
    # Two records that looked distinct until a source filled in a venue.
    a = event("cmog", title="Glassblowing Demo", venue_name="CMoG")
    b = event("chamber", title="Watercolour Workshop", venue_name="CMoG")
    pinned, _ = pin_clusters(conn, dedupe.deduplicate([a, b]))
    assert len(pinned) == 2
    original = {uid for uid, _, _ in pinned}

    # Now they match, so the two clusters must become one.
    b_renamed = event("chamber", title="Glassblowing Demo", venue_name="CMoG")
    merged, retired = pin_clusters(conn, dedupe.deduplicate([a, b_renamed]))

    assert len(merged) == 1
    surviving = merged[0][0]
    assert surviving in original
    assert retired and retired[0] in original - {surviving}
    assert len(state.all_clusters(conn)) == 1


def test_every_member_key_resolves_to_the_cluster_uid(conn):
    events = [
        event("cmog", title="Glassblowing Demo", venue_name="CMoG"),
        event("chamber", title="Glassblowing Demo", venue_name="CMoG"),
    ]
    pinned, _ = pin_clusters(conn, dedupe.deduplicate(events))
    (uid, _, members), = pinned
    for member in members:
        assert state.cluster_for_member(conn, member.key)["published_uid"] == uid
