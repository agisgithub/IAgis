import pytest
from pydantic import ValidationError

from iagis.config import Settings

BASE = {"GLPI_URL":"https://glpi.example", "GLPI_APP_TOKEN":"a", "GLPI_USER_TOKEN":"u",
        "AI_PROVIDER":"gemini", "GEMINI_API_KEY":"g", "AI_MODEL":"gemini-test"}

def test_config_and_lists(tmp_path):
    settings = Settings(**BASE, IAGIS_DATABASE_PATH=str(tmp_path/"x.db"),
                        IAGIS_ALLOWED_ENTITY_IDS="1, 2", IAGIS_DRY_RUN="true")
    assert settings.dry_run is True
    assert settings.authorized_entities == {1, 2}
    assert "a" not in repr(settings.glpi_app_token)

def test_missing_variables(monkeypatch):
    for name in ("GLPI_URL", "GLPI_APP_TOKEN", "GLPI_USER_TOKEN"):
        monkeypatch.delenv(name, raising=False)
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

def test_production_requires_technical_user():
    with pytest.raises(ValidationError, match="IAGIS_GLPI_USER_ID"):
        Settings(**{**BASE, "IAGIS_DRY_RUN": False})

def test_vpn_requires_token_and_loopback_or_tls():
    with pytest.raises(ValidationError, match="IAGIS_VPN_BROKER_TOKEN"):
        Settings(**BASE, IAGIS_VPN_ENABLED=True)
    with pytest.raises(ValidationError, match="loopback"):
        Settings(**BASE, IAGIS_VPN_ENABLED=True, IAGIS_VPN_BROKER_TOKEN="x"*32,
                 IAGIS_VPN_BROKER_URL="http://vpn.example:8091")
    settings = Settings(**BASE, IAGIS_VPN_ENABLED=True,
                        IAGIS_VPN_BROKER_TOKEN="x"*32)
    assert settings.vpn_enabled is True

def test_access_requires_long_token_and_private_broker():
    with pytest.raises(ValidationError, match=r"32\+"):
        Settings(**BASE, IAGIS_ACCESS_ENABLED=True, IAGIS_ACCESS_BROKER_TOKEN="curto")
    with pytest.raises(ValidationError, match="loopback"):
        Settings(**BASE, IAGIS_ACCESS_ENABLED=True, IAGIS_ACCESS_BROKER_TOKEN="x"*32,
                 IAGIS_ACCESS_BROKER_URL="http://access.example:8092")
    settings = Settings(**BASE, IAGIS_ACCESS_ENABLED=True,
                        IAGIS_ACCESS_BROKER_TOKEN="x"*32)
    assert settings.access_enabled is True
