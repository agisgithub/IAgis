"""Contrato estruturado obrigatório da homologação."""
from __future__ import annotations

from datetime import date
from enum import StrEnum

from pydantic import BaseModel, Field, HttpUrl


class Verdict(StrEnum):
    HOMOLOGADO = "HOMOLOGADO"
    HOMOLOGADO_COM_RESTRICOES = "HOMOLOGADO_COM_RESTRICOES"
    NAO_HOMOLOGADO = "NAO_HOMOLOGADO"
    INCONCLUSIVO = "INCONCLUSIVO"


class RiskLevel(StrEnum):
    BAIXO = "BAIXO"
    MEDIO = "MEDIO"
    ALTO = "ALTO"
    CRITICO = "CRITICO"


class Source(BaseModel):
    titulo: str
    url: HttpUrl
    tipo: str
    data_consulta: date
    afirmacoes_suportadas: list[str] = Field(min_length=1)


class GovernanceReport(BaseModel):
    software: str
    fabricante: str
    site_oficial: str
    finalidade: str
    resumo_executivo: str
    licenciamento: str
    privacidade: str
    seguranca: str
    governanca: str
    alternativas: list[str]
    riscos: list[str]
    restricoes: list[str]
    pendencias: list[str]
    fontes: list[Source]
    nivel_risco: RiskLevel
    veredito: Verdict
    justificativa: str
    proxima_etapa: str
    confianca: float = Field(ge=0, le=1)
    # Evidências explícitas consumidas apenas pelo motor determinístico.
    uso_corporativo_proibido: bool = False
    fabricante_identificado: bool = False
    licenca_determinada: bool = False
    risco_critico_sem_mitigacao: bool = False
    dados_incompativeis_finalidade: bool = False
    treinamento_obrigatorio_sem_optout: bool = False
    fontes_essenciais_suficientes: bool = False
    regras_aplicadas: list[str] = Field(default_factory=list)
