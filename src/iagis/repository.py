"""Persistência SQLite transacional para idempotência e auditoria."""
from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Iterator

from .governance_models import GovernanceReport


class Repository:
    def __init__(self, path: Path | str):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    @contextmanager
    def connection(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        try:
            yield connection
            connection.commit()
        finally:
            connection.close()

    def _initialize(self) -> None:
        with self.connection() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS analyses (
                id INTEGER PRIMARY KEY AUTOINCREMENT, ticket_id INTEGER NOT NULL,
                entity_id INTEGER NOT NULL, followup_id INTEGER, detected_at TEXT NOT NULL,
                state TEXT NOT NULL, content_hash TEXT NOT NULL UNIQUE, ai_run_id TEXT,
                verdict TEXT, report TEXT, published_at TEXT, publication_followup_id INTEGER,
                error TEXT)""")
            db.execute("CREATE INDEX IF NOT EXISTS idx_analyses_ticket ON analyses(ticket_id, entity_id)")

    def register(self, ticket_id: int, entity_id: int, followup_id: int | None, content_hash: str) -> int | None:
        try:
            with self.connection() as db:
                cursor = db.execute("""INSERT INTO analyses
                    (ticket_id, entity_id, followup_id, detected_at, state, content_hash)
                    VALUES (?, ?, ?, ?, 'DETECTED', ?)""",
                    (ticket_id, entity_id, followup_id, datetime.now(UTC).isoformat(), content_hash))
                return int(cursor.lastrowid)
        except sqlite3.IntegrityError:
            return None

    def save_report(self, analysis_id: int, report: GovernanceReport, ai_run_id: str | None = None) -> None:
        state = "AWAITING_INFORMATION" if report.pendencias else "ANALYZED"
        with self.connection() as db:
            db.execute("UPDATE analyses SET state=?, ai_run_id=?, verdict=?, report=?, error=NULL WHERE id=?",
                       (state, ai_run_id, report.veredito.value, report.model_dump_json(), analysis_id))

    def fail(self, analysis_id: int, error: str) -> None:
        with self.connection() as db:
            db.execute("UPDATE analyses SET state='FAILED', error=? WHERE id=?", (error[:1000], analysis_id))

    def get(self, analysis_id: int) -> sqlite3.Row | None:
        with self.connection() as db:
            return db.execute("SELECT * FROM analyses WHERE id=?", (analysis_id,)).fetchone()

    def mark_published(self, analysis_id: int, followup_id: int) -> None:
        with self.connection() as db:
            db.execute("""UPDATE analyses SET state='PUBLISHED', published_at=?,
                       publication_followup_id=? WHERE id=? AND published_at IS NULL""",
                       (datetime.now(UTC).isoformat(), followup_id, analysis_id))
