"""Adaptadores de sugestão estruturada para Gemini e OpenAI."""
from __future__ import annotations

import json
from typing import Any
from uuid import uuid4

from .ollama_agent import SUGGESTION_SYSTEM
from .suggestion_models import ResponseSuggestion


def _prompt(context: dict[str, Any]) -> str:
    return (
        "UNTRUSTED_TICKET_BEGIN\n"
        + json.dumps(context, ensure_ascii=False, default=str)
        + "\nUNTRUSTED_TICKET_END"
    )


class GeminiSuggestionAgent:
    def __init__(self, api_key: str, model: str):
        from google import genai
        self.client = genai.Client(api_key=api_key)
        self.model = model

    async def analyze(self, context: dict[str, Any]) -> tuple[ResponseSuggestion, str | None]:
        from google.genai import types
        response = await self.client.aio.models.generate_content(
            model=self.model,
            contents=SUGGESTION_SYSTEM + "\n" + _prompt(context),
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                response_json_schema=ResponseSuggestion.model_json_schema(),
                temperature=0.1,
            ),
        )
        if not response.text:
            raise RuntimeError("Gemini não retornou sugestão estruturada")
        return ResponseSuggestion.model_validate_json(response.text), str(
            getattr(response, "response_id", None) or f"gemini-{uuid4()}"
        )


class OpenAISuggestionAgent:
    def __init__(self, api_key: str, model: str):
        from openai import AsyncOpenAI
        self.client = AsyncOpenAI(api_key=api_key)
        self.model = model

    async def analyze(self, context: dict[str, Any]) -> tuple[ResponseSuggestion, str | None]:
        response = await self.client.chat.completions.parse(
            model=self.model,
            messages=[
                {"role": "system", "content": SUGGESTION_SYSTEM},
                {"role": "user", "content": _prompt(context)},
            ],
            response_format=ResponseSuggestion,
            temperature=0.1,
        )
        suggestion = response.choices[0].message.parsed
        if not isinstance(suggestion, ResponseSuggestion):
            raise RuntimeError("OpenAI não retornou sugestão estruturada")  # noqa: TRY004
        return suggestion, response.id
