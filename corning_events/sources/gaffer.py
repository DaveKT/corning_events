"""Corning's Gaffer District.

Disabled, and reclassified from Tier B to Tier C.

Spec section 5 lists this as server-rendered HTML to be crawled through the
/events/ subtree. As of 2026-08-09 it is neither. The events list on /events/
is rendered client-side by a Simpleview plugin into an empty container, and
the endpoint it calls is not present anywhere in the delivered HTML.

The static festival pages the spec names were tried as a fallback and do not
carry usable data either. Two of the four paths return errors, one has no
dates at all, and the GlassFest page gives dates only as prose without a year
("May 22- 24"). Guessing the year would put wrong dates in a subscribed
calendar, which is worse than publishing nothing.

This is a real gap rather than a duplicate of another source: FLXcalendar
carries no GlassFest, Harvest Festival or other Gaffer District festival, and
neither does the Chamber. Long-lead downtown festivals are currently
uncovered.

Worth retrying if the site changes, or if the plugin's JSON endpoint can be
identified from a browser session.
"""

from __future__ import annotations


def fetch(http) -> list:
    raise NotImplementedError(
        "The Gaffer District events list is rendered client-side and its data "
        "endpoint is not discoverable from the page source. See the module "
        "docstring."
    )
