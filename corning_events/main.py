"""Pipeline orchestrator and command line entry point.

Run with:

    python -m corning_events.main --dry-run

Fetches every enabled source, stores the results, deduplicates across sources,
detects cancellations, and writes the published feeds and index page.
"""

from __future__ import annotations

import argparse
import sys
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path

from . import config, dedupe, feeds, normalize, state
from .http import Fetcher
from .model import STATUS_CANCELLED, STATUS_CONFIRMED, UTC, PublishedEvent
from .sources import FETCHERS
from .sources.ticketmaster import MissingApiKey


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="python -m corning_events.main",
        description=(
            "Fetch events from the configured sources, deduplicate them, and "
            "publish iCal feeds."
        ),
    )
    parser.add_argument(
        "--sources",
        metavar="IDS",
        help=(
            "Comma separated source ids to run, overriding the enabled flags "
            "in config. Defaults to every enabled source."
        ),
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Run without writing feeds or touching the state database.",
    )
    parser.add_argument(
        "--db",
        metavar="PATH",
        type=Path,
        default=config.STATE_DB,
        help=f"State database path. Defaults to {config.STATE_DB}.",
    )
    return parser.parse_args(argv)


def resolve_sources(requested: str | None) -> list[config.SourceConfig]:
    """Pick the sources to run.

    Without --sources this is every source whose enabled flag is set. With it,
    the named sources run regardless of their enabled flag, which is how a
    single parser gets exercised while it is being written.
    """
    if requested is None:
        return config.enabled_sources()

    names = [name.strip() for name in requested.split(",") if name.strip()]
    unknown = sorted(set(names) - set(config.SOURCES))
    if unknown:
        known = ", ".join(sorted(config.SOURCES))
        raise SystemExit(
            f"Unknown source ids: {', '.join(unknown)}\nKnown source ids: {known}"
        )
    return [config.SOURCES[name] for name in names]


def main(argv: list[str] | None = None) -> int:
    # Line buffered so progress interleaves correctly with anything written to
    # stderr, which matters when the output is piped or captured by CI.
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(line_buffering=True)

    args = parse_args(argv)
    selected = resolve_sources(args.sources)

    if not selected:
        print("No sources enabled.")
        print(
            "Every source in config.SOURCES has enabled=False. Sources are "
            "switched on as their parsers land, Tier A in M2 and Tier B in M5."
        )
        print("Pass --sources <id> to run one explicitly. Known ids:")
        for source in sorted(config.SOURCES.values(), key=lambda s: s.source_id):
            print(f"  {source.source_id:<14} {source.name}")
        return 0

    # Flushed so that progress stays in order relative to anything written to
    # stderr when the output is piped.
    print(
        f"Selected {len(selected)} source(s): "
        + ", ".join(s.source_id for s in selected),
        flush=True,
    )
    if args.dry_run:
        print("Dry run: no feeds or state will be written.", flush=True)

    unimplemented = [s.source_id for s in selected if s.source_id not in FETCHERS]
    if unimplemented:
        raise SystemExit(
            "No fetcher registered for: " + ", ".join(sorted(unimplemented))
        )

    run_at = now = datetime.now(UTC)
    results = fetch_all(selected)
    report(results)

    conn = state.connect(args.db)
    try:
        published, rendered = run_pipeline(conn, results, run_at, now)

        if args.dry_run:
            conn.rollback()
            print("  dry run: state rolled back, no files written")
        else:
            for path, data in rendered.items():
                feeds.write(path, data)
            print(f"  wrote {len(rendered)} files to {config.DOCS_DIR}")
            conn.commit()
        outage = critical_outage(conn, results)
    finally:
        conn.close()

    if outage:
        raise SystemExit(outage)

    return 0


def run_pipeline(
    conn, results: list["FetchResult"], run_at: datetime, now: datetime
) -> tuple[list[PublishedEvent], dict[Path, bytes]]:
    """Everything between fetching and writing.

    Kept as one function so that tests exercise the same path a real run takes
    rather than a reimplementation of it. Nothing here touches the filesystem
    or the network, and the caller decides whether to commit.
    """
    persist(conn, results, run_at)

    known = state.all_raw_events(conn)
    pinned, retired = pin_clusters(conn, dedupe.deduplicate(known))

    print(f"\n{len(known)} stored events -> {len(pinned)} published events")
    merged = sum(1 for _, _, members in pinned if len(members) > 1)
    print(f"  {merged} formed from more than one source")

    if retired:
        # These are not cancellations. Two entries turned out to be one event,
        # and the survivor is still in the feed under the older UID, so the
        # client should simply drop the duplicate. Publishing CANCELLED here
        # would show "cancelled" beside a live copy of the same event.
        state.forget_published(conn, retired)
        print(f"  {len(retired)} duplicate UIDs retired into another cluster")

    stale = stale_keys(conn, results, run_at, now)
    published = build_published(conn, pinned, stale, now)

    cancelled = sum(1 for item in published if item.status == STATUS_CANCELLED)
    if cancelled:
        print(
            f"  {cancelled} cancelled upstream, retained for "
            f"{config.CANCELLED_RETENTION_DAYS} days"
        )

    expire(conn, now)
    return published, render(published, now)


def persist(conn, results: list["FetchResult"], run_at: datetime) -> None:
    """Store this run's events and record each source's outcome.

    A source that failed is logged but its stored events are left exactly as
    they were, so the next stage still publishes them and cancellation cannot
    misread an outage as every event disappearing at once.
    """
    for result in results:
        if result.ok:
            state.upsert_raw_events(conn, result.events, run_at)
        state.record_fetch(
            conn,
            result.source_id,
            run_at,
            ok=result.ok,
            event_count=result.count,
            note=result.note,
        )


def pin_clusters(conn, clusters: list[tuple]) -> tuple[list[tuple], list[str]]:
    """Attach a stable published UID to each cluster.

    A UID is minted once, the first time a cluster appears, and reused for the
    life of that cluster no matter which source later wins field resolution.
    Regenerating it would make every subscriber's calendar accumulate a fresh
    copy of the same event (build plan, Part 1 adjustment 6).
    """
    pinned = []
    retired: list[str] = []

    for resolved, members in clusters:
        keys = [member.key for member in members]
        existing = [row for row in (state.cluster_for_member(conn, key) for key in keys) if row]

        if not existing:
            uid = feeds.mint_uid(members[0])
            state.create_cluster(conn, uid, keys)
        else:
            # Two clusters can converge once a source fills in a field that
            # had been missing. The oldest keeps its UID, since its events are
            # the ones already sitting in subscribers' calendars.
            survivor = min(existing, key=lambda row: row["cluster_id"])
            uid = survivor["published_uid"]
            for row in {row["cluster_id"]: row for row in existing}.values():
                if row["cluster_id"] != survivor["cluster_id"]:
                    retired.append(row["published_uid"])
                    state.delete_cluster(conn, row["cluster_id"])
            state.update_cluster_members(conn, survivor["cluster_id"], keys)

        pinned.append((uid, resolved, members))

    return pinned, retired


def stale_keys(
    conn, results: list["FetchResult"], run_at: datetime, now: datetime
) -> set[str]:
    """Member keys that vanished upstream this run, still in the future.

    Only sources that both succeeded and returned something are consulted. A
    failed or empty fetch contributes nothing, so its events can never be read
    as having disappeared, which is the whole point of Part 1 adjustment 3: a
    single timeout must not empty a subscriber's calendar.
    """
    keys: set[str] = set()
    for result in results:
        if result.ok and result.count > 0:
            keys |= state.stale_member_keys(conn, result.source_id, run_at, now)
    return keys


def build_published(
    conn, pinned: list[tuple], stale: set[str], now: datetime
) -> list[PublishedEvent]:
    """Attach status and SEQUENCE to each cluster.

    A cluster counts as cancelled only when every one of its members has gone.
    While any source still lists it, the event is still happening.
    """
    published: list[PublishedEvent] = []

    for uid, resolved, members in pinned:
        gone = all(member.key in stale for member in members)

        if gone and state.get_published(conn, uid) is None:
            # Nothing a subscriber has ever seen, so there is nothing to
            # cancel. Either the cancellation already served its retention
            # window and was expired, in which case re-recording it here
            # would resurrect it with a fresh cancelled_at and a SEQUENCE
            # reset to zero, forever; or it was never published at all. The
            # raw rows linger until pruned, and this skip repeats until then.
            continue

        status = STATUS_CANCELLED if gone else STATUS_CONFIRMED

        item = PublishedEvent(uid=uid, event=resolved, status=status)
        sequence = state.record_published(
            conn,
            uid,
            content_hash=feeds.content_hash(item),
            status=status,
            cancelled_at=now if gone else None,
        )
        row = state.get_published(conn, uid)
        item.sequence = sequence
        item.cancelled_at = (
            datetime.fromisoformat(row["cancelled_at"]) if row["cancelled_at"] else None
        )
        published.append(item)

    return published


def expire(conn, now: datetime) -> None:
    """Forget events cancelled long enough ago, and prune old rows.

    Orphan pruning runs after the raw prune on purpose: it is the raw
    deletions that strand cluster and published rows, and cleaning them in
    the same pass is what keeps the committed database from growing without
    bound.
    """
    cutoff = now - timedelta(days=config.CANCELLED_RETENTION_DAYS)
    state.forget_published(conn, state.cancelled_before(conn, cutoff))
    state.prune_raw_events(conn, now - timedelta(days=config.RAW_EVENT_RETENTION_DAYS))
    state.prune_orphans(conn)
    state.prune_fetch_log(conn)


def render(published: list[PublishedEvent], now: datetime) -> dict[Path, bytes]:
    """Build and validate every feed before a single byte is written.

    Validation happens up front so that a broken run leaves the previous, good
    files in place. An empty or invalid feed is worse than a stale one: it
    fails silently, showing subscribers a calendar with nothing in it.
    """
    outputs: dict[Path, bytes] = {}
    counts: dict[str, int] = {}

    for feed in config.FEEDS:
        selected = feeds.select_for_feed(feed, published, now)
        data = feeds.emit(feed, selected, dtstamp=now)
        feeds.validate(data, expected_count=len(selected), min_events=feed.min_events)
        outputs[config.DOCS_DIR / feed.filename] = data
        counts[feed.slug] = len(selected)
        print(f"  {feed.filename}: {len(selected)} events, {len(data) / 1024:.0f} KB")

    outputs[config.DOCS_DIR / "index.html"] = feeds.render_index(counts, now).encode("utf-8")
    return outputs


def critical_outage(conn, results: list["FetchResult"]) -> str | None:
    """Report a sustained outage of the highest volume source.

    A quietly shrinking feed is worse than a failed build: nobody notices it
    until someone misses an event. Returning a message rather than exiting
    lets the caller finish writing state first.
    """
    for result in results:
        if result.source_id != "flxcalendar" or result.ok:
            continue
        print(
            f"\nWARNING: {result.source_id} failed. Its stored events are "
            "retained and nothing has been cancelled."
        )
        failures = state.consecutive_failures(conn, result.source_id)
        if failures >= config.FLX_FAILURE_LIMIT:
            return (
                f"{result.source_id} has failed {failures} runs in a row, at or "
                f"over the limit of {config.FLX_FAILURE_LIMIT}. Failing the run "
                "so this surfaces as an alert rather than a shrinking feed."
            )
    return None


@dataclass
class FetchResult:
    """Outcome of one source's fetch.

    ``ok`` distinguishes a source that genuinely returned nothing from one
    that failed. Only the former may ever drive cancellation (build plan,
    Part 1 adjustment 3).
    """

    source_id: str
    ok: bool
    events: list = field(default_factory=list)
    note: str | None = None

    @property
    def count(self) -> int:
        return len(self.events)


def fetch_all(selected: list[config.SourceConfig]) -> list[FetchResult]:
    """Fetch every selected source, isolating failures to one source.

    One broken parser must never take the pipeline down with it: the other
    sources still have events to publish, and the failed source's previous
    events stay untouched rather than looking cancelled.
    """
    results = []
    with Fetcher() as http:
        for source in selected:
            print(f"  fetching {source.source_id} ...", end=" ", flush=True)
            try:
                events = FETCHERS[source.source_id](http)
            except MissingApiKey as exc:
                print("skipped")
                results.append(FetchResult(source.source_id, ok=False, note=str(exc)))
            except Exception as exc:
                print("FAILED")
                results.append(
                    FetchResult(
                        source.source_id,
                        ok=False,
                        note=f"{type(exc).__name__}: {exc}",
                    )
                )
            else:
                print(f"{len(events)} events")
                results.append(FetchResult(source.source_id, ok=True, events=events))
    return results


def report(results: list[FetchResult]) -> None:
    total = sum(r.count for r in results if r.ok)
    rings = Counter(
        normalize.classify_ring(event)
        for result in results
        if result.ok
        for event in result.events
    )

    print(f"\n{total} events fetched")
    if rings:
        print("  by ring: " + ", ".join(f"{r}={rings[r]}" for r in config.RING_ORDER if rings[r]))
    for result in results:
        if not result.ok:
            print(f"  {result.source_id}: {result.note}")


if __name__ == "__main__":
    sys.exit(main())
