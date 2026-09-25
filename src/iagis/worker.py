"""Monitor controlado de menções, sugestões, persistência e retries."""
from __future__ import annotations

import asyncio
import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

import structlog
from pydantic import ValidationError

from .access_action_planner import AccessActionPlanner, build_access_action_planner
from .access_client import AccessBrokerClient, AccessBrokerError
from .access_models import (
    AccessAction,
    AccessActionPlan,
    AccessExecutionReport,
    AccessUserResult,
)
from .ai_provider import GovernanceAI, build_governance_ai
from .config import Settings
from .glpi_client import GLPIClient
from .governance_models import GovernanceReport
from .governance_rules import apply_rules
from .mention_detector import Mention, detect_mention
from .ollama_agent import OllamaConfigurationError, OllamaSuggestionAgent
from .report_formatter import (
    format_access_report,
    format_report,
    format_suggestion_for_publication,
    format_vpn_report,
)
from .repository import Repository
from .skill_runtime import execute_skill
from .suggestion_models import ResponseSuggestion
from .vpn_action_planner import VPNActionPlanner, build_vpn_action_planner
from .vpn_client import VPNBrokerClient, normalize_client_name
from .vpn_models import VPNAction, VPNActionPlan, VPNExecutionReport

log = structlog.get_logger()


class PublicationDenied(RuntimeError):
    pass


class PublicationError(RuntimeError):
    pass


@dataclass(frozen=True)
class PendingMention:
    followup_id: int | None
    author_id: int | None
    occurred_at: str
    mention: Mention
    sort_key: tuple[str, int]
    event_key: str


class Worker:
    def __init__(self, settings: Settings, client: GLPIClient, repository: Repository,
                 agent: GovernanceAI | Any | None = None,
                 action_planner: VPNActionPlanner | Any | None = None,
                 vpn_client: VPNBrokerClient | Any | None = None,
                 access_action_planner: AccessActionPlanner | Any | None = None,
                 access_client: AccessBrokerClient | Any | None = None):
        self.settings, self.client, self.repository = settings, client, repository
        self.agent = agent or build_governance_ai(settings)
        self.action_planner = action_planner
        self.vpn_client = vpn_client
        self.access_action_planner = access_action_planner
        self.access_client = access_client
        if settings.vpn_enabled:
            self.action_planner = self.action_planner or build_vpn_action_planner(settings)
            self.vpn_client = self.vpn_client or VPNBrokerClient(
                settings.vpn_broker_url, settings.vpn_broker_token.get_secret_value()
            )
        if settings.access_enabled:
            self.access_action_planner = (
                self.access_action_planner or build_access_action_planner(settings)
            )
            self.access_client = self.access_client or AccessBrokerClient(
                settings.access_broker_url,
                settings.access_broker_token.get_secret_value(),
            )

    def _assert_entity(self, entity_id: int) -> None:
        if self.settings.authorized_entities and entity_id not in self.settings.authorized_entities:
            raise PermissionError("entidade não autorizada")

    def _mentions(self, ticket: Any, followups: list[Any]) -> list[PendingMention]:
        events: list[tuple[int | None, str, int | None, str, int]] = [
            (None, ticket.description, ticket.creator_id, ticket.created_at or "", -1)
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
            found.append(PendingMention(
                followup_id, author_id, occurred_at, mention,
                (occurred_at, stable_id), event_key
            ))
        return sorted(found, key=lambda event: event.sort_key)

    @staticmethod
    def _is_vpn_candidate(context: dict[str, Any]) -> bool:
        text = " ".join(str(context.get(key, "")) for key in (
            "titulo", "descricao", "mensagem_gatilho", "categoria"
        )).casefold()
        return any(term in text for term in (
            "vpn", "openvpn", ".ovpn", "rede privada virtual"
        ))

    @staticmethod
    def _is_access_candidate(context: dict[str, Any]) -> bool:
        text = " ".join(str(context.get(key, "")) for key in (
            "titulo", "descricao", "mensagem_gatilho", "categoria"
        )).casefold()
        provider = any(term in text for term in ("claude", "chatgpt", "chat gpt"))
        action = any(term in text for term in (
            "acesso", "assento", "cadeira", "licença", "licenca", "convite",
            "convid", "liber", "adicion", "inclu", "bloque", "revog", "remov",
        ))
        return provider and action

    @staticmethod
    def _event_is_fresh(occurred_at_value: str, max_age_minutes: int) -> bool:
        try:
            occurred_at = datetime.fromisoformat(occurred_at_value)
            if occurred_at.tzinfo is None:
                occurred_at = occurred_at.astimezone()
            age = datetime.now().astimezone() - occurred_at.astimezone()
            return timedelta(minutes=-5) <= age <= timedelta(minutes=max_age_minutes)
        except (TypeError, ValueError):
            return False

    async def _execute_access_action(self, *, plan: AccessActionPlan, event: PendingMention,
                                     analysis_id: int, ticket: Any,
                                     entity_id: int) -> AccessExecutionReport:
        if (plan.needs_clarification
                or plan.confidence < self.settings.access_action_min_confidence):
            return AccessExecutionReport(
                action=plan.action,
                provider=plan.provider,
                user_emails=plan.user_emails,
                status="NEEDS_INFORMATION",
                message=plan.clarification_question or (
                    "Confirme o produto e informe o e-mail corporativo exato de cada usuário."
                ),
                details=[f"Confiança da classificação: {plan.confidence:.0%}"],
            )
        if plan.provider is None or not plan.user_emails:
            return AccessExecutionReport(
                action=plan.action,
                provider=plan.provider,
                user_emails=plan.user_emails,
                status="NEEDS_INFORMATION",
                message="Informe Claude ou ChatGPT e o e-mail corporativo exato de cada usuário.",
            )
        if (not self.repository.has_access_operation_for_event(event.event_key)
                and not self._event_is_fresh(
                    event.occurred_at, self.settings.access_max_event_age_minutes
                )):
            return AccessExecutionReport(
                action=plan.action,
                provider=plan.provider,
                user_emails=plan.user_emails,
                status="STALE",
                message=(
                    "Por segurança, um pedido antigo de gestão de acesso não é executado. "
                    "Um técnico atribuído deve registrar um novo acompanhamento com o pedido atual."
                ),
            )
        if event.author_id is None:
            return AccessExecutionReport(
                action=plan.action,
                provider=plan.provider,
                user_emails=plan.user_emails,
                status="DENIED",
                message=(
                    "Não consegui confirmar a identidade de quem solicitou a ação. "
                    "Peça a um técnico atribuído ao chamado para registrar o pedido."
                ),
            )
        authorized = await asyncio.to_thread(
            self.client.is_ticket_technician, ticket.id, entity_id, event.author_id
        )
        if not authorized:
            return AccessExecutionReport(
                action=plan.action,
                provider=plan.provider,
                user_emails=plan.user_emails,
                status="DENIED",
                message=(
                    "A gestão de acesso não foi executada: o autor não está atribuído como "
                    "técnico nem pertence a um grupo técnico deste chamado."
                ),
            )

        results: list[AccessUserResult] = []
        for email in plan.user_emails:
            digest = hashlib.sha256(
                f"{event.event_key}\0{plan.provider.value}\0{email}".encode()
            ).hexdigest()
            operation = self.repository.begin_access_operation(
                operation_key=digest,
                event_key=event.event_key,
                analysis_id=analysis_id,
                ticket_id=ticket.id,
                entity_id=entity_id,
                followup_id=event.followup_id,
                requester_user_id=event.author_id,
                provider=plan.provider.value,
                action=plan.action.value,
                user_email=email,
            )
            operation_id = int(operation["id"])
            if operation["state"] == "COMPLETED" and operation["report"]:
                results.append(AccessUserResult.model_validate_json(operation["report"]))
                continue
            if self.settings.dry_run:
                result = AccessUserResult(
                    email=email,
                    assigned=False,
                    status="DRY_RUN",
                    detail="ação validada, mas não executada porque o modo dry-run está ativo",
                )
                self.repository.update_access_operation(
                    operation_id, "DRY_RUN", report=result
                )
                results.append(result)
                continue
            try:
                result = await asyncio.to_thread(
                    self.access_client.apply, plan.provider, plan.action, email
                )
                self.repository.update_access_operation(
                    operation_id, "COMPLETED", report=result
                )
                results.append(result)
            except AccessBrokerError as exc:
                if exc.retryable:
                    self.repository.update_access_operation(
                        operation_id, "FAILED",
                        error=f"{type(exc).__name__}: {str(exc)[:300]}",
                    )
                    raise
                result = AccessUserResult(
                    email=email,
                    assigned=False,
                    status="REJECTED",
                    detail=str(exc)[:300],
                )
                self.repository.update_access_operation(
                    operation_id, "COMPLETED", report=result
                )
                results.append(result)
            except Exception as exc:
                self.repository.update_access_operation(
                    operation_id, "FAILED",
                    error=f"{type(exc).__name__}: {str(exc)[:300]}",
                )
                raise

        if self.settings.dry_run:
            status = "DRY_RUN"
            message = "Pedido validado, sem alteração porque o modo dry-run está ativo."
        elif any(result.status == "REJECTED" for result in results):
            status = "PARTIAL" if any(result.changed for result in results) else "REJECTED"
            message = (
                "Um ou mais usuários não puderam ser processados. Consulte os detalhes; "
                "alterações concluídas para outros usuários permanecem válidas."
            )
        elif plan.action == AccessAction.STATUS:
            status = "COMPLETED"
            message = f"Consulta de acesso ao {plan.provider.value} concluída no Entra."
        elif any(result.changed for result in results):
            verb = "liberação" if plan.action == AccessAction.GRANT else "revogação"
            if any(result.status == "PENDING_SYNC" for result in results):
                status = "PENDING_SYNC"
                message = (
                    f"A {verb} de acesso ao {plan.provider.value} foi aplicada no Entra. "
                    "A conclusão no serviço depende da sincronização SCIM."
                )
            else:
                status = "ENTRA_UPDATED"
                message = (
                    f"A {verb} foi aplicada à Enterprise Application no Entra. "
                    "Esse modo não confirma criação ou remoção de assento dentro do serviço."
                )
        else:
            status = "COMPLETED"
            message = "Nenhuma alteração foi necessária; o estado solicitado já estava aplicado."
        return AccessExecutionReport(
            action=plan.action,
            provider=plan.provider,
            user_emails=plan.user_emails,
            status=status,
            message=message,
            results=results,
            details=[
                "O broker aceita somente os grupos ou a aplicação Entra definidos na configuração.",
                "Credenciais administrativas não são enviadas ao modelo de IA nem ao GLPI.",
            ],
        )

    async def _execute_vpn_action(self, *, plan: VPNActionPlan, event: PendingMention,
                                  analysis_id: int, ticket: Any,
                                  entity_id: int) -> VPNExecutionReport:
        if plan.needs_clarification or plan.confidence < self.settings.vpn_action_min_confidence:
            question = plan.clarification_question or (
                "Confirme a ação desejada e o identificador do perfil VPN."
            )
            return VPNExecutionReport(
                action=plan.action,
                client_name=plan.client_name,
                status="NEEDS_INFORMATION",
                message=question,
                details=[f"Confiança da classificação: {plan.confidence:.0%}"],
            )
        existing_operation = self.repository.get_vpn_operation_by_event(event.event_key)
        if existing_operation is None:
            try:
                occurred_at = datetime.fromisoformat(event.occurred_at)
                if occurred_at.tzinfo is None:
                    occurred_at = occurred_at.astimezone()
                age = datetime.now().astimezone() - occurred_at.astimezone()
                fresh = timedelta(minutes=-5) <= age <= timedelta(
                    minutes=self.settings.vpn_max_event_age_minutes
                )
            except (TypeError, ValueError):
                fresh = False
            if not fresh:
                return VPNExecutionReport(
                    action=plan.action,
                    client_name=plan.client_name,
                    status="STALE",
                    message=(
                        "Por segurança, uma ação OpenVPN antiga não é executada automaticamente. "
                        "Um técnico atribuído deve registrar um novo acompanhamento com o pedido atual."
                    ),
                )
        if event.author_id is None:
            return VPNExecutionReport(
                action=plan.action,
                client_name=plan.client_name,
                status="DENIED",
                message=(
                    "Não consegui confirmar a identidade de quem solicitou a ação. "
                    "Peça a um técnico atribuído ao chamado para registrar o pedido em um acompanhamento."
                ),
            )
        authorized = await asyncio.to_thread(
            self.client.is_ticket_technician, ticket.id, entity_id, event.author_id
        )
        if not authorized:
            return VPNExecutionReport(
                action=plan.action,
                client_name=plan.client_name,
                status="DENIED",
                message=(
                    "A ação OpenVPN não foi executada: o autor do pedido não está atribuído "
                    "como técnico nem pertence a um grupo técnico deste chamado."
                ),
            )

        client_name = None
        if plan.action != VPNAction.LIST:
            try:
                client_name = normalize_client_name(plan.client_name or "")
            except ValueError:
                return VPNExecutionReport(
                    action=plan.action,
                    status="NEEDS_INFORMATION",
                    message="Informe um nome válido e inequívoco para identificar o perfil VPN.",
                )

        operation = self.repository.begin_vpn_operation(
            event_key=event.event_key,
            analysis_id=analysis_id,
            ticket_id=ticket.id,
            entity_id=entity_id,
            followup_id=event.followup_id,
            requester_user_id=event.author_id,
            action=plan.action.value,
            client_name=client_name,
        )
        operation_id = int(operation["id"])
        if operation["state"] == "COMPLETED" and operation["report"]:
            return VPNExecutionReport.model_validate_json(operation["report"])
        if self.settings.dry_run:
            report = VPNExecutionReport(
                action=plan.action,
                client_name=client_name,
                status="DRY_RUN",
                message="Ação validada, mas não executada porque o modo dry-run está ativo.",
            )
            self.repository.update_vpn_operation(operation_id, "DRY_RUN", report=report)
            return report

        try:
            if plan.action == VPNAction.CREATE:
                result, profile = await asyncio.to_thread(self.vpn_client.create, client_name)
                document_id = operation["document_id"]
                filename = f"{client_name}.ovpn"
                if document_id is None:
                    document_id = await asyncio.to_thread(
                        self.client.attach_document,
                        ticket.id, entity_id, filename, profile,
                        "application/x-openvpn-profile",
                    )
                report = VPNExecutionReport(
                    action=plan.action,
                    client_name=client_name,
                    status="COMPLETED",
                    message=("Perfil OpenVPN criado e anexado ao chamado."
                             if result.get("created") else
                             "O perfil OpenVPN já existia e foi anexado novamente ao chamado."),
                    attachment_name=filename,
                    document_id=int(document_id),
                )
                self.repository.update_vpn_operation(
                    operation_id, "COMPLETED", report=report, document_id=int(document_id)
                )
                return report
            if plan.action == VPNAction.REVOKE:
                result = await asyncio.to_thread(self.vpn_client.revoke, client_name)
                report = VPNExecutionReport(
                    action=plan.action,
                    client_name=client_name,
                    status="COMPLETED",
                    message=("Perfil OpenVPN revogado e CRL atualizada."
                             if result.get("revoked") else
                             "O perfil OpenVPN já estava revogado; a CRL foi atualizada."),
                )
            elif plan.action == VPNAction.STATUS:
                result = await asyncio.to_thread(self.vpn_client.status, client_name)
                report = VPNExecutionReport(
                    action=plan.action,
                    client_name=client_name,
                    status="COMPLETED",
                    message=f"Estado atual do perfil OpenVPN: {result.get('status', 'desconhecido')}.",
                )
            elif plan.action == VPNAction.LIST:
                clients = await asyncio.to_thread(self.vpn_client.list_clients)
                details = [
                    f"{item.get('name', 'sem nome')}: {item.get('status', 'desconhecido')}"
                    for item in clients[:50]
                ]
                if len(clients) > 50:
                    details.append(f"Mais {len(clients) - 50} perfil(is) não exibido(s).")
                report = VPNExecutionReport(
                    action=plan.action,
                    status="COMPLETED",
                    message=f"Foram encontrados {len(clients)} perfil(is) OpenVPN.",
                    details=details,
                )
            else:
                raise ValueError("ação VPN inválida para execução")
            self.repository.update_vpn_operation(operation_id, "COMPLETED", report=report)
            return report
        except Exception as exc:
            self.repository.update_vpn_operation(
                operation_id, "FAILED", error=f"{type(exc).__name__}: {str(exc)[:300]}"
            )
            raise

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
                "localizacao_id": ticket.location_id,
                "mensagem_gatilho": event.mention.normalized_content,
                "autor_gatilho_id": event.author_id,
                "acompanhamento_gatilho_id": event.followup_id,
            }
            try:
                if (self.settings.access_enabled and self._is_access_candidate(context)
                        and self.access_action_planner is not None):
                    access_plan, run_id = await self.access_action_planner.plan(context)
                    if access_plan.action != AccessAction.NONE:
                        report = await self._execute_access_action(
                            plan=access_plan, event=event, analysis_id=analysis_id,
                            ticket=ticket, entity_id=entity_id,
                        )
                    else:
                        report, run_id = await self._analyze_response(context, analysis_id)
                elif (self.settings.vpn_enabled and self._is_vpn_candidate(context)
                        and self.action_planner is not None):
                    plan, run_id = await self.action_planner.plan(context)
                    if plan.action != VPNAction.NONE:
                        report = await self._execute_vpn_action(
                            plan=plan, event=event, analysis_id=analysis_id,
                            ticket=ticket, entity_id=entity_id,
                        )
                    else:
                        report, run_id = await self._analyze_response(context, analysis_id)
                else:
                    report, run_id = await self._analyze_response(context, analysis_id)
                if isinstance(report, GovernanceReport):
                    report = apply_rules(report)
                self.repository.save_result(analysis_id, report, run_id)
                if self.settings.dry_run:
                    log.info("result_saved_dry_run", analysis_id=analysis_id, ticket_id=ticket_id,
                             result_type=type(report).__name__)
                else:
                    if isinstance(report, ResponseSuggestion):
                        body = format_suggestion_for_publication(report)
                    elif isinstance(report, AccessExecutionReport):
                        body = format_access_report(report)
                    elif isinstance(report, VPNExecutionReport):
                        body = format_vpn_report(report)
                    else:
                        body = format_report(report)
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
            except Exception as exc:  # noqa: BLE001 - isola falhas por evento e persiste retry
                retryable = not isinstance(exc, (OllamaConfigurationError, PermissionError, ValueError))
                cause = f"{type(exc).__name__}: {str(exc)[:300]}"
                self.repository.fail(analysis_id, cause, retryable=retryable,
                                     retry_delay=self.settings.retry_delay)
                row = self.repository.get(analysis_id)
                log.error("analysis_failed", analysis_id=analysis_id, cause=cause,
                          retryable=retryable, attempt=row["attempt_count"] if row else None,
                          max_attempts=self.settings.max_attempts)
        return results

    async def _analyze_response(self, context: dict[str, Any], analysis_id: int) -> tuple[Any, str | None]:
        route_context = {
            "titulo": context.get("titulo", ""),
            "descricao": context.get("descricao", ""),
            "categoria": context.get("categoria", ""),
        }
        route = self.repository.resolve_agent(route_context)
        agent = self.agent
        prompt = ""
        skill_results: list[dict[str, Any]] = []
        if route:
            if route["provider"] != "ollama":
                raise ValueError("o roteamento administrativo ainda aceita somente modelos Ollama")
            agent = OllamaSuggestionAgent(
                route["base_url"] or self.settings.ollama_url,
                route["model_name"], route["timeout"],
            )
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
            return await agent.analyze_task(context, prompt, skill_results)
        return await agent.analyze(context)

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
            except ValidationError:
                try:
                    result = AccessExecutionReport.model_validate_json(row["report"])
                    body = format_access_report(result)
                except ValidationError:
                    try:
                        result = VPNExecutionReport.model_validate_json(row["report"])
                        body = format_vpn_report(result)
                    except ValidationError:
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
                cursor_key = f"glpi_ticket_cursor:{entity_id}"
                stored_cursor = (self.repository.get_state(cursor_key)
                                 or self.repository.latest_detection_time(entity_id))
                since = (datetime.fromisoformat(stored_cursor) if stored_cursor else
                         started - timedelta(hours=self.settings.glpi_initial_lookback_hours))
                if since.tzinfo is not None:
                    since = since.astimezone()
                since -= timedelta(seconds=self.settings.glpi_poll_overlap_seconds)
                ticket_ids = self.client.list_modified_ticket_ids(
                    entity_id, since.strftime("%Y-%m-%d %H:%M:%S")
                )
                for ticket_id in ticket_ids:
                    checked += 1
                    try:
                        new_mentions += len(await self.analyze_ticket(ticket_id, entity_id))
                    except Exception as exc:  # noqa: BLE001 - um chamado não bloqueia o lote
                        log.warning("ticket_query_failed", ticket_id=ticket_id, entity_id=entity_id,
                                    cause=f"{type(exc).__name__}: {str(exc)[:300]}")
                self.repository.set_state(cursor_key, started.isoformat())
                log.info("poll_completed", entity_id=entity_id, tickets_checked=checked,
                         new_mentions=new_mentions,
                         duration_ms=round((datetime.now().astimezone() - started).total_seconds() * 1000),
                         glpi_health="ok")
            except Exception as exc:  # noqa: BLE001 - worker resiliente de longa duração
                log.error("poll_failed", entity_id=entity_id, tickets_checked=checked,
                          cause=f"{type(exc).__name__}: {str(exc)[:300]}", glpi_health="failed")
            await asyncio.sleep(self.settings.poll_interval)
