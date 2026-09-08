from iagis.repository import Repository

def test_idempotency(tmp_path):
    repo = Repository(tmp_path/"db.sqlite")
    first = repo.register(1, 2, None, "same")
    assert first
    assert repo.register(2, 2, 5, "same") is None

def test_save_and_publish(tmp_path, report):
    repo = Repository(tmp_path/"db.sqlite")
    aid = repo.register(1, 2, None, "hash")
    repo.save_report(aid, report, "run_1")
    assert repo.get(aid)["verdict"] == "HOMOLOGADO"
    repo.mark_published(aid, 10)
    assert repo.get(aid)["state"] == "PUBLISHED"
