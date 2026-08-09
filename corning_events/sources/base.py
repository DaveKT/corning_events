"""The contract every source module implements.

A source module exposes a single function:

    def fetch(session) -> list[Event]

It receives the shared requests session from http.py, which already carries the
User-Agent, timeout and retry policy, and returns a list of Event records. It
does no persistence, no deduplication and no filtering; those belong to later
stages of the pipeline.

Four rules apply to every source:

1. source_uid must be stable across runs. Prefer the identifier the source
   supplies. Where a source supplies none, derive one from the event detail
   URL slug, adding the occurrence date for anything recurring. An unstable
   source_uid breaks cluster pinning and makes subscribers see duplicates.
2. Recurring events are expanded into one Event per occurrence at parse time.
3. Raise on failure rather than returning an empty list. main.py distinguishes
   the two, and an empty list from a broken source would look like every event
   being cancelled at once (build plan, Part 1 adjustment 3).
4. Times are converted to UTC before returning, using normalize.to_utc.
5. Descriptions are passed through normalize.strip_html before returning.
   Nothing downstream can safely do this for you: only the source knows
   whether its description field holds HTML, and running the stripper over
   text that merely contains a "<" would silently eat the rest of the
   sentence. Unstripped HTML reaches subscribers as visible markup.

Shared helpers land here as the parsers reveal what they have in common. Until
M2 there is nothing to share yet.
"""

from __future__ import annotations
