"""State store behaviour, focused on what drives correctness across runs."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from corning_events import state
from corning_events.model import STATUS_CANCELLED, STATUS_CONFIRMED, Event

UTC = timezone.utc
NOW = datetime(2026, 8, 9, 12, 0, tzinfo=UTC)
RUN_1 = datetime(2026, 8, 9, 9, 0, tzinfo=UTC)
RUN_2 = datetime(2026, 8, 10, 9, 0, tzinfo=UTC)


@pytest.fixture
def conn():
    connection = state.connect(":memory:")
    yield connection
    connection.close()


def make_event(uid="uid-1", source_id="flxcalendar", days_ahead=7, **overrides) -> Event:
    defaults = dict(
        source_id=source_id,
        source_uid=uid,
        title="A Concert",
        start=NOW + timedelta(days=days_ahead),
    )
    defaults.update(overrides)
    return Event(**defaults)


def test_migrate_is_idempotent(conn):
    state.migrate(conn)
    state.migrate(conn)
    tables = {
        row["name"]
        for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }
    assert {"raw_events", "clusters", "published", "fetch_log"} <= tables


def test_upsert_round_trips_an_event(conn):
    event = make_event(
        description="Live music",
        venue_name="The Rockwell Museum",
        lat=42.1481,
        lon=-77.0569,
        categories=("Music",),
    )
    state.upsert_raw_event(conn, event, RUN_1)
    restored = state.get_raw_event(conn, "flxcalendar", "uid-1")
    assert restored == event


def test_first_seen_survives_updates_while_last_seen_advances(conn):
    state.upsert_raw_event(conn, make_event(), RUN_1)
    state.upsert_raw_event(conn, make_event(title="Renamed"), RUN_2)

    row = conn.execute("SELECT first_seen, last_seen FROM raw_events").fetchone()
    assert row["first_seen"] == RUN_1.isoformat()
    assert row["last_seen"] == RUN_2.isoformat()
    assert state.get_raw_event(conn, "flxcalendar", "uid-1").title == "Renamed"


def test_stale_keys_finds_future_events_this_run_did_not_report(conn):
    state.upsert_raw_event(conn, make_event("kept"), RUN_1)
    state.upsert_raw_event(conn, make_event("vanished"), RUN_1)
    # Second run reports only one of them.
    state.upsert_raw_event(conn, make_event("kept"), RUN_2)

    stale = state.stale_member_keys(conn, "flxcalendar", RUN_2, NOW)
    assert stale == {"flxcalendar:vanished"}


def test_stale_keys_ignores_events_already_in_the_past(conn):
    # A past event dropping out of a feed is housekeeping, not a cancellation.
    state.upsert_raw_event(conn, make_event("old", days_ahead=-3), RUN_1)
    assert state.stale_member_keys(conn, "flxcalendar", RUN_2, NOW) == set()


def test_stale_keys_are_scoped_to_one_source(conn):
    state.upsert_raw_event(conn, make_event("a", source_id="flxcalendar"), RUN_1)
    state.upsert_raw_event(conn, make_event("b", source_id="cmog"), RUN_1)
    assert state.stale_member_keys(conn, "cmog", RUN_2, NOW) == {"cmog:b"}


def test_clusters_pin_their_published_uid(conn):
    cluster_id = state.create_cluster(conn, "uid@corning-events", ["flxcalendar:a"])
    # A later run merges a second source into the same cluster.
    state.update_cluster_members(conn, cluster_id, ["flxcalendar:a", "cmog:b"])

    for key in ("flxcalendar:a", "cmog:b"):
        row = state.cluster_for_member(conn, key)
        assert row["published_uid"] == "uid@corning-events"
        assert row["cluster_id"] == cluster_id


def test_cluster_lookup_returns_none_for_unknown_members(conn):
    assert state.cluster_for_member(conn, "nope:1") is None


def test_sequence_holds_steady_when_content_is_unchanged(conn):
    assert state.record_published(conn, "uid@x", "hash-a") == 0
    assert state.record_published(conn, "uid@x", "hash-a") == 0
    assert state.record_published(conn, "uid@x", "hash-a") == 0


def test_sequence_increments_only_on_a_content_change(conn):
    assert state.record_published(conn, "uid@x", "hash-a") == 0
    assert state.record_published(conn, "uid@x", "hash-b") == 1
    assert state.record_published(conn, "uid@x", "hash-b") == 1
    assert state.record_published(conn, "uid@x", "hash-c") == 2


def test_sequence_increments_when_an_event_is_cancelled(conn):
    state.record_published(conn, "uid@x", "hash-a")
    bumped = state.record_published(conn, "uid@x", "hash-a", STATUS_CANCELLED, NOW)
    assert bumped == 1
    assert state.get_published(conn, "uid@x")["status"] == STATUS_CANCELLED


def test_cancelled_events_expire_after_the_retention_window(conn):
    long_ago = NOW - timedelta(days=45)
    state.record_published(conn, "old@x", "h", STATUS_CANCELLED, long_ago)
    state.record_published(conn, "recent@x", "h", STATUS_CANCELLED, NOW)
    state.record_published(conn, "live@x", "h", STATUS_CONFIRMED)

    cutoff = NOW - timedelta(days=30)
    assert state.cancelled_before(conn, cutoff) == ["old@x"]

    state.forget_published(conn, ["old@x"])
    assert state.get_published(conn, "old@x") is None


def test_consecutive_failures_counts_back_from_the_latest_run(conn):
    state.record_fetch(conn, "flxcalendar", RUN_1, ok=True, event_count=100)
    assert state.consecutive_failures(conn, "flxcalendar") == 0

    state.record_fetch(conn, "flxcalendar", RUN_2, ok=False, event_count=0, note="timeout")
    state.record_fetch(conn, "flxcalendar", RUN_2 + timedelta(days=1), ok=False, event_count=0)
    assert state.consecutive_failures(conn, "flxcalendar") == 2

    # A success resets the count.
    state.record_fetch(conn, "flxcalendar", RUN_2 + timedelta(days=2), ok=True, event_count=90)
    assert state.consecutive_failures(conn, "flxcalendar") == 0


def test_transaction_rolls_back_on_failure(conn):
    # A partial write would corrupt cancellation detection on the next run.
    with pytest.raises(RuntimeError):
        with state.transaction(conn):
            state.upsert_raw_event(conn, make_event("a"), RUN_1)
            raise RuntimeError("boom")
    assert state.get_raw_event(conn, "flxcalendar", "a") is None


def test_prune_drops_only_events_well_in_the_past(conn):
    state.upsert_raw_event(conn, make_event("old", days_ahead=-400), RUN_1)
    state.upsert_raw_event(conn, make_event("new", days_ahead=5), RUN_1)
    removed = state.prune_raw_events(conn, NOW - timedelta(days=90))
    assert removed == 1
    assert state.get_raw_event(conn, "flxcalendar", "new") is not None


def test_prune_fetch_log_keeps_recent_rows_per_source(conn):
    for day in range(10):
        state.record_fetch(conn, "flxcalendar", RUN_1 + timedelta(days=day), True, 5)
        state.record_fetch(conn, "cmog", RUN_1 + timedelta(days=day), True, 5)
    state.prune_fetch_log(conn, keep_per_source=3)
    counts = dict(
        conn.execute("SELECT source_id, COUNT(*) c FROM fetch_log GROUP BY source_id")
    )
    assert counts == {"flxcalendar": 3, "cmog": 3}
