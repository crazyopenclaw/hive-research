"""
FastAPI communication layer for the backend-native research engine.

This app is intentionally thin: it starts background research sessions,
projects engine state into UI-facing resources, and streams translated events.
The CLI still talks directly to ResearchService.
"""

from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager
from typing import Any

from fastapi import Body, FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse

from src.api.projection_store import ProjectionStore
from src.api.service import ResearchService
from src.api.session_manager import SessionManager, utc_now_iso
from src.models.persona import AgentPersona, generate_persona_prompt


def _question_node_id() -> str:
    return "input"


def _get_snapshot(session: dict[str, Any]) -> dict[str, Any]:
    return ((session.get("state") or {}).get("snapshot") or {})


def _get_latest_agent_status(session: dict[str, Any]) -> dict[str, Any]:
    return ((session.get("state") or {}).get("latest_agent_status") or {})


def _get_archetype_lookup(snapshot: dict[str, Any]) -> dict[str, dict[str, Any]]:
    lookup: dict[str, dict[str, Any]] = {}
    for archetype in snapshot.get("archetypes", []) or []:
        archetype_id = archetype.get("id")
        if archetype_id:
            lookup[archetype_id] = archetype
    return lookup


def _get_agent_lookup(snapshot: dict[str, Any]) -> dict[str, dict[str, Any]]:
    lookup: dict[str, dict[str, Any]] = {}
    for agent in snapshot.get("agents", []) or []:
        agent_id = agent.get("agent_id")
        if agent_id:
            lookup[agent_id] = agent
    return lookup


def _agent_archetype_name(agent: dict[str, Any], snapshot: dict[str, Any]) -> str:
    persona = agent.get("persona") or {}
    archetype_id = persona.get("archetype_id")
    archetype = _get_archetype_lookup(snapshot).get(archetype_id or "")
    if archetype:
        return archetype.get("name", "")
    return persona.get("specialty") or "generalist"


def _build_agent_summary(
    agent: dict[str, Any],
    snapshot: dict[str, Any],
    latest_status: dict[str, Any],
    metrics: dict[str, Any],
) -> dict[str, Any]:
    agent_id = agent.get("agent_id", "")
    persona = agent.get("persona") or {}
    status_meta = latest_status.get(agent_id, {})
    archetype_name = _agent_archetype_name(agent, snapshot)
    return {
        "id": agent_id,
        "display_name": agent.get("name") or agent_id,
        "line_of_inquiry": agent.get("line_of_inquiry", ""),
        "status": agent.get("status", "active"),
        "current_status_text": status_meta.get("status_text") or "idle",
        "persona_summary": {
            "specialty": persona.get("specialty") or "general",
            "skepticism_level": persona.get("skepticism_level"),
            "experiment_appetite": persona.get("experiment_appetite"),
            "model_tier": persona.get("model_tier"),
        },
        "archetype_name": archetype_name,
        "icon_key": archetype_name or persona.get("specialty") or agent_id,
        "metrics": metrics,
    }


def _build_relation_items(
    contradictions: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for contradiction in contradictions[:12]:
        source_id = contradiction.get("source_id", "")
        target_id = contradiction.get("target_id", "")
        items.append(
            {
                "id": contradiction.get("relation_id") or f"{source_id}-{target_id}",
                "summary": (
                    f"{contradiction.get('source_text', '')[:90]} "
                    f"vs {contradiction.get('target_text', '')[:90]}"
                ).strip(),
                "source_id": source_id,
                "target_id": target_id,
                "source_label": source_id,
                "target_label": target_id,
                "weight": contradiction.get("weight", 0.0),
                "status": "open",
            }
        )
    return items


async def _build_overview(
    service: ResearchService,
    session: dict[str, Any],
) -> dict[str, Any]:
    session_id = session["id"]
    snapshot = _get_snapshot(session)
    subproblems = snapshot.get("subproblems", []) or []
    contradictions = await service.queries.get_session_contradictions(session_id=session_id)
    top_hypotheses = await service.queries.get_session_top_hypotheses(session_id=session_id, limit=6)
    experiment_counts = await service.queries.get_session_experiment_counts(session_id)
    coverage_stats = await service.queries.get_coverage_stats(session_id=session_id)
    active_experiments = await service.graph.get_by_label(
        "Experiment",
        filters={"session_id": session_id},
        limit=12,
    )

    coverage_map = snapshot.get("coverage", {}) or {}
    subquestion_total = len(subproblems)
    subquestion_values = [float(coverage_map.get(item.get("id"), 0.0)) for item in subproblems]
    subquestion_covered = sum(1 for value in subquestion_values if value >= 0.6)
    coverage_ratio = (
        sum(subquestion_values) / len(subquestion_values)
        if subquestion_values else 0.0
    )
    open_subproblems = [
        {
            "id": item.get("id"),
            "title": item.get("question", ""),
            "priority": item.get("priority"),
            "success_criteria": item.get("success_criteria", ""),
            "assigned_agent": item.get("assigned_agent", ""),
            "coverage": float(coverage_map.get(item.get("id"), 0.0)),
        }
        for item in sorted(subproblems, key=lambda row: row.get("priority", 999))
        if float(coverage_map.get(item.get("id"), 0.0)) < 0.85
    ]

    active_work_items: list[dict[str, Any]] = []
    for subproblem in open_subproblems[:8]:
        active_work_items.append(
            {
                "id": subproblem["id"],
                "kind": "subproblem",
                "title": subproblem["title"],
                "status": "open",
                "priority": subproblem["priority"],
                "summary": subproblem["success_criteria"],
                "assigned_agent": subproblem["assigned_agent"],
            }
        )
    for experiment in active_experiments:
        if experiment.get("status") in {"pending", "running"}:
            active_work_items.append(
                {
                    "id": experiment.get("id"),
                    "kind": "experiment",
                    "title": experiment.get("spec_expected_outcome") or experiment.get("id"),
                    "status": experiment.get("status"),
                    "priority": "normal",
                    "summary": experiment.get("spec_code", "")[:160],
                    "assigned_agent": experiment.get("created_by"),
                }
            )
    for contradiction in contradictions[:6]:
        active_work_items.append(
            {
                "id": contradiction.get("relation_id") or f"{contradiction.get('source_id')}-{contradiction.get('target_id')}",
                "kind": "review",
                "title": "Resolve contradiction",
                "status": "open",
                "priority": "high",
                "summary": (
                    f"{contradiction.get('source_text', '')[:100]} "
                    f"vs {contradiction.get('target_text', '')[:100]}"
                ),
                "assigned_agent": None,
            }
        )

    dollars_used = float(snapshot.get("dollars_used", 0.0) or 0.0)
    if dollars_used > 0:
        spent = dollars_used
    else:
        spent = max(
            0,
            float(snapshot.get("budget_total", 0) or 0)
            - float(snapshot.get("budget_remaining", 0) or 0),
        )
    budget_total = float(snapshot.get("budget_total", 0) or 0)
    tokens_used = int(snapshot.get("tokens_used", 0) or 0)
    budget_warning = bool(snapshot.get("budget_warning", False))
    llm_budget_usd = float(snapshot.get("llm_budget_usd", 0) or 0)
    report = (session.get("state") or {}).get("report") or {}
    agent_count = len(snapshot.get("agents", []) or [])
    active_count = sum(1 for agent in snapshot.get("agents", []) or [] if agent.get("status") == "active")
    paused_count = sum(1 for agent in snapshot.get("agents", []) or [] if agent.get("status") == "paused")
    done_count = sum(1 for agent in snapshot.get("agents", []) or [] if agent.get("status") == "done")

    plan_summary = {
        "subproblems": subproblems,
        "open_questions": snapshot.get("open_questions", []) or [],
        "key_assumptions": snapshot.get("key_assumptions", []) or [],
        "archetypes": snapshot.get("archetypes", []) or [],
        "director_summary": (
            f"{len(subproblems)} subproblems, "
            f"{len(snapshot.get('archetypes', []) or [])} archetypes, "
            f"{len(snapshot.get('open_questions', []) or [])} open questions."
        ),
    }

    return {
        "id": session_id,
        "question": session["question"],
        "status": session["status"],
        "iteration": snapshot.get("iteration", 0),
        "budget": {
            "spent": spent,
            "total": float(budget_total),
            "remaining": float(snapshot.get("budget_remaining", 0) or 0),
            "tokens_used": tokens_used,
            "dollars_used": dollars_used,
            "dollars_budget": llm_budget_usd,
            "percentage": round(dollars_used / max(0.01, llm_budget_usd) * 100, 1),
            "is_warning": budget_warning,
        },
        "agents": {
            "total": agent_count,
            "active": active_count,
            "paused": paused_count,
            "done": done_count,
        },
        "experiments": experiment_counts,
        "coverage": {
            "ratio": coverage_ratio,
            "subquestions_covered": subquestion_covered,
            "subquestions_total": subquestion_total,
            "open_debates": len(contradictions),
            "hypotheses_count": sum((coverage_stats.get("Hypothesis") or {}).values()),
        },
        "contradiction_count": len(contradictions),
        "top_hypotheses": top_hypotheses,
        "open_subproblems": open_subproblems,
        "pending_experiments": [
            experiment for experiment in active_experiments
            if experiment.get("status") in {"pending", "running"}
        ],
        "report_available": bool(report.get("content")),
        "research_plan": plan_summary,
        "active_work": {
            "count": len(active_work_items),
            "items": active_work_items[:16],
        },
        "contradictions_and_reviews": {
            "count": len(contradictions),
            "items": _build_relation_items(contradictions),
        },
        "best_answer": (
            {
                "label": "Leading hypothesis",
                "confidence": top_hypotheses[0].get("confidence", 0.0),
                "claim": top_hypotheses[0].get("text", ""),
            }
            if top_hypotheses else
            {
                "label": "No leading hypothesis",
                "confidence": 0.0,
                "claim": "",
            }
        ),
        "created_at": session.get("created_at").isoformat() if session.get("created_at") else None,
        "updated_at": session.get("updated_at").isoformat() if session.get("updated_at") else None,
    }


async def _build_graph_view(
    service: ResearchService,
    session: dict[str, Any],
) -> dict[str, Any]:
    session_id = session["id"]
    snapshot = _get_snapshot(session)
    latest_status = _get_latest_agent_status(session)
    agents = snapshot.get("agents", []) or []
    metrics_list = await asyncio.gather(
        *[
            service.queries.get_agent_metrics(agent["agent_id"], session_id=session_id)
            for agent in agents
        ]
    ) if agents else []
    summaries = [
        _build_agent_summary(agent, snapshot, latest_status, metrics)
        for agent, metrics in zip(agents, metrics_list)
    ]

    findings = await service.graph.get_by_label(
        "Finding",
        filters={"session_id": session_id},
        limit=100,
    )
    experiments = await service.graph.get_by_label(
        "Experiment",
        filters={"session_id": session_id},
        limit=50,
    )
    hypotheses = await service.graph.get_by_label(
        "Hypothesis",
        filters={"session_id": session_id},
        limit=50,
    )

    nodes = [
        {
            "id": _question_node_id(),
            "label": session["question"],
            "node_type": "input",
        }
    ]

    station_nodes = [
        {"id": "station-lab", "label": "Lab", "node_type": "station", "station_type": "lab"},
        {"id": "station-archive", "label": "Archive", "node_type": "station", "station_type": "archive"},
        {"id": "station-experiment", "label": "Experiment Station", "node_type": "station", "station_type": "experiment"},
        {"id": "station-center", "label": "Institute Center", "node_type": "station", "station_type": "center"},
        {"id": "station-table", "label": "Debate Table", "node_type": "station", "station_type": "table"},
    ]
    nodes.extend(station_nodes)

    for summary in summaries:
        metrics = summary.get("metrics", {}) or {}
        nodes.append(
            {
                "id": summary["id"],
                "label": summary["display_name"],
                "node_type": "agent",
                "status": summary["status"],
                "current_status_text": summary["current_status_text"],
                "archetype_name": summary["archetype_name"],
                "icon_key": summary["icon_key"],
                "artifact_counts": {
                    "notes": metrics.get("notes_count", 0),
                    "hypotheses": (
                        metrics.get("hypotheses_active", 0)
                        + metrics.get("hypotheses_refuted", 0)
                        + metrics.get("hypotheses_upheld", 0)
                    ),
                    "findings": metrics.get("findings_count", 0),
                    "experiments": metrics.get("experiments_count", 0),
                },
            }
        )

    edges = [
        {
            "id": f"start-{summary['id']}",
            "source": _question_node_id(),
            "target": summary["id"],
            "type": "STARTS_FROM",
            "count": 1,
        }
        for summary in summaries
    ]

    agent_activities = {}
    for agent in agents:
        agent_id = agent.get("agent_id")
        if not agent_id:
            continue
        agent_activities[agent_id] = {
            "findings_count": 0,
            "experiments_count": 0,
            "hypotheses_count": 0,
            "latest_activity": None,
            "latest_summary": None,
        }

    for finding in findings:
        creator = finding.get("created_by")
        if creator and creator in agent_activities:
            agent_activities[creator]["findings_count"] += 1
            agent_activities[creator]["latest_activity"] = "finding"
            agent_activities[creator]["latest_summary"] = (finding.get("text") or "finding")[:60]

    for experiment in experiments:
        creator = experiment.get("created_by")
        if creator and creator in agent_activities:
            agent_activities[creator]["experiments_count"] += 1
            exp_status = experiment.get("status", "pending")
            agent_activities[creator]["latest_activity"] = f"experiment:{exp_status}"
            agent_activities[creator]["latest_summary"] = (experiment.get("spec_expected_outcome") or "experiment")[:60]

    for hypothesis in hypotheses:
        creator = hypothesis.get("created_by")
        if creator and creator in agent_activities:
            agent_activities[creator]["hypotheses_count"] += 1

    for agent_id, activity in agent_activities.items():
        if activity["experiments_count"] > 0:
            edges.append({
                "id": f"{agent_id}-lab",
                "source": agent_id,
                "target": "station-lab",
                "type": "RUNS_EXPERIMENT",
                "count": activity["experiments_count"],
                "summary": activity["latest_summary"],
            })
        if activity["findings_count"] > 0:
            edges.append({
                "id": f"{agent_id}-archive",
                "source": agent_id,
                "target": "station-archive",
                "type": "POSTS_FINDING",
                "count": activity["findings_count"],
                "summary": activity["latest_summary"],
            })
        if activity["hypotheses_count"] > 0:
            edges.append({
                "id": f"{agent_id}-center",
                "source": agent_id,
                "target": "station-center",
                "type": "FORMS_HYPOTHESIS",
                "count": activity["hypotheses_count"],
                "summary": None,
            })

    for edge in await service.queries.get_session_agent_edges(session_id):
        edges.append(
            {
                "id": f"{edge.get('source_agent_id')}-{edge.get('target_agent_id')}-{edge.get('relation_type')}",
                "source": edge.get("source_agent_id"),
                "target": edge.get("target_agent_id"),
                "type": edge.get("relation_type"),
                "count": edge.get("count", 1),
                "weight": edge.get("weight", 0.0),
                "sample_claims": edge.get("sample_claims", []),
            }
        )

    return {
        "question": session["question"],
        "nodes": nodes,
        "edges": edges,
    }


async def _build_agent_detail(
    service: ResearchService,
    store: ProjectionStore,
    session: dict[str, Any],
    agent_id: str,
) -> dict[str, Any]:
    snapshot = _get_snapshot(session)
    agent_lookup = _get_agent_lookup(snapshot)
    agent = agent_lookup.get(agent_id)
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")

    session_id = session["id"]
    persona = agent.get("persona") or {}
    latest_status = _get_latest_agent_status(session).get(agent_id, {})
    work = await service.queries.get_agent_work(agent_id, session_id=session_id)
    metrics = await service.queries.get_agent_metrics(agent_id, session_id=session_id)
    experiments = await service.graph.get_by_label(
        "Experiment",
        filters={"created_by": agent_id, "session_id": session_id},
        limit=10,
    )
    relation_summary_raw = await service.queries.get_agent_relation_summary(session_id, agent_id)
    events = await store.list_events(session_id, limit=200)
    activity = [event for event in events if event.get("agent_id") == agent_id]
    relation_summary = []
    for item in relation_summary_raw:
        other_agent = agent_lookup.get(item.get("other_agent_id"), {})
        relation_summary.append(
            {
                "direction": item.get("direction"),
                "type": item.get("relation_type"),
                "count": item.get("count", 0),
                "weight": item.get("weight", 0.0),
                "other_agent_id": item.get("other_agent_id"),
                "other_label": other_agent.get("name") or item.get("other_agent_id"),
                "other_archetype": _agent_archetype_name(other_agent, snapshot) if other_agent else "unknown",
                "sample_claims": item.get("sample_claims", []),
            }
        )

    hypotheses = sorted(
        work.get("Hypothesis", []),
        key=lambda row: float(row.get("confidence", 0.0) or 0.0),
        reverse=True,
    )
    current_hypothesis = hypotheses[0] if hypotheses else None

    return {
        "agent_id": agent_id,
        "agent_data": {
            "display_name": agent.get("name") or agent_id,
            "status": agent.get("status", "active"),
            "archetype_name": _agent_archetype_name(agent, snapshot),
            "specialty": persona.get("specialty") or "general",
            "line_of_inquiry": agent.get("line_of_inquiry", ""),
        },
        "persona": persona,
        "current_status_text": latest_status.get("status_text") or "idle",
        "current_focus": agent.get("line_of_inquiry", ""),
        "findings_count": metrics.get("findings_count", 0),
        "experiments_count": metrics.get("experiments_count", 0),
        "current_hypothesis": current_hypothesis,
        "recent_artifacts": {
            "notes": work.get("Note", [])[:8],
            "assumptions": work.get("Assumption", [])[:8],
            "hypotheses": hypotheses[:8],
            "findings": work.get("Finding", [])[:8],
        },
        "experiments": experiments,
        "relation_summary": relation_summary,
        "activity_feed": [
            {
                **item,
                "expandable": bool(item.get("payload") or item.get("refs")),
            }
            for item in reversed(activity[-80:])
        ],
        "interview_context": {
            "line_of_inquiry": agent.get("line_of_inquiry", ""),
            "archetype_name": _agent_archetype_name(agent, snapshot),
        },
    }


def create_app() -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        service = ResearchService()
        await service.initialize()
        store = ProjectionStore(service.postgres)
        manager = SessionManager(service, store)
        await manager.startup()
        app.state.service = service
        app.state.store = store
        app.state.manager = manager
        try:
            yield
        finally:
            await manager.shutdown()
            await service.shutdown()

    app = FastAPI(title="ResearchSquid Backend", version="0.1.0", lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.post("/research")
    async def start_research(
        request: Request,
        payload: dict[str, Any] = Body(...),
    ) -> dict[str, Any]:
        question = str(payload.get("question") or "").strip()
        if not question:
            raise HTTPException(status_code=400, detail="question is required")
        return await request.app.state.manager.start_session(payload)

    @app.get("/sessions/{session_id}")
    async def get_session(
        request: Request,
        session_id: str,
    ) -> dict[str, Any]:
        session = await request.app.state.manager.get_session(session_id)
        if not session:
            raise HTTPException(status_code=404, detail="Session not found")
        return {
            "id": session["id"],
            "question": session["question"],
            "status": session["status"],
            "created_at": session.get("created_at").isoformat() if session.get("created_at") else None,
            "updated_at": session.get("updated_at").isoformat() if session.get("updated_at") else None,
        }

    @app.get("/sessions/{session_id}/overview")
    async def get_overview(
        request: Request,
        session_id: str,
    ) -> dict[str, Any]:
        session = await request.app.state.manager.get_session(session_id)
        if not session:
            raise HTTPException(status_code=404, detail="Session not found")
        return await _build_overview(request.app.state.service, session)

    @app.get("/sessions/{session_id}/graph")
    async def get_graph(
        request: Request,
        session_id: str,
    ) -> dict[str, Any]:
        session = await request.app.state.manager.get_session(session_id)
        if not session:
            raise HTTPException(status_code=404, detail="Session not found")
        return await _build_graph_view(request.app.state.service, session)

    @app.get("/sessions/{session_id}/agents")
    async def get_agents(
        request: Request,
        session_id: str,
    ) -> dict[str, Any]:
        session = await request.app.state.manager.get_session(session_id)
        if not session:
            raise HTTPException(status_code=404, detail="Session not found")
        snapshot = _get_snapshot(session)
        latest_status = _get_latest_agent_status(session)
        agents = snapshot.get("agents", []) or []
        metrics_list = await asyncio.gather(
            *[
                request.app.state.service.queries.get_agent_metrics(
                    agent["agent_id"],
                    session_id=session_id,
                )
                for agent in agents
            ]
        ) if agents else []
        return {
            "agents": [
                _build_agent_summary(agent, snapshot, latest_status, metrics)
                for agent, metrics in zip(agents, metrics_list)
            ]
        }

    @app.get("/sessions/{session_id}/agents/{agent_id}")
    async def get_agent_detail(
        request: Request,
        session_id: str,
        agent_id: str,
    ) -> dict[str, Any]:
        session = await request.app.state.manager.get_session(session_id)
        if not session:
            raise HTTPException(status_code=404, detail="Session not found")
        return await _build_agent_detail(
            request.app.state.service,
            request.app.state.store,
            session,
            agent_id,
        )

    @app.get("/sessions/{session_id}/events")
    async def get_events(
        request: Request,
        session_id: str,
        limit: int = Query(default=100, ge=1, le=500),
    ) -> dict[str, Any]:
        session = await request.app.state.manager.get_session(session_id)
        if not session:
            raise HTTPException(status_code=404, detail="Session not found")
        events = await request.app.state.store.list_events(session_id, limit=limit)
        return {"session_id": session_id, "events": list(reversed(events))}

    @app.get("/sessions/{session_id}/stream")
    async def stream_events(
        request: Request,
        session_id: str,
    ) -> StreamingResponse:
        session = await request.app.state.manager.get_session(session_id)
        if not session:
            raise HTTPException(status_code=404, detail="Session not found")

        queue = request.app.state.manager.register_listener(session_id)

        async def event_stream():
            try:
                while True:
                    if await request.is_disconnected():
                        break
                    try:
                        event = await asyncio.wait_for(queue.get(), timeout=15.0)
                    except asyncio.TimeoutError:
                        keepalive = {
                            "timestamp": utc_now_iso(),
                            "kind": "keepalive",
                        }
                        yield f"data: {json.dumps(keepalive)}\n\n"
                        continue
                    yield f"data: {json.dumps(event)}\n\n"
            finally:
                request.app.state.manager.unregister_listener(session_id, queue)

        headers = {
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        }
        return StreamingResponse(event_stream(), media_type="text/event-stream", headers=headers)

    @app.post("/sessions/{session_id}/continue")
    async def continue_session(
        request: Request,
        session_id: str,
        payload: dict[str, Any] = Body(...),
    ) -> dict[str, Any]:
        session = await request.app.state.manager.get_session(session_id)
        if not session:
            raise HTTPException(status_code=404, detail="Session not found")

        additional_budget = int(payload.get("additional_budget", 100))

        state = session.get("state", {})
        snapshot = state.get("snapshot", {})
        current_total = int(snapshot.get("budget_total", 500) or 500)
        current_remaining = int(snapshot.get("budget_remaining", 0) or 0)

        snapshot["budget_total"] = current_total + additional_budget
        snapshot["budget_remaining"] = current_remaining + additional_budget
        snapshot["budget_warning"] = False

        return {
            "session_id": session_id,
            "budget_total": snapshot["budget_total"],
            "budget_remaining": snapshot["budget_remaining"],
            "added_budget": additional_budget,
        }

    @app.get("/sessions/{session_id}/report")
    async def get_report(
        request: Request,
        session_id: str,
    ) -> dict[str, Any]:
        session = await request.app.state.manager.get_session(session_id)
        if not session:
            raise HTTPException(status_code=404, detail="Session not found")
        report = await request.app.state.manager.get_report(session_id)
        overview = await _build_overview(request.app.state.service, session)
        report_state = report or {"status": "pending", "content": "", "generated_at": None}
        return {
            "status": report_state.get("status", "pending"),
            "markdown": report_state.get("content", ""),
            "text": report_state.get("content", ""),
            "generated_at": report_state.get("generated_at"),
            "meta": {
                "question": session["question"],
                "top_hypotheses": overview.get("top_hypotheses", []),
                "contradiction_count": overview.get("contradiction_count", 0),
            },
        }

    @app.get("/sessions/{session_id}/memory/search")
    async def search_memory(
        request: Request,
        session_id: str,
        q: str = Query(..., min_length=1),
        limit: int = Query(default=10, ge=1, le=25),
    ) -> dict[str, Any]:
        session = await request.app.state.manager.get_session(session_id)
        if not session:
            raise HTTPException(status_code=404, detail="Session not found")

        semantic = await request.app.state.service.retriever.retrieve_for_inquiry(
            q,
            top_k=limit,
            session_id=session_id,
        )
        lexical = await request.app.state.service.queries.search_session_text(
            session_id=session_id,
            query_text=q,
            limit=limit,
        )

        deduped: dict[str, dict[str, Any]] = {}
        for item in semantic:
            metadata = item.get("metadata") or {}
            deduped[item["artifact_id"]] = {
                "id": item["artifact_id"],
                "canonical_id": item["artifact_id"],
                "kind": item.get("artifact_type"),
                "title": metadata.get("title") or item.get("text", "")[:80],
                "text": item.get("text", ""),
                "score": item.get("score", 0.0),
                "source_url": metadata.get("url") or metadata.get("source_url"),
                "agent_id": item.get("created_by"),
                "confidence": item.get("confidence"),
            }
        for item in lexical:
            deduped.setdefault(
                item["id"],
                {
                    "id": item["id"],
                    "canonical_id": item["id"],
                    "kind": item.get("kind"),
                    "title": item.get("title"),
                    "text": item.get("text", ""),
                    "score": 0.0,
                    "source_url": None,
                    "agent_id": item.get("created_by"),
                    "confidence": item.get("confidence"),
                },
            )

        results = sorted(deduped.values(), key=lambda row: float(row.get("score") or 0.0), reverse=True)
        return {"query": q, "results": results[:limit]}

    @app.post("/sessions/{session_id}/interview")
    async def interview_agent(
        request: Request,
        session_id: str,
        payload: dict[str, Any] = Body(...),
    ) -> dict[str, Any]:
        session = await request.app.state.manager.get_session(session_id)
        if not session:
            raise HTTPException(status_code=404, detail="Session not found")

        agent_id = str(payload.get("agent_id") or "").strip()
        prompt = str(payload.get("prompt") or payload.get("question") or "").strip()
        if not agent_id or not prompt:
            raise HTTPException(status_code=400, detail="agent_id and prompt are required")

        detail = await _build_agent_detail(
            request.app.state.service,
            request.app.state.store,
            session,
            agent_id,
        )
        persona_dict = detail.get("persona") or {}
        persona_prompt = ""
        if persona_dict:
            try:
                persona_prompt = generate_persona_prompt(AgentPersona(**persona_dict))
            except Exception:
                persona_prompt = ""
        notes = detail.get("recent_artifacts", {}).get("notes", [])[:3]
        hypotheses = detail.get("recent_artifacts", {}).get("hypotheses", [])[:3]
        findings = detail.get("recent_artifacts", {}).get("findings", [])[:3]
        context_lines = []
        for bucket in (notes, hypotheses, findings):
            for item in bucket:
                text = item.get("text") or item.get("title") or item.get("id")
                if text:
                    context_lines.append(f"- {text}")

        system = (
            "You are answering as a research institute agent. Stay grounded in the "
            "agent's actual line of inquiry and recent public work.\n\n"
            f"Agent: {detail['agent_data']['display_name']}\n"
            f"Line of inquiry: {detail['agent_data']['line_of_inquiry']}\n"
            f"Archetype: {detail['agent_data']['archetype_name']}\n\n"
            f"{persona_prompt}\n\n"
            "Recent public work:\n"
            f"{chr(10).join(context_lines) if context_lines else '- No recent public artifacts recorded.'}"
        )
        response = await request.app.state.service.llm.complete(
            prompt=prompt,
            system=system,
        )
        return {
            "agent_id": agent_id,
            "question": prompt,
            "grounded_response": response,
            "timestamp": utc_now_iso(),
        }

    @app.get("/sessions/{session_id}/findings")
    async def get_findings(
        request: Request,
        session_id: str,
        limit: int = Query(default=100, ge=1, le=500),
    ) -> dict[str, Any]:
        session = await request.app.state.manager.get_session(session_id)
        if not session:
            raise HTTPException(status_code=404, detail="Session not found")
        findings = await request.app.state.service.graph.get_by_label(
            "Finding",
            filters={"session_id": session_id},
            limit=limit,
        )
        return {"findings": findings}

    @app.get("/sessions/{session_id}/findings/{finding_id}")
    async def get_finding_detail(
        request: Request,
        session_id: str,
        finding_id: str,
    ) -> dict[str, Any]:
        session = await request.app.state.manager.get_session(session_id)
        if not session:
            raise HTTPException(status_code=404, detail="Session not found")
        finding = await request.app.state.service.graph.get(finding_id)
        if not finding:
            raise HTTPException(status_code=404, detail="Finding not found")
        neighbors = await request.app.state.service.queries.get_neighbors(
            finding_id,
            direction="both",
            limit=20,
        )
        return {"finding": finding, "relations": neighbors}

    @app.get("/sessions/{session_id}/experiments")
    async def get_experiments(
        request: Request,
        session_id: str,
        limit: int = Query(default=100, ge=1, le=500),
    ) -> dict[str, Any]:
        session = await request.app.state.manager.get_session(session_id)
        if not session:
            raise HTTPException(status_code=404, detail="Session not found")
        experiments = await request.app.state.service.graph.get_by_label(
            "Experiment",
            filters={"session_id": session_id},
            limit=limit,
        )
        return {"experiments": experiments}

    @app.get("/sessions/{session_id}/experiments/{experiment_id}")
    async def get_experiment_detail(
        request: Request,
        session_id: str,
        experiment_id: str,
    ) -> dict[str, Any]:
        session = await request.app.state.manager.get_session(session_id)
        if not session:
            raise HTTPException(status_code=404, detail="Session not found")
        experiment = await request.app.state.service.graph.get(experiment_id)
        if not experiment:
            raise HTTPException(status_code=404, detail="Experiment not found")
        neighbors = await request.app.state.service.queries.get_neighbors(
            experiment_id,
            direction="both",
            limit=20,
        )
        return {"experiment": experiment, "relations": neighbors}

    @app.get("/sessions/{session_id}/clusters")
    async def get_clusters(
        request: Request,
        session_id: str,
        limit: int = Query(default=50, ge=1, le=200),
    ) -> dict[str, Any]:
        session = await request.app.state.manager.get_session(session_id)
        if not session:
            raise HTTPException(status_code=404, detail="Session not found")
        clusters = await request.app.state.service.graph.get_by_label(
            "Cluster",
            filters={"session_id": session_id},
            limit=limit,
        )
        return {"clusters": clusters}

    @app.get("/sessions/{session_id}/clusters/{cluster_id}")
    async def get_cluster_detail(
        request: Request,
        session_id: str,
        cluster_id: str,
    ) -> dict[str, Any]:
        session = await request.app.state.manager.get_session(session_id)
        if not session:
            raise HTTPException(status_code=404, detail="Session not found")
        cluster = await request.app.state.service.graph.get(cluster_id)
        if not cluster:
            raise HTTPException(status_code=404, detail="Cluster not found")
        neighbors = await request.app.state.service.queries.get_neighbors(
            cluster_id,
            direction="both",
            limit=50,
        )
        return {"cluster": cluster, "members": neighbors}

    # ── Workspace endpoints ────────────────────────────────────────────────

    @app.get("/sessions/{session_id}/workspaces")
    async def list_workspaces(
        request: Request,
        session_id: str,
    ) -> dict[str, Any]:
        """List all agent workspaces for a session."""
        session = await request.app.state.manager.get_session(session_id)
        if not session:
            raise HTTPException(status_code=404, detail="Session not found")
        workspaces = await request.app.state.service.list_workspaces(session_id)
        return {"workspaces": workspaces}

    @app.get("/sessions/{session_id}/workspaces/{agent_id}/files")
    async def list_workspace_files(
        request: Request,
        session_id: str,
        agent_id: str,
        path: str = Query("", description="Directory path within workspace"),
    ) -> dict[str, Any]:
        """List files in an agent's workspace."""
        session = await request.app.state.manager.get_session(session_id)
        if not session:
            raise HTTPException(status_code=404, detail="Session not found")
        files = await request.app.state.service.list_workspace_files(
            session_id, agent_id, path
        )
        return {"files": files}

    @app.get("/sessions/{session_id}/workspaces/{agent_id}/files/{file_path:path}")
    async def get_workspace_file(
        request: Request,
        session_id: str,
        agent_id: str,
        file_path: str,
    ) -> dict[str, Any]:
        """Get the content of a file from an agent's workspace."""
        session = await request.app.state.manager.get_session(session_id)
        if not session:
            raise HTTPException(status_code=404, detail="Session not found")
        try:
            content = await request.app.state.service.read_workspace_file(
                session_id, agent_id, file_path
            )
            return {"path": file_path, "content": content}
        except FileNotFoundError:
            raise HTTPException(status_code=404, detail="File not found")

    @app.get("/sessions/{session_id}/workspaces/{agent_id}/opencode")
    async def get_opencode_sessions(
        request: Request,
        session_id: str,
        agent_id: str,
    ) -> dict[str, Any]:
        """Get OpenCode session history for an agent."""
        session = await request.app.state.manager.get_session(session_id)
        if not session:
            raise HTTPException(status_code=404, detail="Session not found")
        sessions = await request.app.state.service.get_opencode_sessions(
            session_id, agent_id
        )
        return {"sessions": sessions}

    return app


app = create_app()
