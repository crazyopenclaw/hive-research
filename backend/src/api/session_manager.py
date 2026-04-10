"""
Background session runner plus API-facing event/projection manager.

The research engine remains unchanged. This layer translates engine events into
stable UI events, persists session projections, and fans live updates out to SSE
subscribers.
"""

from __future__ import annotations

import asyncio
import logging
from collections import defaultdict
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from src.api.projection_store import ProjectionStore
from src.api.service import ResearchService
from src.models.events import Event, EventType

logger = logging.getLogger(__name__)


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def deep_merge(base: dict[str, Any], patch: dict[str, Any]) -> dict[str, Any]:
    merged = deepcopy(base)
    for key, value in patch.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def summarize_event(event: Event) -> tuple[str, str, str, str]:
    payload = event.payload or {}
    kind = event.event_type.value
    title = kind.replace("_", " ").title()
    summary = ""
    status_text = ""

    if event.event_type == EventType.RESEARCH_STARTED:
        kind = "session_started"
        title = "Research started"
        summary = payload.get("question", "")
    elif event.event_type == EventType.RESEARCH_COMPLETED:
        kind = "session_completed"
        title = "Research completed"
        summary = (
            f"{payload.get('iterations', 0)} iterations, "
            f"${payload.get('budget_used', 0.0):.2f} spent"
        )
    elif event.event_type == EventType.AGENT_SPAWNED:
        kind = "agent_spawned"
        title = payload.get("name") or event.agent_id or "Agent spawned"
        summary = payload.get("inquiry", "")
        status_text = "ready"
    elif event.event_type == EventType.AGENT_THINKING:
        kind = "agent_thinking"
        title = "Agent thinking"
        summary = payload.get("inquiry", "")
        status_text = f"thinking: {payload.get('inquiry', 'working')}"
    elif event.event_type == EventType.AGENT_ACTION:
        action = str(payload.get("action") or "").strip()
        if action:
            kind = action
            title = action.replace("_", " ").title()
            if action == "downloading_source":
                title = "Downloading source"
                summary = payload.get("title") or payload.get("arxiv_id", "")
                status_text = "downloading paper"
            elif action == "download_source_progress":
                title = "Downloading source"
                summary = (
                    f"{payload.get('title') or payload.get('arxiv_id', '')} "
                    f"({payload.get('progress', 0)}%)"
                ).strip()
                status_text = "downloading paper"
            elif action == "ingesting_source":
                title = "Ingesting source"
                summary = payload.get("title") or payload.get("arxiv_id", "")
                status_text = "ingesting source"
            elif action == "ingested_search_source":
                title = "Source ingested from search"
                summary = payload.get("title") or payload.get("source_id", "")
                status_text = "reading source"
            elif action == "search_source_already_ingested":
                title = "Reused ingested source"
                summary = payload.get("title") or payload.get("arxiv_id", "")
                status_text = "reusing source"
            elif action == "reviewing_hypothesis":
                title = "Reviewing hypothesis"
                summary = payload.get("hypothesis_text", "")
                status_text = "reviewing hypothesis"
            elif action == "reviewed_hypothesis":
                title = "Hypothesis reviewed"
                summary = payload.get("hypothesis_text", "")
                status_text = f"review: {payload.get('verdict', 'done')}"
            else:
                summary_bits = []
                for key in (
                    "reason",
                    "verdict",
                    "subproblems_count",
                    "archetypes_count",
                    "completed_reviews",
                    "completed_pairs",
                    "targets",
                ):
                    value = payload.get(key)
                    if value not in (None, "", [], {}):
                        summary_bits.append(f"{key.replace('_', ' ')}: {value}")
                summary = " | ".join(summary_bits)
            if action == "paused":
                status_text = "paused"
            elif action == "decomposed_question":
                status_text = "planning"
        else:
            created_bits = []
            for label in ("notes", "hypotheses", "relations", "experiments"):
                value = payload.get(label)
                if value not in (None, 0):
                    created_bits.append(f"{label}: {value}")
            kind = "agent_action"
            title = "Agent action"
            summary = " | ".join(created_bits)
            status_text = "updated artifacts" if created_bits else "working"
    elif event.event_type == EventType.SOURCE_DISCOVERED:
        source_name = str(payload.get("source") or "").strip().lower()
        kind = "search_completed"
        title = "Search completed"
        summary = payload.get("query", "")
        status_text = f"searching: {payload.get('query', 'query')}"
        if source_name == "arxiv":
            kind = "arxiv_search_completed"
            title = "arXiv search completed"
    elif event.event_type == EventType.SOURCE_INGESTED:
        kind = "source_ingested"
        title = payload.get("title") or "Source ingested"
        summary = (
            f"{payload.get('chunks_count', 0)} chunks, "
            f"{payload.get('summaries_count', 0)} summaries"
        )
        status_text = "reading source"
    elif event.event_type == EventType.EXPERIMENT_STARTED:
        kind = "experiment_started"
        title = "Sandbox run started"
        summary = payload.get("expected_outcome", "") or payload.get("experiment_id", "")
        status_text = "running sandbox"
    elif event.event_type == EventType.EXPERIMENT_COMPLETED:
        kind = "experiment_completed"
        title = "Sandbox run completed"
        summary = payload.get("stdout_preview", "") or (
            f"exit {payload.get('exit_code', '?')} in "
            f"{payload.get('execution_time', 0)}s"
        )
        status_text = "sandbox complete"
    elif event.event_type == EventType.EXPERIMENT_FAILED:
        kind = "experiment_failed"
        title = "Sandbox run failed"
        summary = payload.get("error", "") or payload.get("stderr_preview", "")
        status_text = "sandbox failed"
    elif event.event_type == EventType.ITERATION_STARTED:
        kind = "iteration_started"
        title = f"Iteration {payload.get('iteration', '?')} started"
        summary = ""
    elif event.event_type == EventType.ITERATION_COMPLETED:
        kind = "iteration_completed"
        title = f"Iteration {payload.get('iteration', '?')} completed"
        summary = payload.get("reasoning", "")
    elif event.event_type == EventType.DEBATE_STARTED:
        kind = "debate_started"
        title = "Debate round started"
    elif event.event_type == EventType.DEBATE_COMPLETED:
        kind = "debate_completed"
        title = "Debate round completed"
    elif event.event_type == EventType.RELATION_CREATED:
        relation_type = str(payload.get("relation_type") or "relation")
        kind = "relation_created"
        title = f"{relation_type.replace('_', ' ').title()} relation"
        summary = (
            f"{payload.get('source_id', '')} -> {payload.get('target_id', '')}"
        )
        status_text = "linking claims"
    elif event.event_type == EventType.MESSAGE_SENT:
        kind = "message_sent"
        title = "Agent message sent"
        summary = f"to {payload.get('to_agent', 'agent')}"
    elif event.event_type == EventType.ARTIFACT_CREATED:
        artifact_type = str(event.artifact_type or payload.get("label", "")).lower()
        if artifact_type in {"sourcechunk", "source_chunk", "source", "message", "relation"}:
            return "", "", "", ""
        if artifact_type == "experiment":
            kind = "experiment_queued"
            title = "Experiment queued"
        else:
            kind = f"{artifact_type}_created" if artifact_type else "artifact_created"
            title = f"Created {artifact_type or 'artifact'}"
        summary = event.artifact_id
        status_map = {
            "note": "writing notes",
            "assumption": "stating assumption",
            "hypothesis": "forming hypothesis",
            "finding": "posting finding",
            "experiment": "queueing experiment",
            "experimentresult": "recording result",
        }
        status_text = status_map.get(artifact_type, "")
    elif event.event_type == EventType.ARTIFACT_REFUTED:
        kind = "artifact_refuted"
        title = "Artifact refuted"
        summary = event.artifact_id
        status_text = "refuted"
    elif event.event_type == EventType.ARTIFACT_UPDATED:
        updated_fields = payload.get("updated_fields", []) or []
        properties = payload.get("properties", {}) or {}
        label = str(payload.get("label") or "").strip()
        if updated_fields == ["read"] or updated_fields == ["embedding_id"]:
            return "", "", "", ""
        if sorted(updated_fields) == ["file_path", "source_type", "title", "uri"]:
            return "", "", "", ""
        if label == "Experiment" and "status" in properties:
            return "", "", "", ""
        if "status" in properties:
            kind = "status_updated"
            title = "Status updated"
            summary = f"{label or 'Artifact'} -> {properties.get('status', '?')}"
            status_text = str(properties.get("status") or "")
        elif "adjudication_status" in properties:
            kind = "adjudication_updated"
            title = "Adjudication updated"
            summary = (
                f"{label or 'Artifact'} -> "
                f"{properties.get('adjudication_status', '?')}"
            )
        else:
            kind = "artifact_updated"
            title = "Artifact updated"
            summary = ", ".join(updated_fields)
    elif event.event_type == EventType.ERROR:
        kind = "error"
        title = "Error"
        summary = payload.get("error", "")
        status_text = "error"

    return kind, title, summary, status_text


def translate_event(event: Event) -> dict[str, Any] | None:
    if event.event_type == EventType.STATE_SNAPSHOT:
        return None

    kind, title, summary, status_text = summarize_event(event)
    if not kind:
        return None

    refs: dict[str, Any] = {}
    if event.artifact_id:
        refs["artifact_id"] = event.artifact_id
    if event.artifact_type:
        refs["artifact_type"] = event.artifact_type

    return {
        "timestamp": event.timestamp.isoformat(),
        "session_id": event.session_id,
        "agent_id": event.agent_id or None,
        "kind": kind,
        "title": title,
        "summary": summary,
        "payload": event.payload or {},
        "refs": refs,
        "status_text": status_text,
    }


@dataclass
class SessionTaskRecord:
    session_id: str
    task: asyncio.Task[Any]


class SessionManager:
    """Owns session background tasks, translated event persistence, and SSE fanout."""

    def __init__(
        self,
        service: ResearchService,
        store: ProjectionStore,
        config: Any = None,
    ) -> None:
        self._service = service
        self._store = store
        self._config = config or service._config
        self._tasks: dict[str, SessionTaskRecord] = {}
        self._listeners: dict[str, set[asyncio.Queue[dict[str, Any]]]] = defaultdict(set)
        self._locks: dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)
        self._cache: dict[str, dict[str, Any]] = {}
        self._subscribed = False
        self._shutting_down = False

    async def startup(self) -> None:
        self._shutting_down = False
        await self._store.mark_nonterminal_sessions_interrupted()
        if not self._subscribed:
            self._service.event_bus.subscribe("*", self._handle_engine_event)
            self._subscribed = True

    async def shutdown(self) -> None:
        self._shutting_down = True
        if self._subscribed:
            self._service.event_bus.unsubscribe("*", self._handle_engine_event)
            self._subscribed = False
        task_records = list(self._tasks.values())
        for record in task_records:
            if not record.task.done():
                record.task.cancel()
        if task_records:
            await asyncio.gather(
                *(record.task for record in task_records),
                return_exceptions=True,
            )
        self._tasks.clear()

    async def start_session(self, payload: dict[str, Any]) -> dict[str, Any]:
        session_id = uuid4().hex[:12]
        question = str(payload.get("question") or "").strip()
        created_at = utc_now_iso()
        llm_budget_usd = payload.get("llm_budget_usd") or payload.get("budget") or 0
        state = {
            "config": payload,
            "snapshot": {
                "question": question,
                "status": "starting",
                "iteration": 0,
                "budget_total": self._config.default_budget,
                "budget_remaining": self._config.default_budget,
                "llm_budget_usd": llm_budget_usd,
                "num_agents": payload.get("agent_count") or payload.get("num_agents") or 0,
            },
            "latest_agent_status": {},
            "report": {
                "status": "pending",
                "content": "",
                "generated_at": None,
            },
            "error": None,
        }

        await self._store.create_session(
            session_id=session_id,
            question=question,
            state=state,
            status="starting",
        )
        self._cache[session_id] = {
            "id": session_id,
            "question": question,
            "state": state,
            "status": "starting",
            "created_at": datetime.fromisoformat(created_at),
            "updated_at": datetime.fromisoformat(created_at),
        }

        task = asyncio.create_task(self._run_session(session_id, payload))
        self._tasks[session_id] = SessionTaskRecord(session_id=session_id, task=task)
        return {
            "id": session_id,
            "question": question,
            "status": "starting",
            "created_at": created_at,
        }

    async def get_session(self, session_id: str) -> dict[str, Any] | None:
        cached = self._cache.get(session_id)
        if cached:
            return cached
        session = await self._store.get_session(session_id)
        if session:
            self._cache[session_id] = session
        return session

    async def get_report(self, session_id: str) -> dict[str, Any] | None:
        session = await self.get_session(session_id)
        if not session:
            return None
        return (session.get("state") or {}).get("report")

    def register_listener(self, session_id: str) -> asyncio.Queue[dict[str, Any]]:
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        self._listeners[session_id].add(queue)
        return queue

    def unregister_listener(
        self,
        session_id: str,
        queue: asyncio.Queue[dict[str, Any]],
    ) -> None:
        if session_id in self._listeners:
            self._listeners[session_id].discard(queue)
            if not self._listeners[session_id]:
                self._listeners.pop(session_id, None)

    async def _run_session(self, session_id: str, payload: dict[str, Any]) -> None:
        question = str(payload.get("question") or "").strip()
        try:
            result = await self._service.start_research(
                question=question,
                sources=payload.get("sources") or [],
                num_agents=payload.get("agent_count") or payload.get("num_agents"),
                budget=payload.get("llm_budget_usd") or payload.get("budget"),
                max_iterations=payload.get("max_iterations"),
                session_id=session_id,
            )
            report = str(result.get("final_report") or "").strip()
            async with self._locks[session_id]:
                session = await self.get_session(session_id)
                if not session:
                    return
                state = deepcopy(session.get("state") or {})
                snapshot = state.setdefault("snapshot", {})
                snapshot["status"] = "completed"
                if report:
                    state["report"] = {
                        "status": "ready",
                        "content": report,
                        "generated_at": utc_now_iso(),
                    }
                session["state"] = state
                session["status"] = "completed"
                session["updated_at"] = datetime.now(timezone.utc)
                self._cache[session_id] = session
                await self._safe_upsert_session_state(
                    session_id=session_id,
                    question=question,
                    state=state,
                    status="completed",
                )
                if report:
                    report_event = {
                        "timestamp": utc_now_iso(),
                        "session_id": session_id,
                        "agent_id": None,
                        "kind": "report_generated",
                        "title": "Report generated",
                        "summary": "Final synthesis is ready.",
                        "payload": {"length": len(report)},
                        "refs": {},
                        "status_text": "",
                    }
                    await self._safe_append_event(session_id, report_event)
                    await self._broadcast(session_id, report_event)
        except asyncio.CancelledError:
            await self._mark_failed(
                session_id,
                question,
                (
                    "Session interrupted by backend shutdown."
                    if self._shutting_down
                    else "Session cancelled."
                ),
                status="interrupted" if self._shutting_down else "cancelled",
            )
            raise
        except Exception as exc:
            logger.exception("Background research session %s failed", session_id)
            await self._mark_failed(session_id, question, str(exc), status="failed")
        finally:
            self._tasks.pop(session_id, None)

    async def _mark_failed(
        self,
        session_id: str,
        question: str,
        error_text: str,
        status: str,
    ) -> None:
        async with self._locks[session_id]:
            session = await self.get_session(session_id)
            if not session:
                return
            state = deepcopy(session.get("state") or {})
            state["error"] = error_text
            snapshot = state.setdefault("snapshot", {})
            snapshot["status"] = status
            session["state"] = state
            session["status"] = status
            session["updated_at"] = datetime.now(timezone.utc)
            self._cache[session_id] = session
            await self._safe_upsert_session_state(
                session_id=session_id,
                question=question,
                state=state,
                status=status,
            )

        ui_event = {
            "timestamp": utc_now_iso(),
            "session_id": session_id,
            "agent_id": None,
            "kind": "error",
            "title": "Session failed",
            "summary": error_text,
            "payload": {"error": error_text},
            "refs": {},
            "status_text": "",
        }
        await self._safe_append_event(session_id, ui_event)
        await self._broadcast(session_id, ui_event)

    async def _handle_engine_event(self, event: Event) -> None:
        if self._shutting_down:
            return
        session_id = event.session_id
        if not session_id:
            return

        async with self._locks[session_id]:
            session = await self.get_session(session_id)
            if not session:
                return

            question = session.get("question", "")
            state = deepcopy(session.get("state") or {})
            snapshot = deepcopy(state.get("snapshot") or {})
            latest_agent_status = deepcopy(state.get("latest_agent_status") or {})
            session_status = session.get("status", "active")

            if event.event_type == EventType.STATE_SNAPSHOT:
                snapshot = deep_merge(snapshot, event.payload or {})
                if snapshot.get("status"):
                    session_status = str(snapshot["status"])
            elif event.event_type == EventType.RESEARCH_STARTED:
                snapshot["status"] = "active"
                session_status = "active"
            elif event.event_type == EventType.RESEARCH_COMPLETED:
                snapshot["status"] = "completed"
                session_status = "completed"

            ui_event = translate_event(event)
            if ui_event and event.agent_id:
                latest_agent_status[event.agent_id] = {
                    "status_text": ui_event.get("status_text") or ui_event.get("title"),
                    "kind": ui_event.get("kind"),
                    "timestamp": ui_event.get("timestamp"),
                }

            state["snapshot"] = snapshot
            state["latest_agent_status"] = latest_agent_status
            session["state"] = state
            session["status"] = session_status
            session["updated_at"] = datetime.now(timezone.utc)
            self._cache[session_id] = session

            await self._safe_upsert_session_state(
                session_id=session_id,
                question=question,
                state=state,
                status=session_status,
            )

            if ui_event:
                await self._safe_append_event(session_id, ui_event)
                await self._broadcast(session_id, ui_event)

    async def _broadcast(self, session_id: str, event: dict[str, Any]) -> None:
        for queue in list(self._listeners.get(session_id, set())):
            await queue.put(event)

    def _should_skip_store_error(self, exc: Exception) -> bool:
        if not self._shutting_down:
            return False
        return "Postgres engine not initialized" in str(exc)

    async def _safe_upsert_session_state(
        self,
        session_id: str,
        question: str,
        state: dict[str, Any],
        status: str,
    ) -> None:
        try:
            await self._store.upsert_session_state(
                session_id=session_id,
                question=question,
                state=state,
                status=status,
            )
        except RuntimeError as exc:
            if self._should_skip_store_error(exc):
                logger.debug(
                    "Skipping session state persistence during shutdown for %s",
                    session_id,
                )
                return
            raise

    async def _safe_append_event(
        self,
        session_id: str,
        event: dict[str, Any],
    ) -> None:
        try:
            await self._store.append_event(session_id, event)
        except RuntimeError as exc:
            if self._should_skip_store_error(exc):
                logger.debug(
                    "Skipping event persistence during shutdown for %s",
                    session_id,
                )
                return
            raise
