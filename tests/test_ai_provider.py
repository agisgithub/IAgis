import pytest
from iagis.ai_provider import UnsupportedProvider, build_governance_ai
from iagis.config import Settings

BASE=dict(GLPI_URL="https://glpi.example",GLPI_APP_TOKEN="a",GLPI_USER_TOKEN="u",
          AI_PROVIDER="ollama",AI_MODEL="local-model")

def test_future_adapter_fails_clearly():
    with pytest.raises(UnsupportedProvider, match="ainda não foi habilitado"):
        build_governance_ai(Settings(**BASE))
