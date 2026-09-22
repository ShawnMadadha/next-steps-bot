import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

DB_PATH = str(Path(__file__).resolve().parents[1] / "next_steps.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS bots (
    id TEXT PRIMARY KEY,
    meeting_url TEXT NOT NULL,
    platform TEXT NOT NULL,
    created_at TEXT NOT NULL,
    ended INTEGER NOT NULL DEFAULT 0,
    post_meeting_at TEXT
);
CREATE TABLE IF NOT EXISTS status_changes (
    bot_id TEXT NOT NULL,
    code TEXT NOT NULL,
    sub_code TEXT,
    created_at TEXT NOT NULL,
    UNIQUE (bot_id, code, sub_code, created_at)
);
CREATE TABLE IF NOT EXISTS utterances (
    id INTEGER PRIMARY KEY,
    bot_id TEXT NOT NULL,
    participant_id INTEGER NOT NULL,
    speaker TEXT NOT NULL,
    text TEXT NOT NULL,
    start_ts REAL NOT NULL,
    UNIQUE (bot_id, participant_id, start_ts, text)
);
CREATE TABLE IF NOT EXISTS commitments (
    id INTEGER PRIMARY KEY,
    bot_id TEXT NOT NULL,
    owner TEXT NOT NULL,
    action TEXT NOT NULL,
    due TEXT,
    confidence REAL NOT NULL,
    quote TEXT,
    status TEXT NOT NULL,
    followup_draft TEXT,
    sent_via TEXT,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS webhook_events (
    id TEXT PRIMARY KEY,
    received_at TEXT NOT NULL
);
"""


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@contextmanager
def db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        with conn:
            yield conn
    finally:
        conn.close()


def init():
    with db() as conn:
        conn.executescript(SCHEMA)


# bots

def add_bot(bot_id, meeting_url, platform):
    with db() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO bots (id, meeting_url, platform, created_at) VALUES (?, ?, ?, ?)",
            (bot_id, meeting_url, platform, now()),
        )


def get_bot(bot_id):
    with db() as conn:
        return conn.execute("SELECT * FROM bots WHERE id = ?", (bot_id,)).fetchone()


def list_bots():
    with db() as conn:
        return conn.execute(
            """
            SELECT b.*,
                   (SELECT code FROM status_changes s WHERE s.bot_id = b.id
                    ORDER BY s.created_at DESC, s.rowid DESC LIMIT 1) AS status,
                   (SELECT COUNT(*) FROM commitments c WHERE c.bot_id = b.id
                    AND c.status != 'dismissed') AS commitment_count
            FROM bots b ORDER BY b.created_at DESC
            """
        ).fetchall()


def mark_ended(bot_id):
    with db() as conn:
        conn.execute("UPDATE bots SET ended = 1 WHERE id = ?", (bot_id,))


def mark_post_meeting_done(bot_id):
    with db() as conn:
        conn.execute("UPDATE bots SET post_meeting_at = ? WHERE id = ?", (now(), bot_id))


# status changes

def add_status_change(bot_id, code, sub_code, created_at):
    with db() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO status_changes (bot_id, code, sub_code, created_at) VALUES (?, ?, ?, ?)",
            (bot_id, code, sub_code, created_at),
        )


def list_status_changes(bot_id):
    with db() as conn:
        return conn.execute(
            "SELECT * FROM status_changes WHERE bot_id = ? ORDER BY created_at, rowid", (bot_id,)
        ).fetchall()


# utterances

def add_utterance(bot_id, participant_id, speaker, text, start_ts):
    """Returns False when this exact utterance is already stored (Recall retried the event)."""
    with db() as conn:
        cur = conn.execute(
            "INSERT OR IGNORE INTO utterances (bot_id, participant_id, speaker, text, start_ts) VALUES (?, ?, ?, ?, ?)",
            (bot_id, participant_id, speaker, text, start_ts),
        )
        return cur.rowcount == 1


def recent_utterances(bot_id, limit):
    with db() as conn:
        rows = conn.execute(
            "SELECT * FROM utterances WHERE bot_id = ? ORDER BY start_ts DESC, id DESC LIMIT ?", (bot_id, limit)
        ).fetchall()
    return list(reversed(rows))


def list_utterances(bot_id):
    with db() as conn:
        return conn.execute(
            "SELECT * FROM utterances WHERE bot_id = ? ORDER BY start_ts, id", (bot_id,)
        ).fetchall()


# commitments

def add_commitment(bot_id, c, status, followup_draft=None):
    with db() as conn:
        cur = conn.execute(
            """INSERT INTO commitments (bot_id, owner, action, due, confidence, quote, status, followup_draft, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (bot_id, c["owner"], c["action"], c.get("due"), c["confidence"], c.get("quote"), status, followup_draft, now()),
        )
        return cur.lastrowid


def get_commitment(commitment_id):
    with db() as conn:
        return conn.execute("SELECT * FROM commitments WHERE id = ?", (commitment_id,)).fetchone()


def list_commitments(bot_id):
    with db() as conn:
        return conn.execute("SELECT * FROM commitments WHERE bot_id = ? ORDER BY id", (bot_id,)).fetchall()


def set_status(commitment_id, status, sent_via=None):
    with db() as conn:
        conn.execute("UPDATE commitments SET status = ?, sent_via = ? WHERE id = ?", (status, sent_via, commitment_id))


def set_followup(commitment_id, draft):
    with db() as conn:
        conn.execute("UPDATE commitments SET followup_draft = ? WHERE id = ?", (draft, commitment_id))


def confirm_proposed(bot_id):
    with db() as conn:
        conn.execute("UPDATE commitments SET status = 'confirmed' WHERE bot_id = ? AND status = 'proposed'", (bot_id,))


# webhook events

def first_time(event_id):
    """Returns False when this webhook id was already handled."""
    with db() as conn:
        cur = conn.execute(
            "INSERT OR IGNORE INTO webhook_events (id, received_at) VALUES (?, ?)", (event_id, now())
        )
        return cur.rowcount == 1
