import importlib.util
import sys
from pathlib import Path

import pytest
import responses

from iagis.access_client import AccessBrokerClient, AccessBrokerError
from iagis.access_models import AccessAction, AccessActionPlan, AccessProvider


def _load_broker():
    path = Path(__file__).parents[1] / "access-broker" / "server.py"
    spec = importlib.util.spec_from_file_location("access_broker_server", path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_access_plan_normalizes_email_and_requires_complete_target():
    plan = AccessActionPlan(
        action=AccessAction.GRANT,
        provider=AccessProvider.CLAUDE,
        user_emails=[" Pessoa@Empresa.COM ", "pessoa@empresa.com"],
        confidence=.99,
        needs_clarification=False,
        clarification_question=None,
        rationale="pedido explícito",
    )
    assert plan.user_emails == ["pessoa@empresa.com"]
    missing = AccessActionPlan(
        action=AccessAction.REVOKE,
        provider=None,
        user_emails=[],
        confidence=.99,
        needs_clarification=False,
        clarification_question=None,
        rationale="incompleto",
    )
    assert missing.needs_clarification is True
    assert missing.clarification_question


@responses.activate
def test_access_broker_client_validates_response_and_hides_token():
    responses.post(
        "http://127.0.0.1:8092/v1/access",
        json={
            "email": "maria@empresa.com",
            "assigned": True,
            "changed": True,
            "status": "PENDING_SYNC",
            "detail": "aguardando SCIM",
        },
        status=200,
    )
    client = AccessBrokerClient("http://127.0.0.1:8092", "secret-token")
    result = client.apply(AccessProvider.CLAUDE, AccessAction.GRANT, "maria@empresa.com")
    assert result.assigned is True and result.changed is True
    assert responses.calls[0].request.headers["Authorization"] == "Bearer secret-token"

    responses.reset()
    responses.post(
        "http://127.0.0.1:8092/v1/access",
        json={"error": "domínio de e-mail não autorizado"},
        status=403,
    )
    with pytest.raises(AccessBrokerError, match="domínio") as error:
        client.apply(AccessProvider.CHATGPT, AccessAction.REVOKE, "x@fora.example")
    assert error.value.retryable is False and error.value.status_code == 403


def test_access_broker_group_operations_are_allowlisted_and_idempotent(monkeypatch):
    broker = _load_broker()
    target = broker.Target(
        mode="group",
        group_ids=(
            "11111111-1111-1111-1111-111111111111",
            "22222222-2222-2222-2222-222222222222",
        ),
    )
    config = broker.Config(
        host="127.0.0.1",
        port=8092,
        broker_token="x" * 32,
        tenant_id="aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
        client_id="bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb",
        client_secret="secret",
        allowed_domains=frozenset({"empresa.com"}),
        targets={"claude": target},
    )
    graph = broker.GraphClient(config)
    user_id = "cccccccc-cccc-cccc-cccc-cccccccccccc"
    monkeypatch.setattr(graph, "resolve_user", lambda email: {
        "id": user_id, "accountEnabled": True
    })
    membership = {target.group_ids[0]: True, target.group_ids[1]: False}
    monkeypatch.setattr(graph, "_is_group_member", lambda group_id, _: membership[group_id])
    calls = []
    monkeypatch.setattr(
        graph, "_request",
        lambda method, path, body=None, expected=(200,): calls.append((method, path, body)) or {},
    )

    result = graph.apply("claude", "grant", "Pessoa@Empresa.com")
    assert result["assigned"] is True and result["changed"] is True
    assert len(calls) == 1 and target.group_ids[1] in calls[0][1]
    with pytest.raises(broker.BrokerError, match="domínio"):
        graph.apply("claude", "grant", "pessoa@externo.example")
    with pytest.raises(broker.BrokerError, match="não configurado"):
        graph.apply("chatgpt", "grant", "pessoa@empresa.com")
