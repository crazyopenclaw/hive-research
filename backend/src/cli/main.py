"""
CLI entrypoint — Typer application for the Research Institute of Squids.

Usage:
    squid research "What mechanisms drive X?" --sources paper.pdf --agents 3
    squid hypotheses
    squid graph
    squid graph --export graph.json
"""

import asyncio
import json
import socket
import subprocess
import time
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

import typer
from rich.console import Console
from neo4j.exceptions import AuthError, ServiceUnavailable
from sqlalchemy.exc import OperationalError

from src.api.service import ResearchService
from src.cli.display import (
    console,
    display_event,
    display_graph_summary,
    display_hypotheses,
    display_report,
    start_display,
    stop_display,
    _short,
)
from src.config import settings
from rich.table import Table
from rich.panel import Panel

app = typer.Typer(
    name="squid",
    help="Research Institute of Squids — AI-powered multi-agent research system.",
    no_args_is_help=True,
)
WORKSPACE_ROOT = Path(__file__).resolve().parents[3]


def _run_async(coro):
    """Run an async coroutine in the event loop."""
    return asyncio.run(coro)


def _port_open(host: str, port: int, timeout: float = 1.0) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def _service_targets():
    neo4j = urlparse(settings.neo4j_uri)
    neo4j_host = neo4j.hostname or "localhost"
    neo4j_port = neo4j.port or 7687

    db_url = settings.database_url.replace("postgresql+asyncpg://", "postgresql://", 1)
    postgres = urlparse(db_url)
    pg_host = postgres.hostname or "localhost"
    pg_port = postgres.port or 5432

    return {
        "neo4j": (neo4j_host, neo4j_port),
        "postgres": (pg_host, pg_port),
    }


def _print_service_help() -> None:
    console.print()
    console.print("[yellow]Local research services are not reachable.[/]")
    console.print("[dim]Start the local databases first:[/]")
    console.print("[dim]  python scripts/dev.py[/]")
    console.print()
    console.print("[dim]Or start only the databases:[/]")
    console.print("[dim]  docker compose -f docker-compose.yml -f docker-compose.dev.yml up -d neo4j postgres[/]")
    console.print()


def _docker_available() -> bool:
    try:
        result = subprocess.run(
            ["docker", "--version"],
            cwd=str(WORKSPACE_ROOT),
            capture_output=True,
            text=True,
        )
        return result.returncode == 0
    except OSError:
        return False


def _neo4j_ready() -> bool:
    from neo4j import GraphDatabase

    driver = GraphDatabase.driver(
        settings.neo4j_uri,
        auth=(settings.neo4j_user, settings.neo4j_password),
    )
    try:
        driver.verify_connectivity()
        return True
    except Exception:
        return False
    finally:
        driver.close()


def _start_local_services() -> bool:
    console.print("[dim]Starting local Neo4j/Postgres services for CLI...[/]")
    result = subprocess.run(
        [
            "docker",
            "compose",
            "-f",
            str(WORKSPACE_ROOT / "docker-compose.yml"),
            "-f",
            str(WORKSPACE_ROOT / "docker-compose.dev.yml"),
            "up",
            "-d",
            "neo4j",
            "postgres",
        ],
        cwd=str(WORKSPACE_ROOT),
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        if result.stdout.strip():
            console.print(result.stdout.strip())
        if result.stderr.strip():
            console.print(result.stderr.strip())
        return False

    deadline = time.time() + 90
    postgres_host, postgres_port = _service_targets()["postgres"]
    while time.time() < deadline:
        if _neo4j_ready() and _port_open(postgres_host, postgres_port):
            return True
        time.sleep(2)
    return False


def _ensure_sandbox_image() -> None:
    inspect = subprocess.run(
        ["docker", "image", "inspect", "squid-sandbox:latest"],
        cwd=str(WORKSPACE_ROOT),
        capture_output=True,
        text=True,
    )
    if inspect.returncode == 0:
        return

    console.print("[dim]Building sandbox image for experiments...[/]")
    result = subprocess.run(
        [
            "docker",
            "build",
            "-t",
            "squid-sandbox:latest",
            "-f",
            str(WORKSPACE_ROOT / "backend" / "Dockerfile.sandbox"),
            str(WORKSPACE_ROOT / "backend"),
        ],
        cwd=str(WORKSPACE_ROOT),
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        console.print("[yellow]Warning: could not build sandbox image. Experiments may fail.[/]")
        if result.stdout.strip():
            console.print(result.stdout.strip())
        if result.stderr.strip():
            console.print(result.stderr.strip())
        return
    console.print("[dim]Sandbox image ready.[/]")


def _preflight_local_services() -> None:
    targets = _service_targets()
    missing = []
    for name, (host, port) in targets.items():
        if host in {"localhost", "127.0.0.1", "::1"} and not _port_open(host, port):
            missing.append((name, host, port))
    if not missing:
        if _docker_available():
            _ensure_sandbox_image()
        return

    if _docker_available():
        if _start_local_services():
            _ensure_sandbox_image()
            return

    console.print("[red]Error: required local services are not running:[/]")
    for name, host, port in missing:
        console.print(f"[dim]  - {name}: {host}:{port}[/]")
    _print_service_help()
    raise typer.Exit(code=1)


@app.command()
def research(
    question: str = typer.Argument(..., help="The research question to investigate."),
    sources: Optional[list[str]] = typer.Option(
        None, "--sources", "-s",
        help="Source files (PDF, text) or URLs to ingest.",
    ),
    agents: int = typer.Option(
        settings.default_agents, "--agents", "-a",
        help="Number of squid agents to spawn.",
    ),
    budget: float = typer.Option(
        settings.default_budget_usd, "--budget", "-b",
        help="Maximum LLM budget in USD.",
    ),
    iterations: int = typer.Option(
        settings.default_iterations, "--iterations", "-i",
        help="Maximum research iterations.",
    ),
) -> None:
    """Start a new research investigation."""

    async def _run():
        service = ResearchService()

        # Subscribe to events for live display
        service.event_bus.subscribe("*", display_event)

        try:
            _preflight_local_services()
            await service.initialize()
            console.print(f"\n[dim]Budget: ${budget:.2f} | Agents: {agents} | Max iterations: {iterations}[/]\n")

            start_display()

            result = await service.start_research(
                question=question,
                sources=sources or [],
                num_agents=agents,
                budget_usd=budget,
                max_iterations=iterations,
            )

            # Display the final report
            events = result.get("events", [])
            for event in events:
                if event.get("type") == "final_report":
                    display_report(event.get("content", ""))

        except KeyboardInterrupt:
            console.print("\n[yellow]Research interrupted by user.[/]")
        except AuthError:
            console.print("\n[red]Neo4j authentication failed.[/]")
            console.print("[dim]Check NEO4J_URI / NEO4J_USER / NEO4J_PASSWORD in the repo-root .env.[/]")
            raise typer.Exit(code=1)
        except ServiceUnavailable:
            console.print("\n[red]Neo4j is not reachable.[/]")
            _print_service_help()
            raise typer.Exit(code=1)
        except OperationalError:
            console.print("\n[red]Postgres is not reachable.[/]")
            _print_service_help()
            raise typer.Exit(code=1)
        except Exception as e:
            console.print(f"\n[red]Error: {e}[/]")
            raise
        finally:
            stop_display()
            await service.shutdown()

    _run_async(_run())


@app.command()
def hypotheses(
    status: str = typer.Option("active", "--status", help="Filter by status."),
) -> None:
    """List all hypotheses in the knowledge graph."""

    async def _run():
        service = ResearchService()
        try:
            await service.initialize()
            hyps = await service.get_hypotheses(status=status)
            if hyps:
                display_hypotheses(hyps)
            else:
                console.print("[dim]No hypotheses found.[/]")
        finally:
            await service.shutdown()

    _run_async(_run())


@app.command()
def graph(
    export: Optional[str] = typer.Option(
        None, "--export", "-e",
        help="Export graph to JSON file.",
    ),
) -> None:
    """Show knowledge graph summary or export."""

    async def _run():
        service = ResearchService()
        try:
            await service.initialize()
            data = await service.get_graph_export()

            if export:
                path = Path(export)
                path.write_text(json.dumps(data, indent=2, default=str))
                console.print(f"[green]Graph exported to {export}[/]")
            else:
                display_graph_summary(data)
        finally:
            await service.shutdown()

    _run_async(_run())


@app.command()
def stats() -> None:
    """Show coverage statistics for the knowledge graph."""

    async def _run():
        service = ResearchService()
        try:
            await service.initialize()
            coverage = await service.get_coverage_stats()
            console.print_json(json.dumps(coverage, default=str))
        finally:
            await service.shutdown()

    _run_async(_run())


@app.command()
def version() -> None:
    """Show version information."""
    console.print("Research Institute of Squids v0.1.0")


# ── Workspace commands ──────────────────────────────────────────────────

workspace_app = typer.Typer(help="Inspect agent workspaces")
app.add_typer(workspace_app, name="workspace")


@workspace_app.command("list")
def workspace_list(
    session: str = typer.Option(..., "--session", "-s", help="Session ID"),
) -> None:
    """List all agent workspaces for a session."""
    async def _run():
        service = ResearchService()
        try:
            await service.initialize()
            workspaces = await service.list_workspaces(session)
            if not workspaces:
                console.print("[dim]No workspaces found for this session.[/]")
                return
            
            table = Table(title=f"Workspaces for session {session[:8]}...")
            table.add_column("Agent", style="cyan")
            table.add_column("Path", style="dim")
            table.add_column("Files", justify="right")
            table.add_column("Size", justify="right")
            
            for ws in workspaces:
                table.add_row(
                    ws.get("agent_id", "?")[:16],
                    str(ws.get("path", ""))[-40:],
                    str(ws.get("file_count", 0)),
                    f"{ws.get('size_kb', 0):.1f} KB",
                )
            console.print(table)
        finally:
            await service.shutdown()
    
    _run_async(_run())


@workspace_app.command("cat")
def workspace_cat(
    session: str = typer.Option(..., "--session", "-s", help="Session ID"),
    agent: str = typer.Option(..., "--agent", "-a", help="Agent ID"),
    path: str = typer.Argument(..., help="File path within workspace"),
) -> None:
    """Display a file from an agent's workspace."""
    async def _run():
        service = ResearchService()
        try:
            await service.initialize()
            content = await service.read_workspace_file(session, agent, path)
            console.print(Panel(
                content,
                title=f"{agent}: {path}",
                border_style="blue",
            ))
        except FileNotFoundError:
            console.print(f"[red]File not found: {path}[/]")
        finally:
            await service.shutdown()
    
    _run_async(_run())


@workspace_app.command("ls")
def workspace_ls(
    session: str = typer.Option(..., "--session", "-s", help="Session ID"),
    agent: str = typer.Option(..., "--agent", "-a", help="Agent ID"),
    path: str = typer.Option("", "--path", "-p", help="Directory path within workspace"),
) -> None:
    """List files in an agent's workspace."""
    async def _run():
        service = ResearchService()
        try:
            await service.initialize()
            files = await service.list_workspace_files(session, agent, path)
            if not files:
                console.print("[dim]No files found.[/]")
                return
            
            for f in files:
                size = f.get('size_kb', 0)
                size_str = f"{size:.1f} KB" if size else ""
                console.print(f"  {f.get('path', ''):<50} [dim]{size_str}[/]")
        finally:
            await service.shutdown()
    
    _run_async(_run())


@workspace_app.command("memory")
def workspace_memory(
    session: str = typer.Option(..., "--session", "-s", help="Session ID"),
    agent: str = typer.Option(..., "--agent", "-a", help="Agent ID"),
    tail: int = typer.Option(50, "--tail", "-n", help="Number of lines to show"),
) -> None:
    """Display an agent's memory.md file."""
    async def _run():
        service = ResearchService()
        try:
            await service.initialize()
            content = await service.read_workspace_file(session, agent, "memory.md")
            lines = content.split("\n")
            if tail and len(lines) > tail:
                content = "\n".join(lines[-tail:])
                header = f"... ({len(lines) - tail} lines above) ...\n"
                content = header + content
            console.print(Panel(
                content,
                title=f"🧠 memory.md",
                border_style="green",
            ))
        except FileNotFoundError:
            console.print("[red]memory.md not found[/]")
        finally:
            await service.shutdown()
    
    _run_async(_run())


@workspace_app.command("opencode")
def workspace_opencode(
    session: str = typer.Option(..., "--session", "-s", help="Session ID"),
    agent: str = typer.Option(..., "--agent", "-a", help="Agent ID"),
) -> None:
    """Display OpenCode session history for an agent."""
    async def _run():
        service = ResearchService()
        try:
            await service.initialize()
            sessions = await service.get_opencode_sessions(session, agent)
            if not sessions:
                console.print("[dim]No OpenCode sessions found.[/]")
                return
            
            table = Table(title=f"OpenCode Sessions for {agent}")
            table.add_column("Topic", style="cyan")
            table.add_column("Status", style="bold")
            table.add_column("Turns", justify="right")
            table.add_column("Cost", justify="right")
            table.add_column("Files", style="dim")
            
            for s in sessions:
                status_color = {
                    "completed": "green",
                    "failed": "red",
                    "abandoned": "yellow",
                    "output_limit_reached": "magenta",
                }.get(s.get('status', ''), 'white')
                table.add_row(
                    _short(s.get('topic', ''), 40),
                    f"[{status_color}]{s.get('status', '?')}[/]",
                    str(s.get('turn_count', 0)),
                    f"${s.get('total_cost_usd', 0):.4f}",
                    str(len(s.get('files_produced', []))),
                )
            console.print(table)
        finally:
            await service.shutdown()
    
    _run_async(_run())


if __name__ == "__main__":
    app()
