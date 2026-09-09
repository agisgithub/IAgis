import sqlite3
from iagis.repository import Repository


def test_same_text_isolated_by_event(tmp_path):
    repo = Repository(tmp_path/"db.sqlite")
    assert repo.claim_event(1, 2, 5, "same", "2:1:followup:5:same", 3)
    assert repo.claim_event(2, 2, 8, "same", "2:2:followup:8:same", 3)
    assert repo.claim_event(1, 2, 5, "same", "2:1:followup:5:same", 3) is None


def test_retry_limit_and_claim(tmp_path):
    repo=Repository(tmp_path/"db.sqlite")
    aid=repo.claim_event(1,2,None,"h","2:1:description:h",2)
    repo.fail(aid,"timeout",retryable=True,retry_delay=5)
    assert repo.claim_event(1,2,None,"h","2:1:description:h",2) is None
    repo.prepare_retry(aid,2)
    assert repo.claim_event(1,2,None,"h","2:1:description:h",2) == aid
    repo.fail(aid,"timeout",retryable=True,retry_delay=5)
    try:
        repo.prepare_retry(aid,2)
    except ValueError as exc:
        assert "limite" in str(exc)
    else:
        raise AssertionError("retry deveria respeitar limite")


def test_legacy_migration_preserves_report_and_removes_global_hash_unique(tmp_path):
    path=tmp_path/"legacy.db"; db=sqlite3.connect(path)
    db.execute("""CREATE TABLE analyses (id INTEGER PRIMARY KEY AUTOINCREMENT,ticket_id INTEGER NOT NULL,
      entity_id INTEGER NOT NULL,followup_id INTEGER,detected_at TEXT NOT NULL,state TEXT NOT NULL,
      content_hash TEXT NOT NULL UNIQUE,ai_run_id TEXT,verdict TEXT,report TEXT,published_at TEXT,
      publication_followup_id INTEGER,error TEXT)""")
    db.execute("INSERT INTO analyses(ticket_id,entity_id,followup_id,detected_at,state,content_hash,report) VALUES(1,2,NULL,'now','ANALYZED','same','saved')")
    db.commit(); db.close()
    repo=Repository(path)
    assert repo.get(1)["report"] == "saved"
    assert repo.claim_event(2,2,7,"same","2:2:followup:7:same",3)


def test_save_result(tmp_path, report):
    repo=Repository(tmp_path/"db")
    aid=repo.claim_event(1,2,None,"h","2:1:description:h",3)
    repo.save_result(aid,report,"run")
    assert repo.get(aid)["state"] == "ANALYZED"
