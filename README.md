Corning Events
===============================================================================

A background service that aggregates public leisure events in and around
Corning, New York and publishes them as subscribable iCal feeds.

Events are collected daily from regional calendars, venue sites and ticketing
APIs, deduplicated across sources, filtered by distance from Corning, and
written out as static `.ics` files served over HTTPS by GitHub Pages. Anyone
with the feed URL can subscribe once in Apple Calendar, Google Calendar or any
other client and see the events alongside their own.

## Status

Planning complete, implementation not yet started. See
[the plan](doc/plans/2026-08-corning-events-ical-aggregator.md) for the current
milestone state. The feed URLs below will not resolve until milestone M6 lands.

## Feeds

| Feed | Contents | Subscribe |
|---|---|---|
| `corning-core.ics` | Core and Near rings, up to 25 miles, all categories. The default. | [https](https://davekt.github.io/corning_events/corning-core.ics) / [webcal](webcal://davekt.github.io/corning_events/corning-core.ics) |
| `flx-all.ics` | Everything within 50 miles, including Ithaca and the Finger Lakes. | [https](https://davekt.github.io/corning_events/flx-all.ics) / [webcal](webcal://davekt.github.io/corning_events/flx-all.ics) |

On iOS the `webcal://` link subscribes in one tap. Google Calendar refreshes
external subscriptions on its own schedule, often many hours behind, and offers
no way to force it.

## Geographic scope

Anchored at 42.1481, -77.0569. Events are classified into rings by city tag,
coordinates, or source, and anything beyond fifty miles is dropped.

| Ring | Radius | Examples |
|---|---|---|
| Core | 0 to 10 miles | Corning, Painted Post, Erwin, Big Flats, Horseheads |
| Near | 10 to 25 miles | Elmira, Bath, Watkins Glen, Addison |
| Regional | 25 to 50 miles | Hammondsport, Ithaca, Penn Yan, Owego, Geneva |

## Documentation

| Path | Contents |
|---|---|
| [`doc/plans/`](doc/plans/) | Development plans, prefixed by year and month |
| [`doc/corning-events-source-spec.md`](doc/corning-events-source-spec.md) | Source registry: every data source, its format, quirks and known breakage |

Note the difference between `doc/` and `docs/`. `doc/` is human documentation and
is edited by hand. `docs/` is the GitHub Pages web root, contains only generated
output, and must never be hand-edited. The awkward pairing exists because
branch-based GitHub Pages will only serve from the repository root or from
`/docs`.

## Running locally

```bash
pip install -r requirements.txt
python -m corning_events.main --dry-run
```

Useful flags: `--sources flxcalendar,ssclibrary` limits the run to named
sources, `--dry-run` skips writing feeds and state, and `--db PATH` points at an
alternative state database.

Tests run offline against saved fixtures:

```bash
pip install -r requirements-dev.txt
pytest
```

`TICKETMASTER_API_KEY` enables the Ticketmaster source. Without it that one
source skips with a warning and everything else runs normally.

## How it works

The daily GitHub Actions run fetches every enabled source, upserts the results
into a SQLite database committed alongside the code, clusters duplicate records
across sources, resolves conflicting field values by source trust order, detects
cancellations, and emits the feeds. State persists in the repository because
stable UIDs, SEQUENCE increments and cancellation detection all require knowing
what the previous run published.

Two properties matter most and are covered by tests. Published UIDs are minted
once per event cluster and never regenerated, because a changing UID makes
subscribers accumulate duplicates. And cancellation only ever fires for a source
whose fetch actually succeeded and returned events, because otherwise one failed
request would wipe a subscriber's calendar.

## Adding a source

Write a module in `src/corning_events/sources/` exposing a fetch function that
returns a list of `Event`, register it in `sources/__init__.py`, add a config
entry with a default ring, and save one raw capture into `tests/fixtures/` with
a parser test against it. Nothing else in the pipeline needs to change.

## Attribution and etiquette

Feeds redistribute listings published by other organizations, so every event
carries its originating source name and a link back in the description, and the
URL property points at the organizer's own page wherever one is known. Requests
identify themselves with a descriptive User-Agent and run at most once per day
per source.

Feed URLs are public. An unlisted URL is obscurity rather than access control,
so nothing private appears in any event field.
