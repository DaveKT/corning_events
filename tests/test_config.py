"""Consistency checks over the configuration and the source registry.

These guard the seams where later milestones are most likely to drift: adding a
source module without a SourceConfig, mistyping a ring name, or aliasing a
category onto a name that does not exist.
"""

from __future__ import annotations

from datetime import timedelta

import pytest

from corning_events import config
from corning_events.main import main, resolve_sources
from corning_events.sources import FETCHERS

VALID_RINGS = set(config.RING_ORDER)


def test_source_keys_match_their_source_id():
    for key, source in config.SOURCES.items():
        assert key == source.source_id


def test_every_configured_source_has_a_fetcher():
    assert set(config.SOURCES) == set(FETCHERS)


def test_source_default_rings_are_valid():
    for source in config.SOURCES.values():
        if source.default_ring is None:
            # Regional aggregators decline to guess, which is what keeps the
            # county tag and fallback steps of the cascade reachable.
            continue
        assert source.default_ring in VALID_RINGS
        # An event defaulting to "out" would be dropped by every feed.
        assert source.default_ring != config.RING_OUT


def test_regional_aggregators_declare_no_default_ring():
    # If every source declared a default, the source-default step of the ring
    # cascade would always resolve and the county and fallback steps below it
    # would be dead code.
    assert config.SOURCES["flxcalendar"].default_ring is None
    assert config.SOURCES["ticketmaster"].default_ring is None


def test_source_names_are_populated():
    # The name appears in per-event attribution, so an empty one ships a
    # broken description line to subscribers.
    for source in config.SOURCES.values():
        assert source.name.strip()
        assert source.homepage.startswith("https://")


def test_ring_radii_increase():
    miles = [config.RING_MAX_MILES[r] for r in (config.RING_CORE, config.RING_NEAR, config.RING_REGIONAL)]
    assert miles == sorted(miles)
    assert config.MAX_RADIUS_MILES == miles[-1]


def test_city_and_county_ring_tables_are_well_formed():
    for table in (config.CITY_RINGS, config.COUNTY_RINGS):
        for key, ring in table.items():
            assert key == key.lower(), f"{key} must be lowercased for lookup"
            assert ring in VALID_RINGS


def test_category_aliases_resolve_to_canonical_names():
    for alias, canonical in config.CATEGORY_ALIASES.items():
        assert alias == alias.lower(), f"{alias} must be lowercased for lookup"
        assert canonical in config.CANONICAL_CATEGORIES


def test_feeds_are_distinct_and_reference_valid_rings():
    slugs = [feed.slug for feed in config.FEEDS]
    assert len(slugs) == len(set(slugs))
    for feed in config.FEEDS:
        assert feed.rings, f"{feed.slug} includes no rings"
        assert set(feed.rings) <= VALID_RINGS
        assert config.RING_OUT not in feed.rings
        assert feed.filename == f"{feed.slug}.ics"
        if feed.categories is not None:
            assert feed.categories <= config.CANONICAL_CATEGORIES


def test_default_feed_is_narrower_than_the_firehose():
    core, firehose = config.FEEDS
    assert set(core.rings) < set(firehose.rings)


def test_enabled_sources_returns_only_enabled_in_trust_order():
    enabled = config.enabled_sources()
    assert all(source.enabled for source in enabled)
    assert [s.trust for s in enabled] == sorted(s.trust for s in enabled)


def test_resolve_sources_rejects_unknown_ids():
    with pytest.raises(SystemExit) as excinfo:
        resolve_sources("flxcalendar,nosuchsource")
    assert "nosuchsource" in str(excinfo.value)


def test_resolve_sources_ignores_the_enabled_flag_when_named():
    # Named sources run even while disabled, which is how a parser under
    # development gets exercised.
    resolved = resolve_sources("flxcalendar")
    assert [s.source_id for s in resolved] == ["flxcalendar"]


def test_main_exits_cleanly_when_nothing_is_enabled(capsys):
    if config.enabled_sources():
        pytest.skip("some sources are enabled, so this M0 path no longer applies")
    assert main([]) == 0
    assert "No sources enabled." in capsys.readouterr().out


def test_repo_root_resolves_to_the_repository():
    # The package sits at the repository root, so a wrong parents[] index here
    # would silently write feeds and state outside the repo.
    assert (config.REPO_ROOT / "pyproject.toml").is_file()
    assert config.DOCS_DIR.name == "docs"
    assert config.STATE_DB.parent.name == "state"


def test_refresh_interval_forms_agree():
    # One is a timedelta for icalendar, the other an ISO string for the X-
    # property. They describe the same interval and must not drift.
    assert config.REFRESH_INTERVAL == timedelta(hours=config.REFRESH_INTERVAL_HOURS)
    assert config.REFRESH_INTERVAL_ISO == f"PT{config.REFRESH_INTERVAL_HOURS}H"
