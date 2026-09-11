"""Contratos e fábrica de provedores de IA, isolados da orquestração GLPI."""
from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from .config import Settings
from .governance_models import GovernanceReport
from .suggestion_models import ResponseSuggestion


@runtime_checkable
class GovernanceAI(Protocol):
    async def analyze(self, context: dict[str, Any]) -> tuple[GovernanceReport | ResponseSuggestion, str | None]: ...


class UnsupportedProvider(ValueError):
    pass


def build_governance_ai(settings: Settings) -> GovernanceAI:
    """Cria o adaptador escolhido sem expor credenciais à camada do worker."""
    if settings.ai_provider == "ollama":
        from .ollama_agent import OllamaSuggestionAgent
        if settings.operation_mode != "suggestion":
            raise UnsupportedProvider("Ollama está habilitado somente no modo suggestion neste piloto")
        return OllamaSuggestionAgent(settings.ollama_url, settings.ai_model, settings.ollama_timeout)
    if settings.ai_provider == "gemini":
        from .gemini_agent import GeminiGovernanceAgent
        return GeminiGovernanceAgent(settings.gemini_api_key.get_secret_value(), settings.ai_model)
    if settings.ai_provider == "openai":
        from .governance_agent import GovernanceAgent
        return GovernanceAgent(settings.openai_api_key.get_secret_value(), settings.ai_model)
    if settings.ai_provider == "anthropic":
        raise UnsupportedProvider(
            f"provedor {settings.ai_provider!r} previsto na configuração, mas o adaptador ainda não foi habilitado"
        )
    raise UnsupportedProvider(f"provedor desconhecido: {settings.ai_provider!r}")
