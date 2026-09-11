import pytest
pytest.importorskip("fastapi")
from fastapi.testclient import TestClient
from iagis.admin import app
from iagis.config import get_settings


def test_admin_requires_auth(monkeypatch,tmp_path):
    monkeypatch.setenv("GLPI_URL","https://example.invalid")
    monkeypatch.setenv("GLPI_APP_TOKEN","x"); monkeypatch.setenv("GLPI_USER_TOKEN","x")
    monkeypatch.setenv("IAGIS_ADMIN_PASSWORD","secret")
    monkeypatch.setenv("IAGIS_DATABASE_PATH",str(tmp_path/"db")); get_settings.cache_clear()
    client=TestClient(app)
    assert client.get("/").status_code == 401
    assert client.get("/",auth=("iagis","secret")).status_code == 200
    get_settings.cache_clear()
