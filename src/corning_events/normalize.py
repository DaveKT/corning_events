"""Text, time and geography normalization.

Lands in M1. Strips HTML to plain text, normalizes titles and venue names for
the dedupe cascade, converts local times to UTC via zoneinfo, splits the
FLXcalendar "Venue Name @ Street Address" LOCATION format, computes haversine
distance, and classifies an event into a ring.
"""
