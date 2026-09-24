"""Contratos estruturados para planejamento e execução de ações OpenVPN."""
from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, Field, model_validator


class VPNAction(StrEnum):
    NONE = "none"
    CREATE = "create"
    REVOKE = "revoke"
    STATUS = "status"
    LIST = "list"


class VPNActionPlan(BaseModel):
    action: VPNAction = VPNAction.NONE
    client_name: str | None = None
    confidence: float = Field(ge=0, le=1)
    needs_clarification: bool = False
    clarification_question: str | None = None
    rationale: str = Field(default="", max_length=500)

    @model_validator(mode="after")
    def require_client_for_targeted_actions(self) -> VPNActionPlan:
        if (self.action in {VPNAction.CREATE, VPNAction.REVOKE, VPNAction.STATUS}
                and (not self.client_name or not self.client_name.strip())):
            self.needs_clarification = True
            self.clarification_question = (
                self.clarification_question
                or "Qual nome deve identificar o perfil VPN?"
            )
        return self


class VPNExecutionReport(BaseModel):
    action: VPNAction
    client_name: str | None = None
    status: str
    message: str
    attachment_name: str | None = None
    document_id: int | None = None
    details: list[str] = Field(default_factory=list)
