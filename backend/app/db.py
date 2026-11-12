"""SQLite persistence: schema, connections, and small query helpers."""
from __future__ import annotations

import os
import sqlite3
from contextlib import contextmanager

DB_PATH = os.environ.get("LEDGER_DB", os.path.join(os.path.dirname(__file__), "..", "ledger.db"))

SCHEMA = """
CREATE TABLE IF NOT EXISTS events(
  event_id     TEXT PRIMARY KEY,
  member_id    TEXT NOT NULL,
  type         TEXT NOT NULL,
  occurred_at  TEXT NOT NULL,
  seq          INTEGER NOT NULL,
  amount       INTEGER NOT NULL,
  expires_at   TEXT,
  ref_event_id TEXT,
  status       TEXT NOT NULL,            -- applied | pending
  content_hash TEXT NOT NULL,
  created_at   TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_events_member ON events(member_id, status);

CREATE TABLE IF NOT EXISTS versions(
  id         INTEGER PRIMARY KEY AUTOINCREMENT,
  member_id  TEXT NOT NULL,
  version    INTEGER NOT NULL,
  kind       TEXT NOT NULL,              -- published | draft
  payload    TEXT NOT NULL,
  conflicts  TEXT NOT NULL DEFAULT '[]',
  created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_versions_member ON versions(member_id, kind, version);
"""


@contextmanager
def connect(path: str | None = None):
    db = sqlite3.connect(path or DB_PATH)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys = ON")
    try:
        yield db
        db.commit()
    finally:
        db.close()


def init_db(path: str | None = None) -> None:
    with connect(path) as db:
        db.executescript(SCHEMA)
