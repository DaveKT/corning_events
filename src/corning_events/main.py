"""Pipeline orchestrator and command line entry point.

Run with:

    python -m corning_events.main --dry-run

The full pipeline is assembled across milestones M1 to M4. Until then this
resolves which sources would run and reports what is not built yet.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import config
from .sources import FETCHERS


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

    raise SystemExit(
        "The pipeline is not implemented yet. M1 adds the model, state store, "
        "normalizers and emitter; M2 adds the Tier A parsers."
    )


if __name__ == "__main__":
    sys.exit(main())
