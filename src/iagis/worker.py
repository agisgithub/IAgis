"""Orquestração controlada entre leitura, IA, regras, persistência e publicação."""
from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any

import structlog

from .config import Settings
from .glpi_client import GLPIClient
from .governance_agent import GovernanceAgent
from .governance_models import GovernanceReport, RiskLevel, Verdict
from .governance_rules import apply_rules
from .mention_detector import detect_mention
from .report_formatter import format_report
from .repository import Repository

log = structlog.get_logger()


class PublicationDenied(RuntimeError):
    pass


class Worker:
    def __init__(self, settings: Settings, client: GLPIClient, repository: Repository,
                 agent: GovernanceAgent | Any | None = None):
        self.settings, self.client, self.repository = settings, client, repository
        self.agent = agent or GovernanceAgent(settings.openai_model)

    def _assert_entity(self, entity_id: int) -> None:
        if self.settings.authorized_entities and entity_id not in self.settings.authorized_entities:
            raise PermissionError("entidade não autorizada")

    async def analyze_ticket(self, ticket_id: int, entity_id: int) -> tuple[int | None, GovernanceReport | None]:
        self._assert_entity(entity_id)
        ticket = self.client.get_ticket(ticket_id, entity_id)
        followups = self.client.get_followups(ticket_id, entity_id)
        attachments = self.client.get_attachments(ticket_id, entity_id)
        candidates = [(None, ticket.description, None)] + [(f.id, f.content, f.author_id) for f in followups]
        mentions = [(fid, m) for fid, content, author in candidates if
                    (m := detect_mention(content, self.settings.mention, author_id=author,
                                         own_user_id=self.settings.glpi_user_id))]
        if not mentions:
            return None, None
        followup_id, mention = mentions[-1]
        analysis_id = self.repository.register(ticket_id, entity_id, followup_id, mention.content_hash)
        if analysis_id is None:
            return None, None
        context = {
            "ticket_id": ticket.id, "titulo": ticket.title, "descricao": ticket.description,
            "solicitante": ticket.requester, "entidade": ticket.entity, "categoria": ticket.category,
            "acompanhamentos": [f.model_dump() for f in followups],
            # Somente metadados; anexos jamais são baixados ou executados.
            "anexos_metadados": [a.model_dump() for a in attachments],
            "data_analise": datetime.now(UTC).date().isoformat(),
        }
        try:
            report, run_id = await self.agent.analyze(context)
            report = apply_rules(report)
            self.repository.save_report(analysis_id, report, run_id)
            if self._can_auto_publish(report):
                content = format_report(report)
                publication_id = self.client.create_followup(ticket_id, entity_id, content)
                self.repository.mark_published(analysis_id, publication_id)
            return analysis_id, report
        except Exception as exc:
            self.repository.fail(analysis_id, type(exc).__name__)
            log.error("analysis_failed", analysis_id=analysis_id, error_type=type(exc).__name__)
            raise

    def _can_auto_publish(self, report: GovernanceReport) -> bool:
        return (
            not self.settings.dry_run
            and not report.pendencias
            and report.veredito != Verdict.INCONCLUSIVO
            and report.nivel_risco not in {RiskLevel.ALTO, RiskLevel.CRITICO}
            and report.veredito.value in self.settings.authorized_auto_verdicts
        )

    def publish(self, analysis_id: int, *, confirm: bool) -> int:
        row = self.repository.get(analysis_id)
        if row is None or not row["report"]:
            raise PublicationDenied("análise inexistente ou sem relatório")
        report = GovernanceReport.model_validate_json(row["report"])
        preview = format_report(report)
        if not confirm:
            raise PublicationDenied("publicação exige --confirm")
        if self.settings.dry_run:
            raise PublicationDenied("dry-run ativo: publicação bloqueada")
        if row["published_at"]:
            raise PublicationDenied("análise já publicada")
        if report.veredito == Verdict.INCONCLUSIVO or report.nivel_risco in {RiskLevel.ALTO, RiskLevel.CRITICO}:
            # --confirm é a aprovação manual explícita para casos sensíveis.
            pass
        elif report.veredito.value not in self.settings.authorized_auto_verdicts:
            raise PublicationDenied("veredito não autorizado pela configuração")
        followup_id = self.client.create_followup(row["ticket_id"], row["entity_id"], preview)
        self.repository.mark_published(analysis_id, followup_id)
        return followup_id

    async def run_forever(self, entity_id: int) -> None:
        self._assert_entity(entity_id)
        while True:
            try:
                for ticket in self.client.list_tickets(entity_id):
                    try:
                        await self.analyze_ticket(ticket.id, entity_id)
                    except Exception as exc:
                        log.warning("ticket_processing_failed", ticket_id=ticket.id,
                                    entity_id=entity_id, error_type=type(exc).__name__)
            except Exception as exc:
                log.error("poll_failed", entity_id=entity_id, error_type=type(exc).__name__)
            await asyncio.sleep(self.settings.poll_interval)
