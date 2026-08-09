"""Feed filtering, iCal emission and output validation.

Lands in M1 for emission and validation, wired end to end in M4. Builds each
FeedConfig in config.FEEDS with the icalendar library, which handles line
folding, escaping and CRLF. Validation reparses the output, checks the 75
octet line limit, requires UID, DTSTAMP, DTSTART and SUMMARY on every VEVENT,
and applies config.MIN_EVENTS_SANITY_FLOOR before anything is written.
"""
