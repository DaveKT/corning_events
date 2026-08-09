"""Pipeline orchestrator and command line entry point.

Run with:

    python -m corning_events.main --dry-run

The full pipeline is assembled across milestones M1 to M4. Until then this
resolves which sources would run and reports what is not built yet.
"""

from __future__ import annotations

import argparse
import sys
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from . import config, normalize
from .http import Fetcher
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

    results = fetch_all(selected)
    report(results)

    raise SystemExit(
        "Fetching works; the rest of the pipeline does not exist yet. M3 adds "
        "deduplication and persistence, M4 emits the feeds."
    )


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
