import pytest
from iagis.config import Settings
from iagis.glpi_models import Ticket, Followup
from iagis.ollama_agent import OllamaConfigurationError, OllamaError
from iagis.repository import Repository
from iagis.suggestion_models import ResponseSuggestion
from iagis.worker import PublicationDenied, Worker

BASE=dict(GLPI_URL="https://glpi.example", GLPI_APP_TOKEN="a", GLPI_USER_TOKEN="u",
          AI_PROVIDER="ollama", AI_MODEL="qwen-test", IAGIS_MODE="suggestion")
SUGGESTION=ResponseSuggestion(resumo_pedido="Pedido",sugestao_resposta="Favor informar versão.",
                              informacoes_faltantes=["Versão"],limitacoes=["Sem aprovação"],confianca=.7)

class Client:
    def __init__(self,ticket_id=1,description="@IAgis avalie",followups=None):
        self.ticket_id=ticket_id; self.description=description; self.followups=followups or []; self.created=[]
    def get_ticket(self,ticket_id,entity_id):
        return Ticket(id=ticket_id,entity_id=entity_id,title="X",description=self.description,requester="R")
    def get_followups(self,*args): return self.followups
    def get_attachments(self,*args): return []
    def create_followup(self,*args): self.created.append(args); return 99

class Agent:
    def __init__(self,result=SUGGESTION,error=None): self.result=result; self.error=error; self.contexts=[]
    async def analyze(self,context):
        self.contexts.append(context)
        if self.error: raise self.error
        return self.result,"run"

def settings(tmp_path,**overrides):
    values={**BASE,"IAGIS_DATABASE_PATH":str(tmp_path/"db"),"IAGIS_DRY_RUN":True,
            "IAGIS_ALLOWED_ENTITY_IDS":"2","IAGIS_GLPI_USER_ID":"9",**overrides}
    return Settings(**values)

@pytest.mark.asyncio
async def test_capitalizations_and_repeated_event(tmp_path):
    client=Client(description="@iAgIs avalie"); agent=Agent(); repo=Repository(tmp_path/"db")
    worker=Worker(settings(tmp_path),client,repo,agent)
    assert len(await worker.analyze_ticket(1,2))==1
    assert await worker.analyze_ticket(1,2)==[]
    assert len(agent.contexts)==1

@pytest.mark.asyncio
async def test_equal_text_in_distinct_followups_and_explicit_order(tmp_path):
    items=[Followup(id=20,content="@iagis igual",date="2026-01-02"),
           Followup(id=10,content="@iagis igual",date="2026-01-01")]
    client=Client(description="sem menção",followups=items); agent=Agent()
    results=await Worker(settings(tmp_path),client,Repository(tmp_path/"db"),agent).analyze_ticket(1,2)
    assert len(results)==2
    rows=Repository(tmp_path/"db").recent()
    assert [row["id"] for row in reversed(rows)] == [1,2]

@pytest.mark.asyncio
async def test_equal_text_in_different_tickets(tmp_path):
    repo=Repository(tmp_path/"db"); agent=Agent(); worker=Worker(settings(tmp_path),Client(),repo,agent)
    assert await worker.analyze_ticket(1,2)
    assert await worker.analyze_ticket(2,2)

@pytest.mark.asyncio
async def test_description_edit_is_new_version(tmp_path):
    repo=Repository(tmp_path/"db"); client=Client(); worker=Worker(settings(tmp_path),client,repo,Agent())
    assert await worker.analyze_ticket(1,2)
    client.description="@iagis conteúdo editado"
    assert await worker.analyze_ticket(1,2)

@pytest.mark.asyncio
async def test_own_followup_ignored(tmp_path):
    client=Client(description="sem",followups=[Followup(id=1,content="@iagis",author_id=9)])
    assert await Worker(settings(tmp_path),client,Repository(tmp_path/"db"),Agent()).analyze_ticket(1,2)==[]

@pytest.mark.asyncio
async def test_transient_failure_can_retry_but_configuration_cannot(tmp_path):
    repo=Repository(tmp_path/"db"); client=Client(); failing=Agent(error=OllamaError("timeout"))
    worker=Worker(settings(tmp_path),client,repo,failing)
    assert await worker.analyze_ticket(1,2)==[]
    assert repo.get(1)["retryable"] == 1
    failing.error=None
    assert len(await worker.retry_analysis(1))==1

    repo2=Repository(tmp_path/"db2")
    worker2=Worker(settings(tmp_path,IAGIS_DATABASE_PATH=str(tmp_path/"db2")),client,repo2,
                   Agent(error=OllamaConfigurationError("modelo ausente")))
    assert await worker2.analyze_ticket(1,2)==[]
    assert repo2.get(1)["retryable"] == 0

@pytest.mark.asyncio
async def test_entity_isolation(tmp_path):
    worker=Worker(settings(tmp_path),Client(),Repository(tmp_path/"db"),Agent())
    with pytest.raises(PermissionError): await worker.analyze_ticket(1,8)

def test_publication_always_blocked_in_pilot(tmp_path):
    client=Client(); worker=Worker(settings(tmp_path),client,Repository(tmp_path/"db"),Agent())
    with pytest.raises(PublicationDenied,match="nunca publica"): worker.publish(1,confirm=True)
    assert client.created == []
