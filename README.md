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

Live and running. A GitHub Actions workflow rebuilds the feeds every morning
and publishes them to GitHub Pages. Each run collects around 538 events from
five sources and publishes 518, with 313 inside the 25 mile default feed.

Three sources in the registry are disabled because they cannot be read by a
client that identifies itself honestly. See Coverage gaps below.

## Feeds

**[Open the subscribe page](https://davekt.github.io/corning_events/)** and
tap a Subscribe link. That is the easiest route on a phone, and the only place
the one-tap links work.

| Feed | Contents | Direct link |
|---|---|---|
| `corning-core.ics` | Core and Near rings, up to 25 miles, all categories. The default. | [corning-core.ics](https://davekt.github.io/corning_events/corning-core.ics) |
| `flx-all.ics` | Everything within 50 miles, including Ithaca and the Finger Lakes. | [flx-all.ics](https://davekt.github.io/corning_events/flx-all.ics) |

One-tap subscription on iOS and macOS needs the `webcal` scheme. GitHub strips
`webcal://` links out of README files, so they cannot be clickable here; copy
one of these instead, or use the subscribe page above where they do work.

```
webcal://davekt.github.io/corning_events/corning-core.ics
webcal://davekt.github.io/corning_events/flx-all.ics
```

In Google Calendar, use *Other calendars, From URL* and paste the https link.
Google refreshes external subscriptions on its own schedule, often many hours
behind, and offers no way to force it.

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
| [`plans/archive/`](plans/archive/) | The build plan, now complete. Records every decision and every place the source spec turned out to be wrong |
| [`plans/spec/`](plans/spec/) | Reference material the plans are written against |
| [`plans/spec/corning-events-source-spec.md`](plans/spec/corning-events-source-spec.md) | Source registry: every data source, its format, quirks and known breakage |

`docs/` is not documentation. It is the GitHub Pages web root, holds only
generated `.ics` output and its index page, and must never be hand-edited. The
name is fixed because branch-based GitHub Pages will serve only from the
repository root or from `/docs`.

## Running locally

Requires Python 3.11 or newer. There is no install step: the package sits at
the repository root, so run everything from there.

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt
python -m corning_events.main --dry-run
```

Useful flags: `--sources flxcalendar,ssclibrary` limits the run to named
sources and runs them even while disabled in config, which is how a parser
under development gets exercised. `--dry-run` skips writing feeds and state,
and `--db PATH` points at an alternative state database.

Tests run offline against saved fixtures:

```bash
pytest
```

`TICKETMASTER_API_KEY` enables the Ticketmaster source. Without it that one
source skips with a warning and everything else runs normally.

## Coverage gaps

Three sources are registered but disabled, and one is unverified. Each module
documents its own situation.

| Source | Problem | Route back |
|---|---|---|
| Corning Museum of Glass | Every `cmog.org` domain answers a non-browser request with a Cloudflare bot challenge. The largest single gap: the museum has the highest event volume in the city and nothing else carries it | Ask the museum for an allowlist entry or a feed URL |
| Southeast Steuben County Library | Same bot challenge. Partly covered by FLXcalendar and the Chamber | Ask the library, who publish the feed for public subscription |
| Corning's Gaffer District | Events render client-side with no reachable data endpoint. Its static festival pages give dates as prose with no year, and guessing would put wrong dates in a calendar. Nothing else covers GlassFest and the other downtown festivals | Read the endpoint out of a browser session |
| Ticketmaster | Implemented but never run against a live response, since no API key was available | Add `TICKETMASTER_API_KEY` as a repository secret |

The parsers for the first two are written and tested, so each needs only a
config change if access is arranged.

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

Write a module in `corning_events/sources/` exposing a fetch function that
returns a list of `Event`, following the contract documented in
[`sources/base.py`](corning_events/sources/base.py). Register it in the
`FETCHERS` map in `sources/__init__.py`, add a matching `SourceConfig` to
`config.SOURCES` with a default ring and trust band, and save one raw capture
into `tests/fixtures/` with a parser test against it. Nothing else in the
pipeline needs to change.

## Attribution and etiquette

Feeds redistribute listings published by other organizations, so every event
carries its originating source name and a link back in the description, and the
URL property points at the organizer's own page wherever one is known. Requests
identify themselves with a descriptive User-Agent and run at most once per day
per source.

Feed URLs are public. An unlisted URL is obscurity rather than access control,
so nothing private appears in any event field.
