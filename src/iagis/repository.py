"""SQLite transacional: migração, eventos idempotentes, claims e repetição limitada."""
from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Iterator

from pydantic import BaseModel


class Repository:
    def __init__(self, path: Path | str):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    @contextmanager
    def connection(self) -> Iterator[sqlite3.Connection]:
        db = sqlite3.connect(self.path, timeout=30)
        db.row_factory = sqlite3.Row
        try:
            yield db
            db.commit()
        finally:
            db.close()

    @staticmethod
    def _create(db: sqlite3.Connection, name: str = "analyses") -> None:
        db.execute(f"""CREATE TABLE IF NOT EXISTS {name} (
            id INTEGER PRIMARY KEY AUTOINCREMENT, ticket_id INTEGER NOT NULL,
            entity_id INTEGER NOT NULL, followup_id INTEGER, event_key TEXT NOT NULL UNIQUE,
            detected_at TEXT NOT NULL, state TEXT NOT NULL, content_hash TEXT NOT NULL,
            attempt_count INTEGER NOT NULL DEFAULT 1, last_attempt_at TEXT,
            next_retry_at TEXT, retryable INTEGER NOT NULL DEFAULT 0,
            ai_run_id TEXT, verdict TEXT, report TEXT, published_at TEXT,
            publication_followup_id INTEGER, error TEXT)""")

    def _initialize(self) -> None:
        with self.connection() as db:
            exists = db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='analyses'").fetchone()
            if not exists:
                self._create(db)
            else:
                columns = {row["name"] for row in db.execute("PRAGMA table_info(analyses)")}
                if "event_key" not in columns:
                    self._migrate_legacy(db)
            db.execute("CREATE INDEX IF NOT EXISTS idx_analyses_ticket ON analyses(ticket_id, entity_id)")
            db.execute("CREATE INDEX IF NOT EXISTS idx_analyses_state ON analyses(state, next_retry_at)")
            db.execute("PRAGMA user_version=2")

    def _migrate_legacy(self, db: sqlite3.Connection) -> None:
        """Remove UNIQUE global do hash sem perder IDs, relatórios ou publicações."""
        self._create(db, "analyses_v2")
        db.execute("""INSERT INTO analyses_v2
            (id,ticket_id,entity_id,followup_id,event_key,detected_at,state,content_hash,
             attempt_count,last_attempt_at,retryable,ai_run_id,verdict,report,published_at,
             publication_followup_id,error)
            SELECT id,ticket_id,entity_id,followup_id,
              printf('%d:%d:%s:%s',entity_id,ticket_id,
                     CASE WHEN followup_id IS NULL THEN 'description' ELSE 'followup:'||followup_id END,
                     content_hash),
              detected_at,state,content_hash,1,detected_at,0,ai_run_id,verdict,report,published_at,
              publication_followup_id,error FROM analyses""")
        db.execute("DROP TABLE analyses")
        db.execute("ALTER TABLE analyses_v2 RENAME TO analyses")

    def claim_event(self, ticket_id: int, entity_id: int, followup_id: int | None,
                    content_hash: str, event_key: str, max_attempts: int) -> int | None:
        now = datetime.now(UTC).isoformat()
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM analyses WHERE event_key=?", (event_key,)).fetchone()
            if row is None:
                cur = db.execute("""INSERT INTO analyses
                    (ticket_id,entity_id,followup_id,event_key,detected_at,state,content_hash,
                     attempt_count,last_attempt_at) VALUES (?,?,?,?,?,'PROCESSING',?,1,?)""",
                    (ticket_id, entity_id, followup_id, event_key, now, content_hash, now))
                return int(cur.lastrowid)
            eligible = (row["state"] == "FAILED" and row["retryable"] and
                        row["attempt_count"] < max_attempts and
                        (not row["next_retry_at"] or row["next_retry_at"] <= now))
            if not eligible:
                return None
            updated = db.execute("""UPDATE analyses SET state='PROCESSING',attempt_count=attempt_count+1,
                last_attempt_at=?,error=NULL WHERE id=? AND state='FAILED'""", (now, row["id"]))
            return int(row["id"]) if updated.rowcount == 1 else None

    def save_result(self, analysis_id: int, report: BaseModel, ai_run_id: str | None = None) -> None:
        state = "SUGGESTED" if report.__class__.__name__ == "ResponseSuggestion" else "ANALYZED"
        if getattr(report, "pendencias", None):
            state = "AWAITING_INFORMATION"
        with self.connection() as db:
            db.execute("""UPDATE analyses SET state=?,ai_run_id=?,verdict=?,report=?,error=NULL,
                retryable=0,next_retry_at=NULL WHERE id=? AND state='PROCESSING'""",
                (state, ai_run_id, str(getattr(report, "veredito", "")) or None,
                 report.model_dump_json(), analysis_id))

    # Compatibilidade com chamadas existentes.
    save_report = save_result

    def fail(self, analysis_id: int, error: str, *, retryable: bool = False, retry_delay: int = 300) -> None:
        retry_at = (datetime.now(UTC) + timedelta(seconds=retry_delay)).isoformat() if retryable else None
        with self.connection() as db:
            db.execute("""UPDATE analyses SET state='FAILED',error=?,retryable=?,next_retry_at=?
                WHERE id=? AND state='PROCESSING'""", (error[:1000], int(retryable), retry_at, analysis_id))

    def prepare_retry(self, analysis_id: int, max_attempts: int) -> sqlite3.Row:
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM analyses WHERE id=?", (analysis_id,)).fetchone()
            if row is None:
                raise ValueError("análise não encontrada")
            if row["state"] != "FAILED":
                raise ValueError("somente análises FAILED podem ser repetidas")
            if row["attempt_count"] >= max_attempts:
                raise ValueError("limite de tentativas atingido")
            db.execute("UPDATE analyses SET retryable=1,next_retry_at=NULL WHERE id=?", (analysis_id,))
            return row

    def get(self, analysis_id: int) -> sqlite3.Row | None:
        with self.connection() as db:
            return db.execute("SELECT * FROM analyses WHERE id=?", (analysis_id,)).fetchone()

    def recent(self, limit: int = 20) -> list[sqlite3.Row]:
        with self.connection() as db:
            return db.execute("""SELECT id,ticket_id,entity_id,state,detected_at,attempt_count,error
                FROM analyses ORDER BY id DESC LIMIT ?""", (limit,)).fetchall()

    def mark_published(self, analysis_id: int, followup_id: int) -> None:
        with self.connection() as db:
            db.execute("""UPDATE analyses SET state='PUBLISHED',published_at=?,publication_followup_id=?
                WHERE id=? AND published_at IS NULL""",
                (datetime.now(UTC).isoformat(), followup_id, analysis_id))
