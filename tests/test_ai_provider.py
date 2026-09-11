from iagis.ai_provider import build_governance_ai
from iagis.config import Settings
from iagis.ollama_agent import OllamaSuggestionAgent


def test_ollama_provider_is_configurable():
    settings = Settings(GLPI_URL="https://glpi.example", GLPI_APP_TOKEN="a", GLPI_USER_TOKEN="u",
                        AI_PROVIDER="ollama", AI_MODEL="qwen-test", OLLAMA_URL="http://ollama:11434",
                        OLLAMA_TIMEOUT=42)
    agent = build_governance_ai(settings)
    assert isinstance(agent, OllamaSuggestionAgent)
    assert agent.model == "qwen-test" and agent.timeout == 42
