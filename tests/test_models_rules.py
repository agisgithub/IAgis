import pytest
from pydantic import ValidationError
from iagis.governance_models import GovernanceReport, Verdict
from iagis.governance_rules import apply_rules

def test_model_rejects_bad_confidence(report):
    with pytest.raises(ValidationError):
        report.model_copy(update={"confianca": 2}).model_dump_json() if False else GovernanceReport(**{**report.model_dump(), "confianca": 2})

def test_rules_reject_homologation_without_sources(report):
    candidate = GovernanceReport(**{**report.model_dump(), "fontes_essenciais_suficientes": False})
    assert apply_rules(candidate).veredito == Verdict.INCONCLUSIVO

def test_critical_rule_denies(report):
    result = apply_rules(report.model_copy(update={"risco_critico_sem_mitigacao": True}))
    assert result.veredito == Verdict.NAO_HOMOLOGADO
    assert "CRITICAL_UNMITIGATED_RISK" in result.regras_aplicadas

def test_unknown_license_is_inconclusive(report):
    # Construct as initially inconclusive so model-level guard and deterministic guard agree.
    changed = report.model_copy(update={"licenca_determinada": False, "veredito": Verdict.INCONCLUSIVO})
    result = apply_rules(changed)
    assert result.veredito == Verdict.INCONCLUSIVO
    assert "LICENSE_UNDETERMINED" in result.regras_aplicadas

def test_restrictions_upgrade(report):
    result = apply_rules(report.model_copy(update={"restricoes": ["sem nuvem"]}))
    assert result.veredito == Verdict.HOMOLOGADO_COM_RESTRICOES
