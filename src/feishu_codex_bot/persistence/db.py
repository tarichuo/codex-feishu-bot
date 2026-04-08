"""SQLite connection and schema management for the Feishu Codex Bot."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
import sqlite3
from pathlib import Path


SCHEMA_STATEMENTS: tuple[str, ...] = (
    """
    CREATE TABLE IF NOT EXISTS schema_metadata (
        key TEXT PRIMARY KEY,
        value TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS sessions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        scope_type TEXT NOT NULL,
        scope_key TEXT NOT NULL,
        bot_app_id TEXT NOT NULL,
        user_open_id TEXT,
        chat_id TEXT,
        thread_id TEXT NOT NULL,
        thread_generation INTEGER NOT NULL DEFAULT 1,
        last_message_at TEXT,
        expires_at TEXT,
        status TEXT NOT NULL DEFAULT 'active',
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    )
    """,
    """
    CREATE UNIQUE INDEX IF NOT EXISTS idx_sessions_scope_key
    ON sessions (scope_key)
    """,
    """
    CREATE TABLE IF NOT EXISTS processed_messages (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        bot_app_id TEXT NOT NULL,
        feishu_event_id TEXT,
        feishu_message_id TEXT NOT NULL,
        chat_id TEXT,
        sender_open_id TEXT,
        session_scope_key TEXT,
        turn_id TEXT,
        status TEXT NOT NULL,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    )
    """,
    """
    CREATE UNIQUE INDEX IF NOT EXISTS idx_processed_messages_message
    ON processed_messages (bot_app_id, feishu_message_id)
    """,
    """
    CREATE UNIQUE INDEX IF NOT EXISTS idx_processed_messages_event
    ON processed_messages (bot_app_id, feishu_event_id)
    WHERE feishu_event_id IS NOT NULL
    """,
    """
    CREATE TABLE IF NOT EXISTS reply_messages (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        bot_app_id TEXT NOT NULL,
        feishu_message_id TEXT NOT NULL,
        reply_message_id TEXT NOT NULL,
        thread_id TEXT NOT NULL,
        turn_id TEXT,
        agent_item_id TEXT,
        status TEXT NOT NULL,
        reaction_applied INTEGER NOT NULL DEFAULT 0,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    )
    """,
    """
    CREATE UNIQUE INDEX IF NOT EXISTS idx_reply_messages_reply
    ON reply_messages (bot_app_id, reply_message_id)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_reply_messages_feishu_message
    ON reply_messages (bot_app_id, feishu_message_id)
    """,
    """
    CREATE TABLE IF NOT EXISTS pending_actions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        request_id TEXT NOT NULL,
        action_type TEXT NOT NULL,
        thread_id TEXT NOT NULL,
        turn_id TEXT NOT NULL,
        item_id TEXT,
        session_scope_key TEXT,
        feishu_message_id TEXT,
        payload_json TEXT NOT NULL,
        status TEXT NOT NULL,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    )
    """,
    """
    CREATE UNIQUE INDEX IF NOT EXISTS idx_pending_actions_request
    ON pending_actions (request_id)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_pending_actions_status
    ON pending_actions (status)
    """,
    """
    CREATE TABLE IF NOT EXISTS media_assets (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        bot_app_id TEXT NOT NULL,
        source_type TEXT NOT NULL,
        source_message_id TEXT,
        local_path TEXT NOT NULL,
        mime_type TEXT,
        sha256 TEXT,
        size_bytes INTEGER,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        expires_at TEXT
    )
    """,
    """
    CREATE UNIQUE INDEX IF NOT EXISTS idx_media_assets_local_path
    ON media_assets (local_path)
    """,
    """
    CREATE TABLE IF NOT EXISTS security_alerts (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        bot_app_id TEXT NOT NULL,
        sender_open_id TEXT,
        chat_id TEXT,
        chat_type TEXT NOT NULL,
        feishu_message_id TEXT NOT NULL,
        feishu_event_id TEXT,
        owner_open_id TEXT NOT NULL,
        owner_alert_message_id TEXT,
        status TEXT NOT NULL,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_security_alerts_sender
    ON security_alerts (bot_app_id, sender_open_id, created_at)
    """,
)


def connect_sqlite(db_path: Path) -> sqlite3.Connection:
    """Create a SQLite connection configured for this application."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(db_path)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA journal_mode = WAL")
    connection.execute("PRAGMA busy_timeout = 5000")
    connection.execute("PRAGMA synchronous = NORMAL")
    return connection


class DatabaseManager:
    """Manage SQLite connections and initialize the application schema."""

    def __init__(self, db_path: Path) -> None:
        self._db_path = db_path.resolve()

    @property
    def db_path(self) -> Path:
        """Return the resolved database path."""
        return self._db_path

    @contextmanager
    def connection(self) -> Iterator[sqlite3.Connection]:
        """Yield a configured connection and ensure it is always closed."""
        connection = connect_sqlite(self._db_path)
        try:
            yield connection
        finally:
            connection.close()

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        """Yield a connection inside a transaction and commit or rollback safely."""
        with self.connection() as connection:
            try:
                yield connection
            except Exception:
                connection.rollback()
                raise
            else:
                connection.commit()

    def initialize(self) -> None:
        """Initialize the SQLite schema required by the application."""
        with self.transaction() as connection:
            for statement in SCHEMA_STATEMENTS:
                connection.execute(statement)
            connection.execute(
                """
                INSERT INTO schema_metadata(key, value)
                VALUES ('schema_version', '1')
                ON CONFLICT(key) DO UPDATE SET value = excluded.value
                """
            )
