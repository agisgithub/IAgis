from iagis.report_formatter import format_report, format_suggestion_for_publication
from iagis.suggestion_models import ResponseSuggestion

def test_complete_report_and_disclaimer(report):
    text = format_report(report)
    for heading in ("PARECER DE HOMOLOGAÇÃO", "Licenciamento", "Privacidade", "Segurança", "Fontes consultadas"):
        assert heading in text
    assert "não substitui validação do instalador" in text
    assert "hash" in text and "EDR" in text

def test_production_suggestion_has_no_trigger_or_governance_language():
    suggestion=ResponseSuggestion(resumo_pedido="Pedido",sugestao_resposta="Podemos ajudar.",
        informacoes_faltantes=[],limitacoes=[],confianca=.8)
    text=format_suggestion_for_publication(suggestion)
    assert "Resposta IAgis" in text
    assert "@iagis" not in text.lower()
    assert "homologação" not in text.lower()
