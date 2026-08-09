"""SQLite persistence for cross-run state.

Lands in M1. Four tables, defined in the build plan: raw_events keyed by
(source_id, source_uid), clusters holding pinned published UIDs, published
holding the last emitted content hash and SEQUENCE per UID, and fetch_log
recording per-source success so cancellation never fires off a failed fetch.
"""
