"""Representações normalizadas de objetos retornados pelo GLPI."""
from __future__ import annotations

from pydantic import BaseModel, Field


class Entity(BaseModel):
    id: int
    name: str


class Followup(BaseModel):
    id: int
    content: str = ""
    author_id: int | None = None
    date: str | None = None


class Attachment(BaseModel):
    id: int
    name: str = ""
    mime_type: str | None = None
    size: int | None = None
    metadata: dict = Field(default_factory=dict)


class Ticket(BaseModel):
    id: int
    entity_id: int
    title: str = ""
    description: str = ""
    requester: str = "não confirmado"
    entity: str = "não confirmada"
    category: str = "não confirmada"
    raw: dict = Field(default_factory=dict, exclude=True)
