"""Normalizer behaviour, including the edge cases the spec called out."""

from __future__ import annotations

from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import pytest

from corning_events import config, normalize
from corning_events.model import Event

UTC = timezone.utc


def make_event(**overrides) -> Event:
    defaults = dict(
        source_id="flxcalendar",
        source_uid="uid-1",
        title="A Concert",
        start=datetime(2026, 8, 15, 22, 0, tzinfo=UTC),
    )
    defaults.update(overrides)
    return Event(**defaults)


# ---------------------------------------------------------------------------
# HTML
# ---------------------------------------------------------------------------


def test_strip_html_removes_facebook_pasted_markup():
    # Representative of what FLXcalendar descriptions carry: inline styles and
    # generated class names pasted straight out of Facebook (spec 4.6).
    html = (
        '<div class="x1n2onr6 x1ja2u2z" style="font-size:14px;color:#050505">'
        "<p>Join us Saturday for <b>live music</b>!</p>"
        '<p style="margin:0">Doors at 7.</p></div>'
    )
    # The bolded words stay inline; only the paragraphs become line breaks.
    assert normalize.strip_html(html) == "Join us Saturday for live music!\nDoors at 7."


def test_strip_html_drops_script_and_style_content():
    html = "<style>.a{color:red}</style><p>Real text</p><script>alert(1)</script>"
    assert normalize.strip_html(html) == "Real text"


def test_strip_html_decodes_entities_and_collapses_whitespace():
    assert normalize.strip_html("<p>Wine &amp;   Cheese Night</p>") == "Wine & Cheese Night"


def test_strip_html_passes_through_plain_text():
    assert normalize.strip_html("Just text") == "Just text"


def test_strip_html_returns_none_for_empty_input():
    assert normalize.strip_html(None) is None
    assert normalize.strip_html("") is None
    assert normalize.strip_html("<p></p>") is None


# ---------------------------------------------------------------------------
# Titles and venues
# ---------------------------------------------------------------------------


def test_normalize_title_folds_case_and_punctuation():
    assert normalize.normalize_title("Wine & Cheese: A Night!") == "wine cheese a night"


def test_normalize_title_strips_trailing_venue_fragment():
    assert normalize.normalize_title("Trivia Night @ Burgers and Beer") == "trivia night"


def test_normalize_title_strips_leading_venue_prefix():
    title = "Corning Museum of Glass Glassblowing Demo"
    assert normalize.normalize_title(title, "Corning Museum of Glass") == "glassblowing demo"


def test_normalize_title_keeps_title_that_is_only_the_venue():
    # Stripping here would leave an empty string and match everything.
    assert normalize.normalize_title("The Rockwell Museum", "The Rockwell Museum") == "the rockwell museum"


def test_normalize_title_makes_aggregator_variants_agree():
    a = normalize.normalize_title("Glassblowing Demo @ Corning Museum of Glass")
    b = normalize.normalize_title("Corning Museum of Glass Glassblowing Demo", "Corning Museum of Glass")
    assert a == b == "glassblowing demo"


def test_normalize_title_strips_a_decorative_year():
    # "India Day 2026" and "India Day" are the same event; the date fields
    # already carry the year. Leaving it in forced that real pair through the
    # weaker containment rule instead of the exact title match.
    assert normalize.normalize_title("India Day 2026") == "india day"
    assert normalize.normalize_title("India Day") == "india day"


def test_normalize_title_keeps_a_title_that_is_only_a_year():
    # Stripping here would make every such title equal to every other.
    assert normalize.normalize_title("2026") == "2026"


def test_normalize_title_does_not_strip_year_like_digits_inside_words():
    assert normalize.normalize_title("Route 2026K Ride") == "route 2026k ride"


def test_normalize_venue_handles_absent_values():
    assert normalize.normalize_venue(None) == ""
    assert normalize.normalize_venue("The Rockwell Museum!") == "the rockwell museum"


# ---------------------------------------------------------------------------
# Location splitting
# ---------------------------------------------------------------------------


def test_split_location_uses_the_at_separator():
    venue, address = normalize.split_location("Rockwell Museum @ 111 Cedar St, Corning")
    assert venue == "Rockwell Museum"
    assert address == "111 Cedar St, Corning"


def test_split_location_treats_a_bare_string_as_a_venue():
    assert normalize.split_location("Centerway Square") == ("Centerway Square", None)


def test_split_location_handles_absent_values():
    assert normalize.split_location(None) == (None, None)
    assert normalize.split_location("   ") == (None, None)


# ---------------------------------------------------------------------------
# Categories
# ---------------------------------------------------------------------------


def test_canonical_categories_maps_aliases_and_drops_unknowns():
    assert normalize.canonical_categories(["Live Music", "Nonsense", "Film"]) == ("Music", "Film")


def test_canonical_categories_splits_delimited_strings_and_dedupes():
    assert normalize.canonical_categories("Music, live music; Comedy") == ("Music", "Comedy")


def test_canonical_categories_handles_absent_values():
    assert normalize.canonical_categories(None) == ()
    assert normalize.canonical_categories([]) == ()


# ---------------------------------------------------------------------------
# Time
# ---------------------------------------------------------------------------


def test_to_utc_assumes_the_local_zone_for_naive_input():
    # 18:30 EDT is 22:30 UTC.
    naive = datetime(2026, 8, 15, 18, 30)
    assert normalize.to_utc(naive) == datetime(2026, 8, 15, 22, 30, tzinfo=UTC)


def test_to_utc_respects_an_existing_zone():
    aware = datetime(2026, 8, 15, 18, 30, tzinfo=ZoneInfo("America/Los_Angeles"))
    assert normalize.to_utc(aware) == datetime(2026, 8, 16, 1, 30, tzinfo=UTC)


def test_to_utc_handles_standard_time_offset():
    # 18:30 EST in January is 23:30 UTC, a different offset from August.
    assert normalize.to_utc(datetime(2026, 1, 15, 18, 30)) == datetime(2026, 1, 15, 23, 30, tzinfo=UTC)


def test_to_utc_is_idempotent():
    once = normalize.to_utc(datetime(2026, 8, 15, 18, 30))
    assert normalize.to_utc(once) == once


# ---------------------------------------------------------------------------
# Geography
# ---------------------------------------------------------------------------


def test_distance_from_anchor_matches_the_spec_measurements():
    # Ithaca is measured at 34.8 miles in spec section 1.
    assert normalize.distance_from_anchor_miles(42.4440, -76.5019) == pytest.approx(34.8, abs=1.5)
    # Elmira at 13.2 miles.
    assert normalize.distance_from_anchor_miles(42.0898, -76.8077) == pytest.approx(13.2, abs=1.5)


def test_ring_for_distance_uses_inclusive_upper_bounds():
    assert normalize.ring_for_distance(0) == config.RING_CORE
    assert normalize.ring_for_distance(10.0) == config.RING_CORE
    assert normalize.ring_for_distance(10.1) == config.RING_NEAR
    assert normalize.ring_for_distance(25.0) == config.RING_NEAR
    assert normalize.ring_for_distance(49.9) == config.RING_REGIONAL
    assert normalize.ring_for_distance(50.1) == config.RING_OUT


# ---------------------------------------------------------------------------
# Ring classification cascade
# ---------------------------------------------------------------------------


def test_city_tag_wins_over_everything_else():
    event = make_event(city_tag="Corning", lat=42.4440, lon=-76.5019, county_tag="Tompkins County")
    assert normalize.classify_ring(event) == config.RING_CORE


def test_city_tag_lookup_is_case_insensitive():
    assert normalize.classify_ring(make_event(city_tag="WATKINS GLEN")) == config.RING_NEAR


def test_coordinates_are_used_when_the_city_tag_is_unknown():
    event = make_event(city_tag="Nowhereville", lat=42.4440, lon=-76.5019)
    assert normalize.classify_ring(event) == config.RING_REGIONAL


def test_distant_coordinates_classify_as_out():
    # Buffalo, well beyond the 50 mile limit.
    assert normalize.classify_ring(make_event(lat=42.8864, lon=-78.8784)) == config.RING_OUT


def test_source_default_applies_when_there_is_no_tag_or_coordinates():
    assert normalize.classify_ring(make_event(source_id="cmog")) == config.RING_CORE
    assert normalize.classify_ring(make_event(source_id="clemenscenter")) == config.RING_NEAR


def test_county_tag_is_reached_for_sources_with_no_default_ring():
    # flxcalendar declares no default precisely so this step stays reachable.
    assert config.SOURCES["flxcalendar"].default_ring is None
    event = make_event(source_id="flxcalendar", county_tag="Tompkins County")
    assert normalize.classify_ring(event) == config.RING_REGIONAL


def test_unclassifiable_events_fall_back_to_regional():
    # Regional keeps them out of the default feed but inside the firehose.
    assert normalize.classify_ring(make_event(source_id="flxcalendar")) == config.RING_REGIONAL
