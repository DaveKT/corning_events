"""Source registry.

Maps each source_id in config.SOURCES to the fetch function that implements
it. Adding a source means writing a module here, registering it below, and
adding a matching SourceConfig in config.SOURCES. The consistency test in
tests/test_config.py holds the two registries in step.

Named FETCHERS rather than SOURCES so it is never confused with
config.SOURCES, which holds the SourceConfig metadata.
"""

from __future__ import annotations

from collections.abc import Callable

from . import (
    chamber,
    clemenscenter,
    cmog,
    flxcalendar,
    gaffer,
    rockwell,
    ssclibrary,
    ticketmaster,
)

FETCHERS: dict[str, Callable[..., list]] = {
    "flxcalendar": flxcalendar.fetch,
    "ssclibrary": ssclibrary.fetch,
    "clemenscenter": clemenscenter.fetch,
    "ticketmaster": ticketmaster.fetch,
    "chamber": chamber.fetch,
    "cmog": cmog.fetch,
    "rockwell": rockwell.fetch,
    "gaffer": gaffer.fetch,
}
