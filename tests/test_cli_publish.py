from contextlib import contextmanager
from typer.testing import CliRunner
from iagis import cli
from iagis.config import Settings
from iagis.repository import Repository
from iagis.suggestion_models import ResponseSuggestion

class Client:
    def __init__(self): self.calls=[]
    def create_followup(self,*args): self.calls.append(args); return 77


def test_cli_publish_confirm_in_suggestion_mode(monkeypatch,tmp_path):
    settings=Settings(GLPI_URL="https://glpi.example",GLPI_APP_TOKEN="a",GLPI_USER_TOKEN="u",
        AI_PROVIDER="ollama",AI_MODEL="qwen",IAGIS_MODE="suggestion",IAGIS_DRY_RUN=False,
        IAGIS_GLPI_USER_ID=9,IAGIS_DATABASE_PATH=str(tmp_path/"db"),
        IAGIS_ALLOWED_ENTITY_IDS="2",IAGIS_ENTITY_ID=2)
    repo=Repository(tmp_path/"db")
    aid=repo.claim_event(1,2,None,"h","2:1:description:h",3)
    repo.save_result(aid,ResponseSuggestion(resumo_pedido="Pedido",sugestao_resposta="Resposta",
        informacoes_faltantes=[],limitacoes=[],confianca=.8))
    client=Client()
    @contextmanager
    def fake_client(_): yield client
    monkeypatch.setattr(cli,"_settings",lambda:settings)
    monkeypatch.setattr(cli,"_client",fake_client)
    result=CliRunner().invoke(cli.app,["publish","--analysis-id",str(aid),"--confirm"])
    assert result.exit_code == 0
    assert "Acompanhamento publicado: 77" in result.output
    assert len(client.calls) == 1
