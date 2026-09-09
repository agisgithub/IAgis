"""Barreira determinística posterior à recomendação do modelo."""
from __future__ import annotations

from .governance_models import GovernanceReport, Verdict


def apply_rules(report: GovernanceReport) -> GovernanceReport:
    changes: list[str] = []
    target = report.veredito
    deny = {
        "CORPORATE_USE_PROHIBITED": report.uso_corporativo_proibido,
        "CRITICAL_UNMITIGATED_RISK": report.risco_critico_sem_mitigacao,
        "DATA_PURPOSE_INCOMPATIBLE": report.dados_incompativeis_finalidade,
        "MANDATORY_TRAINING_NO_OPTOUT": report.treinamento_obrigatorio_sem_optout,
    }
    inconclusive = {
        "MAINTAINER_UNIDENTIFIED": not report.fabricante_identificado,
        "LICENSE_UNDETERMINED": not report.licenca_determinada,
        "ESSENTIAL_SOURCES_INSUFFICIENT": not report.fontes_essenciais_suficientes,
    }
    fired_deny = [name for name, fired in deny.items() if fired]
    fired_unknown = [name for name, fired in inconclusive.items() if fired]
    if fired_deny:
        target = Verdict.NAO_HOMOLOGADO
        changes.extend(fired_deny)
    elif fired_unknown:
        target = Verdict.INCONCLUSIVO
        changes.extend(fired_unknown)
    elif report.restricoes and target == Verdict.HOMOLOGADO:
        target = Verdict.HOMOLOGADO_COM_RESTRICOES
        changes.append("RESTRICTIONS_PRESENT")
    if target != report.veredito:
        changes.append(f"VERDICT_CHANGED:{report.veredito}->{target}")
    return report.model_copy(update={"veredito": target, "regras_aplicadas": report.regras_aplicadas + changes})
