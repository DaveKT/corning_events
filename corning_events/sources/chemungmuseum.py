"""Chemung County Historical Society, Elmira.

WordPress with The Events Calendar, so the same iCal parser serves it as the
other Tribe feeds. Spec section 5 listed it as an HTML target and suggested
probing for an iCal endpoint first; the probe found one, which turned a
scraping job into a parsing job.

Low volume, a handful of events at a time, but they are museum talks and
readings that nothing else in the registry carries.
"""

from __future__ import annotations

from ..model import Event
from .base import parse_ics

FEED_URL = "https://chemungvalleymuseum.org/?post_type=tribe_events&ical=1&eventDisplay=list"

SOURCE_ID = "chemungmuseum"


def fetch(http) -> list[Event]:
    return parse(http.bytes(FEED_URL))


def parse(raw: bytes, now=None) -> list[Event]:
    # The society publishes this feed itself, so its event URLs are canonical.
    return parse_ics(raw, SOURCE_ID, now=now, url_is_canonical=True)
