from datetime import datetime

import pytest

from iagis.access_client import AccessBrokerError
from iagis.access_models import (
    AccessAction,
    AccessActionPlan,
    AccessExecutionReport,
    AccessProvider,
    AccessUserResult,
)
from iagis.config import Settings
from iagis.glpi_models import Followup, Ticket
from iagis.ollama_agent import OllamaConfigurationError, OllamaError
from iagis.repository import Repository
from iagis.suggestion_models import ResponseSuggestion
from iagis.vpn_models import VPNAction, VPNActionPlan, VPNExecutionReport
from iagis.worker import PublicationDenied, Worker

BASE={"GLPI_URL":"https://glpi.example", "GLPI_APP_TOKEN":"a", "GLPI_USER_TOKEN":"u",
      "AI_PROVIDER":"ollama", "AI_MODEL":"qwen-test", "IAGIS_MODE":"suggestion"}
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
            "IAGIS_ALLOWED_ENTITY_IDS":"2","IAGIS_ENTITY_ID":"2","IAGIS_GLPI_USER_ID":"9",**overrides}
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

@pytest.mark.asyncio
async def test_dry_run_does_not_publish(tmp_path):
    client=Client(); repo=Repository(tmp_path/"db")
    await Worker(settings(tmp_path),client,repo,Agent()).analyze_ticket(1,2)
    assert client.created == [] and repo.get(1)["state"] == "SUGGESTED"

@pytest.mark.asyncio
async def test_production_publishes_once_and_does_not_retrigger(tmp_path):
    client=Client(); repo=Repository(tmp_path/"db")
    worker=Worker(settings(tmp_path,IAGIS_DRY_RUN=False),client,repo,Agent())
    assert len(await worker.analyze_ticket(1,2)) == 1
    assert len(client.created) == 1 and repo.get(1)["state"] == "PUBLISHED"
    assert "@IAgis" not in client.created[0][2] and "@iagis" not in client.created[0][2].lower()
    assert await worker.analyze_ticket(1,2) == []
    assert len(client.created) == 1

@pytest.mark.asyncio
async def test_publication_failure_is_recorded(tmp_path):
    class FailingClient(Client):
        def create_followup(self,*args):
            raise RuntimeError("GLPI unavailable")
    client=FailingClient(); repo=Repository(tmp_path/"db")
    worker=Worker(settings(tmp_path,IAGIS_DRY_RUN=False),client,repo,Agent())
    assert await worker.analyze_ticket(1,2) == []
    assert repo.get(1)["state"] == "FAILED"
    assert "PublicationError" in repo.get(1)["error"]

def test_manual_publish_requires_confirm_and_works_in_suggestion_mode(tmp_path):
    client=Client(); repo=Repository(tmp_path/"db")
    aid=repo.claim_event(1,2,None,"h","2:1:description:h",3)
    repo.save_result(aid,SUGGESTION,"run")
    worker=Worker(settings(tmp_path,IAGIS_DRY_RUN=False),client,repo,Agent())
    with pytest.raises(PublicationDenied,match="confirm"): worker.publish(aid,confirm=False)
    assert worker.publish(aid,confirm=True) == 99
    with pytest.raises(PublicationDenied,match="já publicada"): worker.publish(aid,confirm=True)
    assert len(client.created) == 1

class ActionPlanner:
    def __init__(self, plan): self.result = plan; self.contexts=[]
    async def plan(self, context):
        self.contexts.append(context)
        return self.result, "planner-run"

class Broker:
    def __init__(self): self.created=[]; self.revoked=[]
    def create(self, name):
        self.created.append(name)
        return {"name": name, "created": True}, b"client\n<key>private</key>\n"
    def revoke(self, name): self.revoked.append(name); return {"revoked": True}
    def status(self, name): return {"name": name, "status": "active"}
    def list_clients(self): return [{"name":"joao", "status":"active"}]

class VPNClient(Client):
    def __init__(self, authorized=True):
        super().__init__(description="sem", followups=[
            Followup(id=12, content="@iagis crie uma VPN para João da Silva", author_id=7,
                     date=datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S"))
        ])
        self.authorized=authorized; self.attachments=[]
    def is_ticket_technician(self, *args): return self.authorized
    def attach_document(self, ticket_id, entity_id, filename, content, mime):
        self.attachments.append((ticket_id,entity_id,filename,content,mime)); return 55

@pytest.mark.asyncio
async def test_authorized_vpn_create_attaches_profile_and_is_idempotent(tmp_path):
    client=VPNClient(); broker=Broker(); repo=Repository(tmp_path/"db")
    planner=ActionPlanner(VPNActionPlan(
        action=VPNAction.CREATE,client_name="João da Silva",confidence=.99,
        needs_clarification=False,clarification_question=None,rationale="pedido explícito",
    ))
    worker=Worker(settings(tmp_path,IAGIS_DRY_RUN=False,IAGIS_VPN_ENABLED=True,
                           IAGIS_VPN_BROKER_TOKEN="x"*32),
                  client,repo,Agent(),planner,broker)
    results=await worker.analyze_ticket(1,2)
    assert isinstance(results[0][1],VPNExecutionReport)
    assert broker.created == ["joao-da-silva"]
    assert client.attachments[0][2] == "joao-da-silva.ovpn"
    assert len(client.created) == 1
    assert await worker.analyze_ticket(1,2) == []

@pytest.mark.asyncio
async def test_unauthorized_vpn_request_is_denied_without_broker_call(tmp_path):
    client=VPNClient(authorized=False); broker=Broker()
    planner=ActionPlanner(VPNActionPlan(
        action=VPNAction.REVOKE,client_name="joao",confidence=.99,
        needs_clarification=False,clarification_question=None,rationale="pedido explícito",
    ))
    worker=Worker(settings(tmp_path,IAGIS_DRY_RUN=False,IAGIS_VPN_ENABLED=True,
                           IAGIS_VPN_BROKER_TOKEN="x"*32),
                  client,Repository(tmp_path/"db"),Agent(),planner,broker)
    results=await worker.analyze_ticket(1,2)
    assert results[0][1].status == "DENIED"
    assert broker.revoked == []
    assert "não foi executada" in client.created[0][2]

@pytest.mark.asyncio
async def test_vpn_retry_does_not_issue_or_attach_twice_after_publication_failure(tmp_path):
    class FlakyClient(VPNClient):
        def __init__(self): super().__init__(); self.fail_once=True
        def create_followup(self,*args):
            if self.fail_once:
                self.fail_once=False
                raise RuntimeError("temporary")
            return super().create_followup(*args)
    client=FlakyClient(); broker=Broker(); repo=Repository(tmp_path/"db")
    planner=ActionPlanner(VPNActionPlan(
        action=VPNAction.CREATE,client_name="joao",confidence=.99,
        needs_clarification=False,clarification_question=None,rationale="pedido explícito",
    ))
    worker=Worker(settings(tmp_path,IAGIS_DRY_RUN=False,IAGIS_VPN_ENABLED=True,
                           IAGIS_VPN_BROKER_TOKEN="x"*32),
                  client,repo,Agent(),planner,broker)
    assert await worker.analyze_ticket(1,2) == []
    assert repo.get(1)["state"] == "FAILED"
    assert len(broker.created) == 1 and len(client.attachments) == 1
    assert len(await worker.retry_analysis(1)) == 1
    assert len(broker.created) == 1 and len(client.attachments) == 1
    assert repo.get(1)["state"] == "PUBLISHED"

@pytest.mark.asyncio
async def test_stale_vpn_request_never_touches_broker(tmp_path):
    client=VPNClient(); client.followups[0].date="2020-01-01 10:00:00"
    broker=Broker(); planner=ActionPlanner(VPNActionPlan(
        action=VPNAction.CREATE,client_name="joao",confidence=.99,
        needs_clarification=False,clarification_question=None,rationale="pedido explícito",
    ))
    worker=Worker(settings(tmp_path,IAGIS_DRY_RUN=False,IAGIS_VPN_ENABLED=True,
                           IAGIS_VPN_BROKER_TOKEN="x"*32),
                  client,Repository(tmp_path/"db"),Agent(),planner,broker)
    results=await worker.analyze_ticket(1,2)
    assert results[0][1].status == "STALE"
    assert broker.created == [] and client.attachments == []

class AccessBroker:
    def __init__(self): self.calls=[]
    def apply(self, provider, action, email):
        self.calls.append((provider, action, email))
        assigned = action != AccessAction.REVOKE
        return AccessUserResult(
            email=email, assigned=assigned, changed=True,
            status="PENDING_SYNC", detail="aguardando SCIM",
        )

class AccessClient(Client):
    def __init__(self, authorized=True, date=None):
        super().__init__(description="sem", followups=[
            Followup(
                id=13,
                content="@iagis libere Claude para maria@empresa.com",
                author_id=7,
                date=date or datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S"),
            )
        ])
        self.authorized=authorized
    def is_ticket_technician(self, *args): return self.authorized

@pytest.mark.asyncio
async def test_authorized_access_grant_is_published_and_idempotent(tmp_path):
    client=AccessClient(); broker=AccessBroker(); repo=Repository(tmp_path/"db")
    planner=ActionPlanner(AccessActionPlan(
        action=AccessAction.GRANT,provider=AccessProvider.CLAUDE,
        user_emails=["maria@empresa.com"],confidence=.99,
        needs_clarification=False,clarification_question=None,rationale="pedido explícito",
    ))
    worker=Worker(
        settings(tmp_path,IAGIS_DRY_RUN=False,IAGIS_ACCESS_ENABLED=True,
                 IAGIS_ACCESS_BROKER_TOKEN="x"*32),
        client,repo,Agent(),access_action_planner=planner,access_client=broker,
    )
    results=await worker.analyze_ticket(1,2)
    assert isinstance(results[0][1],AccessExecutionReport)
    assert results[0][1].status == "PENDING_SYNC"
    assert len(broker.calls) == 1 and len(client.created) == 1
    assert "maria@empresa.com" in client.created[0][2]
    assert await worker.analyze_ticket(1,2) == []

@pytest.mark.asyncio
async def test_access_request_requires_assigned_technician_and_fresh_event(tmp_path):
    plan=AccessActionPlan(
        action=AccessAction.REVOKE,provider=AccessProvider.CHATGPT,
        user_emails=["maria@empresa.com"],confidence=.99,
        needs_clarification=False,clarification_question=None,rationale="pedido explícito",
    )
    broker=AccessBroker()
    denied=await Worker(
        settings(tmp_path,IAGIS_DRY_RUN=False,IAGIS_ACCESS_ENABLED=True,
                 IAGIS_ACCESS_BROKER_TOKEN="x"*32),
        AccessClient(authorized=False),Repository(tmp_path/"denied"),Agent(),
        access_action_planner=ActionPlanner(plan),access_client=broker,
    ).analyze_ticket(1,2)
    assert denied[0][1].status == "DENIED" and broker.calls == []

    stale=await Worker(
        settings(tmp_path,IAGIS_DRY_RUN=False,IAGIS_ACCESS_ENABLED=True,
                 IAGIS_ACCESS_BROKER_TOKEN="x"*32),
        AccessClient(date="2020-01-01 10:00:00"),Repository(tmp_path/"stale"),Agent(),
        access_action_planner=ActionPlanner(plan),access_client=broker,
    ).analyze_ticket(1,2)
    assert stale[0][1].status == "STALE" and broker.calls == []

@pytest.mark.asyncio
async def test_access_retry_does_not_apply_twice_after_publication_failure(tmp_path):
    class FlakyAccessClient(AccessClient):
        def __init__(self): super().__init__(); self.fail_once=True
        def create_followup(self,*args):
            if self.fail_once:
                self.fail_once=False
                raise RuntimeError("temporary")
            return super().create_followup(*args)
    client=FlakyAccessClient(); broker=AccessBroker(); repo=Repository(tmp_path/"db")
    plan=AccessActionPlan(
        action=AccessAction.GRANT,provider=AccessProvider.CLAUDE,
        user_emails=["maria@empresa.com"],confidence=.99,
        needs_clarification=False,clarification_question=None,rationale="pedido explícito",
    )
    worker=Worker(
        settings(tmp_path,IAGIS_DRY_RUN=False,IAGIS_ACCESS_ENABLED=True,
                 IAGIS_ACCESS_BROKER_TOKEN="x"*32),
        client,repo,Agent(),access_action_planner=ActionPlanner(plan),access_client=broker,
    )
    assert await worker.analyze_ticket(1,2) == []
    assert len(broker.calls) == 1
    assert len(await worker.retry_analysis(1)) == 1
    assert len(broker.calls) == 1 and repo.get(1)["state"] == "PUBLISHED"

@pytest.mark.asyncio
async def test_access_business_rejection_is_reported_without_retry_loop(tmp_path):
    class UnconfiguredBroker:
        def apply(self, *args):
            raise AccessBrokerError(
                "provedor chatgpt não configurado", status_code=409
            )
    client=AccessClient(); repo=Repository(tmp_path/"db")
    plan=AccessActionPlan(
        action=AccessAction.GRANT,provider=AccessProvider.CHATGPT,
        user_emails=["maria@empresa.com"],confidence=.99,
        needs_clarification=False,clarification_question=None,rationale="pedido explícito",
    )
    result=await Worker(
        settings(tmp_path,IAGIS_DRY_RUN=False,IAGIS_ACCESS_ENABLED=True,
                 IAGIS_ACCESS_BROKER_TOKEN="x"*32),
        client,repo,Agent(),access_action_planner=ActionPlanner(plan),
        access_client=UnconfiguredBroker(),
    ).analyze_ticket(1,2)
    assert result[0][1].status == "REJECTED"
    assert repo.get(1)["state"] == "PUBLISHED" and len(client.created) == 1
