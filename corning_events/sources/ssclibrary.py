"""Southeast Steuben County Library, Corning.

WordPress with The Events Calendar, the same format as clemenscenter, so the
parsing is shared (spec section 3.2).

Disabled. As of 2026-08-09 the host answers any non-browser request with a
Cloudflare bot challenge ("Just a moment...") and a 403, so the feed cannot be
read by an honest client. Getting past that would mean impersonating a browser
to defeat a protection the site owner deliberately switched on, which is out
of bounds. The parser is written and tested so that enabling the source is a
one line change if access is arranged.

Library events are not lost entirely in the meantime: FLXcalendar carries some
of them, and the Chamber of Commerce republishes others.

Worth knowing: the library also programs offsite, for instance Movie Night in
Centerway Square and Storytime in the Park. Those are genuine public events
and must not be filtered out as library-only programming.
"""

from __future__ import annotations

from ..model import Event
from .base import parse_ics

FEED_URL = "https://ssclibrary.org/?post_type=tribe_events&ical=1&eventDisplay=list"

SOURCE_ID = "ssclibrary"


def fetch(http) -> list[Event]:
    return parse(http.bytes(FEED_URL))


def parse(raw: bytes, now=None) -> list[Event]:
    # The library publishes this feed itself, so its event URLs are canonical.
    return parse_ics(raw, SOURCE_ID, now=now, url_is_canonical=True)
