from iagis.report_formatter import format_report

def test_complete_report_and_disclaimer(report):
    text = format_report(report)
    for heading in ("PARECER DE HOMOLOGAÇÃO", "Licenciamento", "Privacidade", "Segurança", "Fontes consultadas"):
        assert heading in text
    assert "não substitui validação do instalador" in text
    assert "hash" in text and "EDR" in text
