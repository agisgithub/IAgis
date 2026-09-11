"""Monitor controlado de menções, sugestões, persistência e retries."""
from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import structlog

from .ai_provider import GovernanceAI, build_governance_ai
from .config import Settings
from .glpi_client import GLPIClient
from .governance_models import GovernanceReport
from .governance_rules import apply_rules
from .mention_detector import Mention, detect_mention
from .ollama_agent import OllamaConfigurationError
from .ollama_agent import OllamaSuggestionAgent
from .repository import Repository
from .report_formatter import format_report, format_suggestion_for_publication
from .skill_runtime import execute_skill
from .suggestion_models import ResponseSuggestion

log = structlog.get_logger()


class PublicationDenied(RuntimeError):
    pass


class PublicationError(RuntimeError):
    pass


@dataclass(frozen=True)
class PendingMention:
    followup_id: int | None
    mention: Mention
    sort_key: tuple[str, int]
    event_key: str


class Worker:
    def __init__(self, settings: Settings, client: GLPIClient, repository: Repository,
                 agent: GovernanceAI | Any | None = None):
        self.settings, self.client, self.repository = settings, client, repository
        self.agent = agent or build_governance_ai(settings)

    def _assert_entity(self, entity_id: int) -> None:
        if self.settings.authorized_entities and entity_id not in self.settings.authorized_entities:
            raise PermissionError("entidade não autorizada")

    def _mentions(self, ticket: Any, followups: list[Any]) -> list[PendingMention]:
        events: list[tuple[int | None, str, int | None, str, int]] = [
            (None, ticket.description, None, "", -1)
        ]
        for item in followups:
            events.append((item.id, item.content, item.author_id, item.date or "", item.id))
        found: list[PendingMention] = []
        for followup_id, content, author_id, occurred_at, stable_id in events:
            mention = detect_mention(content, self.settings.mention, author_id=author_id,
                                     own_user_id=self.settings.glpi_user_id)
            if not mention:
                continue
            source = "description" if followup_id is None else f"followup:{followup_id}"
            # O hash representa a versão do conteúdo; a chave inclui escopo e origem do evento.
            event_key = f"{ticket.entity_id}:{ticket.id}:{source}:{mention.content_hash}"
            found.append(PendingMention(followup_id, mention, (occurred_at, stable_id), event_key))
        return sorted(found, key=lambda event: event.sort_key)

    async def analyze_ticket(self, ticket_id: int, entity_id: int) -> list[tuple[int, Any]]:
        self._assert_entity(entity_id)
        ticket = self.client.get_ticket(ticket_id, entity_id)
        followups = self.client.get_followups(ticket_id, entity_id)
        attachments = self.client.get_attachments(ticket_id, entity_id)
        mentions = self._mentions(ticket, followups)
        results: list[tuple[int, Any]] = []
        for event in mentions:
            analysis_id = self.repository.claim_event(
                ticket_id, entity_id, event.followup_id, event.mention.content_hash,
                event.event_key, self.settings.max_attempts,
            )
            if analysis_id is None:
                log.debug("event_ignored_duplicate", ticket_id=ticket_id, entity_id=entity_id,
                          followup_id=event.followup_id)
                continue
            log.info("analysis_started", analysis_id=analysis_id, ticket_id=ticket_id,
                     entity_id=entity_id, followup_id=event.followup_id)
            context = {
                "ticket_id": ticket.id, "titulo": ticket.title, "descricao": ticket.description,
                "solicitante": ticket.requester, "entidade": ticket.entity, "categoria": ticket.category,
                "acompanhamentos": [f.model_dump() for f in followups],
                "anexos_metadados": [a.model_dump() for a in attachments],
                "data_analise": datetime.now().astimezone().date().isoformat(),
            }
            try:
                route_context = {"titulo": ticket.title, "descricao": ticket.description,
                                 "categoria": ticket.category}
                route = self.repository.resolve_agent(route_context)
                agent = self.agent
                prompt = ""
                skill_results: list[dict[str, Any]] = []
                if route:
                    if route["provider"] != "ollama":
                        raise ValueError("o piloto administrativo executa somente modelos Ollama")
                    agent = OllamaSuggestionAgent(route["base_url"] or self.settings.ollama_url,
                                                  route["model_name"], route["timeout"])
                    prompt = route["prompt"]
                    for skill in self.repository.agent_skills(route["agent_id"]):
                        schema = json.loads(skill["input_schema"])
                        skill_input = {key: context.get(key) for key in schema.get("properties", {})}
                        output = await asyncio.to_thread(
                            execute_skill, skill["source"], skill["input_schema"],
                            skill["output_schema"], skill_input,
                        )
                        skill_results.append({"skill": skill["name"], "output": output})
                    log.info("agent_routed", analysis_id=analysis_id, agent=route["agent_name"],
                             model=route["model_name"], skills=len(skill_results))
                if hasattr(agent, "analyze_task"):
                    report, run_id = await agent.analyze_task(context, prompt, skill_results)
                else:
                    report, run_id = await agent.analyze(context)
                if isinstance(report, GovernanceReport):
                    report = apply_rules(report)
                self.repository.save_result(analysis_id, report, run_id)
                if self.settings.dry_run:
                    log.info("result_saved_dry_run", analysis_id=analysis_id, ticket_id=ticket_id,
                             result_type=type(report).__name__)
                else:
                    body = (format_suggestion_for_publication(report)
                            if isinstance(report, ResponseSuggestion) else format_report(report))
                    if not self.repository.begin_publication(analysis_id):
                        raise PublicationDenied("análise não está disponível para publicação")
                    try:
                        publication_id = self.client.create_followup(ticket_id, entity_id, body)
                    except Exception as exc:
                        raise PublicationError(f"GLPI follow-up falhou: {type(exc).__name__}") from exc
                    self.repository.mark_published(analysis_id, publication_id)
                    log.info("published_followup", analysis_id=analysis_id, ticket_id=ticket_id,
                             followup_id=publication_id)
                results.append((analysis_id, report))
            except Exception as exc:
                retryable = not isinstance(exc, (OllamaConfigurationError, PermissionError, ValueError))
                cause = f"{type(exc).__name__}: {str(exc)[:300]}"
                self.repository.fail(analysis_id, cause, retryable=retryable,
                                     retry_delay=self.settings.retry_delay)
                row = self.repository.get(analysis_id)
                log.error("analysis_failed", analysis_id=analysis_id, cause=cause,
                          retryable=retryable, attempt=row["attempt_count"] if row else None,
                          max_attempts=self.settings.max_attempts)
        return results

    async def retry_analysis(self, analysis_id: int) -> list[tuple[int, Any]]:
        row = self.repository.prepare_retry(analysis_id, self.settings.max_attempts)
        self._assert_entity(row["entity_id"])
        return await self.analyze_ticket(row["ticket_id"], row["entity_id"])

    def publish(self, analysis_id: int, *, confirm: bool) -> int:
        if not confirm:
            raise PublicationDenied("publicação exige --confirm")
        if self.settings.dry_run:
            raise PublicationDenied("dry-run ativo: publicação bloqueada")
        row = self.repository.get(analysis_id)
        if row is None or not row["report"]:
            raise PublicationDenied("análise inexistente ou sem resposta")
        if row["published_at"]:
            raise PublicationDenied("análise já publicada")
        if not self.repository.begin_publication(analysis_id):
            raise PublicationDenied("análise não está disponível para publicação")
        try:
            try:
                result = ResponseSuggestion.model_validate_json(row["report"])
                body = format_suggestion_for_publication(result)
            except Exception:
                result = GovernanceReport.model_validate_json(row["report"])
                body = format_report(result)
            followup_id = self.client.create_followup(row["ticket_id"], row["entity_id"], body)
            self.repository.mark_published(analysis_id, followup_id)
            log.info("published_followup", analysis_id=analysis_id, ticket_id=row["ticket_id"],
                     followup_id=followup_id)
            return followup_id
        except Exception as exc:
            cause = f"publication: {type(exc).__name__}: {str(exc)[:300]}"
            self.repository.fail(analysis_id, cause, retryable=True,
                                 retry_delay=self.settings.retry_delay)
            log.error("publication_failed", analysis_id=analysis_id,
                      ticket_id=row["ticket_id"], cause=cause)
            raise

    async def run_forever(self, entity_id: int) -> None:
        self._assert_entity(entity_id)
        log.info("monitor_started", provider=self.settings.ai_provider, model=self.settings.ai_model,
                 entity_id=entity_id, dry_run=self.settings.dry_run,
                 process_health="running", integrations_health="unknown")
        while True:
            checked = new_mentions = 0
            started = datetime.now().astimezone()
            try:
                tickets = self.client.list_tickets(entity_id)
                for ticket in tickets:
                    checked += 1
                    try:
                        new_mentions += len(await self.analyze_ticket(ticket.id, entity_id))
                    except Exception as exc:
                        log.warning("ticket_query_failed", ticket_id=ticket.id, entity_id=entity_id,
                                    cause=f"{type(exc).__name__}: {str(exc)[:300]}")
                log.info("poll_completed", entity_id=entity_id, tickets_checked=checked,
                         new_mentions=new_mentions,
                         duration_ms=round((datetime.now().astimezone() - started).total_seconds() * 1000),
                         glpi_health="ok")
            except Exception as exc:
                log.error("poll_failed", entity_id=entity_id, tickets_checked=checked,
                          cause=f"{type(exc).__name__}: {str(exc)[:300]}", glpi_health="failed")
            await asyncio.sleep(self.settings.poll_interval)
