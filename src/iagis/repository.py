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
            db.executescript("""
                CREATE TABLE IF NOT EXISTS ai_models (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL UNIQUE,
                    provider TEXT NOT NULL, model_name TEXT NOT NULL, base_url TEXT,
                    timeout REAL NOT NULL DEFAULT 120, enabled INTEGER NOT NULL DEFAULT 1);
                CREATE TABLE IF NOT EXISTS agents_config (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL UNIQUE,
                    description TEXT NOT NULL DEFAULT '', prompt TEXT NOT NULL,
                    model_id INTEGER NOT NULL REFERENCES ai_models(id), enabled INTEGER NOT NULL DEFAULT 1);
                CREATE TABLE IF NOT EXISTS skills (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL UNIQUE,
                    description TEXT NOT NULL DEFAULT '', input_schema TEXT NOT NULL,
                    output_schema TEXT NOT NULL, source TEXT NOT NULL,
                    enabled INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS agent_skills (
                    agent_id INTEGER NOT NULL REFERENCES agents_config(id) ON DELETE CASCADE,
                    skill_id INTEGER NOT NULL REFERENCES skills(id) ON DELETE CASCADE,
                    position INTEGER NOT NULL DEFAULT 0, PRIMARY KEY(agent_id, skill_id));
                CREATE TABLE IF NOT EXISTS routing_rules (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL,
                    priority INTEGER NOT NULL DEFAULT 100, field TEXT NOT NULL,
                    pattern TEXT NOT NULL, agent_id INTEGER NOT NULL REFERENCES agents_config(id),
                    enabled INTEGER NOT NULL DEFAULT 1);
            """)
            db.execute("PRAGMA user_version=3")

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

    def admin_rows(self, table: str) -> list[sqlite3.Row]:
        allowed = {"ai_models", "agents_config", "skills", "routing_rules"}
        if table not in allowed:
            raise ValueError("tabela administrativa inválida")
        with self.connection() as db:
            return db.execute(f"SELECT * FROM {table} ORDER BY id").fetchall()

    def add_model(self, name: str, provider: str, model_name: str, base_url: str, timeout: float) -> None:
        with self.connection() as db:
            db.execute("INSERT INTO ai_models(name,provider,model_name,base_url,timeout) VALUES(?,?,?,?,?)",
                       (name, provider, model_name, base_url, timeout))

    def add_agent(self, name: str, description: str, prompt: str, model_id: int) -> None:
        with self.connection() as db:
            db.execute("INSERT INTO agents_config(name,description,prompt,model_id) VALUES(?,?,?,?)",
                       (name, description, prompt, model_id))

    def add_skill(self, name: str, description: str, input_schema: str,
                  output_schema: str, source: str) -> None:
        with self.connection() as db:
            db.execute("""INSERT INTO skills(name,description,input_schema,output_schema,source,created_at)
                        VALUES(?,?,?,?,?,?)""",
                       (name, description, input_schema, output_schema, source, datetime.now(UTC).isoformat()))

    def add_rule(self, name: str, priority: int, field: str, pattern: str, agent_id: int) -> None:
        with self.connection() as db:
            db.execute("INSERT INTO routing_rules(name,priority,field,pattern,agent_id) VALUES(?,?,?,?,?)",
                       (name, priority, field, pattern, agent_id))

    def set_enabled(self, table: str, row_id: int, enabled: bool) -> None:
        allowed = {"ai_models", "agents_config", "skills", "routing_rules"}
        if table not in allowed:
            raise ValueError("tabela administrativa inválida")
        with self.connection() as db:
            db.execute(f"UPDATE {table} SET enabled=? WHERE id=?", (int(enabled), row_id))

    def bind_skill(self, agent_id: int, skill_id: int) -> None:
        with self.connection() as db:
            db.execute("INSERT OR IGNORE INTO agent_skills(agent_id,skill_id,position) VALUES(?,?,?)",
                       (agent_id, skill_id, skill_id))

    def resolve_agent(self, context: dict[str, str]) -> sqlite3.Row | None:
        """Primeira regra habilitada por prioridade; regex é deliberadamente evitada."""
        with self.connection() as db:
            rules = db.execute("""SELECT r.*,a.name agent_name,a.prompt,m.provider,m.model_name,m.base_url,m.timeout
                FROM routing_rules r JOIN agents_config a ON a.id=r.agent_id
                JOIN ai_models m ON m.id=a.model_id
                WHERE r.enabled=1 AND a.enabled=1 AND m.enabled=1 ORDER BY r.priority,r.id""").fetchall()
            for rule in rules:
                if rule["pattern"].casefold() in context.get(rule["field"], "").casefold():
                    return rule
        return None

    def agent_skills(self, agent_id: int) -> list[sqlite3.Row]:
        with self.connection() as db:
            return db.execute("""SELECT s.* FROM skills s JOIN agent_skills x ON x.skill_id=s.id
                WHERE x.agent_id=? AND s.enabled=1 ORDER BY x.position,s.id""", (agent_id,)).fetchall()
