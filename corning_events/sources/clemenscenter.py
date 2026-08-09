"""Clemens Center, Elmira.

WordPress with The Events Calendar, exposing a standard iCal feed. The
region's main touring venue and the biggest single contributor of ticketed
performances (spec section 3.3).

The feed carries no LOCATION and no GEO, which is why the source declares a
default ring of Near: every event happens at the venue itself, about twenty
miles from the anchor.
"""

from __future__ import annotations

from ..model import Event
from .base import parse_ics

FEED_URL = "https://clemenscenter.org/?post_type=tribe_events&ical=1&eventDisplay=list"

SOURCE_ID = "clemenscenter"


def fetch(http) -> list[Event]:
    return parse(http.bytes(FEED_URL))


def parse(raw: bytes, now=None) -> list[Event]:
    # The venue publishes this feed itself, so its event URLs are canonical.
    return parse_ics(raw, SOURCE_ID, now=now, url_is_canonical=True)
