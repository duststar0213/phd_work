"""SQLite store for study participants, sessions, canvas snapshots, and events."""

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parent / "data"
DB_PATH = DATA_DIR / "repertoire.db"
ALLOWLIST_PATH = DATA_DIR / "allowlist.txt"
SESSION_DAYS = 14
CODE_MINUTES = 10
STUDY_CAP = 15


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def connect() -> sqlite3.Connection:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db() -> None:
    with connect() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS participants (
              id INTEGER PRIMARY KEY,
              username TEXT UNIQUE NOT NULL,
              password_hash TEXT NOT NULL,
              created_at TEXT NOT NULL,
              last_seen_at TEXT
            );

            CREATE TABLE IF NOT EXISTS sessions (
              token TEXT PRIMARY KEY,
              participant_id INTEGER NOT NULL,
              created_at TEXT NOT NULL,
              expires_at TEXT NOT NULL,
              FOREIGN KEY (participant_id) REFERENCES participants(id)
            );

            CREATE TABLE IF NOT EXISTS canvas_state (
              participant_id INTEGER PRIMARY KEY,
              payload TEXT NOT NULL,
              updated_at TEXT NOT NULL,
              FOREIGN KEY (participant_id) REFERENCES participants(id)
            );

            CREATE TABLE IF NOT EXISTS events (
              id INTEGER PRIMARY KEY,
              participant_id INTEGER NOT NULL,
              ts TEXT NOT NULL,
              type TEXT NOT NULL,
              payload TEXT NOT NULL DEFAULT '{}',
              FOREIGN KEY (participant_id) REFERENCES participants(id)
            );

            CREATE TABLE IF NOT EXISTS login_codes (
              email TEXT PRIMARY KEY,
              code_hash TEXT NOT NULL,
              expires_at TEXT NOT NULL,
              created_at TEXT NOT NULL
            );
            """
        )


def get_participant_by_username(username: str) -> sqlite3.Row | None:
    with connect() as conn:
        return conn.execute(
            "SELECT * FROM participants WHERE username = ?",
            (username,),
        ).fetchone()


def get_participant(participant_id: int) -> sqlite3.Row | None:
    with connect() as conn:
        return conn.execute(
            "SELECT * FROM participants WHERE id = ?",
            (participant_id,),
        ).fetchone()


def insert_participant(username: str, password_hash: str = "") -> int:
    now = utc_now()
    with connect() as conn:
        cur = conn.execute(
            "INSERT INTO participants (username, password_hash, created_at) VALUES (?, ?, ?)",
            (username, password_hash, now),
        )
        return int(cur.lastrowid)


def set_password(participant_id: int, password_hash: str) -> None:
    with connect() as conn:
        conn.execute(
            "UPDATE participants SET password_hash = ? WHERE id = ?",
            (password_hash, participant_id),
        )


def has_password(person: sqlite3.Row) -> bool:
    return bool((person["password_hash"] or "").strip())


def delete_sessions_for(participant_id: int) -> None:
    with connect() as conn:
        conn.execute("DELETE FROM sessions WHERE participant_id = ?", (participant_id,))


def participant_count() -> int:
    with connect() as conn:
        row = conn.execute(
            "SELECT COUNT(*) AS n FROM participants WHERE username LIKE '%@%'"
        ).fetchone()
    return int(row["n"] if row else 0)


class StudyFullError(Exception):
    """New email cannot join because the study already has STUDY_CAP people."""


class NotInvitedError(Exception):
    """Email is not on the optional invite allowlist."""


def invited_emails() -> set[str] | None:
    """None = no allowlist (anyone with the access code can join). Non-empty set = invite-only."""
    if not ALLOWLIST_PATH.exists():
        return None
    emails: set[str] = set()
    text = ALLOWLIST_PATH.read_text(encoding="utf-8")
    for line in text.splitlines():
        line = line.strip().lower()
        if not line or line.startswith("#"):
            continue
        emails.add(line)
    return emails or None


def require_invited(email: str) -> None:
    invited = invited_emails()
    if invited is None:
        return
    if email not in invited:
        raise NotInvitedError()


def get_or_create_participant(email: str, cap: int = STUDY_CAP) -> sqlite3.Row:
    """Same email always returns the same person. New emails fail if the study is full."""
    person = get_participant_by_username(email)
    if person:
        return person
    if participant_count() >= cap:
        raise StudyFullError()
    participant_id = insert_participant(email)
    person = get_participant(participant_id)
    if not person:
        raise RuntimeError("failed to create participant")
    return person


def save_login_code(email: str, code_hash: str, expires_at: str) -> None:
    now = utc_now()
    with connect() as conn:
        conn.execute(
            """
            INSERT INTO login_codes (email, code_hash, expires_at, created_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(email) DO UPDATE SET
              code_hash = excluded.code_hash,
              expires_at = excluded.expires_at,
              created_at = excluded.created_at
            """,
            (email, code_hash, expires_at, now),
        )


def get_login_code(email: str) -> sqlite3.Row | None:
    with connect() as conn:
        return conn.execute(
            "SELECT * FROM login_codes WHERE email = ?",
            (email,),
        ).fetchone()


def delete_login_code(email: str) -> None:
    with connect() as conn:
        conn.execute("DELETE FROM login_codes WHERE email = ?", (email,))


def touch_participant(participant_id: int) -> None:
    with connect() as conn:
        conn.execute(
            "UPDATE participants SET last_seen_at = ? WHERE id = ?",
            (utc_now(), participant_id),
        )


def create_session(participant_id: int, token: str) -> None:
    now = datetime.now(timezone.utc)
    expires = now + timedelta(days=SESSION_DAYS)
    with connect() as conn:
        conn.execute(
            """
            INSERT INTO sessions (token, participant_id, created_at, expires_at)
            VALUES (?, ?, ?, ?)
            """,
            (
                token,
                participant_id,
                now.strftime("%Y-%m-%dT%H:%M:%SZ"),
                expires.strftime("%Y-%m-%dT%H:%M:%SZ"),
            ),
        )


def participant_for_token(token: str) -> sqlite3.Row | None:
    if not token:
        return None
    now = utc_now()
    with connect() as conn:
        row = conn.execute(
            """
            SELECT p.* FROM sessions s
            JOIN participants p ON p.id = s.participant_id
            WHERE s.token = ? AND s.expires_at > ?
            """,
            (token, now),
        ).fetchone()
    return row


def delete_session(token: str) -> None:
    with connect() as conn:
        conn.execute("DELETE FROM sessions WHERE token = ?", (token,))


def save_canvas(participant_id: int, payload: dict) -> None:
    now = utc_now()
    blob = json.dumps(payload, ensure_ascii=False)
    with connect() as conn:
        conn.execute(
            """
            INSERT INTO canvas_state (participant_id, payload, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(participant_id) DO UPDATE SET
              payload = excluded.payload,
              updated_at = excluded.updated_at
            """,
            (participant_id, blob, now),
        )


def load_canvas(participant_id: int) -> dict | None:
    with connect() as conn:
        row = conn.execute(
            "SELECT payload, updated_at FROM canvas_state WHERE participant_id = ?",
            (participant_id,),
        ).fetchone()
    if not row:
        return None
    data = json.loads(row["payload"])
    data["updated_at"] = row["updated_at"]
    return data


def add_event(participant_id: int, event_type: str, payload: dict | None = None) -> None:
    with connect() as conn:
        conn.execute(
            "INSERT INTO events (participant_id, ts, type, payload) VALUES (?, ?, ?, ?)",
            (
                participant_id,
                utc_now(),
                event_type,
                json.dumps(payload or {}, ensure_ascii=False),
            ),
        )


def list_export() -> list[dict]:
    with connect() as conn:
        people = conn.execute(
            "SELECT id, username, created_at, last_seen_at FROM participants ORDER BY username"
        ).fetchall()
        out = []
        for person in people:
            canvas_row = conn.execute(
                "SELECT payload, updated_at FROM canvas_state WHERE participant_id = ?",
                (person["id"],),
            ).fetchone()
            events = conn.execute(
                "SELECT ts, type, payload FROM events WHERE participant_id = ? ORDER BY id",
                (person["id"],),
            ).fetchall()
            canvas = json.loads(canvas_row["payload"]) if canvas_row else None
            out.append(
                {
                    "email": person["username"],
                    "created_at": person["created_at"],
                    "last_seen_at": person["last_seen_at"],
                    "canvas_updated_at": canvas_row["updated_at"] if canvas_row else None,
                    "canvas": canvas,
                    "events": [
                        {
                            "ts": item["ts"],
                            "type": item["type"],
                            "payload": json.loads(item["payload"]),
                        }
                        for item in events
                    ],
                }
            )
        return out
