"""Saída estruturada do modo simples de atendimento."""
from pydantic import BaseModel, Field


class ResponseSuggestion(BaseModel):
    resumo_pedido: str = Field(min_length=1)
    sugestao_resposta: str = Field(min_length=1)
    informacoes_faltantes: list[str] = Field(default_factory=list)
    limitacoes: list[str] = Field(default_factory=list)
    confianca: float = Field(ge=0, le=1)
