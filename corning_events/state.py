"""SQLite persistence for cross-run state.

The daily workflow commits this database back to the repository, because
stable UIDs, SEQUENCE increments and cancellation detection all depend on
knowing what the previous run saw and published.

Four tables:

``raw_events``
    One row per source occurrence, keyed by ``(source_id, source_uid)``.
    ``last_seen`` drives cancellation detection.
``clusters``
    Published identity. ``published_uid`` is minted once and never
    regenerated, no matter which source later wins field resolution.
``published``
    What the last emitted feed contained, so SEQUENCE only increments on a
    genuine content change.
``fetch_log``
    Per source outcome per run. Cancellation consults this so that a failed
    fetch can never be mistaken for every event disappearing at once.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

from .model import STATUS_CANCELLED, STATUS_CONFIRMED, Event

SCHEMA = """
CREATE TABLE IF NOT EXISTS raw_events (
  source_id  TEXT NOT NULL,
  source_uid TEXT NOT NULL,
  start      TEXT NOT NULL,
  payload    TEXT NOT NULL,
  first_seen TEXT NOT NULL,
  last_seen  TEXT NOT NULL,
  PRIMARY KEY (source_id, source_uid)
);
CREATE INDEX IF NOT EXISTS raw_events_start ON raw_events (start);

CREATE TABLE IF NOT EXISTS clusters (
  cluster_id    INTEGER PRIMARY KEY,
  published_uid TEXT NOT NULL UNIQUE,
  member_keys   TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS cluster_members (
  member_key TEXT PRIMARY KEY,
  cluster_id INTEGER NOT NULL REFERENCES clusters (cluster_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS published (
  published_uid TEXT PRIMARY KEY,
  sequence      INTEGER NOT NULL DEFAULT 0,
  content_hash  TEXT NOT NULL,
  status        TEXT NOT NULL DEFAULT 'CONFIRMED',
  cancelled_at  TEXT
);

CREATE TABLE IF NOT EXISTS fetch_log (
  source_id   TEXT NOT NULL,
  run_at      TEXT NOT NULL,
  ok          INTEGER NOT NULL,
  event_count INTEGER NOT NULL,
  note        TEXT
);
CREATE INDEX IF NOT EXISTS fetch_log_source ON fetch_log (source_id, run_at);
"""


def connect(path: Path | str) -> sqlite3.Connection:
    """Open the state database, creating and migrating it if needed."""
    path = Path(path)
    if str(path) != ":memory:":
        path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    migrate(conn)
    return conn


def migrate(conn: sqlite3.Connection) -> None:
    """Apply the schema. Idempotent, so it runs on every open."""
    conn.executescript(SCHEMA)
    conn.commit()


@contextmanager
def transaction(conn: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    """Commit on success, roll back on any exception.

    A partial write here would corrupt cancellation detection on the next run,
    so every multi statement update goes through this.
    """
    try:
        yield conn
    except Exception:
        conn.rollback()
        raise
    conn.commit()


def _iso(value: datetime) -> str:
    return value.isoformat()


# ---------------------------------------------------------------------------
# raw_events
# ---------------------------------------------------------------------------


def upsert_raw_event(conn: sqlite3.Connection, event: Event, run_at: datetime) -> None:
    """Record that a source reported this event on this run.

    ``first_seen`` survives updates; ``last_seen`` moves forward every time the
    event is reported. The gap between ``last_seen`` and the current run is
    what identifies a disappearance.
    """
    conn.execute(
        """
        INSERT INTO raw_events (source_id, source_uid, start, payload, first_seen, last_seen)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT (source_id, source_uid) DO UPDATE SET
            start     = excluded.start,
            payload   = excluded.payload,
            last_seen = excluded.last_seen
        """,
        (
            event.source_id,
            event.source_uid,
            _iso(event.start),
            json.dumps(event.to_dict(), sort_keys=True),
            _iso(run_at),
            _iso(run_at),
        ),
    )


def upsert_raw_events(
    conn: sqlite3.Connection, events: Iterable[Event], run_at: datetime
) -> int:
    count = 0
    for event in events:
        upsert_raw_event(conn, event, run_at)
        count += 1
    return count


def get_raw_event(
    conn: sqlite3.Connection, source_id: str, source_uid: str
) -> Event | None:
    row = conn.execute(
        "SELECT payload FROM raw_events WHERE source_id = ? AND source_uid = ?",
        (source_id, source_uid),
    ).fetchone()
    return Event.from_dict(json.loads(row["payload"])) if row else None


def all_raw_events(conn: sqlite3.Connection) -> list[Event]:
    rows = conn.execute("SELECT payload FROM raw_events ORDER BY start").fetchall()
    return [Event.from_dict(json.loads(row["payload"])) for row in rows]


def stale_member_keys(
    conn: sqlite3.Connection, source_id: str, run_at: datetime, now: datetime
) -> set[str]:
    """Keys from one source that this run did not report, still in the future.

    Only ever call this for a source whose fetch succeeded and returned at
    least one event. Calling it after a failed fetch would present every event
    from that source as cancelled (build plan, Part 1 adjustment 3).
    """
    rows = conn.execute(
        """
        SELECT source_id, source_uid FROM raw_events
        WHERE source_id = ? AND last_seen < ? AND start > ?
        """,
        (source_id, _iso(run_at), _iso(now)),
    ).fetchall()
    return {f"{row['source_id']}:{row['source_uid']}" for row in rows}


def prune_raw_events(conn: sqlite3.Connection, before: datetime) -> int:
    """Drop records whose start is well in the past, to bound the database."""
    cursor = conn.execute("DELETE FROM raw_events WHERE start < ?", (_iso(before),))
    return cursor.rowcount


# ---------------------------------------------------------------------------
# clusters
# ---------------------------------------------------------------------------


def cluster_for_member(conn: sqlite3.Connection, member_key: str) -> sqlite3.Row | None:
    return conn.execute(
        """
        SELECT c.cluster_id, c.published_uid, c.member_keys
        FROM cluster_members m JOIN clusters c ON c.cluster_id = m.cluster_id
        WHERE m.member_key = ?
        """,
        (member_key,),
    ).fetchone()


def create_cluster(
    conn: sqlite3.Connection, published_uid: str, member_keys: Iterable[str]
) -> int:
    keys = sorted(set(member_keys))
    cursor = conn.execute(
        "INSERT INTO clusters (published_uid, member_keys) VALUES (?, ?)",
        (published_uid, json.dumps(keys)),
    )
    cluster_id = int(cursor.lastrowid)
    _set_members(conn, cluster_id, keys)
    return cluster_id


def update_cluster_members(
    conn: sqlite3.Connection, cluster_id: int, member_keys: Iterable[str]
) -> None:
    keys = sorted(set(member_keys))
    conn.execute(
        "UPDATE clusters SET member_keys = ? WHERE cluster_id = ?",
        (json.dumps(keys), cluster_id),
    )
    conn.execute("DELETE FROM cluster_members WHERE cluster_id = ?", (cluster_id,))
    _set_members(conn, cluster_id, keys)


def _set_members(
    conn: sqlite3.Connection, cluster_id: int, keys: Iterable[str]
) -> None:
    conn.executemany(
        "INSERT OR REPLACE INTO cluster_members (member_key, cluster_id) VALUES (?, ?)",
        [(key, cluster_id) for key in keys],
    )


def all_clusters(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT cluster_id, published_uid, member_keys FROM clusters"
    ).fetchall()


# ---------------------------------------------------------------------------
# published
# ---------------------------------------------------------------------------


def get_published(conn: sqlite3.Connection, published_uid: str) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM published WHERE published_uid = ?", (published_uid,)
    ).fetchone()


def record_published(
    conn: sqlite3.Connection,
    published_uid: str,
    content_hash: str,
    status: str = STATUS_CONFIRMED,
    cancelled_at: datetime | None = None,
) -> int:
    """Store what was published and return the SEQUENCE to emit.

    SEQUENCE increments only when the content hash changes, because clients
    ignore modifications that arrive without a bump, and bumping it on every
    run would make every event look modified every day.
    """
    existing = get_published(conn, published_uid)
    if existing is None:
        sequence = 0
    elif existing["content_hash"] == content_hash and existing["status"] == status:
        sequence = int(existing["sequence"])
    else:
        sequence = int(existing["sequence"]) + 1

    conn.execute(
        """
        INSERT INTO published (published_uid, sequence, content_hash, status, cancelled_at)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT (published_uid) DO UPDATE SET
            sequence     = excluded.sequence,
            content_hash = excluded.content_hash,
            status       = excluded.status,
            cancelled_at = excluded.cancelled_at
        """,
        (
            published_uid,
            sequence,
            content_hash,
            status,
            _iso(cancelled_at) if cancelled_at else None,
        ),
    )
    return sequence


def cancelled_before(conn: sqlite3.Connection, cutoff: datetime) -> list[str]:
    """UIDs cancelled long enough ago to drop from the feed entirely."""
    rows = conn.execute(
        """
        SELECT published_uid FROM published
        WHERE status = ? AND cancelled_at IS NOT NULL AND cancelled_at < ?
        """,
        (STATUS_CANCELLED, _iso(cutoff)),
    ).fetchall()
    return [row["published_uid"] for row in rows]


def forget_published(conn: sqlite3.Connection, uids: Iterable[str]) -> None:
    conn.executemany(
        "DELETE FROM published WHERE published_uid = ?", [(uid,) for uid in uids]
    )


# ---------------------------------------------------------------------------
# fetch_log
# ---------------------------------------------------------------------------


def record_fetch(
    conn: sqlite3.Connection,
    source_id: str,
    run_at: datetime,
    ok: bool,
    event_count: int,
    note: str | None = None,
) -> None:
    conn.execute(
        """
        INSERT INTO fetch_log (source_id, run_at, ok, event_count, note)
        VALUES (?, ?, ?, ?, ?)
        """,
        (source_id, _iso(run_at), 1 if ok else 0, event_count, note),
    )


def consecutive_failures(conn: sqlite3.Connection, source_id: str) -> int:
    """How many runs in a row this source has failed, most recent first.

    Drives the FLXcalendar failure limit, which fails the workflow so that a
    sustained outage produces an email rather than a quietly shrinking feed.
    """
    rows = conn.execute(
        "SELECT ok FROM fetch_log WHERE source_id = ? ORDER BY run_at DESC, rowid DESC",
        (source_id,),
    ).fetchall()
    failures = 0
    for row in rows:
        if row["ok"]:
            break
        failures += 1
    return failures


def prune_fetch_log(conn: sqlite3.Connection, keep_per_source: int = 60) -> None:
    """Bound the log so the committed database does not grow without limit."""
    conn.execute(
        """
        DELETE FROM fetch_log WHERE rowid NOT IN (
            SELECT rowid FROM (
                SELECT rowid,
                       ROW_NUMBER() OVER (
                           PARTITION BY source_id ORDER BY run_at DESC, rowid DESC
                       ) AS rn
                FROM fetch_log
            ) WHERE rn <= ?
        )
        """,
        (keep_per_source,),
    )
