"""Command-line entry points for database, crawling, API, and scheduling."""

from __future__ import annotations

import asyncio
import inspect
import json
from collections.abc import Callable
from importlib import import_module
from pathlib import Path
from typing import Annotated, Any

import typer

from .settings import Settings, get_settings


app = typer.Typer(
    name="dmp",
    help="Collect, normalize, and serve data-market catalog content.",
    no_args_is_help=True,
    add_completion=False,
)


def _load_callable(module_name: str, function_name: str) -> Callable[..., Any]:
    """Import an optional application component only when its command runs."""

    qualified_module = f"{__package__}.{module_name}"
    try:
        module = import_module(qualified_module)
        function = getattr(module, function_name)
    except (ImportError, AttributeError) as exc:
        typer.secho(
            f"Command component is unavailable: {qualified_module}.{function_name}. "
            "Install the required extra or add the application module first.",
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(code=2) from exc

    if not callable(function):
        typer.secho(
            f"Command component is not callable: {qualified_module}.{function_name}",
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(code=2)
    return function


def _invoke(function: Callable[..., Any], /, **kwargs: Any) -> Any:
    """Invoke either a synchronous or asynchronous application component."""

    result = function(**kwargs)
    if inspect.isawaitable(result):
        return asyncio.run(result)
    return result


def _settings() -> Settings:
    """Load settings after Typer has accepted the selected command."""

    return get_settings()


def _json_ready(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    return value


@app.command("init-db")
def init_db(
    drop_existing: Annotated[
        bool,
        typer.Option(
            "--drop-existing",
            help="Drop existing catalog tables before recreating them.",
        ),
    ] = False,
) -> None:
    """Create database tables and indexes.

    Expected component: ``database.init_database(settings, drop_existing)``.
    """

    initializer = _load_callable("database", "init_database")
    _invoke(initializer, settings=_settings(), drop_existing=drop_existing)
    typer.echo("Database initialization completed.")


@app.command("bootstrap")
def bootstrap() -> None:
    """Initialize schema, 38 sources, default schedules, mappings and admin user."""

    initializer = _load_callable("bootstrap", "ensure_defaults")
    result = _invoke(initializer, settings=_settings())
    typer.echo(json.dumps(_json_ready(result), ensure_ascii=False, default=str))


@app.command("archive")
def archive(
    limit: Annotated[int, typer.Option(min=1, max=10000)] = 500,
) -> None:
    """Move expired online raw snapshots to the configured archive volume."""

    runner = _load_callable("archive", "archive_expired_snapshots")
    result = _invoke(runner, settings=_settings(), limit=limit)
    typer.echo(json.dumps(_json_ready(result), ensure_ascii=False, default=str))


@app.command("seed")
def seed(
    source: Annotated[
        Path | None,
        typer.Option(
            "--source",
            "-s",
            exists=True,
            dir_okay=False,
            readable=True,
            resolve_path=True,
            help="Optional CSV or JSON platform registry.",
        ),
    ] = None,
    update_existing: Annotated[
        bool,
        typer.Option(
            "--update-existing/--insert-only",
            help="Update matching platform records while seeding.",
        ),
    ] = True,
) -> None:
    """Seed the platform registry.

    Expected component: ``seed.seed_platforms(settings, source, update_existing)``.
    """

    seeder = _load_callable("seed", "seed_platforms")
    result = _invoke(
        seeder,
        settings=_settings(),
        source=source,
        update_existing=update_existing,
    )
    if result is not None:
        typer.echo(json.dumps(_json_ready(result), ensure_ascii=False, default=str))


@app.command("crawl")
def crawl(
    platform_ids: Annotated[
        list[str] | None,
        typer.Option(
            "--platform",
            "-p",
            help="Platform ID to crawl; repeat the option for multiple platforms.",
        ),
    ] = None,
    full: Annotated[
        bool,
        typer.Option(
            "--full/--incremental",
            help="Force a full crawl instead of the default incremental crawl.",
        ),
    ] = False,
    max_pages: Annotated[
        int | None,
        typer.Option(
            "--max-pages",
            min=1,
            help="Override the configured per-platform page limit for this run.",
        ),
    ] = None,
) -> None:
    """Run a crawl immediately.

    Expected component: ``crawler.run_crawl(settings, platform_ids, full,
    max_pages)``. The component may be synchronous or asynchronous.
    """

    runner = _load_callable("crawler", "run_crawl")
    result = _invoke(
        runner,
        settings=_settings(),
        platform_ids=platform_ids,
        full=full,
        max_pages=max_pages,
    )
    if result is not None:
        typer.echo(json.dumps(_json_ready(result), ensure_ascii=False, default=str))


@app.command("serve")
def serve(
    host: Annotated[str | None, typer.Option(help="API bind address.")] = None,
    port: Annotated[
        int | None,
        typer.Option(min=1, max=65_535, help="API bind port."),
    ] = None,
    reload: Annotated[
        bool | None,
        typer.Option("--reload/--no-reload", help="Enable development auto-reload."),
    ] = None,
) -> None:
    """Start the HTTP API.

    Expected component: ``api.create_app() -> FastAPI``.
    """

    create_app = _load_callable("api", "create_app")
    settings = _settings()
    should_reload = settings.api_reload if reload is None else reload

    import uvicorn

    if should_reload:
        target: Any = "data_market_probe.api:create_app"
    else:
        target = create_app()
    uvicorn.run(
        target,
        factory=should_reload,
        host=host or settings.api_host,
        port=port or settings.api_port,
        reload=should_reload,
        log_level=settings.log_level.lower(),
    )


@app.command("schedule")
def schedule(
    run_now: Annotated[
        bool,
        typer.Option(help="Run one incremental crawl before waiting for the schedule."),
    ] = False,
) -> None:
    """Start the foreground daily scheduler.

    Expected component: ``scheduler.run_scheduler(settings, run_now)``.
    """

    runner = _load_callable("scheduler", "run_scheduler")
    settings = _settings()
    typer.echo(f"Starting daily scheduler at {settings.schedule_label}.")
    _invoke(runner, settings=settings, run_now=run_now)


@app.command("status")
def status(
    pretty: Annotated[
        bool,
        typer.Option("--pretty/--compact", help="Pretty-print the status JSON."),
    ] = True,
) -> None:
    """Show database, crawl, and scheduler status.

    Expected component: ``status.get_status(settings)``.
    """

    getter = _load_callable("status", "get_status")
    result = _invoke(getter, settings=_settings())
    typer.echo(
        json.dumps(
            _json_ready(result),
            ensure_ascii=False,
            default=str,
            indent=2 if pretty else None,
        )
    )


@app.command("acceptance-report")
def acceptance_report(
    output: Annotated[
        Path | None,
        typer.Option("--output", "-o", help="Optional JSON report destination."),
    ] = None,
) -> None:
    """Generate the per-platform, per-collection completion matrix."""

    builder = _load_callable("reporting", "build_acceptance_report")
    result = _invoke(builder, settings=_settings(), output=output)
    typer.echo(json.dumps(_json_ready(result), ensure_ascii=False, default=str, indent=2))


def main() -> None:
    """Run the Typer application."""

    app()


if __name__ == "__main__":
    main()
