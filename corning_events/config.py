"""Central configuration.

Every tunable knob for the pipeline lives here so that no other module carries
magic numbers. Values trace back to the build plan in plans/ and to the source
spec in plans/spec/; section references in comments point at the spec.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

# The package sits directly under the repository root, so one level up from
# this file. Override with CORNING_EVENTS_ROOT to write output elsewhere.
REPO_ROOT = Path(
    os.environ.get("CORNING_EVENTS_ROOT", Path(__file__).resolve().parents[1])
)

# GitHub Pages web root. Generated output only, never hand-edited.
DOCS_DIR = REPO_ROOT / "docs"

# Cross-run state. Committed to the repository by the daily workflow because
# stable UIDs, SEQUENCE increments and cancellation detection all depend on
# knowing what the previous run published.
STATE_DIR = REPO_ROOT / "state"
STATE_DB = STATE_DIR / "events.db"

# ---------------------------------------------------------------------------
# Geography
# ---------------------------------------------------------------------------

# Corning, New York (spec section 1).
ANCHOR_LAT = 42.1481
ANCHOR_LON = -77.0569

LOCAL_TZ = "America/New_York"

RING_CORE = "core"
RING_NEAR = "near"
RING_REGIONAL = "regional"
RING_OUT = "out"

# Ordered from nearest to furthest. RING_OUT is excluded from every feed.
RING_ORDER = (RING_CORE, RING_NEAR, RING_REGIONAL, RING_OUT)

# Outer radius of each ring in miles, measured from the anchor (spec section 1).
RING_MAX_MILES = {
    RING_CORE: 10.0,
    RING_NEAR: 25.0,
    RING_REGIONAL: 50.0,
}

# Anything beyond this is dropped at ingest.
MAX_RADIUS_MILES = RING_MAX_MILES[RING_REGIONAL]

# City to ring, keyed by lowercased city tag. This is the primary geographic
# signal because FLXcalendar carries x-tags on 99.4 percent of records while
# GEO appears on only 48.6 percent (spec section 4.4).
CITY_RINGS = {
    # Core, 0 to 10 miles
    "corning": RING_CORE,
    "painted post": RING_CORE,
    "riverside": RING_CORE,
    "gang mills": RING_CORE,
    "erwin": RING_CORE,
    "big flats": RING_CORE,
    "horseheads": RING_CORE,
    # Near, 10 to 25 miles
    "elmira": RING_NEAR,
    "bath": RING_NEAR,
    "watkins glen": RING_NEAR,
    "addison": RING_NEAR,
    # Regional, 25 to 50 miles
    "hammondsport": RING_REGIONAL,
    "seneca lake": RING_REGIONAL,
    "hector": RING_REGIONAL,
    "ithaca": RING_REGIONAL,
    "penn yan": RING_REGIONAL,
    "trumansburg": RING_REGIONAL,
    "owego": RING_REGIONAL,
    "montour falls": RING_REGIONAL,
    "hornell": RING_REGIONAL,
    "geneva": RING_REGIONAL,
    "waverly": RING_REGIONAL,
    "keuka lake": RING_REGIONAL,
}

# County to ring, keyed by lowercased county tag. Coarser than CITY_RINGS and
# consulted only after it and after coordinates and source defaults.
COUNTY_RINGS = {
    "steuben county": RING_NEAR,
    "chemung county": RING_NEAR,
    "tompkins county": RING_REGIONAL,
    "schuyler county": RING_REGIONAL,
    "yates county": RING_REGIONAL,
    "seneca county": RING_REGIONAL,
    "tioga county": RING_REGIONAL,
}

# Applied when city, coordinates, source default and county all fail to
# resolve. Regional keeps the event out of the default feed but inside the
# firehose (build plan, ring classification step 5).
FALLBACK_RING = RING_REGIONAL

# ---------------------------------------------------------------------------
# Sources
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SourceConfig:
    """Registry entry for one upstream source.

    Attributes:
        source_id: Stable key. Also the module name under sources/.
        name: Human readable name, used for per-event attribution.
        enabled: Whether a scheduled run fetches it. Sources are switched on as
            their parsers land, so everything starts False.
        default_ring: Ring applied when a record carries no usable city tag and
            no coordinates. None for regional aggregators, whose events could
            be anywhere and for which a default would be a guess. Leaving it
            None lets the county tag and the fallback do their work.
        trust: Field resolution priority when duplicates merge. Lower wins.
        homepage: Human facing page, used in attribution and for debugging.
    """

    source_id: str
    name: str
    enabled: bool
    default_ring: str | None
    trust: int
    homepage: str


# Trust bands, following the spec section 10 ordering. Venue sites are
# authoritative on times and cancellations; aggregators lag.
TRUST_VENUE = 10
TRUST_CHAMBER = 20
TRUST_FLXCALENDAR = 30
TRUST_TOURISM = 40
TRUST_TICKETING = 50
TRUST_OTHER = 60

SOURCES = {
    source.source_id: source
    for source in (
        # Tier A, machine readable. Parsers land in M2.
        SourceConfig(
            source_id="flxcalendar",
            name="FLXcalendar",
            enabled=True,
            default_ring=None,  # regional aggregator, spans every ring
            trust=TRUST_FLXCALENDAR,
            homepage="https://www.flxcalendar.com/",
        ),
        SourceConfig(
            source_id="ssclibrary",
            name="Southeast Steuben County Library",
            enabled=False,
            default_ring=RING_CORE,
            trust=TRUST_VENUE,
            homepage="https://ssclibrary.org/calendar/",
        ),
        SourceConfig(
            source_id="clemenscenter",
            name="Clemens Center",
            enabled=True,
            default_ring=RING_NEAR,
            trust=TRUST_VENUE,
            homepage="https://clemenscenter.org/events-calendar/",
        ),
        SourceConfig(
            source_id="ticketmaster",
            name="Ticketmaster",
            enabled=True,
            default_ring=None,  # radius search, records carry coordinates
            trust=TRUST_TICKETING,
            homepage="https://www.ticketmaster.com/",
        ),
        # Tier B, HTML scraping. Parsers land in M5.
        SourceConfig(
            source_id="chemungmuseum",
            name="Chemung County Historical Society",
            enabled=True,
            default_ring=RING_NEAR,
            trust=TRUST_VENUE,
            homepage="https://chemungvalleymuseum.org/events/",
        ),
        SourceConfig(
            source_id="chamber",
            name="Corning Area Chamber of Commerce",
            enabled=True,
            default_ring=RING_CORE,
            trust=TRUST_CHAMBER,
            homepage="https://www.corningny.com/events",
        ),
        SourceConfig(
            source_id="cmog",
            name="Corning Museum of Glass",
            enabled=False,  # every cmog.org domain 403s a non-browser client
            default_ring=RING_CORE,
            trust=TRUST_VENUE,
            homepage="https://whatson.cmog.org/events-programs",
        ),
        SourceConfig(
            source_id="rockwell",
            name="The Rockwell Museum",
            enabled=True,
            default_ring=RING_CORE,
            trust=TRUST_VENUE,
            homepage="https://rockwellmuseum.org/community-education/events/",
        ),
        SourceConfig(
            source_id="gaffer",
            name="Corning's Gaffer District",
            enabled=False,  # events list is client-side, no reachable endpoint
            default_ring=RING_CORE,
            trust=TRUST_VENUE,
            homepage="https://www.gafferdistrict.com/events/",
        ),
    )
}


def enabled_sources() -> list[SourceConfig]:
    """Return the sources a scheduled run would fetch, in trust order."""
    return sorted(
        (s for s in SOURCES.values() if s.enabled),
        key=lambda s: (s.trust, s.source_id),
    )


# FLXcalendar is the highest volume source, so a sustained outage there is
# worth failing the workflow over. Consecutive failed runs before exit nonzero.
FLX_FAILURE_LIMIT = 3

# ---------------------------------------------------------------------------
# Categories
# ---------------------------------------------------------------------------

# The FLXcalendar topical taxonomy (spec section 4.5), used as the canonical
# vocabulary. Other sources are mapped onto it where they carry categories at
# all. Both v1 feeds are all-categories, so this drives nothing yet; it exists
# so category filtered feeds stay a config change rather than a code change.
CANONICAL_CATEGORIES = frozenset(
    {
        "Art", "BIPOC", "Beer", "Bikes", "Boats", "Cars", "Causes", "Comedy",
        "Community", "Competitions", "Connection", "Crafts", "Dancing",
        "Education", "Faith", "Family Fun", "Farmers Market", "Festivals",
        "Film", "Food", "Games", "Glass", "Health", "History & Heritage",
        "Holidays", "LGBTQ+", "Literature", "Music", "Nature",
        "New to the Area", "Other", "Performing Arts", "Pets", "Running",
        "Science", "Seniors", "Sober-friendly", "Sports", "Support",
        "Tastings", "Teen", "Virtual", "Volunteering", "Wine",
        "Youth Empowerment",
    }
)

# Lowercased inbound category to canonical category. Extend as sources land.
# "history and heritage" is here because spec section 4.5 spells it with the
# word "and" while the live feed uses an ampersand.
CATEGORY_ALIASES = {
    "live music": "Music",
    "concert": "Music",
    "concerts": "Music",
    "theatre": "Performing Arts",
    "theater": "Performing Arts",
    "arts & theatre": "Performing Arts",
    "kids": "Family Fun",
    "family": "Family Fun",
    "children": "Family Fun",
    "farmers market": "Farmers Market",
    "market": "Farmers Market",
    "lecture": "Education",
    "author talk": "Literature",
    "exhibition": "Art",
    "gallery": "Art",
    "race": "Running",
    "5k": "Running",
    "fundraiser": "Causes",
    "history and heritage": "History & Heritage",
    "arts": "Art",
    "trivia": "Games",
}

# ---------------------------------------------------------------------------
# Feeds
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class FeedConfig:
    """One published .ics file.

    Attributes:
        slug: Filename stem, also the identifier used on the command line.
        calendar_name: X-WR-CALNAME, the name subscribers see in their client.
        description: X-WR-CALDESC, shown by some clients.
        rings: Rings included. Everything else is filtered out.
        categories: Canonical categories included, or None for all.
        min_events: Refuse to publish this feed with fewer events than this.
            A narrow feed added later needs a lower floor than a broad one.
    """

    slug: str
    calendar_name: str
    description: str
    rings: tuple[str, ...]
    categories: frozenset[str] | None = None
    min_events: int = 5

    @property
    def filename(self) -> str:
        return f"{self.slug}.ics"


FEEDS = (
    FeedConfig(
        slug="corning-core",
        calendar_name="Corning Area Events",
        description="Events within about 25 miles of Corning, New York.",
        rings=(RING_CORE, RING_NEAR),
    ),
    FeedConfig(
        slug="flx-all",
        calendar_name="FLX All Events",
        description=(
            "Events within about 50 miles of Corning, New York, including "
            "Ithaca and the Finger Lakes."
        ),
        rings=(RING_CORE, RING_NEAR, RING_REGIONAL),
    ),
)

# Where the feeds are published. Used to build subscribe links on the index
# page and in the README.
PAGES_BASE_URL = "https://davekt.github.io/corning_events"

# ---------------------------------------------------------------------------
# iCal emission
# ---------------------------------------------------------------------------

PRODID = "-//corning-events//Corning Events Aggregator//EN"

# Apple Calendar honours REFRESH-INTERVAL; X-PUBLISHED-TTL is the older
# Microsoft equivalent. Emit both, trust neither (spec section 9.2).
#
# Both derive from one number because they must agree. REFRESH-INTERVAL is a
# known DURATION property, so icalendar wants a timedelta and renders it
# itself. X-PUBLISHED-TTL is an X- property, so icalendar has no type for it
# and would serialize a timedelta as "12:00:00" rather than "PT12H"; it needs
# the ISO 8601 string handed over ready made.
REFRESH_INTERVAL_HOURS = 12
REFRESH_INTERVAL = timedelta(hours=REFRESH_INTERVAL_HOURS)
REFRESH_INTERVAL_ISO = f"PT{REFRESH_INTERVAL_HOURS}H"

# Events starting before this many days ago are dropped from the feed. A small
# window keeps today's events visible for clients that refresh late.
PAST_WINDOW_DAYS = 1

# Events starting beyond this are dropped. Also bounds RRULE expansion.
HORIZON_DAYS = 365

# Stored events whose start is older than this are dropped, to bound a
# database that is committed to the repository on every run.
RAW_EVENT_RETENTION_DAYS = 90

# A cancelled event stays in the feed this long so subscribers see that it was
# cancelled rather than watching it silently vanish (spec section 9.2).
CANCELLED_RETENTION_DAYS = 30

# Descriptions are truncated at a word boundary beyond this. Venue feeds in
# particular pad every event with box office hours, share links and visitor
# information, which is unreadable on a phone. The event URL is always emitted,
# so nothing is lost that a tap cannot recover.
MAX_DESCRIPTION_CHARS = 600

# Sanity floor. If the default feed would carry fewer events than this,
# something upstream has broken, so abort rather than overwrite a good feed
# with an empty one.
MIN_EVENTS_SANITY_FLOOR = 5

# ---------------------------------------------------------------------------
# Deduplication
# ---------------------------------------------------------------------------

# difflib.SequenceMatcher ratio at or above which two normalized titles are
# considered the same event.
TITLE_SIMILARITY_THRESHOLD = 0.85

# Coordinates within this distance are treated as the same venue.
GEO_MATCH_METRES = 150.0

# How completely the shorter title's words must sit inside the longer one's
# for the containment rule to fire. Character similarity punishes an added
# suffix, so this catches pairs like "Wise Crackers All Stars" against "Wise
# Crackers All Stars Comedy Show", which scores only 0.79 by character.
TITLE_CONTAINMENT_THRESHOLD = 0.9

# Containment is not trusted below this many words. A one word title matches
# far too much to merge on.
MIN_TITLE_TOKENS = 2

# Suffix appended to every published UID. UIDs are minted once per cluster and
# never regenerated, because a changing UID makes subscribers accumulate
# duplicates (build plan, Part 1 adjustment 6).
UID_DOMAIN = "corning-events"

# ---------------------------------------------------------------------------
# HTTP
# ---------------------------------------------------------------------------

USER_AGENT = (
    "CorningEventsAggregator/1.0 "
    "(+https://github.com/DaveKT/corning_events)"
)

HTTP_TIMEOUT_SECONDS = 30
HTTP_MAX_RETRIES = 3
HTTP_BACKOFF_SECONDS = 2.0

# Courtesy pause between requests to the same host when a source pages through
# several URLs.
HTTP_INTER_REQUEST_SECONDS = 1.0
