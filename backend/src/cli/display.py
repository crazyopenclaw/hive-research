"""Rich console display helpers for the CLI."""

from __future__ import annotations

import json

from rich.console import Console
from rich.live import Live
from rich.panel import Panel
from rich.table import Table

from src.models.events import Event, EventType

console = Console()

EVENT_COLORS = {
    EventType.RESEARCH_STARTED: "bold green",
    EventType.RESEARCH_COMPLETED: "bold green",
    EventType.AGENT_SPAWNED: "cyan",
    EventType.AGENT_THINKING: "dim cyan",
    EventType.AGENT_ACTION: "blue",
    EventType.SOURCE_INGESTED: "yellow",
    EventType.SOURCE_DISCOVERED: "yellow",
    EventType.EXPERIMENT_STARTED: "magenta",
    EventType.EXPERIMENT_COMPLETED: "green",
    EventType.EXPERIMENT_FAILED: "red",
    EventType.ARTIFACT_CREATED: "dim white",
    EventType.ARTIFACT_REFUTED: "bold red",
    EventType.RELATION_CREATED: "dim blue",
    EventType.MESSAGE_SENT: "dim cyan",
    EventType.ITERATION_STARTED: "bold white",
    EventType.ITERATION_COMPLETED: "bold white",
    EventType.DEBATE_STARTED: "bold magenta",
    EventType.DEBATE_COMPLETED: "bold magenta",
    EventType.ERROR: "bold red",
    # Workspace events
    EventType.WORKSPACE_CREATED: "bold blue",
    EventType.WORKSPACE_FILE_WRITTEN: "blue",
    EventType.WORKSPACE_SCRIPT_EXECUTED: "dim blue",
    EventType.WORKSPACE_SCRIPT_FAILED: "red",
    EventType.WORKSPACE_MEMORY_UPDATED: "green",
    EventType.WORKSPACE_EXPERIMENT_SUBMITTED: "magenta",
    EventType.WORKSPACE_SNAPSHOTTED: "dim white",
    EventType.WORKSPACE_OPENCODE_SERVER_STARTED: "bold cyan",
    EventType.WORKSPACE_OPENCODE_TASK_COMPLETED: "cyan",
    EventType.WORKSPACE_OPENCODE_UNAVAILABLE: "yellow",
}

RELATION_COLORS = {
    "SUPPORTS": "green",
    "EXTENDS": "cyan",
    "CONTRADICTS": "bold red",
    "REFUTES": "red",
    "QUESTIONS": "yellow",
    "DEPENDS_ON": "magenta",
    "DERIVED_FROM": "blue",
}

MESSAGE_COLORS = {
    "QUESTION": "yellow",
    "OBJECTION": "bold red",
    "EVIDENCE": "green",
    "ACKNOWLEDGMENT": "cyan",
    "REPLICATION_REQUEST": "magenta",
    "DEPENDENCY_WARNING": "bold yellow",
}

VERDICT_COLORS = {
    "support": "green",
    "challenge": "bold red",
    "extend": "cyan",
    "refute": "red",
}

SOURCE_PROGRESS_ACTIONS = {
    "downloading_source",
    "download_source_progress",
    "ingesting_source",
    "ingested_search_source",
    "search_source_already_ingested",
}

AGENT_NAME_BY_ID: dict[str, str] = {
    "system": "System",
    "director": "Director",
    "controller": "Controller",
    "debate-system": "Debate System",
    "adjudicator": "Adjudicator",
}

_ACTIVE_PROGRESS_KEY: str | None = None
_ACTIVE_PROGRESS_WIDTH = 0
_ACTIVE_SOURCE_ROWS: dict[str, str] = {}
_SOURCE_LIVE: Live | None = None


def _remember_agent_name(agent_id: str, payload: dict) -> None:
    name = str(payload.get("name") or "").strip()
    if agent_id and name:
        AGENT_NAME_BY_ID[agent_id] = name


def _agent_name(agent_id: str | None) -> str:
    if not agent_id:
        return "System"
    return AGENT_NAME_BY_ID.get(agent_id, agent_id)


def _short(text: str | None, limit: int = 140) -> str:
    value = (text or "").strip().replace("\n", " ")
    if len(value) <= limit:
        return value
    return value[: limit - 3].rstrip() + "..."


def _kv(payload: dict, keys: list[str]) -> str:
    parts: list[str] = []
    for key in keys:
        value = payload.get(key)
        if value not in (None, "", [], {}):
            parts.append(f"{key.replace('_', ' ')}={value}")
    return ", ".join(parts)


def _print_bullets(items: list[str], limit: int = 4) -> None:
    shown = 0
    for item in items[:limit]:
        text = str(item or "").strip()
        if not text:
            continue
        console.print(f"    - {_short(text, 180)}")
        shown += 1
    remaining = len(items) - shown
    if remaining > 0:
        console.print(f"    [dim]... {remaining} more[/]")


def _relation_endpoint(payload: dict, side: str) -> str:
    preview = str(payload.get(f"{side}_preview") or "").strip()
    if preview:
        return _short(preview, 100)

    text = str(payload.get(f"{side}_text") or "").strip()
    if text:
        return _short(text, 100)

    artifact_id = str(
        payload.get(f"{side}_id")
        or payload.get(f"{side}_artifact_id")
        or ""
    ).strip()
    artifact_type = str(payload.get(f"{side}_type") or "").strip()
    short_id = artifact_id[:12] + "..." if artifact_id else ""
    if artifact_type and short_id:
        return f"{artifact_type} {short_id}"
    return short_id


def _badge(text: str, palette: dict[str, str], fallback: str = "white") -> str:
    label = str(text or "").upper()
    color = palette.get(label, fallback)
    return f"[{color}][{label}][/{color}]"


def _progress_bar(progress: int, width: int = 18) -> str:
    clamped = max(0, min(100, int(progress)))
    filled = round(width * clamped / 100)
    return "[" + ("#" * filled) + ("-" * (width - filled)) + f"] {clamped:>3d}%"


def _json_preview(data: object, limit: int = 160) -> str:
    if data in (None, "", {}, []):
        return ""
    try:
        text = json.dumps(data, ensure_ascii=False, sort_keys=True)
    except TypeError:
        text = str(data)
    return _short(text, limit)


def _code_preview(code: str, limit: int = 180) -> str:
    cleaned = " ".join(line.strip() for line in str(code or "").splitlines() if line.strip())
    return _short(cleaned, limit)


def _source_progress_key(agent_id: str, payload: dict) -> str:
    return "|".join(
        [
            agent_id,
            str(payload.get("arxiv_id") or "").strip(),
            str(payload.get("source_id") or "").strip(),
            str(payload.get("title") or "").strip(),
        ]
    )


def _size_text(payload: dict) -> str:
    total_bytes = payload.get("total_bytes")
    if not total_bytes:
        return ""
    downloaded_mb = float(payload.get("bytes_downloaded", 0) or 0) / (1024 * 1024)
    total_mb = float(total_bytes) / (1024 * 1024)
    return f" | {downloaded_mb:.2f}/{total_mb:.2f} MB"


def _render_source_progress_line(agent: str, payload: dict, action: str) -> str:
    title = _short(payload.get("title", ""), 48)
    if action == "downloading_source":
        progress = 0
        stage = "starting"
        prefix = f"{agent} downloading arXiv PDF"
    elif action == "download_source_progress":
        progress = int(payload.get("progress", 0) or 0)
        stage = str(payload.get("stage", "downloading")).replace("_", " ")
        prefix = f"{agent} downloading arXiv PDF"
    elif action == "ingesting_source":
        progress = int(payload.get("progress", 100) or 100)
        stage = "ingesting"
        prefix = f"{agent} ingesting arXiv PDF"
    elif action == "ingested_search_source":
        progress = 100
        stage = "ingested"
        prefix = f"{agent} ingested arXiv PDF"
    else:
        progress = 100
        stage = "reused"
        prefix = f"{agent} reused arXiv paper"
    return (
        f"  {prefix}: {title} "
        f"{_progress_bar(progress)} ({stage})"
        f"{_size_text(payload)}"
    )


def _render_source_live_panel() -> Panel:
    table = Table.grid(expand=True)
    for row in _ACTIVE_SOURCE_ROWS.values():
        table.add_row(_short(row, max(40, console.width - 8)))
    return Panel(
        table,
        title="Source Progress",
        border_style="yellow",
        expand=True,
    )


def _refresh_source_live() -> None:
    global _SOURCE_LIVE
    if _ACTIVE_SOURCE_ROWS:
        if _SOURCE_LIVE is None:
            _SOURCE_LIVE = Live(
                _render_source_live_panel(),
                console=console,
                refresh_per_second=8,
                transient=True,
            )
            _SOURCE_LIVE.start()
        else:
            _SOURCE_LIVE.update(_render_source_live_panel(), refresh=True)
    elif _SOURCE_LIVE is not None:
        _SOURCE_LIVE.stop()
        _SOURCE_LIVE = None


def _update_source_progress(agent_id: str, agent: str, payload: dict, action: str) -> None:
    key = _source_progress_key(agent_id, payload)
    _ACTIVE_SOURCE_ROWS[key] = _render_source_progress_line(agent, payload, action)
    _refresh_source_live()


def _finish_source_progress(payload: dict) -> None:
    key = _source_progress_key(str(payload.get("created_by") or payload.get("agent_id") or ""), payload)
    _ACTIVE_SOURCE_ROWS.pop(key, None)
    _refresh_source_live()


def display_event(event: Event) -> None:
    """Render a live research event to the terminal."""
    color = EVENT_COLORS.get(event.event_type, "white")
    payload = event.payload or {}
    agent_id = event.agent_id or "system"
    _remember_agent_name(agent_id, payload)
    agent = _agent_name(agent_id)
    action = ""
    if event.event_type == EventType.AGENT_ACTION:
        action = str(payload.get("action") or "").strip()

    match event.event_type:
        case EventType.RESEARCH_STARTED:
            console.print(
                Panel(
                    f"[bold]{payload.get('question', '')}[/bold]",
                    title="Research Started",
                    border_style="green",
                )
            )

        case EventType.AGENT_SPAWNED:
            console.print(
                f"  [{color}]+ {payload.get('name', agent)}[/] ({agent_id}): {payload.get('inquiry', '')}"
            )

        case EventType.AGENT_THINKING:
            console.print(
                f"  [{color}]... {agent} thinking about:[/] {_short(payload.get('inquiry', ''))}"
            )

        case EventType.AGENT_ACTION:
            if action == "decomposed_question":
                console.print(
                    f"  [{color}]Director[/] decomposed question: "
                    f"subproblems={payload.get('subproblems_count', 0)}, "
                    f"archetypes={payload.get('archetypes_count', 0)}"
                )
                if payload.get("reasoning_summary"):
                    console.print(
                        f"    rationale: {_short(payload.get('reasoning_summary', ''), 180)}"
                    )
                if payload.get("open_questions"):
                    console.print("    open questions:")
                    _print_bullets(payload.get("open_questions", []))
                if payload.get("key_assumptions"):
                    console.print("    key assumptions:")
                    _print_bullets(payload.get("key_assumptions", []))
                if payload.get("archetype_names"):
                    console.print(
                        f"    archetypes: {_short(', '.join(payload.get('archetype_names', [])), 180)}"
                    )
                if payload.get("archetype_reasoning"):
                    console.print(
                        f"    archetype rationale: {_short(payload.get('archetype_reasoning', ''), 180)}"
                    )
            elif action == "decomposition_started":
                console.print(
                    f"  [{color}]Director[/] analyzing the research question and drafting subproblems..."
                )
                if payload.get("question"):
                    console.print(f"    question: {_short(payload.get('question', ''), 180)}")
            elif action == "decomposition_completed":
                console.print(
                    f"  [{color}]Director[/] drafted the research plan: "
                    f"subproblems={payload.get('subproblems_count', 0)}, "
                    f"open_questions={payload.get('open_questions_count', 0)}, "
                    f"assumptions={payload.get('key_assumptions_count', 0)}"
                )
                if payload.get("reasoning_summary"):
                    console.print(
                        f"    rationale: {_short(payload.get('reasoning_summary', ''), 180)}"
                    )
            elif action == "archetype_design_started":
                console.print(
                    f"  [{color}]Director[/] designing agent archetypes: "
                    f"target={payload.get('max_archetypes', 0)}, "
                    f"subproblems={payload.get('subproblems_count', 0)}"
                )
            elif action == "archetype_design_completed":
                console.print(
                    f"  [{color}]Director[/] archetype design completed: "
                    f"count={payload.get('archetypes_count', 0)}"
                )
                if payload.get("archetype_names"):
                    console.print(
                        f"    archetypes: {_short(', '.join(payload.get('archetype_names', [])), 180)}"
                    )
                if payload.get("reasoning_summary"):
                    console.print(
                        f"    rationale: {_short(payload.get('reasoning_summary', ''), 180)}"
                    )
            elif action == "reclustered":
                console.print(
                    f"  [{color}]Debate System[/] reclustered: "
                    f"{payload.get('num_clusters', 0)} clusters, sizes={payload.get('cluster_sizes', [])}"
                )
                for cluster in payload.get("clusters", [])[:5]:
                    names = ", ".join(
                        member.get("name", member.get("agent_id", "?"))
                        for member in cluster.get("members", [])
                    )
                    shared = len(cluster.get("shared_hypotheses", []))
                    contested = len(cluster.get("contested_hypotheses", []))
                    console.print(
                        f"    {cluster.get('cluster_id', 'cluster')}: {names} "
                        f"[dim](shared={shared}, contested={contested})[/]"
                    )
            elif action == "intra_cluster_review_started":
                console.print(
                    f"  [{color}]Debate System[/] intra-cluster review started: "
                    f"clusters={payload.get('clusters', 0)}, planned_reviews={payload.get('planned_reviews', 0)}"
                )
                for item in payload.get("review_plan_preview", [])[:5]:
                    reviewer = _agent_name(item.get("reviewer_id"))
                    peer = _agent_name(item.get("peer_id"))
                    target = _short(item.get("hypothesis_text", ""), 120)
                    console.print(
                        f"    {reviewer} [cyan]reviewing[/] {peer}'s hypothesis: {target}"
                    )
            elif action == "intra_cluster_review_progress":
                reviewer = _agent_name(payload.get("reviewer_id"))
                peer = _agent_name(payload.get("peer_id"))
                console.print(
                    f"    [{color}]Review progress[/]: "
                    f"{payload.get('completed_reviews', 0)}/{payload.get('total_reviews', 0)} complete, "
                    f"{payload.get('failed_reviews', 0)} failed - "
                    f"{reviewer} vs {peer}: {_short(payload.get('hypothesis_text', ''), 100)}"
                )
            elif action == "intra_cluster_review_completed":
                console.print(
                    f"  [{color}]Debate System[/] intra-cluster review completed: "
                    f"completed_reviews={payload.get('completed_reviews', 0)}, "
                    f"failed_reviews={payload.get('failed_reviews', 0)}, "
                    f"experiments_proposed={payload.get('experiments_proposed', 0)}"
                )
            elif action == "inter_cluster_debate_started":
                console.print(
                    f"  [{color}]Debate System[/] inter-cluster debate started: "
                    f"pairs={payload.get('pairs', 0)}"
                )
                for item in payload.get("pair_preview", [])[:5]:
                    challenger = _agent_name(item.get("challenger_id"))
                    owner = _agent_name(item.get("target_owner_id"))
                    target = _short(item.get("target_hypothesis_text", ""), 130)
                    if target:
                        console.print(
                            f"    {challenger} [bold red]challenges[/] {owner}'s hypothesis: {target}"
                        )
            elif action == "inter_cluster_debate_progress":
                challenger = _agent_name(payload.get("challenger_id"))
                owner = _agent_name(payload.get("target_owner_id"))
                console.print(
                    f"    [{color}]Debate progress[/]: "
                    f"{payload.get('completed_pairs', 0)}/{payload.get('total_pairs', 0)} complete, "
                    f"{payload.get('failed_pairs', 0)} failed - "
                    f"{challenger} challenging {owner}: "
                    f"{_short(payload.get('target_hypothesis_text', ''), 100)}"
                )
            elif action == "inter_cluster_debate_completed":
                console.print(
                    f"  [{color}]Debate System[/] inter-cluster debate completed: "
                    f"completed_pairs={payload.get('completed_pairs', 0)}, "
                    f"failed_pairs={payload.get('failed_pairs', 0)}, "
                    f"experiments_proposed={payload.get('experiments_proposed', 0)}"
                )
            elif action == "counter_responses_started":
                console.print(
                    f"  [{color}]Debate System[/] counter-responses started: "
                    f"challenged_hypotheses={payload.get('challenged_hypotheses', 0)}"
                )
                for item in payload.get("challenged_hypothesis_preview", [])[:4]:
                    author = _agent_name(item.get("author_id"))
                    reviewer = _agent_name(item.get("reviewer_id"))
                    console.print(
                        f"    {author} responding to {reviewer}'s challenge on "
                        f"{_short(item.get('hypothesis_text', ''), 80)}"
                    )
                    console.print(
                        f"      critique: {_short(item.get('critique', ''), 120)}"
                    )
            elif action == "counter_response_progress":
                author = _agent_name(payload.get("author_id"))
                reviewer = _agent_name(payload.get("reviewer_id"))
                console.print(
                    f"    [{color}]Counter-response progress[/]: "
                    f"{payload.get('completed_responses', 0)}/{payload.get('total_responses', 0)} complete, "
                    f"{payload.get('failed_responses', 0)} failed - "
                    f"{author} replying to {reviewer} on "
                    f"{_short(payload.get('hypothesis_text', ''), 90)}"
                )
            elif action == "counter_responses_completed":
                console.print(
                    f"  [{color}]Debate System[/] counter-responses completed: "
                    f"responses_attempted={payload.get('responses_attempted', 0)}, "
                    f"failed_responses={payload.get('failed_responses', 0)}"
                )
            elif action == "adjudication_started":
                console.print(
                    f"  [{color}]Adjudicator[/] started: targets={payload.get('targets', 0)}"
                )
                for item in payload.get("target_preview", [])[:5]:
                    console.print(
                        f"    reviewing contested hypothesis: "
                        f"{_short(item.get('target_text', ''), 120)}"
                    )
            elif action == "adjudicating_hypothesis":
                console.print(
                    f"    [{color}]Adjudicating[/]: {_short(payload.get('target_text', ''), 120)} "
                    f"[dim](supporters={payload.get('supporters', 0)}, "
                    f"contradictors={payload.get('contradictors', 0)})[/]"
                )
            elif action == "adjudication_progress":
                console.print(
                    f"    [{color}]Adjudication progress[/]: "
                    f"{payload.get('completed_targets', 0)}/{payload.get('total_targets', 0)} complete, "
                    f"{payload.get('failed_targets', 0)} failed - "
                    f"{_short(payload.get('target_text', ''), 100)} "
                    f"=> {_short(payload.get('ruling', ''), 70)}"
                )
            elif action == "adjudication_completed":
                console.print(
                    f"  [{color}]Adjudicator[/] completed: "
                    f"targets={payload.get('targets', 0)}, "
                    f"failed_targets={payload.get('failed_targets', 0)}"
                )
            elif action == "reviewed_hypothesis":
                verdict = str(payload.get("verdict", "")).lower()
                console.print(
                    f"  [{color}]{agent}[/] reviewed hypothesis "
                    f"{payload.get('hypothesis_id', '')[:12]}... "
                    f"-> {_badge(verdict, VERDICT_COLORS)}: "
                    f"{_short(payload.get('hypothesis_text', ''), 120)}"
                )
            elif action == "downloading_source":
                _update_source_progress(agent_id, agent, payload, action)
            elif action == "download_source_progress":
                _update_source_progress(agent_id, agent, payload, action)
            elif action == "ingesting_source":
                _update_source_progress(agent_id, agent, payload, action)
            elif action == "ingested_search_source":
                _ACTIVE_SOURCE_ROWS.pop(_source_progress_key(agent_id, payload), None)
                _refresh_source_live()
                if payload.get("file_path"):
                    console.print(
                        f"  [{color}]{agent}[/] ingested [yellow]arXiv PDF[/]: "
                        f"{_short(payload.get('title', ''), 120)}"
                    )
                    console.print(f"    file: {_short(payload.get('file_path', ''), 160)}")
                else:
                    console.print(
                        f"  [{color}]{agent}[/] ingested [yellow]arXiv PDF[/]: "
                        f"{_short(payload.get('title', ''), 120)}"
                    )
            elif action == "search_source_already_ingested":
                _ACTIVE_SOURCE_ROWS.pop(_source_progress_key(agent_id, payload), None)
                _refresh_source_live()
                console.print(
                    f"  [{color}]{agent}[/] reused existing [yellow]arXiv paper[/]: "
                    f"{_short(payload.get('title', ''), 120)} "
                    f"[dim]({payload.get('arxiv_id', '')})[/]"
                )
            elif action == "reviewing_hypothesis":
                owner = _agent_name(payload.get("created_by"))
                console.print(
                    f"  [{color}]{agent}[/] reviewing {owner}'s hypothesis: "
                    f"{_short(payload.get('hypothesis_text', ''), 140)}"
                )
            elif action:
                console.print(f"  [{color}]{agent}[/] {action}: {_kv(payload, [k for k in payload.keys() if k != 'action'])}")
            else:
                summary = _kv(payload, ['notes', 'hypotheses', 'relations', 'experiments'])
                if summary:
                    if all((payload.get(key, 0) == 0) for key in ["notes", "hypotheses", "relations", "experiments"]):
                        console.print(f"  [{color}]{agent}[/] produced no new artifacts this pass")
                    else:
                        console.print(f"  [{color}]{agent}[/]: {summary}")

        case EventType.SOURCE_INGESTED:
            console.print(
                f"  [{color}]Ingested:[/] {_short(payload.get('title', ''), 80)} "
                f"({payload.get('chunks_count', 0)} chunks)"
            )

        case EventType.SOURCE_DISCOVERED:
            source_name = str(payload.get("source", "")).strip()
            if source_name == "arxiv":
                console.print(
                    f"  [{color}]Discovered:[/] {payload.get('results_count', 0)} arXiv papers: "
                    f"{_short(payload.get('query', ''), 90)}"
                )
                for title in payload.get("titles", [])[:3]:
                    console.print(f"    - {_short(title, 120)}")
            else:
                console.print(
                    f"  [{color}]Discovered:[/] {payload.get('results_count', 0)} results from "
                    f"{source_name}: {_short(payload.get('query', ''), 90)}"
                )

        case EventType.EXPERIMENT_STARTED:
            extra = ""
            if payload.get("hypothesis_id"):
                extra = f" for hypothesis {str(payload.get('hypothesis_id'))[:12]}..."
            console.print(f"  [{color}]Experiment started:[/] {event.artifact_id[:12]}...{extra}")
            if payload.get("expected_outcome"):
                console.print(f"    expected: {_short(payload.get('expected_outcome', ''), 140)}")
            if payload.get("input_data") not in (None, {}, []):
                console.print(f"    inputs:   {_json_preview(payload.get('input_data'))}")
            if payload.get("code_preview"):
                console.print(f"    code:     {_code_preview(payload.get('code_preview', ''))}")

        case EventType.EXPERIMENT_COMPLETED:
            exit_code = payload.get("exit_code", "?")
            status = "PASS" if exit_code == 0 else f"EXIT {exit_code}"
            console.print(f"  [{color}]Experiment {status}:[/] {event.artifact_id[:12]}...")
            if payload.get("stdout_preview"):
                console.print(f"    output: {_short(payload.get('stdout_preview', ''), 150)}")
            if payload.get("stderr_preview"):
                console.print(f"    stderr: {_short(payload.get('stderr_preview', ''), 150)}")
            if payload.get("artifacts") not in (None, {}, []):
                console.print(f"    results: {_json_preview(payload.get('artifacts'))}")
            if payload.get("input_data") not in (None, {}, []):
                console.print(f"    inputs:  {_json_preview(payload.get('input_data'))}")

        case EventType.EXPERIMENT_FAILED:
            code_suffix = ""
            if payload.get("exit_code") not in (None, ""):
                code_suffix = f" (exit {payload.get('exit_code')})"
            console.print(
                f"  [{color}]Experiment FAILED{code_suffix}:[/] {_short(payload.get('error', ''), 180)}"
            )
            if payload.get("expected_outcome"):
                console.print(f"    expected: {_short(payload.get('expected_outcome', ''), 140)}")
            if payload.get("input_data") not in (None, {}, []):
                console.print(f"    inputs:   {_json_preview(payload.get('input_data'))}")
            if payload.get("code_preview"):
                console.print(f"    code:     {_code_preview(payload.get('code_preview', ''))}")
            if payload.get("stdout_preview"):
                console.print(f"    output:   {_short(payload.get('stdout_preview', ''), 150)}")

        case EventType.ITERATION_STARTED:
            iteration = payload.get("iteration", 0)
            phase = payload.get("phase", "research")
            console.print(f"\n[{color}]{'=' * 60}[/]")
            console.print(f"[{color}]  Iteration {iteration} - {str(phase).upper()} PHASE[/]")
            console.print(f"[{color}]{'=' * 60}[/]")

        case EventType.ITERATION_COMPLETED:
            iteration = payload.get("iteration", 0)
            should_stop = payload.get("should_stop", False)
            status_text = "STOPPING" if should_stop else "CONTINUING"
            console.print(f"\n  [{color}]Iteration {iteration} complete - {status_text}[/]")
            console.print(f"  Reasoning: {_short(payload.get('reasoning', ''), 200)}")
            for directive in payload.get("directives", []):
                console.print(f"    > {directive}")

        case EventType.DEBATE_STARTED:
            console.print(f"\n  [{color}]Debate round starting...[/]")

        case EventType.DEBATE_COMPLETED:
            console.print(
                f"  [{color}]Debate complete:[/] {payload.get('contradictions_found', 0)} contradictions found"
            )

        case EventType.ARTIFACT_CREATED:
            artifact_type = str(event.artifact_type or payload.get("label", "")).lower()
            if artifact_type in {"source", "sourcechunk", "source_chunk", "relation", "message"}:
                return
            if artifact_type == "note":
                console.print(f"  [{color}]{agent}[/] note: {_short(payload.get('text', ''), 170)}")
            elif artifact_type == "assumption":
                basis = _short(payload.get("basis", ""), 90)
                suffix = f" (basis: {basis})" if basis else ""
                console.print(f"  [{color}]{agent}[/] assumption: {_short(payload.get('text', ''), 150)}{suffix}")
            elif artifact_type == "hypothesis":
                confidence = payload.get("confidence")
                confidence_text = f" [dim](confidence {confidence:.2f})[/]" if isinstance(confidence, (int, float)) else ""
                console.print(
                    f"  [{color}]{agent}[/] hypothesis:{confidence_text} {_short(payload.get('text', ''), 170)}"
                )
            elif artifact_type == "finding":
                conclusion = str(payload.get("conclusion_type", "")).strip()
                conclusion_text = f" [{conclusion}]" if conclusion else ""
                console.print(
                    f"  [{color}]{agent}[/] finding{conclusion_text}: {_short(payload.get('text', ''), 170)}"
                )
            elif artifact_type == "experiment":
                summary = _short(payload.get("expected_outcome", ""), 150) or _short(payload.get("code_preview", ""), 150)
                console.print(f"  [{color}]{agent}[/] experiment proposed: {summary}")
            elif artifact_type == "experimentresult":
                console.print(
                    f"  [{color}]Experiment result[/]: exit={payload.get('exit_code', '?')}, "
                    f"stdout={_short(payload.get('stdout_preview', ''), 120)}"
                )
            else:
                console.print(f"  [{color}]Created {artifact_type or 'artifact'}:[/] {event.artifact_id[:12]}...")

        case EventType.RELATION_CREATED:
            rel_type = str(payload.get("relation_type", "relation")).upper()
            console.print(f"  [{color}]{agent}[/] relation {_badge(rel_type, RELATION_COLORS)}:")
            console.print(f"    from: {_relation_endpoint(payload, 'source')}")
            console.print(f"    to:   {_relation_endpoint(payload, 'target')}")
            if payload.get("reasoning"):
                console.print(f"    why:  {_short(payload.get('reasoning', ''), 140)}")

        case EventType.MESSAGE_SENT:
            to_agent = _agent_name(payload.get("to_agent"))
            message_type = str(payload.get("message_type", "message")).upper()
            console.print(
                f"  [{color}]{agent}[/] sent {_badge(message_type, MESSAGE_COLORS)} to {to_agent}: "
                f"{_short(payload.get('text', ''), 170)}"
            )

        case EventType.ARTIFACT_REFUTED:
            console.print(f"  [{color}]REFUTED:[/] {event.artifact_id[:12]}... by {agent}")

        case EventType.ARTIFACT_UPDATED:
            updated_fields = payload.get("updated_fields", [])
            properties = payload.get("properties", {})
            label = str(payload.get("label", "")).strip()
            if updated_fields == ["read"] or updated_fields == ["embedding_id"]:
                return
            if sorted(updated_fields) == ["file_path", "source_type", "title", "uri"]:
                return
            if label == "Experiment" and "status" in properties:
                return
            if "status" in properties:
                console.print(
                    f"  [{color}]Status update:[/] {event.artifact_id[:12]}... -> {properties.get('status', '?')}"
                )
            elif "adjudication_status" in properties:
                console.print(
                    f"  [{color}]Adjudication update:[/] {event.artifact_id[:12]}... -> "
                    f"{properties.get('adjudication_status', '?')}"
                )
            else:
                console.print(
                    f"  [{color}]Artifact updated:[/] {event.artifact_id[:12]}... "
                    f"{_kv(payload, ['updated_fields'])}"
                )

        case EventType.STATE_SNAPSHOT:
            if not payload:
                return
            interesting = _kv(payload, ["iteration", "status", "budget_remaining", "budget_total"])
            if interesting:
                console.print(f"  [{color}]State snapshot:[/] {interesting}")

        case EventType.RESEARCH_COMPLETED:
            console.print(
                Panel(
                    f"Iterations: {payload.get('iterations', 0)}\n"
                    f"Spend: ${payload.get('budget_used', 0.0):.2f}",
                    title="Research Complete",
                    border_style="green",
                )
            )

        case EventType.ERROR:
            console.print(f"  [{color}]ERROR: {payload.get('error', 'Unknown error')}[/]")

        # Workspace events
        case EventType.WORKSPACE_CREATED:
            console.print(
                f"  [{color}]Workspace created:[/] {agent} "
                f"path={_short(payload.get('path', ''), 60)}"
            )

        case EventType.WORKSPACE_FILE_WRITTEN:
            file_path = _short(payload.get('path', ''), 50)
            size = payload.get('size_bytes', 0)
            size_kb = size / 1024 if size else 0
            console.print(
                f"  [{color}]Workspace file:[/] {agent} "
                f"{file_path} ({size_kb:.1f} KB)"
            )

        case EventType.WORKSPACE_MEMORY_UPDATED:
            auto_logged = payload.get('auto_logged', False)
            auto_tag = " [dim](auto)" if auto_logged else ""
            console.print(
                f"  [{color}]Memory updated:[/] {agent}{auto_tag} "
                f"iteration={payload.get('iteration', '?')}"
            )
            if payload.get('issues'):
                for issue in payload.get('issues', [])[:2]:
                    console.print(f"    [dim]warning: {issue}[/]")

        case EventType.WORKSPACE_EXPERIMENT_SUBMITTED:
            console.print(
                f"  [{color}]Experiment submitted:[/] {agent} "
                f"hypothesis={_short(payload.get('hypothesis_id', ''), 12)}..."
            )

        case EventType.WORKSPACE_SNAPSHOTTED:
            console.print(
                f"  [{color}]Workspace snapshotted:[/] {agent} "
                f"path={_short(payload.get('snapshot_path', ''), 60)}"
            )

        case EventType.WORKSPACE_OPENCODE_SERVER_STARTED:
            console.print(
                f"  [{color}]OpenCode server:[/] {agent} "
                f"port={payload.get('port', '?')} "
                f"path={_short(payload.get('workspace', ''), 50)}"
            )

        case EventType.WORKSPACE_OPENCODE_TASK_COMPLETED:
            satisfied = payload.get('satisfied', False)
            status_icon = "✓" if satisfied else "○"
            cost = payload.get('cost_usd', 0)
            console.print(
                f"  [{color}]OpenCode task:[/] {agent} "
                f"[{status_icon}] {payload.get('topic', 'untitled')} "
                f"({payload.get('iterations', 0)} turns, ${cost:.4f})"
            )

        case EventType.WORKSPACE_OPENCODE_UNAVAILABLE:
            reason = payload.get('reason', 'not installed')
            console.print(
                f"  [yellow]OpenCode unavailable:[/] {agent} ({reason})"
            )

        case _:
            console.print(
                f"  [{color}]{event.event_type.value}[/]: {agent} "
                f"{_kv(payload, list(payload.keys())[:5])}"
            )


def display_hypotheses(hypotheses: list[dict]) -> None:
    """Render a table of hypotheses."""
    table = Table(title="Hypotheses")
    table.add_column("ID", style="dim", max_width=12)
    table.add_column("Status", style="bold")
    table.add_column("Confidence", justify="right")
    table.add_column("By", style="cyan")
    table.add_column("Text", max_width=60)

    for hypothesis in hypotheses:
        status_color = {
            "active": "green",
            "refuted": "red",
            "superseded": "yellow",
        }.get(hypothesis.get("status", ""), "white")

        table.add_row(
            hypothesis.get("id", "")[:12],
            f"[{status_color}]{hypothesis.get('status', '?')}[/]",
            f"{hypothesis.get('confidence', 0):.0%}",
            hypothesis.get("created_by", "?"),
            hypothesis.get("text", "")[:60],
        )

    console.print(table)


def display_graph_summary(graph_data: dict) -> None:
    """Render a summary of the knowledge graph."""
    nodes = graph_data.get("nodes", [])
    edges = graph_data.get("edges", [])

    label_counts: dict[str, int] = {}
    for node in nodes:
        for label in node.get("labels", []):
            label_counts[label] = label_counts.get(label, 0) + 1

    table = Table(title="Knowledge Graph")
    table.add_column("Node Type")
    table.add_column("Count", justify="right")

    for label, count in sorted(label_counts.items()):
        table.add_row(label, str(count))

    table.add_row("", "")
    table.add_row("[bold]Total Nodes[/]", f"[bold]{len(nodes)}[/]")
    table.add_row("[bold]Total Edges[/]", f"[bold]{len(edges)}[/]")

    console.print(table)


def display_report(report: str) -> None:
    """Render the final synthesis report."""
    console.print(
        Panel(
            report,
            title="Research Synthesis Report",
            border_style="green",
            expand=True,
        )
    )
