import pytest
import responses

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

@responses.activate
def test_incremental_ticket_search_paginates_without_listing_every_ticket():
    responses.get(BASE+"/search/Ticket", json={
        "totalcount": 3,
        "data": [{"2": 10, "19": "2026-09-24 10:00:00"},
                 {"2": 11, "19": "2026-09-24 10:01:00"}],
    }, status=206)
    responses.get(BASE+"/search/Ticket", json={
        "totalcount": 3,
        "data": [{"2": 12, "19": "2026-09-24 10:02:00"}],
    }, status=200)
    result = client().list_modified_ticket_ids(2, "2026-09-24 09:59:00", page_size=2)
    assert result == [10, 11, 12]
    assert len(responses.calls) == 2
    assert "criteria%5B0%5D%5Bfield%5D=19" in responses.calls[0].request.url
    assert "range=0-1" in responses.calls[0].request.url

@responses.activate
def test_technician_authorization_accepts_direct_assignment_and_group_membership():
    responses.get(BASE+"/Ticket/4/Ticket_User", json=[{"users_id": 7, "type": 2}])
    assert client().is_ticket_technician(4, 2, 7)

    responses.get(BASE+"/Ticket/5/Ticket_User", json=[])
    responses.get(BASE+"/Ticket/5/Group_Ticket", json=[{"groups_id": 9, "type": 2}])
    responses.get(BASE+"/Group/9/Group_User", json=[{"users_id": 8}])
    assert client().is_ticket_technician(5, 2, 8)

@responses.activate
def test_attach_document_uses_multipart_then_links_ticket():
    responses.post(BASE+"/Document", json={"id": 41}, status=201)
    responses.post(BASE+"/Document_Item", json={"id": 42}, status=201)
    document_id = client().attach_document(
        4, 2, "cliente.ovpn", b"client\n<key>secret</key>\n",
        "application/x-openvpn-profile",
    )
    assert document_id == 41
    assert responses.calls[0].request.headers["Content-Type"].startswith("multipart/form-data;")
    assert b"cliente.ovpn" in responses.calls[0].request.body
    assert responses.calls[1].request.body == b'{"input": {"documents_id": 41, "itemtype": "Ticket", "items_id": 4}}'
