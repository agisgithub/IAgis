from iagis.ai_provider import build_governance_ai
from iagis.cloud_suggestion_agent import GeminiSuggestionAgent, OpenAISuggestionAgent
from iagis.config import Settings
from iagis.ollama_agent import OllamaSuggestionAgent


def test_ollama_provider_is_configurable():
    settings = Settings(GLPI_URL="https://glpi.example", GLPI_APP_TOKEN="a", GLPI_USER_TOKEN="u",
                        AI_PROVIDER="ollama", AI_MODEL="qwen-test", OLLAMA_URL="http://ollama:11434",
                        OLLAMA_TIMEOUT=42)
    agent = build_governance_ai(settings)
    assert isinstance(agent, OllamaSuggestionAgent)
    assert agent.model == "qwen-test" and agent.timeout == 42

def test_cloud_providers_use_suggestion_adapters_in_suggestion_mode():
    common = dict(GLPI_URL="https://glpi.example", GLPI_APP_TOKEN="a", GLPI_USER_TOKEN="u",
                  AI_MODEL="test-model", IAGIS_MODE="suggestion")
    gemini = build_governance_ai(Settings(
        **common, AI_PROVIDER="gemini", GEMINI_API_KEY="g"
    ))
    openai = build_governance_ai(Settings(
        **common, AI_PROVIDER="openai", OPENAI_API_KEY="o"
    ))
    assert isinstance(gemini, GeminiSuggestionAgent)
    assert isinstance(openai, OpenAISuggestionAgent)
