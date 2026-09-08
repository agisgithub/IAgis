from datetime import date

import pytest

from iagis.governance_models import GovernanceReport, RiskLevel, Source, Verdict


@pytest.fixture
def report():
    return GovernanceReport(
        software="Example 1", fabricante="Example Inc.", site_oficial="https://example.com",
        finalidade="edição", resumo_executivo="Resumo confirmado.", licenciamento="Licença comercial.",
        privacidade="Política analisada.", seguranca="Processo documentado.", governanca="Controles aplicáveis.",
        alternativas=["Alternativa A"], riscos=["Telemetria"], restricoes=[], pendencias=[],
        fontes=[Source(titulo="Official", url="https://example.com/legal", tipo="oficial",
                       data_consulta=date(2026, 1, 1), afirmacoes_suportadas=["licença"])],
        nivel_risco=RiskLevel.BAIXO, veredito=Verdict.HOMOLOGADO, justificativa="Evidências suficientes.",
        proxima_etapa="Validação técnica.", confianca=.9, fabricante_identificado=True,
        licenca_determinada=True, fontes_essenciais_suficientes=True,
    )
