"""CLI administrativa do IAgis."""
from __future__ import annotations

import asyncio
import json
from contextlib import contextmanager
from typing import Iterator

import typer

from .config import Settings, get_settings
from .glpi_client import GLPIClient
from .governance_models import GovernanceReport
from .logging_config import configure_logging
from .report_formatter import format_report, format_suggestion
from .repository import Repository
from .suggestion_models import ResponseSuggestion
from .worker import PublicationDenied, Worker

app = typer.Typer(help="IAgis Agent — sugestões de atendimento para revisão humana")


def _settings() -> Settings:
    return get_settings()


@contextmanager
def _client(settings: Settings) -> Iterator[GLPIClient]:
    with GLPIClient(settings.glpi_url, settings.glpi_app_token.get_secret_value(),
                    settings.glpi_user_token.get_secret_value()) as client:
        yield client


@app.callback()
def callback() -> None:
    configure_logging()


@app.command()
def check(entity_id: int = typer.Option(0, help="Entidade explícita (0 para sessão raiz)")) -> None:
    """Valida configuração e conexão sem exibir segredos."""
    settings = _settings()
    with _client(settings) as client:
        client.check(entity_id)
    typer.echo("Configuração presente e conexão GLPI válida (segredos não exibidos).")


@app.command()
def entities(entity_id: int = typer.Option(0, help="Entidade explícita de contexto")) -> None:
    settings = _settings()
    with _client(settings) as client:
        for entity in client.list_entities(entity_id):
            typer.echo(f"{entity.id}\t{entity.name}")


@app.command()
def ticket(ticket_id: int = typer.Option(...), entity_id: int = typer.Option(...)) -> None:
    settings = _settings()
    with _client(settings) as client:
        item = client.get_ticket(ticket_id, entity_id)
        # Exibe campos operacionais; nunca cabeçalhos ou credenciais.
        typer.echo(json.dumps(item.model_dump(exclude={"raw"}), ensure_ascii=False, indent=2))


@app.command()
def analyze(ticket_id: int = typer.Option(...), entity_id: int = typer.Option(...)) -> None:
    settings = _settings()
    repository = Repository(settings.database_path)
    with _client(settings) as client:
        results = asyncio.run(Worker(settings, client, repository).analyze_ticket(ticket_id, entity_id))
    if not results:
        typer.echo("Nenhuma menção nova encontrada (ou eventos ainda aguardam retry).")
        return
    for analysis_id, result in results:
        typer.echo(f"analysis_id={analysis_id}")
        typer.echo(format_suggestion(result) if isinstance(result, ResponseSuggestion) else format_report(result))


@app.command()
def preview(analysis_id: int = typer.Option(...)) -> None:
    row = Repository(_settings().database_path).get(analysis_id)
    if row is None or not row["report"]:
        raise typer.BadParameter("análise não encontrada ou ainda sem relatório")
    try:
        result = ResponseSuggestion.model_validate_json(row["report"])
        typer.echo(format_suggestion(result))
    except Exception:
        typer.echo(format_report(GovernanceReport.model_validate_json(row["report"])))


@app.command()
def publish(analysis_id: int = typer.Option(...), confirm: bool = typer.Option(False, "--confirm")) -> None:
    """Mantido por compatibilidade, mas bloqueado integralmente no piloto."""
    settings = _settings()
    try:
        Worker(settings, None, Repository(settings.database_path)).publish(analysis_id, confirm=confirm)  # type: ignore[arg-type]
    except PublicationDenied as exc:
        typer.echo(f"Publicação bloqueada: {exc}", err=True)
        raise typer.Exit(2) from exc


@app.command("analyses")
def analyses_command(limit: int = typer.Option(20, min=1, max=200)) -> None:
    """Lista análises recentes sem exibir o conteúdo dos chamados."""
    rows = Repository(_settings().database_path).recent(limit)
    typer.echo("ID\tCHAMADO\tENTIDADE\tESTADO\tTENTATIVAS\tHORÁRIO\tERRO")
    for row in rows:
        typer.echo(f"{row['id']}\t{row['ticket_id']}\t{row['entity_id']}\t{row['state']}\t"
                   f"{row['attempt_count']}\t{row['detected_at']}\t{row['error'] or '-'}")


@app.command()
def retry(analysis_id: int = typer.Option(...)) -> None:
    """Tenta novamente, uma vez, uma análise FAILED dentro do limite configurado."""
    settings = _settings()
    repository = Repository(settings.database_path)
    with _client(settings) as client:
        results = asyncio.run(Worker(settings, client, repository).retry_analysis(analysis_id))
    if not results:
        typer.echo("Retry preparado, mas o evento não ficou elegível; consulte 'analyses'.")
        raise typer.Exit(2)
    typer.echo(f"Análise {analysis_id} processada novamente.")

@app.command()
def worker(entity_id: int = typer.Option(...)) -> None:
    settings = _settings()
    repository = Repository(settings.database_path)
    with _client(settings) as client:
        asyncio.run(Worker(settings, client, repository).run_forever(entity_id))


@app.command()
def admin() -> None:
    """Inicia a página administrativa no endereço restrito configurado."""
    import uvicorn
    settings = _settings()
    if not settings.admin_password.get_secret_value():
        raise typer.BadParameter("IAGIS_ADMIN_PASSWORD é obrigatória")
    uvicorn.run("iagis.admin:app", host=settings.admin_host, port=settings.admin_port,
                log_level="info", access_log=False)


if __name__ == "__main__":
    app()
