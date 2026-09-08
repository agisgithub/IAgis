import responses
import pytest
from iagis.glpi_client import GLPIClient, GLPIError

BASE="https://glpi.example/apirest.php"

def client():
    c=GLPIClient("https://glpi.example", "app", "user")
    c.session_token="session"
    return c

@responses.activate
def test_get_ticket_and_explicit_entity():
    responses.get(BASE+"/Ticket/4", json={"id":4,"entities_id":2,"name":"T"}, status=200)
    result=client().get_ticket(4,2)
    assert result.id == 4
    assert responses.calls[0].request.params["entities_id"] == "2"

@responses.activate
def test_wrong_entity_rejected():
    responses.get(BASE+"/Ticket/4", json={"id":4,"entities_id":3}, status=200)
    with pytest.raises(GLPIError): client().get_ticket(4,2)

@responses.activate
def test_http_failure_sanitized():
    responses.get(BASE+"/Ticket/4", json={"password":"leak"}, status=400)
    with pytest.raises(GLPIError) as error: client().get_ticket(4,2)
    assert "leak" not in str(error.value)

@responses.activate
def test_pagination_206():
    responses.get(BASE+"/Entity", json=[{"id":1,"name":"A"}], status=206,
                  headers={"Content-Range":"0-0/2"})
    responses.get(BASE+"/Entity", json=[{"id":2,"name":"B"}], status=200)
    assert [x.id for x in client().list_entities(0)] == [1,2]

@responses.activate
def test_session_always_closed():
    responses.get(BASE+"/initSession", json={"session_token":"s"})
    responses.get(BASE+"/killSession", json=True)
    with GLPIClient("https://glpi.example", "a", "u"):
        pass
    assert responses.calls[-1].request.url.startswith(BASE+"/killSession")
