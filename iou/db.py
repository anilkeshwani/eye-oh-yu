from __future__ import annotations

import os
import sqlite3
from pathlib import Path

CURRENT_SCHEMA_VERSION = 1

SCHEMA = f"""
CREATE TABLE IF NOT EXISTS schema_version (
    version INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS person (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL UNIQUE COLLATE NOCASE,
    slack_id TEXT UNIQUE,
    slack_handle TEXT UNIQUE COLLATE NOCASE,
    archived INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

CREATE TABLE IF NOT EXISTS expense (
    id INTEGER PRIMARY KEY,
    description TEXT NOT NULL,
    amount_cents INTEGER NOT NULL CHECK (amount_cents > 0),
    currency TEXT NOT NULL DEFAULT 'EUR',
    payer_id INTEGER NOT NULL REFERENCES person(id),
    spent_at TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    category TEXT,
    source TEXT,
    split_mode TEXT NOT NULL CHECK (split_mode IN ('equal', 'exact')),
    voided INTEGER NOT NULL DEFAULT 0,
    void_reason TEXT,
    superseded_by INTEGER REFERENCES expense(id)
);

CREATE TABLE IF NOT EXISTS expense_share (
    expense_id INTEGER NOT NULL REFERENCES expense(id),
    person_id INTEGER NOT NULL REFERENCES person(id),
    amount_cents INTEGER NOT NULL CHECK (amount_cents > 0),
    PRIMARY KEY (expense_id, person_id)
);

CREATE TABLE IF NOT EXISTS settlement (
    id INTEGER PRIMARY KEY,
    from_id INTEGER NOT NULL REFERENCES person(id),
    to_id INTEGER NOT NULL REFERENCES person(id),
    amount_cents INTEGER NOT NULL CHECK (amount_cents > 0),
    note TEXT,
    source TEXT,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    voided INTEGER NOT NULL DEFAULT 0,
    void_reason TEXT,
    superseded_by INTEGER REFERENCES settlement(id),
    CHECK (from_id != to_id)
);

CREATE INDEX IF NOT EXISTS idx_expense_spent_at ON expense(spent_at);
CREATE INDEX IF NOT EXISTS idx_expense_share_person ON expense_share(person_id);
CREATE INDEX IF NOT EXISTS idx_settlement_from ON settlement(from_id);
CREATE INDEX IF NOT EXISTS idx_settlement_to ON settlement(to_id);

INSERT INTO schema_version (version) VALUES ({CURRENT_SCHEMA_VERSION});
"""


def resolve_db_path(explicit: str | None) -> Path:
    if explicit:
        return Path(explicit).expanduser()
    env = os.environ.get("IOU_DB")
    if env:
        return Path(env).expanduser()
    return Path("iou.db")


def connect(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    fresh = conn.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'schema_version'"
    ).fetchone() is None
    if fresh:
        conn.executescript(SCHEMA)
        conn.commit()
        return conn
    row = conn.execute("SELECT version FROM schema_version").fetchone()
    version = row["version"] if row is not None else None
    if version != CURRENT_SCHEMA_VERSION:
        conn.close()
        raise RuntimeError(
            f"database at {db_path} has schema version {version}, "
            f"this build expects version {CURRENT_SCHEMA_VERSION}"
        )
    return conn
