"""Cliente sem privilégios para o broker local de gestão de acessos."""
from __future__ import annotations

from typing import Any

import requests

from .access_models import AccessAction, AccessProvider, AccessUserResult


class AccessBrokerError(RuntimeError):
    def __init__(self, message: str, *, status_code: int | None = None):
        super().__init__(message)
        self.status_code = status_code

    @property
    def retryable(self) -> bool:
        return self.status_code is None or self.status_code == 429 or self.status_code >= 500


class AccessBrokerClient:
    def __init__(self, base_url: str, token: str, timeout: tuple[float, float] = (3, 45)):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.http = requests.Session()
        self.http.headers.update({"Authorization": f"Bearer {token}"})

    def apply(self, provider: AccessProvider, action: AccessAction,
              email: str) -> AccessUserResult:
        try:
            response = self.http.post(
                f"{self.base_url}/v1/access",
                json={"provider": provider.value, "action": action.value, "email": email},
                timeout=self.timeout,
            )
        except requests.RequestException as exc:
            raise AccessBrokerError(
                f"broker de acesso indisponível: {type(exc).__name__}"
            ) from exc
        if not response.ok:
            reason = ""
            try:
                reason = str(response.json().get("error", ""))[:200]
            except (ValueError, AttributeError):
                pass
            suffix = f": {reason}" if reason else ""
            raise AccessBrokerError(
                f"broker de acesso retornou HTTP {response.status_code}{suffix}",
                status_code=response.status_code,
            )
        try:
            payload: dict[str, Any] = response.json()
            return AccessUserResult.model_validate(payload)
        except (ValueError, TypeError) as exc:
            raise AccessBrokerError("broker de acesso retornou resposta inválida") from exc
