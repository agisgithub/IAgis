"""Contratos estruturados para gestão de acessos SaaS via diretório corporativo."""
from __future__ import annotations

import re
from enum import StrEnum

from pydantic import BaseModel, Field, field_validator, model_validator


class AccessProvider(StrEnum):
    CLAUDE = "claude"
    CHATGPT = "chatgpt"


class AccessAction(StrEnum):
    NONE = "none"
    GRANT = "grant"
    REVOKE = "revoke"
    STATUS = "status"


class AccessActionPlan(BaseModel):
    action: AccessAction
    provider: AccessProvider | None
    user_emails: list[str] = Field(default_factory=list, max_length=10)
    confidence: float = Field(ge=0, le=1)
    needs_clarification: bool
    clarification_question: str | None
    rationale: str = Field(max_length=500)

    @field_validator("user_emails")
    @classmethod
    def normalize_emails(cls, values: list[str]) -> list[str]:
        normalized: list[str] = []
        for value in values:
            email = value.strip().casefold()
            if not re.fullmatch(r"[a-z0-9.!#$%&'*+/=?^_`{|}~-]+@[a-z0-9-]+(?:\.[a-z0-9-]+)+", email):
                raise ValueError("e-mail corporativo inválido")
            if email not in normalized:
                normalized.append(email)
        return normalized

    @model_validator(mode="after")
    def require_target_for_actions(self) -> AccessActionPlan:
        if self.action != AccessAction.NONE and self.provider is None:
            self.needs_clarification = True
            self.clarification_question = (
                self.clarification_question or "O acesso é para Claude ou ChatGPT?"
            )
        if self.action != AccessAction.NONE and not self.user_emails:
            self.needs_clarification = True
            self.clarification_question = (
                self.clarification_question
                or "Informe o e-mail corporativo exato de cada usuário."
            )
        return self


class AccessUserResult(BaseModel):
    email: str
    assigned: bool
    changed: bool = False
    status: str
    detail: str


class AccessExecutionReport(BaseModel):
    action: AccessAction
    provider: AccessProvider | None = None
    user_emails: list[str] = Field(default_factory=list)
    status: str
    message: str
    results: list[AccessUserResult] = Field(default_factory=list)
    details: list[str] = Field(default_factory=list)
