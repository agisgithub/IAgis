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
from .report_formatter import format_report
from .repository import Repository
from .worker import PublicationDenied, Worker

app = typer.Typer(help="IAgis Agent — homologação documental e de governança")


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
        analysis_id, report = asyncio.run(Worker(settings, client, repository).analyze_ticket(ticket_id, entity_id))
    if report is None:
        typer.echo("Nenhuma menção nova encontrada (ou conteúdo já processado).")
        return
    typer.echo(f"analysis_id={analysis_id}")
    typer.echo(format_report(report))


@app.command()
def preview(analysis_id: int = typer.Option(...)) -> None:
    row = Repository(_settings().database_path).get(analysis_id)
    if row is None or not row["report"]:
        raise typer.BadParameter("análise não encontrada ou ainda sem relatório")
    typer.echo(format_report(GovernanceReport.model_validate_json(row["report"])))


@app.command()
def publish(analysis_id: int = typer.Option(...), confirm: bool = typer.Option(False, "--confirm")) -> None:
    settings = _settings()
    repository = Repository(settings.database_path)
    row = repository.get(analysis_id)
    if row is None or not row["report"]:
        raise typer.BadParameter("análise não encontrada ou ainda sem relatório")
    typer.echo(format_report(GovernanceReport.model_validate_json(row["report"])))
    if not confirm:
        typer.echo("Prévia somente. Use --confirm para solicitar publicação.")
        return
    try:
        with _client(settings) as client:
            followup_id = Worker(settings, client, repository).publish(analysis_id, confirm=True)
        typer.echo(f"Acompanhamento publicado: {followup_id}")
    except PublicationDenied as exc:
        typer.echo(f"Publicação bloqueada: {exc}", err=True)
        raise typer.Exit(2) from exc


@app.command()
def worker(entity_id: int = typer.Option(...)) -> None:
    settings = _settings()
    repository = Repository(settings.database_path)
    with _client(settings) as client:
        asyncio.run(Worker(settings, client, repository).run_forever(entity_id))


if __name__ == "__main__":
    app()
