"""Broker mínimo: limita a gestão SaaS a grupos ou atribuições Entra predefinidos."""
from __future__ import annotations

import hmac
import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

GRAPH_BASE = "https://graph.microsoft.com/v1.0"
EMAIL_RE = re.compile(r"[a-z0-9.!#$%&'*+/=?^_`{|}~-]+@[a-z0-9-]+(?:\.[a-z0-9-]+)+")
GUID_RE = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")
DEFAULT_APP_ROLE = "00000000-0000-0000-0000-000000000000"


class BrokerError(RuntimeError):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


class GraphError(RuntimeError):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status


@dataclass(frozen=True)
class Target:
    mode: str
    group_ids: tuple[str, ...] = ()
    service_principal_id: str | None = None
    app_role_id: str = DEFAULT_APP_ROLE


@dataclass(frozen=True)
class Config:
    host: str
    port: int
    broker_token: str
    tenant_id: str
    client_id: str
    client_secret: str
    allowed_domains: frozenset[str]
    targets: dict[str, Target]

    @classmethod
    def from_env(cls) -> Config:
        host = os.getenv("IAGIS_ACCESS_BROKER_HOST", "127.0.0.1")
        if host not in {"127.0.0.1", "::1", "localhost"}:
            raise RuntimeError("o broker de acesso só pode escutar no loopback")
        broker_token = os.getenv("IAGIS_ACCESS_BROKER_TOKEN", "")
        if len(broker_token) < 32:
            raise RuntimeError("IAGIS_ACCESS_BROKER_TOKEN deve possuir ao menos 32 caracteres")
        tenant_id = _required("AZURE_TENANT_ID")
        client_id = _required("AZURE_CLIENT_ID")
        client_secret = _required("AZURE_CLIENT_SECRET")
        if not GUID_RE.fullmatch(tenant_id) or not GUID_RE.fullmatch(client_id):
            raise RuntimeError("AZURE_TENANT_ID e AZURE_CLIENT_ID devem ser UUIDs")
        domains = frozenset(
            item.strip().casefold()
            for item in _required("IAGIS_ACCESS_ALLOWED_EMAIL_DOMAINS").split(",")
            if item.strip()
        )
        targets: dict[str, Target] = {}
        for provider in ("CLAUDE", "CHATGPT"):
            mode = os.getenv(f"IAGIS_ACCESS_{provider}_MODE", "").strip().casefold()
            if not mode:
                continue
            if mode == "group":
                group_ids = tuple(
                    item.strip() for item in os.getenv(
                        f"IAGIS_ACCESS_{provider}_GROUP_IDS", ""
                    ).split(",") if item.strip()
                )
                if not group_ids or any(not GUID_RE.fullmatch(item) for item in group_ids):
                    raise RuntimeError(f"IAGIS_ACCESS_{provider}_GROUP_IDS inválido")
                targets[provider.casefold()] = Target(mode=mode, group_ids=group_ids)
            elif mode == "app_role":
                service_principal_id = _required(
                    f"IAGIS_ACCESS_{provider}_SERVICE_PRINCIPAL_ID"
                )
                app_role_id = os.getenv(
                    f"IAGIS_ACCESS_{provider}_APP_ROLE_ID", DEFAULT_APP_ROLE
                ).strip()
                if (not GUID_RE.fullmatch(service_principal_id)
                        or not GUID_RE.fullmatch(app_role_id)):
                    raise RuntimeError(f"configuração app_role de {provider} inválida")
                targets[provider.casefold()] = Target(
                    mode=mode,
                    service_principal_id=service_principal_id,
                    app_role_id=app_role_id,
                )
            else:
                raise RuntimeError(f"modo de acesso de {provider} inválido: {mode}")
        if not targets:
            raise RuntimeError("configure ao menos um alvo Claude ou ChatGPT")
        return cls(
            host=host,
            port=int(os.getenv("IAGIS_ACCESS_BROKER_PORT", "8092")),
            broker_token=broker_token,
            tenant_id=tenant_id,
            client_id=client_id,
            client_secret=client_secret,
            allowed_domains=domains,
            targets=targets,
        )


def _required(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise RuntimeError(f"{name} é obrigatório")
    return value


class GraphClient:
    def __init__(self, config: Config):
        self.config = config
        self._access_token = ""
        self._expires_at = 0.0

    def _token(self) -> str:
        if self._access_token and time.monotonic() < self._expires_at - 60:
            return self._access_token
        body = urllib.parse.urlencode({
            "client_id": self.config.client_id,
            "client_secret": self.config.client_secret,
            "scope": "https://graph.microsoft.com/.default",
            "grant_type": "client_credentials",
        }).encode()
        url = (
            f"https://login.microsoftonline.com/{self.config.tenant_id}"
            "/oauth2/v2.0/token"
        )
        request = urllib.request.Request(
            url, data=body, headers={"Content-Type": "application/x-www-form-urlencoded"}
        )
        payload = self._open_json(request, include_auth=False)
        token = str(payload.get("access_token", ""))
        if not token:
            raise GraphError(502, "Entra não retornou token de acesso")
        self._access_token = token
        self._expires_at = time.monotonic() + int(payload.get("expires_in", 3600))
        return token

    def _request(self, method: str, path: str, body: dict[str, Any] | None = None,
                 *, expected: tuple[int, ...] = (200,)) -> dict[str, Any]:
        data = json.dumps(body).encode() if body is not None else None
        request = urllib.request.Request(
            f"{GRAPH_BASE}/{path.lstrip('/')}",
            data=data,
            method=method,
            headers={
                "Authorization": f"Bearer {self._token()}",
                "Content-Type": "application/json",
            },
        )
        return self._open_json(request, expected=expected)

    @staticmethod
    def _open_json(request: urllib.request.Request, *, include_auth: bool = True,
                   expected: tuple[int, ...] = (200,)) -> dict[str, Any]:
        del include_auth  # documenta que o token endpoint não depende do Graph token
        try:
            with urllib.request.urlopen(request, timeout=20) as response:
                if response.status not in expected:
                    raise GraphError(response.status, "resposta inesperada do Microsoft Graph")
                content = response.read()
                return json.loads(content) if content else {}
        except urllib.error.HTTPError as exc:
            # Não propaga payload do provedor: ele pode conter identificadores sensíveis.
            raise GraphError(exc.code, f"Microsoft Graph retornou HTTP {exc.code}") from exc
        except (urllib.error.URLError, TimeoutError) as exc:
            raise GraphError(503, f"Microsoft Graph indisponível: {type(exc).__name__}") from exc

    def resolve_user(self, email: str) -> dict[str, Any]:
        encoded = urllib.parse.quote(email, safe="")
        try:
            user = self._request(
                "GET", f"users/{encoded}?$select=id,userPrincipalName,mail,accountEnabled"
            )
        except GraphError as exc:
            if exc.status != 404:
                raise
            literal = email.replace("'", "''")
            query = urllib.parse.urlencode({
                "$filter": f"mail eq '{literal}'",
                "$select": "id,userPrincipalName,mail,accountEnabled",
            })
            rows = self._request("GET", f"users?{query}").get("value", [])
            if len(rows) != 1:
                raise BrokerError("usuário não encontrado de forma inequívoca no Entra", 404)
            user = rows[0]
        if user.get("accountEnabled") is False:
            raise BrokerError("a conta do usuário está desabilitada no Entra", 409)
        return user

    def _is_group_member(self, group_id: str, user_id: str) -> bool:
        try:
            self._request("GET", f"groups/{group_id}/members/{user_id}?$select=id")
            return True
        except GraphError as exc:
            if exc.status == 404:
                return False
            raise

    def _apply_groups(self, target: Target, action: str,
                      user_id: str) -> tuple[bool, bool, str]:
        states = {group_id: self._is_group_member(group_id, user_id)
                  for group_id in target.group_ids}
        changed = False
        if action == "grant":
            for group_id, assigned in states.items():
                if not assigned:
                    self._request(
                        "POST", f"groups/{group_id}/members/$ref",
                        {"@odata.id": f"{GRAPH_BASE}/directoryObjects/{user_id}"},
                        expected=(204,),
                    )
                    changed = True
            return True, changed, "atribuição aos grupos Entra concluída; aguardando sincronização SCIM"
        if action == "revoke":
            for group_id, assigned in states.items():
                if assigned:
                    self._request(
                        "DELETE", f"groups/{group_id}/members/{user_id}/$ref",
                        expected=(204,),
                    )
                    changed = True
            return False, changed, "remoção dos grupos Entra concluída; aguardando sincronização SCIM"
        assigned_count = sum(states.values())
        return bool(assigned_count), False, (
            f"presente em {assigned_count} de {len(states)} grupo(s) Entra configurado(s)"
        )

    def _assignments(self, target: Target, user_id: str) -> list[dict[str, Any]]:
        service_principal_id = target.service_principal_id or ""
        query = urllib.parse.urlencode({"$filter": f"principalId eq {user_id}"})
        rows = self._request(
            "GET", f"servicePrincipals/{service_principal_id}/appRoleAssignedTo?{query}"
        ).get("value", [])
        return [row for row in rows if row.get("appRoleId") == target.app_role_id]

    def _apply_app_role(self, target: Target, action: str,
                        user_id: str) -> tuple[bool, bool, str]:
        assignments = self._assignments(target, user_id)
        service_principal_id = target.service_principal_id or ""
        if action == "grant":
            if not assignments:
                self._request(
                    "POST", f"servicePrincipals/{service_principal_id}/appRoleAssignedTo",
                    {
                        "principalId": user_id,
                        "resourceId": service_principal_id,
                        "appRoleId": target.app_role_id,
                    },
                    expected=(201,),
                )
            return True, not assignments, "atribuição direta à aplicação Entra concluída"
        if action == "revoke":
            for assignment in assignments:
                self._request(
                    "DELETE",
                    f"servicePrincipals/{service_principal_id}/appRoleAssignedTo/{assignment['id']}",
                    expected=(204,),
                )
            return False, bool(assignments), "atribuição direta à aplicação Entra removida"
        return bool(assignments), False, "atribuição direta consultada no Entra"

    def apply(self, provider: str, action: str, email: str) -> dict[str, Any]:
        normalized_email = email.strip().casefold()
        if not EMAIL_RE.fullmatch(normalized_email):
            raise BrokerError("e-mail inválido")
        domain = normalized_email.rsplit("@", 1)[1]
        if domain not in self.config.allowed_domains:
            raise BrokerError("domínio de e-mail não autorizado", 403)
        target = self.config.targets.get(provider)
        if target is None:
            raise BrokerError(f"provedor {provider} não configurado", 409)
        if action not in {"grant", "revoke", "status"}:
            raise BrokerError("ação inválida")
        user = self.resolve_user(normalized_email)
        user_id = str(user.get("id", ""))
        if not GUID_RE.fullmatch(user_id):
            raise GraphError(502, "Entra retornou identificador de usuário inválido")
        if target.mode == "group":
            assigned, changed, detail = self._apply_groups(target, action, user_id)
        else:
            assigned, changed, detail = self._apply_app_role(target, action, user_id)
        status = "UNCHANGED"
        if action in {"grant", "revoke"} and changed:
            status = "PENDING_SYNC" if target.mode == "group" else "ENTRA_UPDATED"
        if action == "status":
            status = "ASSIGNED" if assigned else "NOT_ASSIGNED"
        return {
            "email": normalized_email,
            "assigned": assigned,
            "changed": changed,
            "status": status,
            "detail": detail,
        }


class Handler(BaseHTTPRequestHandler):
    server_version = "iagis-access-broker/1"

    @property
    def config(self) -> Config:
        return self.server.config  # type: ignore[attr-defined]

    @property
    def graph(self) -> GraphClient:
        return self.server.graph  # type: ignore[attr-defined]

    def log_message(self, fmt: str, *args: object) -> None:
        print(json.dumps({"event": "http", "message": fmt % args}, ensure_ascii=False), flush=True)

    def _json(self, status: int, payload: dict[str, Any]) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _authorized(self) -> bool:
        header = self.headers.get("Authorization", "")
        expected = f"Bearer {self.config.broker_token}"
        return hmac.compare_digest(header, expected)

    def do_GET(self) -> None:
        if self.path == "/health":
            self._json(200, {"status": "ok", "providers": sorted(self.config.targets)})
            return
        self._json(404, {"error": "rota inexistente"})

    def do_POST(self) -> None:
        if self.path != "/v1/access":
            self._json(404, {"error": "rota inexistente"})
            return
        if not self._authorized():
            self._json(401, {"error": "não autorizado"})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length <= 0 or length > 4096:
                raise BrokerError("corpo inválido")
            payload = json.loads(self.rfile.read(length))
            if not isinstance(payload, dict):
                raise BrokerError("JSON inválido")
            provider = str(payload.get("provider", "")).casefold()
            action = str(payload.get("action", "")).casefold()
            email = str(payload.get("email", ""))
            self._json(200, self.graph.apply(provider, action, email))
        except BrokerError as exc:
            self._json(exc.status, {"error": str(exc)})
        except (GraphError, KeyError) as exc:
            status = exc.status if isinstance(exc, GraphError) else 502
            self._json(status if 400 <= status < 600 else 502, {"error": str(exc)})
        except (ValueError, TypeError, json.JSONDecodeError):
            self._json(400, {"error": "requisição inválida"})


class Server(ThreadingHTTPServer):
    def __init__(self, config: Config):
        super().__init__((config.host, config.port), Handler)
        self.config = config
        self.graph = GraphClient(config)


def main() -> None:
    config = Config.from_env()
    print(json.dumps({
        "event": "broker_started", "host": config.host, "port": config.port,
        "providers": sorted(config.targets),
    }), flush=True)
    Server(config).serve_forever()


if __name__ == "__main__":
    main()
