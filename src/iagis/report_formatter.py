"""Formatação textual segura para acompanhamento GLPI."""
from __future__ import annotations

from datetime import date, datetime

from .access_models import AccessExecutionReport
from .governance_models import GovernanceReport
from .suggestion_models import ResponseSuggestion
from .vpn_models import VPNExecutionReport


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
{analysis_date or datetime.now().astimezone().date()}

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


def format_suggestion(suggestion: ResponseSuggestion) -> str:
    """Prévia simples; deliberadamente não usa linguagem de homologação formal."""
    if not isinstance(suggestion, ResponseSuggestion):
        raise TypeError("sugestão inválida")
    return f"""SUGESTÃO DE RESPOSTA — REVISÃO HUMANA OBRIGATÓRIA

Resumo do pedido
{suggestion.resumo_pedido}

Sugestão de resposta ao usuário
{suggestion.sugestao_resposta}

Informações faltantes
{_items(suggestion.informacoes_faltantes)}

Limitações
{_items(suggestion.limitacoes)}

Confiança declarada pelo modelo
{suggestion.confianca:.0%}

Esta sugestão não foi publicada no GLPI, não representa aprovação ou homologação e não confirma
qualquer ação. Anexos não foram baixados nem executados.
"""


def format_suggestion_for_publication(suggestion: ResponseSuggestion) -> str:
    """Resposta operacional sem menção-gatilho e sem linguagem de homologação."""
    if not isinstance(suggestion, ResponseSuggestion):
        raise TypeError("sugestão inválida")
    missing = "\n".join(f"- {item}" for item in suggestion.informacoes_faltantes)
    limitations = "\n".join(f"- {item}" for item in suggestion.limitacoes)
    sections = ["Resposta IAgis", "", suggestion.sugestao_resposta]
    if missing:
        sections.extend(["", "Para prosseguirmos, precisamos destas informações:", missing])
    if limitations:
        sections.extend(["", "Limitações desta resposta:", limitations])
    sections.extend(["", "Resposta gerada por IA para apoio ao atendimento; valide informações críticas."])
    return "\n".join(sections)


def format_vpn_report(report: VPNExecutionReport) -> str:
    sections = ["Resposta IAgis — OpenVPN", "", report.message]
    if report.client_name:
        sections.extend(["", f"Identificador do perfil: {report.client_name}"])
    if report.attachment_name:
        sections.extend(["", f"Arquivo anexado ao chamado: {report.attachment_name}"])
    if report.details:
        sections.extend(["", "Detalhes:", _items(report.details)])
    sections.extend([
        "",
        "A autorização foi validada contra os técnicos atribuídos ao chamado antes da operação.",
    ])
    return "\n".join(sections)


def format_access_report(report: AccessExecutionReport) -> str:
    product = report.provider.value.capitalize() if report.provider else "não confirmado"
    sections = [f"Resposta IAgis — Acesso {product}", "", report.message]
    if report.results:
        rows = []
        for result in report.results:
            state = "atribuído" if result.assigned else "não atribuído"
            change = "alterado" if result.changed else "sem alteração"
            rows.append(f"{result.email}: {state}; {change}; {result.detail}")
        sections.extend(["", "Usuários:", _items(rows)])
    elif report.user_emails:
        sections.extend(["", "Usuários:", _items(report.user_emails)])
    if report.details:
        sections.extend(["", "Controles aplicados:", _items(report.details)])
    sections.extend([
        "",
        (
            "A identidade do solicitante foi validada contra os técnicos atribuídos ao chamado. "
            "Quando houver SCIM, confirme a conclusão no serviço após a sincronização do diretório."
        ),
    ])
    return "\n".join(sections)
