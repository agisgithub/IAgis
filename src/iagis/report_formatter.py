"""Formatação textual segura para acompanhamento GLPI."""
from __future__ import annotations

from datetime import date

from .governance_models import GovernanceReport


def _items(values: list[str]) -> str:
    return "\n".join(f"- {item}" for item in values) if values else "- Nenhum item confirmado."


def format_report(report: GovernanceReport, analysis_date: date | None = None) -> str:
    sources = "\n".join(
        f"- {s.titulo} ({s.tipo}) — {s.url} — consulta: {s.data_consulta}; suporta: "
        + ", ".join(s.afirmacoes_suportadas) for s in report.fontes
    ) or "- Nenhuma fonte confirmada."
    rules = _items(report.regras_aplicadas)
    return f"""PARECER DE HOMOLOGAÇÃO DE GOVERNANÇA

Software
{report.software}

Fabricante
{report.fabricante}

Finalidade
{report.finalidade}

Data
{analysis_date or date.today()}

Veredito
{report.veredito.value}

Nível de risco
{report.nivel_risco.value}

Resumo executivo
{report.resumo_executivo}

Licenciamento
{report.licenciamento}

Privacidade
{report.privacidade}

Segurança
{report.seguranca}

Governança
{report.governanca}

Riscos
{_items(report.riscos)}

Restrições
{_items(report.restricoes)}

Pendências
{_items(report.pendencias)}

Alternativas
{_items(report.alternativas)}

Justificativa
{report.justificativa}

Próxima etapa
{report.proxima_etapa}

Regras determinísticas aplicadas
{rules}

Fontes consultadas
{sources}

ESCOPO E LIMITAÇÕES
Esta homologação documental e de governança não substitui validação do instalador, análise de hash
e assinatura, teste técnico, validação do EDR ou aprovação final para implantação. Nenhum instalador
foi baixado, instalado ou executado por esta análise.
"""
