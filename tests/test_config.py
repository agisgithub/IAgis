import pytest
from pydantic import ValidationError
from iagis.config import Settings

BASE = dict(GLPI_URL="https://glpi.example", GLPI_APP_TOKEN="a", GLPI_USER_TOKEN="u",
            AI_PROVIDER="gemini", GEMINI_API_KEY="g", AI_MODEL="gemini-test")

def test_config_and_lists(tmp_path):
    settings = Settings(**BASE, IAGIS_DATABASE_PATH=str(tmp_path/"x.db"),
                        IAGIS_ALLOWED_ENTITY_IDS="1, 2", IAGIS_DRY_RUN="true")
    assert settings.dry_run is True
    assert settings.authorized_entities == {1, 2}
    assert "a" not in repr(settings.glpi_app_token)

def test_missing_variables():
    with pytest.raises(ValidationError):
        Settings()

def test_tls_required():
    with pytest.raises(ValidationError):
        Settings(**{**BASE, "GLPI_URL": "http://unsafe"})

def test_gemini_key_required():
    with pytest.raises(ValidationError):
        Settings(**{k:v for k,v in BASE.items() if k != "GEMINI_API_KEY"})

def test_future_provider_can_be_configured_without_importing_adapter():
    settings = Settings(**{**BASE, "AI_PROVIDER":"ollama", "GEMINI_API_KEY":""})
    assert settings.ai_provider == "ollama"
