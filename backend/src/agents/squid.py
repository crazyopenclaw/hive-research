"""
Squid agent — the core researcher of the institute.

Each Squid owns a line of inquiry (a subproblem from the Director).
It reads sources, writes notes, forms hypotheses, designs experiments,
and engages with other agents' work through relations and messages.

Each squid has a unique AgentPersona that shapes its behavior
through prompt injection and model selection. A skeptic reasons
differently from an empiricist, even on the same subproblem.

The squid's research cycle is a mentor-gated ReAct loop:
think → decide action (search/fetch/read/produce) → execute → observe
Every `mentor_check_interval` steps, the Director evaluates progress
and can continue, redirect, or stop the squid.
"""

import asyncio
import logging
import time
from collections import defaultdict
from typing import Any, Callable, Coroutine

from pydantic import BaseModel, Field

from src.config import Settings, settings as default_settings
from src.events.bus import EventBus
from src.graph.repository import GraphRepository
from src.llm.client import LLMClient
from src.llm.prompts import SQUID_SYSTEM, SQUID_THINK, SQUID_PRODUCE
from src.models.agent_state import SquidState, SquidAction, SquidObservation, MentorVerdict, Subproblem
from src.models.claim import Assumption, Hypothesis
from src.models.events import Event, EventType
from src.models.experiment import Experiment, ExperimentSpec
from src.models.message import Message, MessageType, MESSAGE_PRIORITY
from src.models.note import Note
from src.models.persona import AgentPersona, generate_persona_prompt
from src.models.relation import Relation, RelationType
from src.rag.indexer import RAGIndexer
from src.rag.retriever import RAGRetriever
from src.search.arxiv import ArxivSearch
from src.search.tavily import TavilySearch
from src.agents.workspace_tools import WorkspaceTools, OpenCodeTask

logger = logging.getLogger(__name__)


class SquidOutput(BaseModel):
    """Structured output from a Squid's analysis cycle."""

    notes: list[dict[str, Any]] = Field(default_factory=list)
    assumptions: list[dict[str, Any]] = Field(default_factory=list)
    hypotheses: list[dict[str, Any]] = Field(default_factory=list)
    relations: list[dict[str, Any]] = Field(default_factory=list)
    experiment_proposals: list[dict[str, Any]] = Field(default_factory=list)
    messages: list[dict[str, Any]] = Field(default_factory=list)
    search_queries: list[dict[str, Any]] = Field(default_factory=list)
    key_facts: list[dict[str, Any]] = Field(default_factory=list)
    opencode_task: OpenCodeTask | None = Field(
        default=None,
        description=(
            "Optional code exploration task to delegate to OpenCode. "
            "Only set when code analysis would materially advance a hypothesis."
        ),
    )


class SquidAgent:
    """
    A research squid that investigates a specific subproblem.

    Uses a mentor-gated ReAct loop: think → act → observe → repeat.
    Every `mentor_check_interval` steps, the Director evaluates whether
    the squid should continue, be redirected, or stop and produce findings.

    Only the global budget cap and Director verdict can stop the loop.
    There is no fixed step count limit.
    """

    def __init__(
        self,
        llm: LLMClient,
        graph: GraphRepository,
        retriever: RAGRetriever,
        indexer: RAGIndexer | None,
        event_bus: EventBus,
        tavily: TavilySearch | None = None,
        arxiv_search: ArxivSearch | None = None,
        config: Settings | None = None,
        workspace_tools: WorkspaceTools | None = None,
        graph_queries: Any | None = None,
        agent_memory: Any | None = None,
        search_cache: dict | None = None,
        mentor: Any | None = None,
        team_progress_fn: Callable[..., Coroutine[Any, Any, str]] | None = None,
        hindsight_fn: Callable[..., Coroutine[Any, Any, str]] | None = None,
        global_budget_fn: Callable[[], float] | None = None,
    ) -> None:
        self._llm = llm
        self._graph = graph
        self._retriever = retriever
        self._indexer = indexer
        self._bus = event_bus
        self._tavily = tavily
        self._arxiv = arxiv_search
        self._config = config or default_settings
        self._workspace = workspace_tools
        self._graph_queries = graph_queries
        self._memory = agent_memory
        self._search_cache = search_cache
        self._mentor = mentor
        self._team_progress_fn = team_progress_fn
        self._hindsight_fn = hindsight_fn
        self._global_budget_fn = global_budget_fn
        self._source_ingest_locks: dict[str, asyncio.Lock] = defaultdict(
            asyncio.Lock
        )

    async def run(self, state: SquidState) -> dict[str, Any]:
        """
        Execute a mentor-gated ReAct research cycle for this squid.

        Instead of a single LLM call, the squid loops:
            think → decide action → execute → observe → (mentor check every N steps)
        The Director evaluates progress and can continue, redirect, or stop.
        The loop only ends on: produce action, mentor stop, or global budget exhaustion.
        """
        squid_start = time.time()
        agent_id = state["agent_id"]
        agent_name = state["agent_name"]
        subproblem = state["subproblem"]
        query = subproblem["question"]
        success_criteria = subproblem.get("success_criteria", "")
        research_strategy = subproblem.get("research_strategy", "")
        session_id = state.get("session_id", "")
        research_question = state.get("research_question", query)

        persona_dict = state.get("persona", {})
        persona = AgentPersona(**persona_dict) if persona_dict else None

        await self._bus.publish(Event(
            event_type=EventType.AGENT_THINKING,
            agent_id=agent_id,
            payload={
                "inquiry": query,
                "archetype": persona.archetype_id if persona else None,
                "model_tier": persona.model_tier if persona else "default",
                "phase": "react_loop_start",
            },
        ))

        # ── ReAct loop state ──────────────────────────────────────
        observations: list[dict[str, Any]] = list(state.get("observations", []))
        step = state.get("step_count", 0)
        total_cost = 0.0
        budget_total = state.get("budget_remaining_usd", 0.0)

        # ── ReAct loop ────────────────────────────────────────────
        while True:
            # CHECK: Global budget exhausted
            current_budget = (
                self._global_budget_fn() if self._global_budget_fn
                else budget_total
            )
            if current_budget <= 0:
                logger.info("Squid %s: global budget exhausted, forcing produce", agent_id[:12])
                context = await self._gather_context(agent_id, query, session_id)
                return await self._force_produce(context, observations, state, persona)

            # THINK: Decide next action
            context = await self._gather_context(agent_id, query, session_id)
            action = await self._think(context, observations, state, persona, step)
            total_cost += 0.0  # cost tracked by usage_accumulator inside _think

            # PRODUCE: Final step — produce all artifacts
            if action.action_type == "produce" or step >= 50:
                result = await self._produce_findings(
                    context, observations, state, persona
                )
                await self._store_artifacts(result, agent_id, state)
                await self._post_production(result, state, agent_id, session_id, persona, query)
                result["spent_usd"] = total_cost
                return result

            # ACT + OBSERVE: Execute the chosen action
            observation = await self._execute_action(action, agent_id, session_id)
            if observation:
                observations.append(observation.model_dump())
            step += 1

            await self._bus.publish(Event(
                event_type=EventType.AGENT_ACTION,
                agent_id=agent_id,
                payload={
                    "action": "react_step",
                    "step": step,
                    "action_type": action.action_type,
                    "query_or_url": action.query or action.url or action.artifact_id or "",
                },
            ))

            # MENTOR GATE: Every N steps, evaluate progress
            check_interval = self._config.mentor_check_interval
            if (
                step % check_interval == 0
                and self._mentor is not None
            ):
                verdict = await self._check_mentor(
                    agent_id, subproblem, observations, step,
                    total_cost, budget_total, research_question,
                )
                if verdict.action == "stop":
                    logger.info(
                        "Squid %s: mentor says stop at step %d", agent_id[:12], step
                    )
                    context = await self._gather_context(agent_id, query, session_id)
                    result = await self._produce_findings(
                        context, observations, state, persona
                    )
                    await self._store_artifacts(result, agent_id, state)
                    await self._post_production(result, state, agent_id, session_id, persona, query)
                    result["spent_usd"] = total_cost
                    return result

                if verdict.action == "redirect" and verdict.direction:
                    observations.append({
                        "action_type": "mentor_redirect",
                        "query_or_url": "mentor_direction",
                        "result_summary": f"Mentor redirect: {verdict.direction}",
                        "source_ids": [],
                        "key_data_points": [verdict.direction],
                    })

            # Hard safety cap on step count (should rarely be hit)
            if step >= 50:
                logger.warning("Squid %s: hit hard step cap 50, forcing produce", agent_id[:12])
                context = await self._gather_context(agent_id, query, session_id)
                result = await self._produce_findings(
                    context, observations, state, persona
                )
                await self._store_artifacts(result, agent_id, state)
                await self._post_production(result, state, agent_id, session_id, persona, query)
                result["spent_usd"] = total_cost
                return result

    # ── ReAct Step Methods ────────────────────────────────────────

    async def _gather_context(
        self, agent_id: str, query: str, session_id: str
    ) -> dict[str, list[dict]]:
        """Retrieve fresh RAG + graph context."""
        return await self._retriever.retrieve_agent_context(
            agent_id, query,
            top_k=self._config.retrieval_agent_context_top_k,
            session_id=session_id,
            graph_queries=self._graph_queries,
        )

    async def _think(
        self,
        context: dict[str, list[dict]],
        observations: list[dict[str, Any]],
        state: SquidState,
        persona: AgentPersona | None,
        step: int,
    ) -> SquidAction:
        """Decide next action based on context and accumulated observations."""
        agent_id = state["agent_id"]
        agent_name = state["agent_name"]
        subproblem = state["subproblem"]
        query = subproblem["question"]
        session_id = state.get("session_id", "")

        # Format context
        source_chunks = self._format_artifacts(context.get("source_chunk", []))
        existing_work = self._format_existing_work(context)
        memory_section = await self._build_memory_section(agent_id, query, context, session_id)
        briefing_section = self._build_briefing_section(state)
        strategy_section = self._build_strategy_section(subproblem)
        observations_section = self._format_observations(observations)

        system_prompt = SQUID_SYSTEM
        if persona:
            persona_block = generate_persona_prompt(persona, config=self._config)
            system_prompt = f"{SQUID_SYSTEM}\n\n{persona_block}"

        prompt = SQUID_THINK.format(
            agent_name=agent_name,
            agent_id=agent_id,
            line_of_inquiry=query,
            subproblem=query,
            success_criteria=subproblem.get("success_criteria", ""),
            research_strategy=subproblem.get("research_strategy", ""),
            context_section=source_chunks or "No source material found yet.",
            observations_section=observations_section or "No observations yet.",
            existing_work=existing_work or "No existing work from other agents.",
            step_number=step + 1,
        ) + briefing_section + memory_section + strategy_section

        usage: dict[str, Any] = {"cost": 0.0}
        try:
            if persona:
                action = await self._llm.complete_structured_for_persona(
                    prompt=prompt,
                    response_model=SquidAction,
                    persona=persona,
                    system=system_prompt,
                    temperature=self._config.temperature_squid,
                    usage_accumulator=usage,
                )
            else:
                action = await self._llm.complete_structured(
                    prompt=prompt,
                    response_model=SquidAction,
                    system=system_prompt,
                    temperature=self._config.temperature_squid,
                    usage_accumulator=usage,
                )
        except Exception as exc:
            logger.warning("Squid %s think step failed, defaulting to produce: %s", agent_id[:12], exc)
            return SquidAction(action_type="produce", reasoning=f"Think step failed: {exc}")

        logger.info(
            "Squid %s step %d: %s — %s",
            agent_id[:12], step + 1, action.action_type, action.reasoning[:80]
        )
        return action

    async def _execute_action(
        self, action: SquidAction, agent_id: str, session_id: str
    ) -> SquidObservation | None:
        """Execute the chosen action and return an observation."""
        if action.action_type == "search":
            return await self._execute_search_action(action, agent_id, session_id)
        elif action.action_type == "fetch":
            return await self._execute_fetch_action(action, agent_id, session_id)
        elif action.action_type == "read":
            return await self._execute_read_action(action, agent_id, session_id)
        return None

    async def _execute_search_action(
        self, action: SquidAction, agent_id: str, session_id: str
    ) -> SquidObservation:
        """Execute a single search query and return observation."""
        query = action.query.strip()
        source = action.source
        if not query:
            return SquidObservation(
                action_type="search",
                query_or_url="",
                result_summary="Empty search query, no action taken.",
                source_ids=[],
                key_data_points=[],
            )

        cache_key = (query.lower().strip(), source)
        if self._search_cache is not None and cache_key in self._search_cache:
            return SquidObservation(
                action_type="search",
                query_or_url=query,
                result_summary=f"Search for '{query[:80]}' was already performed (cached).",
                source_ids=[],
                key_data_points=[],
            )

        summaries = []
        source_ids = []
        key_points = []

        if source == "arxiv" and self._arxiv:
            papers = await self._arxiv.search(
                query, max_results=self._config.squid_search_max_results, agent_id=agent_id,
            )
            if papers:
                if self._indexer:
                    await self._ingest_arxiv_results(papers, agent_id=agent_id, session_id=session_id)
                for p in papers[:3]:
                    summaries.append(f"[arXiv] {p.get('title', '?')} ({p.get('year', '?')})")
                    key_points.append(f"Paper: {p.get('title', '?')}")
            if self._search_cache is not None:
                self._search_cache[cache_key] = True

        elif source == "tavily" and self._tavily:
            results = await self._tavily.search(
                query, max_results=self._config.squid_search_max_results, agent_id=agent_id,
            )
            if results and self._indexer:
                await self._ingest_tavily_results(
                    results, agent_id=agent_id, session_id=session_id,
                )
                await self._deep_fetch_urls(
                    results, agent_id=agent_id, session_id=session_id,
                )
                for r in results[:4]:
                    title = r.get("title", "Untitled")
                    score = r.get("score", 0)
                    summaries.append(f"[web] {title} (relevance: {score:.2f})")
                    key_points.append(r.get("content", "")[:150])
            if self._search_cache is not None:
                self._search_cache[cache_key] = True

        summary = "\n".join(summaries) if summaries else f"No results found for '{query[:60]}'."
        return SquidObservation(
            action_type="search",
            query_or_url=query,
            result_summary=summary,
            source_ids=source_ids,
            key_data_points=key_points,
        )

    async def _execute_fetch_action(
        self, action: SquidAction, agent_id: str, session_id: str
    ) -> SquidObservation:
        """Fetch a specific URL and return observation."""
        url = action.url.strip()
        if not url or not self._indexer:
            return SquidObservation(
                action_type="fetch",
                query_or_url=url or "",
                result_summary="No URL provided or indexer unavailable.",
                source_ids=[],
                key_data_points=[],
            )

        async with self._source_ingest_locks[f"react-fetch:{url}"]:
            existing = await self._graph.get_by_label(
                "Source", filters={"uri": url, "session_id": session_id}, limit=1,
            )
            if existing:
                return SquidObservation(
                    action_type="fetch",
                    query_or_url=url,
                    result_summary=f"URL already ingested: {url[:80]}",
                    source_ids=[existing[0].get("id", "")],
                    key_data_points=[],
                )
            try:
                source_id = await self._indexer.ingest_url(url, agent_id)
                return SquidObservation(
                    action_type="fetch",
                    query_or_url=url,
                    result_summary=f"Fetched and ingested: {url[:80]}",
                    source_ids=[source_id] if source_id else [],
                    key_data_points=[f"Fetched: {url[:100]}"],
                )
            except Exception as exc:
                return SquidObservation(
                    action_type="fetch",
                    query_or_url=url,
                    result_summary=f"Fetch failed for {url[:60]}: {exc!s}",
                    source_ids=[],
                    key_data_points=[],
                )

    async def _execute_read_action(
        self, action: SquidAction, agent_id: str, session_id: str
    ) -> SquidObservation:
        """Read a specific artifact from the knowledge graph in detail."""
        artifact_id = action.artifact_id.strip()
        if not artifact_id:
            return SquidObservation(
                action_type="read",
                query_or_url="",
                result_summary="No artifact ID provided.",
                source_ids=[],
                key_data_points=[],
            )

        try:
            artifact = await self._graph.get_by_id(artifact_id)
            if not artifact:
                return SquidObservation(
                    action_type="read",
                    query_or_url=artifact_id,
                    result_summary=f"Artifact {artifact_id[:12]} not found.",
                    source_ids=[],
                    key_data_points=[],
                )
            text = artifact.get("text", "")
            source_title = artifact.get("source_title", "")
            summary = f"[{artifact.get('label', 'artifact')}] {text[:300]}"
            if source_title:
                summary += f"\nSource: {source_title}"
            return SquidObservation(
                action_type="read",
                query_or_url=artifact_id,
                result_summary=summary,
                source_ids=[artifact_id],
                key_data_points=[text[:200]],
            )
        except Exception as exc:
            return SquidObservation(
                action_type="read",
                query_or_url=artifact_id,
                result_summary=f"Error reading artifact: {exc!s}",
                source_ids=[],
                key_data_points=[],
            )

    async def _produce_findings(
        self,
        context: dict[str, list[dict]],
        observations: list[dict[str, Any]],
        state: SquidState,
        persona: AgentPersona | None,
    ) -> SquidOutput:
        """Final production step: produce all artifacts from gathered evidence."""
        agent_id = state["agent_id"]
        agent_name = state["agent_name"]
        subproblem = state["subproblem"]
        query = subproblem["question"]
        session_id = state.get("session_id", "")

        source_chunks = self._format_artifacts(context.get("source_chunk", []))
        existing_work = self._format_existing_work(context)
        memory_section = await self._build_memory_section(agent_id, query, context, session_id)
        briefing_section = self._build_briefing_section(state)
        strategy_section = self._build_strategy_section(subproblem)
        observations_section = self._format_observations(observations)
        messages_text = await self._get_unread_messages(agent_id)

        system_prompt = SQUID_SYSTEM
        if persona:
            persona_block = generate_persona_prompt(persona, config=self._config)
            system_prompt = f"{SQUID_SYSTEM}\n\n{persona_block}"

        prompt = SQUID_PRODUCE.format(
            agent_name=agent_name,
            agent_id=agent_id,
            line_of_inquiry=query,
            subproblem=query,
            success_criteria=subproblem.get("success_criteria", ""),
            research_strategy=subproblem.get("research_strategy", ""),
            context_section=source_chunks or "No source material found yet.",
            observations_section=observations_section or "No observations this cycle.",
            existing_work=existing_work or "No existing work from other agents.",
        ) + briefing_section + memory_section + strategy_section

        llm_start = time.time()
        usage: dict[str, Any] = {"cost": 0.0}

        if persona:
            output = await self._llm.complete_structured_for_persona(
                prompt=prompt,
                response_model=SquidOutput,
                persona=persona,
                system=system_prompt,
                temperature=self._config.temperature_squid,
                usage_accumulator=usage,
            )
        else:
            output = await self._llm.complete_structured(
                prompt=prompt,
                response_model=SquidOutput,
                system=system_prompt,
                temperature=self._config.temperature_squid,
                usage_accumulator=usage,
            )

        logger.warning(
            "Squid %s PRODUCE: notes=%d, hypotheses=%d, key_facts=%d, "
            "search_queries=%d, cost=$%.4f",
            agent_id[:12], len(output.notes), len(output.hypotheses),
            len(output.key_facts), len(output.search_queries),
            usage.get("cost", 0.0),
        )

        # Execute follow-up search queries produced during findings
        if output.search_queries:
            await self._execute_searches(output.search_queries, agent_id, session_id)

        return output

    async def _force_produce(
        self,
        context: dict[str, list[dict]],
        observations: list[dict[str, Any]],
        state: SquidState,
        persona: AgentPersona | None,
    ) -> dict[str, Any]:
        """Force produce findings when budget is exhausted. Returns minimal artifacts."""
        try:
            output = await self._produce_findings(context, observations, state, persona)
            await self._store_artifacts(output, state["agent_id"], state)
            return {
                "notes_created": [],
                "assumptions_created": [],
                "hypotheses_created": [],
                "relations_created": [],
                "experiments_proposed": [],
                "messages_sent": [],
                "spent_usd": 0.0,
            }
        except Exception as exc:
            logger.error("Squid %s force produce failed: %s", state["agent_id"][:12], exc)
            return {
                "notes_created": [],
                "assumptions_created": [],
                "hypotheses_created": [],
                "relations_created": [],
                "experiments_proposed": [],
                "messages_sent": [],
                "spent_usd": 0.0,
            }

    async def _check_mentor(
        self,
        agent_id: str,
        subproblem: Subproblem,
        observations: list[dict[str, Any]],
        step: int,
        budget_spent: float,
        budget_total: float,
        research_question: str,
    ) -> MentorVerdict:
        """Call the Director as mentor to evaluate this squid's progress."""
        team_progress = ""
        if self._team_progress_fn:
            try:
                team_progress = await self._team_progress_fn()
            except Exception as exc:
                logger.warning("Team progress fn failed: %s", exc)

        hindsight_context = ""
        if self._hindsight_fn:
            try:
                hindsight_context = await self._hindsight_fn(subproblem.get("question", ""))
            except Exception as exc:
                logger.warning("Hindsight fn failed: %s", exc)

        try:
            verdict = await self._mentor.evaluate_progress(
                squid_id=agent_id,
                subproblem=subproblem,
                observations=observations,
                team_progress=team_progress,
                hindsight_context=hindsight_context,
                step_count=step,
                budget_spent=budget_spent,
                budget_total=budget_total,
                research_question=research_question,
            )
            return verdict
        except Exception as exc:
            logger.warning("Mentor evaluation failed, defaulting to continue: %s", exc)
            return MentorVerdict(action="continue", reasoning=f"Eval failed: {exc}", direction="")

    # ── Helper Methods ────────────────────────────────────────────

    def _format_observations(self, observations: list[dict[str, Any]]) -> str:
        """Format accumulated observations for prompts."""
        if not observations:
            return ""
        parts = []
        for i, o in enumerate(observations[-10:]):
            action_type = o.get("action_type", "?")
            summary = o.get("result_summary", "No summary")
            if action_type == "mentor_redirect":
                direction = o.get("key_data_points", [""])[0] if o.get("key_data_points") else ""
                parts.append(f"→ MENTOR REDIRECT: {direction}")
            else:
                parts.append(f"Step {i+1} [{action_type}]: {summary}")
        return "\n".join(parts)

    def _build_briefing_section(self, state: SquidState) -> str:
        briefing = state.get("iteration_summary", "")
        return f"\n\n=== INSTITUTE BRIEFING ===\n{briefing}" if briefing else ""

    def _build_strategy_section(self, subproblem: dict) -> str:
        strategy = subproblem.get("research_strategy", "")
        return f"\n\n=== RESEARCH STRATEGY ===\n{strategy}" if strategy else ""

    async def _build_memory_section(
        self, agent_id: str, query: str, context: dict, session_id: str
    ) -> str:
        """Build the Hindsight memory section for prompts."""
        if not self._memory:
            return ""
        memory_section = ""
        mem_context = await self._memory.recall_for_research(query)
        private = mem_context.get("private_memories")
        if private:
            memory_section = self._format_memory_context(private)

        past_failures = await self._memory.recall_past_failures(query)
        if past_failures:
            failures_text = "\n".join(
                f"  - {f.get('text', f.get('content', ''))[:200]}"
                for f in past_failures
                if f.get("text") or f.get("content")
            )
            if failures_text:
                memory_section += (
                    f"\n\n=== PAST FAILED APPROACHES ===\n"
                    f"Avoid repeating these:\n{failures_text}"
                )

        agent_hyps = [
            a for a in context.get("hypothesis", [])
            if a.get("created_by") == agent_id
        ]
        if agent_hyps:
            top_hyp = agent_hyps[0]
            reflection = await self._memory.reflect_on_hypothesis(
                top_hyp.get("text", ""), top_hyp.get("id", ""),
            )
            if reflection:
                memory_section += (
                    f"\n\n=== HYPOTHESIS REFLECTION ===\n{reflection}"
                )
        return memory_section

    async def _get_unread_messages(self, agent_id: str) -> str:
        """Get and mark unread messages for the agent."""
        unread = await self._graph.get_unread_messages(agent_id)
        for msg in unread:
            await self._graph.mark_message_read(msg["id"])
        return self._format_messages(unread)

    async def _post_production(
        self,
        output: SquidOutput,
        state: SquidState,
        agent_id: str,
        session_id: str,
        persona: AgentPersona | None,
        query: str,
    ) -> None:
        """Post-production steps: workspace updates, Hindsight retention, events."""
        # Workspace updates
        if self._workspace:
            iteration = state.get("iteration", 0)
            agent_hypotheses = await self._graph.get_by_label(
                "Hypothesis",
                filters={"created_by": agent_id, "session_id": session_id},
                limit=100,
            )
            findings_summary = self._summarize_iteration_for_memory(output)
            await self._workspace.append_memory(findings_summary, iteration)
            await self._workspace.sync_hypotheses_from_dag(agent_hypotheses)
            await self._workspace.update_beliefs(agent_hypotheses)
            if output.opencode_task:
                loop_result = await self._workspace.run_opencode_loop(output.opencode_task)
                if loop_result:
                    status = "satisfied" if loop_result.satisfied else "not satisfied"
                    await self._workspace.append_memory(
                        f"- OpenCode task '{output.opencode_task.topic}': "
                        f"{loop_result.total_iterations} iterations, {status}, "
                        f"produced: {', '.join(loop_result.files_produced) or 'no files'}",
                        iteration,
                    )

        # Hindsight retention
        if self._memory:
            iteration = state.get("iteration", 0)
            findings_summary = self._summarize_iteration_for_memory(output)
            agent_hypotheses = await self._graph.get_by_label(
                "Hypothesis",
                filters={"created_by": agent_id, "session_id": session_id},
                limit=20,
            )
            await self._memory.retain_iteration(
                iteration=iteration,
                findings_summary=findings_summary,
                hypotheses=agent_hypotheses,
            )
            await self._memory.reflect_on_progress(query)

        await self._bus.publish(Event(
            event_type=EventType.AGENT_ACTION,
            agent_id=agent_id,
            payload={
                "notes": len(output.notes),
                "hypotheses": len(output.hypotheses),
                "relations": 0,
                "experiments": len(output.experiment_proposals),
                "phase": "react_loop_complete",
                "observations_count": len(state.get("observations", [])),
            },
        ))
    async def _store_artifacts(
        self,
        output: SquidOutput,
        agent_id: str,
        state: SquidState,
    ) -> dict[str, Any]:
        """Store all LLM-generated artifacts in the knowledge graph."""
        notes_created: list[str] = []
        assumptions_created: list[str] = []
        hypotheses_created: list[str] = []
        relations_created: list[str] = []
        experiments_proposed: list[str] = []
        messages_sent: list[str] = []

        # Store notes
        for note_data in output.notes:
            note = Note(
                text=note_data.get("text", ""),
                source_chunk_ids=note_data.get("source_chunk_ids", []),
                created_by=agent_id,
                confidence=note_data.get(
                    "confidence", self._config.note_default_confidence
                ),
            )
            await self._graph.create(note)
            if note.source_chunk_ids:
                await self._graph.link_note_to_chunks(
                    note.id, note.source_chunk_ids
                )
            notes_created.append(note.id)

        # Store assumptions
        for assum_data in output.assumptions:
            assumption = Assumption(
                text=assum_data.get("text", ""),
                basis=assum_data.get("basis", ""),
                strength=assum_data.get("strength", "moderate"),
                created_by=agent_id,
            )
            await self._graph.create(assumption)
            assumptions_created.append(assumption.id)

        # Store hypotheses (with deduplication)
        for hyp_data in output.hypotheses:
            hyp_text = hyp_data.get("text", "")
            if not hyp_text:
                continue

            # Check for near-duplicate hypotheses from other agents
            similar = await self._retriever.find_similar_hypotheses(
                hyp_text,
                threshold=self._config.hypothesis_dedup_threshold,
                exclude_agent=agent_id,
                session_id=state.get("session_id", ""),
            )

            if similar:
                # Near-duplicate found — link to existing instead of creating
                existing_id = similar[0]["artifact_id"]
                relation = Relation(
                    source_artifact_id=existing_id,
                    target_artifact_id=existing_id,
                    relation_type=RelationType.EXTENDS,
                    reasoning=(
                        f"Agent {agent_id} independently proposed a similar "
                        f"hypothesis (dedup merged)"
                    ),
                    weight=self._config.dedup_relation_weight,
                    created_by=agent_id,
                )
                await self._graph.create_relation(relation)
                # Still track the existing hypothesis as "ours" for context
                hypotheses_created.append(existing_id)
                continue

            hypothesis = Hypothesis(
                text=hyp_text,
                supporting_evidence=hyp_data.get("supporting_evidence", []),
                testable=hyp_data.get("testable", True),
                created_by=agent_id,
                confidence=hyp_data.get(
                    "confidence", self._config.hypothesis_default_confidence
                ),
            )
            await self._graph.create(hypothesis)
            hypotheses_created.append(hypothesis.id)

        # Store key_facts as tagged Notes for structured factual claims
        for fact_data in output.key_facts:
            claim = fact_data.get("claim", "")
            value = fact_data.get("value", "")
            if not claim:
                continue
            fact_text = f"[KEY FACT] {claim}"
            if value:
                fact_text += f" — Value: {value}"
            fact_note = Note(
                text=fact_text,
                source_chunk_ids=fact_data.get("source_chunk_ids", []),
                created_by=agent_id,
                confidence=fact_data.get(
                    "confidence", self._config.note_default_confidence
                ),
                tags=["key_fact"],
            )
            await self._graph.create(fact_note)
            if fact_note.source_chunk_ids:
                await self._graph.link_note_to_chunks(
                    fact_note.id, fact_note.source_chunk_ids
                )
            notes_created.append(fact_note.id)

        # Store relations
        for rel_data in output.relations:
            relation = Relation(
                source_artifact_id=rel_data.get("source_artifact_id", ""),
                target_artifact_id=rel_data.get("target_artifact_id", ""),
                relation_type=RelationType.from_llm(
                    rel_data.get("relation_type", "")
                ),
                reasoning=rel_data.get("reasoning", ""),
                weight=rel_data.get(
                    "weight", self._config.relation_default_weight
                ),
                created_by=agent_id,
            )
            if relation.source_artifact_id and relation.target_artifact_id:
                await self._graph.create_relation(relation)
                relations_created.append(relation.id)

        # Store experiment proposals
        for exp_data in output.experiment_proposals:
            spec = ExperimentSpec(
                code=exp_data.get("code", ""),
                expected_outcome=exp_data.get("expected_outcome", ""),
                timeout_seconds=exp_data.get(
                    "timeout_seconds",
                    self._config.default_experiment_timeout_seconds,
                ),
            )
            experiment = Experiment(
                hypothesis_id=exp_data.get("hypothesis_id", ""),
                spec=spec,
                created_by=agent_id,
            )
            await self._graph.create(experiment)
            if experiment.hypothesis_id:
                await self._graph.link_hypothesis_to_experiment(
                    experiment.hypothesis_id, experiment.id
                )
            experiments_proposed.append(experiment.id)

        # Send messages (with typed protocol)
        for msg_data in output.messages:
            message = Message(
                from_agent=agent_id,
                to_agent=msg_data.get("to_agent", ""),
                text=msg_data.get("text", ""),
                message_type=MessageType.from_llm(
                    msg_data.get("message_type", "question")
                ),
                regarding_artifact_id=msg_data.get("regarding_artifact_id", ""),
                created_by=agent_id,
            )
            if message.to_agent:
                await self._graph.create_message(message)
                messages_sent.append(message.id)

        return {
            "notes_created": notes_created,
            "assumptions_created": assumptions_created,
            "hypotheses_created": hypotheses_created,
            "relations_created": relations_created,
            "experiments_proposed": experiments_proposed,
            "messages_sent": messages_sent,
        }

    async def _execute_searches(
        self,
        queries: list[dict[str, Any]],
        agent_id: str,
        session_id: str,
    ) -> None:
        """Execute any search queries the squid requested."""
        for q in queries:
            source = q.get("source", "tavily")
            query = q.get("query", "")

            if not query:
                continue

            # Check session-level search cache if available
            cache_key = (query.lower().strip(), source)
            if hasattr(self, "_search_cache") and self._search_cache is not None:
                if cache_key in self._search_cache:
                    logger.info(
                        "Search cache hit for %s query: %s",
                        source, query[:60],
                    )
                    continue

            if source == "arxiv" and self._arxiv:
                papers = await self._arxiv.search(
                    query,
                    max_results=self._config.squid_search_max_results,
                    agent_id=agent_id,
                )
                if self._indexer:
                    await self._ingest_arxiv_results(
                        papers,
                        agent_id=agent_id,
                        session_id=session_id,
                    )
                if hasattr(self, "_search_cache") and self._search_cache is not None:
                    self._search_cache[cache_key] = True
            elif source == "tavily" and self._tavily:
                results = await self._tavily.search(
                    query,
                    max_results=self._config.squid_search_max_results,
                    agent_id=agent_id,
                )
                if self._indexer and results:
                    await self._ingest_tavily_results(
                        results,
                        agent_id=agent_id,
                        session_id=session_id,
                    )
                    # Deep-fetch high-value URLs for richer content
                    await self._deep_fetch_urls(
                        results,
                        agent_id=agent_id,
                        session_id=session_id,
                    )
                if hasattr(self, "_search_cache") and self._search_cache is not None:
                    self._search_cache[cache_key] = True

    async def _ingest_arxiv_results(
        self,
        papers: list[dict[str, Any]],
        agent_id: str,
        session_id: str,
    ) -> None:
        """Download and ingest discovered arXiv papers into shared memory in parallel."""
        if not self._indexer or not papers:
            return

        tasks = [
            self._ingest_single_arxiv_paper(paper, agent_id, session_id)
            for paper in papers
        ]
        await asyncio.gather(*tasks, return_exceptions=True)

    async def _ingest_single_arxiv_paper(
        self,
        paper: dict[str, Any],
        agent_id: str,
        session_id: str,
    ) -> None:
        """Worker to download and ingest a single arXiv paper."""
        arxiv_id = str(paper.get("arxiv_id", "")).strip()
        if not arxiv_id:
            return

        canonical_uri = f"arxiv:{arxiv_id}"
        async with self._source_ingest_locks[canonical_uri]:
            existing = await self._graph.get_by_label(
                "Source",
                filters={
                    "uri": canonical_uri,
                    "session_id": session_id,
                },
                limit=1,
            )
            if existing:
                await self._bus.publish(Event(
                    event_type=EventType.AGENT_ACTION,
                    agent_id=agent_id,
                    payload={
                        "action": "search_source_already_ingested",
                        "source": "arxiv",
                        "title": paper.get("title", ""),
                        "arxiv_id": arxiv_id,
                        "source_id": existing[0].get("id", ""),
                    },
                ))
                return

            await self._bus.publish(Event(
                event_type=EventType.AGENT_ACTION,
                agent_id=agent_id,
                payload={
                    "action": "downloading_source",
                    "source": "arxiv",
                    "title": paper.get("title", ""),
                    "arxiv_id": arxiv_id,
                },
            ))

            try:
                pdf_path = await self._arxiv.download(
                    arxiv_id,
                    agent_id=agent_id,
                )
                await self._bus.publish(Event(
                    event_type=EventType.AGENT_ACTION,
                    agent_id=agent_id,
                    payload={
                        "action": "ingesting_source",
                        "source": "arxiv",
                        "title": paper.get("title", ""),
                        "arxiv_id": arxiv_id,
                        "progress": 100,
                        "stage": "ingesting",
                    },
                ))
                source_id = await self._indexer.ingest_pdf(pdf_path, agent_id)
                await self._graph.update(
                    source_id,
                    {
                        "source_type": "arxiv",
                        "uri": canonical_uri,
                        "title": paper.get("title", ""),
                        "file_path": pdf_path,
                    },
                )
                await self._bus.publish(Event(
                    event_type=EventType.AGENT_ACTION,
                    agent_id=agent_id,
                    payload={
                        "action": "ingested_search_source",
                        "source": "arxiv",
                        "title": paper.get("title", ""),
                        "arxiv_id": arxiv_id,
                        "source_id": source_id,
                        "file_path": pdf_path,
                    },
                ))
            except Exception as exc:
                await self._bus.publish(Event(
                    event_type=EventType.ERROR,
                    agent_id=agent_id,
                    payload={
                        "error": (
                            f"Failed to download or ingest arXiv paper "
                            f"{arxiv_id}: {exc}"
                        ),
                    },
                ))

    async def _ingest_tavily_results(
        self,
        results: list[dict[str, Any]],
        agent_id: str,
        session_id: str,
    ) -> None:
        """Ingest Tavily search results into the RAG pipeline.

        Mirrors the _ingest_arxiv_results pattern: per-URL dedup locks,
        graph existence check, ingest_text() with URL as Source.uri.
        """
        if not self._indexer or not results:
            return

        tasks = [
            self._ingest_single_tavily_result(result, agent_id, session_id)
            for result in results
        ]
        await asyncio.gather(*tasks, return_exceptions=True)

    async def _ingest_single_tavily_result(
        self,
        result: dict[str, Any],
        agent_id: str,
        session_id: str,
    ) -> None:
        """Ingest a single Tavily search result into the knowledge graph."""
        url = result.get("url", "").strip()
        content = result.get("content", "").strip()
        title = result.get("title", "Untitled")

        if not url or not content:
            return

        async with self._source_ingest_locks[url]:
            existing = await self._graph.get_by_label(
                "Source",
                filters={"uri": url, "session_id": session_id},
                limit=1,
            )
            if existing:
                return

            try:
                source_id = await self._indexer.ingest_text(
                    content, agent_id, title=title
                )
                await self._graph.update(
                    source_id,
                    {
                        "source_type": "web",
                        "uri": url,
                        "title": title,
                        "session_id": session_id,
                    },
                )
                await self._bus.publish(Event(
                    event_type=EventType.AGENT_ACTION,
                    agent_id=agent_id,
                    payload={
                        "action": "ingested_search_source",
                        "source": "tavily",
                        "title": title,
                        "url": url,
                        "source_id": source_id,
                    },
                ))
            except Exception as exc:
                await self._bus.publish(Event(
                    event_type=EventType.ERROR,
                    agent_id=agent_id,
                    payload={
                        "error": f"Failed to ingest Tavily result {url}: {exc}",
                    },
                ))

    async def _deep_fetch_urls(
        self,
        results: list[dict[str, Any]],
        agent_id: str,
        session_id: str,
    ) -> None:
        """Deep-fetch high-value URLs for richer content extraction.

        For Tavily results with high relevance scores, fetch the full page
        via ingest_url() to get complete content beyond the snippet.
        """
        if not self._indexer:
            return

        deep_fetch_max = getattr(
            self._config, "squid_deep_fetch_max", 2
        )
        # Sort by score descending, take top N with score > 0.7
        high_value = sorted(
            [r for r in results if r.get("score", 0) > 0.7],
            key=lambda r: r.get("score", 0),
            reverse=True,
        )[:deep_fetch_max]

        for result in high_value:
            url = result.get("url", "").strip()
            if not url:
                continue

            async with self._source_ingest_locks[f"deep:{url}"]:
                try:
                    await self._indexer.ingest_url(url, agent_id)
                except Exception:
                    pass  # Graceful degradation — snippet was already ingested

    def _summarize_iteration_for_memory(self, output: SquidOutput) -> str:
        """
        Format iteration output as a memory.md entry.

        Produces structured bullet points from notes/hypotheses created.
        Does NOT call LLM — just formats the already-produced data.
        """
        lines: list[str] = []
        if output.notes:
            lines.append(f"- Wrote {len(output.notes)} notes")
            for n in output.notes[:3]:  # First 3 to keep memory concise
                lines.append(f"  - {n.get('text', '')[:100]}")
        if output.hypotheses:
            lines.append(f"- Formed {len(output.hypotheses)} hypotheses")
        if output.experiment_proposals:
            lines.append(f"- Proposed {len(output.experiment_proposals)} experiments")
        if not lines:
            lines.append("- No significant artifacts produced this cycle")
        return "\n".join(lines)

    def _format_artifacts(self, artifacts: list[dict]) -> str:
        """Format a list of artifact dicts into readable text for prompts."""
        if not artifacts:
            return ""
        parts = []
        for a in artifacts:
            header = (
                f"[{a.get('artifact_type', 'unknown')}] "
                f"(ID: {a.get('artifact_id', '?')}, "
                f"confidence: {a.get('confidence', '?')}, "
                f"by: {a.get('created_by', '?')}"
            )
            # Include source attribution if available (enriched by retriever)
            source_title = a.get("source_title", "")
            source_uri = a.get("source_uri", "")
            if source_title or source_uri:
                header += f", from: \"{source_title or 'unknown'}\" — {source_uri or 'no URI'}"
            header += ")"
            parts.append(f"{header}\n{a.get('text', '')}\n")
        return "\n---\n".join(parts)

    def _format_existing_work(self, context: dict[str, list[dict]]) -> str:
        """Format all existing work (excluding source chunks) for the prompt."""
        parts = []
        for atype in ["note", "hypothesis", "assumption", "finding", "experiment_result"]:
            artifacts = context.get(atype, [])
            if artifacts:
                parts.append(f"\n=== {atype.upper()}S ===")
                if atype == "experiment_result":
                    parts.append(self._format_experiment_results(artifacts))
                else:
                    parts.append(self._format_artifacts(artifacts))
        return "\n".join(parts)

    def _format_experiment_results(self, results: list[dict]) -> str:
        """Format experiment results for the prompt — emphasis on what was tested and what happened."""
        parts = []
        for r in results[:10]:  # Cap at 10 to avoid prompt bloat
            parts.append(
                f"[experiment_result] (exit_code: {r.get('exit_code', '?')}, "
                f"experiment: {(r.get('experiment_id', '') or r.get('id', '?'))[:12]})\n"
                f"Stdout: {(r.get('stdout', '') or '')[:300]}\n"
                f"Interpretation: {r.get('interpretation', 'None')}"
            )
        return "\n---\n".join(parts)

    def _format_messages(self, messages: list[dict]) -> str:
        """
        Format unread messages for the prompt, grouped by type.

        Messages are sorted by priority: dependency warnings and
        objections first, acknowledgments last. This ensures the
        agent addresses critical feedback before routine messages.
        """
        if not messages:
            return ""

        # Sort by message type priority (lower = more urgent)
        def msg_priority(m: dict) -> int:
            mtype = m.get("message_type", "question")
            try:
                return MESSAGE_PRIORITY.get(MessageType(mtype), 4)
            except ValueError:
                return 4

        sorted_msgs = sorted(messages, key=msg_priority)

        parts = []
        current_type = None
        for m in sorted_msgs:
            mtype = m.get("message_type", "question").upper()
            if mtype != current_type:
                current_type = mtype
                parts.append(f"\n--- {current_type} ---")

            parts.append(
                f"From {m.get('from_agent', '?')} [{mtype}]: "
                f"{m.get('text', '')}"
                f" (re: {m.get('regarding_artifact_id', 'general')})"
            )
        return "\n".join(parts)

    def _format_memory_context(self, memories: list[dict] | Any) -> str:
        """Format Hindsight recall results into a prompt section."""
        if not memories:
            return ""
        parts = ["\n\n=== AGENT WORKING MEMORY ==="]
        if isinstance(memories, list):
            for m in memories[:8]:
                text = m.get("content", m.get("text", str(m)))[:300]
                parts.append(f"- {text}")
        elif isinstance(memories, str):
            parts.append(memories[:2000])
        else:
            parts.append(str(memories)[:2000])
        return "\n".join(parts)

