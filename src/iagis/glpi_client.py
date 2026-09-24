"""Cliente restrito e resiliente para GLPI REST API V1."""
from __future__ import annotations

import json
from collections.abc import Iterator
from typing import Any, Self

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from .glpi_models import Attachment, Entity, Followup, Ticket


class GLPIError(RuntimeError):
    pass


class GLPIClient:
    """Sessão GLPI. Nenhum método destrutivo ou de encerramento de ticket é exposto."""

    def __init__(self, base_url: str, app_token: str, user_token: str, timeout: tuple[float, float] = (5, 30)):
        if not base_url.startswith("https://"):
            raise ValueError("TLS é obrigatório")
        self.base_url = base_url.rstrip("/") + "/apirest.php"
        self.app_token = app_token
        self.user_token = user_token
        self.timeout = timeout
        self.session_token: str | None = None
        self.http = requests.Session()
        retry = Retry(total=3, connect=3, read=2, status=2, backoff_factor=0.4,
                      status_forcelist=(429, 502, 503, 504), allowed_methods=frozenset({"GET", "POST"}))
        self.http.mount("https://", HTTPAdapter(max_retries=retry))
        self.http.verify = True

    def __enter__(self) -> Self:
        self.open()
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def _headers(self, *, authenticated: bool = True,
                 content_type: str | None = "application/json") -> dict[str, str]:
        headers = {"App-Token": self.app_token}
        if content_type:
            headers["Content-Type"] = content_type
        if authenticated:
            if not self.session_token:
                raise GLPIError("sessão GLPI não iniciada")
            headers["Session-Token"] = self.session_token
        return headers

    def _request(self, method: str, path: str, *, entity_id: int | None = None,
                 content_type: str | None = "application/json", **kwargs: Any) -> requests.Response:
        if entity_id is not None:
            kwargs.setdefault("params", {})["entities_id"] = entity_id
        response = self.http.request(method, f"{self.base_url}/{path.lstrip('/')}",
                                     headers=self._headers(content_type=content_type),
                                     timeout=self.timeout, **kwargs)
        if response.status_code not in (*range(200, 300),):
            message = f"GLPI retornou HTTP {response.status_code} em {method} {path}"
            if response.status_code in (401, 403):
                message += " (acesso negado)"
            raise GLPIError(message)
        return response

    def open(self) -> None:
        response = self.http.get(
            f"{self.base_url}/initSession",
            headers={"App-Token": self.app_token, "Authorization": f"user_token {self.user_token}"},
            timeout=self.timeout,
        )
        if not response.ok:
            raise GLPIError(f"falha ao iniciar sessão GLPI: HTTP {response.status_code}")
        token = response.json().get("session_token")
        if not token:
            raise GLPIError("GLPI não retornou Session-Token")
        self.session_token = token

    def close(self) -> None:
        if self.session_token:
            try:
                self._request("GET", "killSession")
            finally:
                self.session_token = None
                self.http.close()

    def check(self, entity_id: int) -> dict[str, Any]:
        return self._request("GET", "getFullSession", entity_id=entity_id).json()

    def _paginated(self, path: str, entity_id: int, page_size: int = 100) -> Iterator[dict[str, Any]]:
        start = 0
        while True:
            response = self._request("GET", path, entity_id=entity_id,
                                     params={"range": f"{start}-{start + page_size - 1}"})
            payload = response.json()
            rows = payload if isinstance(payload, list) else payload.get("data", [])
            yield from rows
            content_range = response.headers.get("Content-Range", "")
            if response.status_code != 206 or not rows:
                break
            if ("/" in content_range and content_range.rsplit("/", 1)[1].isdigit()
                    and start + len(rows) >= int(content_range.rsplit("/", 1)[1])):
                break
            start += len(rows)

    def list_entities(self, entity_id: int) -> list[Entity]:
        return [Entity(id=x["id"], name=x.get("completename") or x.get("name", ""))
                for x in self._paginated("Entity", entity_id)]

    def get_ticket(self, ticket_id: int, entity_id: int) -> Ticket:
        data = self._request("GET", f"Ticket/{ticket_id}", entity_id=entity_id).json()
        actual_entity = int(data.get("entities_id", entity_id))
        if actual_entity != entity_id:
            raise GLPIError("chamado pertence a outra entidade")
        return Ticket(id=int(data["id"]), entity_id=actual_entity, title=data.get("name", ""),
                      description=data.get("content", ""), requester=str(data.get("requester", "não confirmado")),
                      entity=str(data.get("entity", entity_id)), category=str(data.get("category", "não confirmada")),
                      creator_id=_optional_int(data.get("users_id_recipient")),
                      location_id=_optional_int(data.get("locations_id")), raw=data)

    def list_tickets(self, entity_id: int) -> list[Ticket]:
        """Lista chamados visíveis na entidade; a paginação evita assumir limites do servidor."""
        tickets: list[Ticket] = []
        for data in self._paginated("Ticket", entity_id):
            actual_entity = int(data.get("entities_id", entity_id))
            if actual_entity != entity_id:
                continue
            tickets.append(Ticket(
                id=int(data["id"]), entity_id=actual_entity, title=data.get("name", ""),
                description=data.get("content", ""), requester=str(data.get("requester", "não confirmado")),
                entity=str(data.get("entity", entity_id)), category=str(data.get("category", "não confirmada")),
                creator_id=_optional_int(data.get("users_id_recipient")),
                location_id=_optional_int(data.get("locations_id")), raw=data,
            ))
        return tickets

    def list_modified_ticket_ids(self, entity_id: int, modified_since: str,
                                 page_size: int = 200) -> list[int]:
        """Busca somente chamados alterados desde o cursor usando o Search API do GLPI."""
        start = 0
        ticket_ids: list[int] = []
        while True:
            params = {
                "criteria[0][field]": "19",
                "criteria[0][searchtype]": "morethan",
                "criteria[0][value]": modified_since,
                "forcedisplay[0]": "2",
                "forcedisplay[1]": "19",
                "sort": "19",
                "order": "ASC",
                "range": f"{start}-{start + page_size - 1}",
            }
            response = self._request("GET", "search/Ticket", entity_id=entity_id, params=params)
            payload = response.json()
            rows = payload.get("data", []) if isinstance(payload, dict) else []
            if isinstance(rows, dict):
                rows = list(rows.values())
            for row in rows:
                ticket_id = _optional_int(row.get("2"))
                if ticket_id is not None:
                    ticket_ids.append(ticket_id)
            total = _optional_int(payload.get("totalcount")) if isinstance(payload, dict) else None
            if not rows or response.status_code != 206 or (total is not None and start + len(rows) >= total):
                break
            start += len(rows)
        return list(dict.fromkeys(ticket_ids))

    def get_followups(self, ticket_id: int, entity_id: int) -> list[Followup]:
        rows = self._paginated(f"Ticket/{ticket_id}/ITILFollowup", entity_id)
        return [Followup(id=int(x["id"]), content=x.get("content", ""),
                         author_id=x.get("users_id"), date=x.get("date")) for x in rows]

    def get_attachments(self, ticket_id: int, entity_id: int) -> list[Attachment]:
        rows = self._paginated(f"Ticket/{ticket_id}/Document_Item", entity_id)
        return [Attachment(id=int(x.get("documents_id", x["id"])), name=x.get("name", ""),
                           mime_type=x.get("mime", None), size=x.get("filesize"), metadata=x) for x in rows]

    def create_followup(self, ticket_id: int, entity_id: int, content: str) -> int:
        payload = {"input": {"itemtype": "Ticket", "items_id": ticket_id, "content": content}}
        data = self._request("POST", "ITILFollowup", entity_id=entity_id, json=payload).json()
        return int(data["id"])

    def is_ticket_technician(self, ticket_id: int, entity_id: int, user_id: int) -> bool:
        """Autoriza somente técnico atribuído ou membro de grupo técnico do chamado."""
        ticket_users = self._paginated(f"Ticket/{ticket_id}/Ticket_User", entity_id)
        if any(_optional_int(row.get("type")) == 2 and
               _optional_int(row.get("users_id")) == user_id for row in ticket_users):
            return True
        ticket_groups = list(self._paginated(f"Ticket/{ticket_id}/Group_Ticket", entity_id))
        technical_group_ids = {
            group_id
            for row in ticket_groups
            if _optional_int(row.get("type")) == 2
            if (group_id := _optional_int(row.get("groups_id"))) is not None
        }
        for group_id in technical_group_ids:
            members = self._paginated(f"Group/{group_id}/Group_User", entity_id)
            if any(_optional_int(row.get("users_id")) == user_id for row in members):
                return True
        return False

    def attach_document(self, ticket_id: int, entity_id: int, filename: str,
                        content: bytes, mime_type: str = "application/octet-stream") -> int:
        """Envia um documento ao GLPI e o vincula explicitamente ao chamado."""
        manifest = {"input": {"name": filename, "_filename": [filename]}}
        files = {
            "uploadManifest": (None, json.dumps(manifest), "application/json"),
            "filename[0]": (filename, content, mime_type),
        }
        document = self._request(
            "POST", "Document", entity_id=entity_id, content_type=None, files=files
        ).json()
        document_id = int(document["id"])
        link_payload = {
            "input": {
                "documents_id": document_id,
                "itemtype": "Ticket",
                "items_id": ticket_id,
            }
        }
        self._request("POST", "Document_Item", entity_id=entity_id, json=link_payload)
        return document_id


def _optional_int(value: Any) -> int | None:
    try:
        return int(value) if value not in (None, "") else None
    except (TypeError, ValueError):
        return None
