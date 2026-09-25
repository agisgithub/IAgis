"""Cliente sem privilégios para o broker local que protege a PKI OpenVPN."""
from __future__ import annotations

import re
import unicodedata
from typing import Any
from urllib.parse import quote

import requests


class VPNBrokerError(RuntimeError):
    pass


class VPNBrokerClient:
    def __init__(self, base_url: str, token: str, timeout: tuple[float, float] = (3, 120)):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.http = requests.Session()
        self.http.headers.update({"Authorization": f"Bearer {token}"})

    def _request(self, method: str, path: str, **kwargs: Any) -> requests.Response:
        try:
            response = self.http.request(
                method, f"{self.base_url}/{path.lstrip('/')}", timeout=self.timeout, **kwargs
            )
        except requests.RequestException as exc:
            raise VPNBrokerError(f"broker VPN indisponível: {type(exc).__name__}") from exc
        if not response.ok:
            raise VPNBrokerError(f"broker VPN retornou HTTP {response.status_code}")
        return response

    def create(self, client_name: str) -> tuple[dict[str, Any], bytes]:
        name = normalize_client_name(client_name)
        result = self._request("POST", "v1/clients", json={"name": name}).json()
        profile = self._request("GET", f"v1/clients/{quote(name, safe='')}/profile").content
        if not profile or len(profile) > 256 * 1024 or b"<key>" not in profile:
            raise VPNBrokerError("broker retornou um perfil OpenVPN inválido")
        return result, profile

    def revoke(self, client_name: str) -> dict[str, Any]:
        name = normalize_client_name(client_name)
        return self._request("POST", f"v1/clients/{quote(name, safe='')}/revoke").json()

    def status(self, client_name: str) -> dict[str, Any]:
        name = normalize_client_name(client_name)
        return self._request("GET", f"v1/clients/{quote(name, safe='')}").json()

    def list_clients(self) -> list[dict[str, Any]]:
        payload = self._request("GET", "v1/clients").json()
        rows = payload.get("clients", [])
        return rows if isinstance(rows, list) else []


def normalize_client_name(value: str) -> str:
    folded = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode("ascii")
    normalized = re.sub(r"[^A-Za-z0-9._-]+", "-", folded.strip()).strip("-._").lower()
    normalized = re.sub(r"[-_.]{2,}", "-", normalized)[:64]
    if not normalized:
        raise ValueError("nome do cliente VPN não contém caracteres válidos")
    return normalized
