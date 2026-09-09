"""Agente especialista: somente pesquisa e geração de relatório, sem acesso ao GLPI."""
from __future__ import annotations

import json
from datetime import date
from typing import Any

from agents import Agent, Runner, WebSearchTool, set_default_openai_client
from openai import AsyncOpenAI

from .agent_instructions import SYSTEM_INSTRUCTIONS
from .governance_models import GovernanceReport

class GovernanceAgent:
    def __init__(self, api_key: str, model: str):
        set_default_openai_client(AsyncOpenAI(api_key=api_key), use_for_tracing=True)
        self.agent = Agent(
            name="IAgis Governança",
            instructions=SYSTEM_INSTRUCTIONS,
            model=model,
            tools=[WebSearchTool()],
            output_type=GovernanceReport,
        )

    async def analyze(self, context: dict[str, Any]) -> tuple[GovernanceReport, str | None]:
        # JSON delimita os dados; não é interpolado nas instruções privilegiadas.
        prompt = "UNTRUSTED_DATA_BEGIN\n" + json.dumps(context, ensure_ascii=False, default=str) + \
                 "\nUNTRUSTED_DATA_END\nData da análise: " + date.today().isoformat()
        result = await Runner.run(self.agent, prompt)
        report = result.final_output
        if not isinstance(report, GovernanceReport):
            report = GovernanceReport.model_validate(report)
        run_id = getattr(getattr(result, "last_response", None), "id", None)
        return report, run_id
