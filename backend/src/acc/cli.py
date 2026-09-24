"""`acc` command-line interface (runs inside the api container or locally)."""

import asyncio
import json
import sys
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Annotated

import typer
from pydantic import SecretStr

from acc.config import Settings, get_settings
from acc.db.session import SessionFactory, create_engine, create_session_factory
from acc.domain.errors import DomainError
from acc.logging import configure_logging

app = typer.Typer(no_args_is_help=True, add_completion=False)


@app.callback()
def main() -> None:
    """AI Command Center administration."""


def _run_with_db[T](fn: Callable[[SessionFactory, Settings], Awaitable[T]]) -> T:
    """Run an async function against the runtime database, then dispose the engine."""
    settings = get_settings()
    configure_logging(settings.log_level, json=settings.json_logs)

    async def run() -> T:
        engine = create_engine(settings.database_url.get_secret_value())
        try:
            return await fn(create_session_factory(engine), settings)
        finally:
            await engine.dispose()

    try:
        return asyncio.run(run())
    except DomainError as exc:
        typer.echo(f"error: {exc.message}", err=True)
        raise typer.Exit(1) from exc


@app.command("create-operator")
def create_operator(
    email: Annotated[str, typer.Option("--email", help="Operator login email")],
    name: Annotated[str, typer.Option("--name", help="Display name (defaults to email)")] = "",
    password_stdin: Annotated[
        bool, typer.Option("--password-stdin", help="Read the password from stdin")
    ] = False,
) -> None:
    """Create the operator account. There is no sign-up page by design."""
    from acc.commands.auth import create_operator as create

    password = (
        sys.stdin.readline().rstrip("\n")
        if password_stdin
        else typer.prompt("Password", hide_input=True, confirmation_prompt=True)
    )
    user_id = _run_with_db(
        lambda sf, _: create(sf, email=email, display_name=name, password=password)
    )
    typer.echo(f"Created operator {email} ({user_id})")


@app.command("seed")
def seed() -> None:
    """Create the default project and agents if missing (idempotent; never edits rows)."""
    from acc.commands.seed import seed as run_seed

    result = _run_with_db(lambda sf, _: run_seed(sf))
    created = [*result.created_projects, *result.created_agents]
    typer.echo(f"Seed: created {', '.join(created)}" if created else "Seed: nothing to do")


@app.command("ensure-partitions")
def ensure_partitions(
    months_ahead: Annotated[int, typer.Option("--months-ahead", min=0, max=24)] = 3,
) -> None:
    """Create missing monthly partitions of the events table."""
    from acc.commands.maintenance import ensure_event_partitions

    created = _run_with_db(lambda sf, _: ensure_event_partitions(sf, months_ahead))
    typer.echo(f"Event partitions: {created} created")


@app.command("openapi")
def openapi(
    output: Annotated[
        Path | None, typer.Option("--output", "-o", help="Write to a file instead of stdout")
    ] = None,
) -> None:
    """Print the OpenAPI schema. Needs no database or Redis."""
    from acc.api.app import create_app

    placeholder = Settings(
        env="development",
        database_url=SecretStr("postgresql+asyncpg://unused@localhost/unused"),
        redis_url=SecretStr("redis://localhost"),
        session_secret=SecretStr("x" * 32),
        log_level="WARNING",
    )
    schema = json.dumps(create_app(placeholder).openapi(), indent=2, sort_keys=True) + "\n"
    if output:
        output.write_text(schema)
    else:
        sys.stdout.write(schema)


if __name__ == "__main__":
    app()
