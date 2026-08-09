"""End to end pipeline behaviour, exercised without a network.

These drive the same `run_pipeline` a real run uses, feeding it synthetic
fetch results, so the guarantees asserted here are the ones production has.

Two of them matter more than the rest. Idempotency, because a feed that
changes when nothing changed makes every client re-notify. And the outage
guard, because the alternative is a failed request quietly emptying two
people's calendars.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

import pytest
from icalendar import Calendar

from corning_events import config, feeds, state
from corning_events.main import FetchResult, run_pipeline
from corning_events.model import STATUS_CANCELLED, STATUS_CONFIRMED, Event

UTC = timezone.utc
NOW = datetime(2026, 8, 9, 12, 0, tzinfo=UTC)
CORE_PATH = config.DOCS_DIR / "corning-core.ics"

DTSTAMP_LINE = re.compile(rb"^DTSTAMP:.*$", re.MULTILINE)


@pytest.fixture
def conn():
    connection = state.connect(":memory:")
    yield connection
    connection.close()


# Events sit well beyond the cancellation retention window, so that a test
# advancing the clock to check retention does not simply age them out first.
FIRST_EVENT_OFFSET = timedelta(days=60)


def make_events(count=8, source_id="flxcalendar", **overrides) -> list[Event]:
    """Enough Corning events to clear the sanity floor."""
    events = []
    for index in range(count):
        fields = dict(
            source_id=source_id,
            source_uid=f"{source_id}-{index}",
            title=f"Corning Event {index}",
            start=NOW + FIRST_EVENT_OFFSET + timedelta(days=index),
            city_tag="Corning",
            venue_name=f"Venue {index}",
        )
        fields.update(overrides)
        events.append(Event(**fields))
    return events


def run(conn, events, now=NOW, ok=True, source_id="flxcalendar", note=None):
    results = [FetchResult(source_id, ok=ok, events=events, note=note)]
    return run_pipeline(conn, results, run_at=now, now=now)


def vevents(data: bytes):
    return {str(c["UID"]): c for c in Calendar.from_ical(data).walk("VEVENT")}


def strip_dtstamp(data: bytes) -> bytes:
    return DTSTAMP_LINE.sub(b"DTSTAMP:REDACTED", data)


# ---------------------------------------------------------------------------
# Idempotency
# ---------------------------------------------------------------------------


def test_two_identical_runs_produce_identical_feeds(conn):
    events = make_events()
    _, first = run(conn, events)
    published, second = run(conn, events, now=NOW + timedelta(days=1))

    for path in first:
        if path.suffix != ".ics":
            continue
        assert strip_dtstamp(first[path]) == strip_dtstamp(second[path]), (
            f"{path.name} changed between runs with no upstream change"
        )
    assert all(item.sequence == 0 for item in published)


def test_an_unchanged_event_never_bumps_its_sequence(conn):
    events = make_events()
    for day in range(4):
        published, _ = run(conn, events, now=NOW + timedelta(days=day))
    assert {item.sequence for item in published} == {0}


def test_uids_are_stable_across_runs(conn):
    events = make_events()
    first, _ = run(conn, events)
    second, _ = run(conn, events, now=NOW + timedelta(days=1))
    assert {i.uid for i in first} == {i.uid for i in second}


def test_a_real_change_bumps_the_sequence_once(conn):
    events = make_events()
    run(conn, events)

    events[0].title = "Corning Event 0, Rescheduled"
    published, _ = run(conn, events, now=NOW + timedelta(days=1))
    changed = [i for i in published if i.event.title.endswith("Rescheduled")]
    assert changed and changed[0].sequence == 1

    # Running again with the same content must not bump it further.
    published, _ = run(conn, events, now=NOW + timedelta(days=2))
    changed = [i for i in published if i.event.title.endswith("Rescheduled")]
    assert changed[0].sequence == 1


# ---------------------------------------------------------------------------
# Cancellation
# ---------------------------------------------------------------------------


def test_an_event_that_vanishes_upstream_is_published_as_cancelled(conn):
    events = make_events()
    first, _ = run(conn, events)
    doomed = next(i for i in first if i.event.source_uid == "flxcalendar-0")

    # The source stops listing it while its start is still in the future.
    published, outputs = run(conn, events[1:], now=NOW + timedelta(days=1))

    cancelled = next(i for i in published if i.uid == doomed.uid)
    assert cancelled.status == STATUS_CANCELLED
    assert cancelled.sequence == 1, "clients ignore changes without a SEQUENCE bump"
    assert cancelled.cancelled_at is not None

    # It stays in the feed so subscribers see the cancellation.
    component = vevents(outputs[CORE_PATH])[doomed.uid]
    assert str(component["STATUS"]) == STATUS_CANCELLED


def test_a_cancelled_event_keeps_its_original_cancellation_date(conn):
    events = make_events()
    run(conn, events)
    published, _ = run(conn, events[1:], now=NOW + timedelta(days=1))
    first_cancelled_at = next(
        i.cancelled_at for i in published if i.status == STATUS_CANCELLED
    )

    # Refreshing the timestamp each run would push the retention deadline
    # forward forever, and the event would never leave the feed.
    published, _ = run(conn, events[1:], now=NOW + timedelta(days=5))
    still = next(i.cancelled_at for i in published if i.status == STATUS_CANCELLED)
    assert still == first_cancelled_at


def test_a_cancelled_event_leaves_the_feed_after_the_retention_window(conn):
    events = make_events(count=9)
    first, _ = run(conn, events)
    doomed = next(i for i in first if i.event.source_uid == "flxcalendar-0")

    run(conn, events[1:], now=NOW + timedelta(days=1))
    beyond = NOW + timedelta(days=config.CANCELLED_RETENTION_DAYS + 2)
    _, outputs = run(conn, events[1:], now=beyond)

    assert doomed.uid not in vevents(outputs[CORE_PATH])


def test_a_past_event_disappearing_is_not_a_cancellation(conn):
    # Feeds drop events once they have happened. That is housekeeping.
    past = Event(
        source_id="flxcalendar",
        source_uid="already-happened",
        title="Last Week's Concert",
        start=NOW - timedelta(days=7),
        city_tag="Corning",
    )
    run(conn, make_events() + [past])
    published, _ = run(conn, make_events(), now=NOW + timedelta(days=1))

    gone = [i for i in published if i.event.source_uid == "already-happened"]
    assert not gone or gone[0].status == STATUS_CONFIRMED


# ---------------------------------------------------------------------------
# The outage guard
# ---------------------------------------------------------------------------


def test_a_failed_fetch_cancels_nothing(conn):
    events = make_events()
    run(conn, events)

    # The source raises rather than returning events.
    published, outputs = run(
        conn, [], now=NOW + timedelta(days=1), ok=False, note="RequestException: timeout"
    )

    assert all(item.status == STATUS_CONFIRMED for item in published)
    assert len(published) == len(events), "stored events must survive an outage"
    assert len(vevents(outputs[CORE_PATH])) == len(events)


def test_an_empty_but_successful_fetch_cancels_nothing(conn):
    # A redesigned page that parses to zero events looks identical to every
    # event being cancelled at once. It is not treated as one.
    events = make_events()
    run(conn, events)
    published, _ = run(conn, [], now=NOW + timedelta(days=1), ok=True)

    assert all(item.status == STATUS_CONFIRMED for item in published)
    assert len(published) == len(events)


def test_one_source_failing_does_not_cancel_another_sources_events(conn):
    flx = make_events(source_id="flxcalendar")
    venue = make_events(count=2, source_id="cmog", title="Museum Talk")
    run_pipeline(
        conn,
        [FetchResult("flxcalendar", True, flx), FetchResult("cmog", True, venue)],
        run_at=NOW,
        now=NOW,
    )

    later = NOW + timedelta(days=1)
    published, _ = run_pipeline(
        conn,
        [
            FetchResult("flxcalendar", True, flx),
            FetchResult("cmog", False, [], note="timeout"),
        ],
        run_at=later,
        now=later,
    )
    assert all(item.status == STATUS_CONFIRMED for item in published)


# ---------------------------------------------------------------------------
# Feed contents and safety
# ---------------------------------------------------------------------------


def test_feeds_split_by_ring(conn):
    near = make_events(count=6, city_tag="Corning")
    far = make_events(count=3, source_id="clemenscenter", city_tag="Ithaca")
    far = [Event(**{**vars(e), "source_uid": f"reg-{i}"}) for i, e in enumerate(far)]

    _, outputs = run_pipeline(
        conn,
        [FetchResult("flxcalendar", True, near), FetchResult("clemenscenter", True, far)],
        run_at=NOW,
        now=NOW,
    )
    core = vevents(outputs[config.DOCS_DIR / "corning-core.ics"])
    firehose = vevents(outputs[config.DOCS_DIR / "flx-all.ics"])

    assert len(core) == 6, "Ithaca is Regional and belongs only in the firehose"
    assert len(firehose) == 9


def test_every_emitted_feed_is_valid_ical(conn):
    _, outputs = run(conn, make_events())
    for path, data in outputs.items():
        if path.suffix == ".ics":
            feeds.validate(data, expected_count=len(vevents(data)))


def test_an_empty_result_refuses_to_overwrite_a_good_feed(conn):
    # The sanity floor. Publishing an empty calendar fails silently: the
    # subscription still works and simply shows nothing.
    with pytest.raises(feeds.FeedValidationError, match="sanity floor"):
        run(conn, make_events(count=2))


# ---------------------------------------------------------------------------
# Index page
# ---------------------------------------------------------------------------


def test_the_index_page_lists_both_feeds_with_working_links(conn):
    _, outputs = run(conn, make_events())
    page = outputs[config.DOCS_DIR / "index.html"].decode("utf-8")

    for feed in config.FEEDS:
        assert feed.calendar_name in page
        assert f"{config.PAGES_BASE_URL}/{feed.filename}" in page
    # One tap subscription on iOS needs the webcal scheme.
    assert page.count("webcal://") == len(config.FEEDS)
    assert "<!doctype html>" in page.lower()


def test_the_index_page_reports_the_event_counts(conn):
    _, outputs = run(conn, make_events(count=8))
    page = outputs[config.DOCS_DIR / "index.html"].decode("utf-8")
    assert "Currently 8 events" in page
