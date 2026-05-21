"""
Program Director agent — decomposes the research question and designs
agent archetypes.

The Director is the first agent to run. It takes the user's question
and breaks it into focused subproblems, then designs up to 20 distinct
agent archetypes suited to the research domain. Archetypes define the
diverse research perspectives that will populate the institute.
"""

import json
import time
from typing import Any

from pydantic import BaseModel, Field, field_validator

from src.config import Settings, settings as default_settings
from src.events.bus import EventBus
from src.llm.client import LLMClient
from src.llm.prompts import (
    DIRECTOR_SYSTEM,
    DIRECTOR_DECOMPOSE,
    DIRECTOR_DESIGN_ARCHETYPES,
    MENTOR_EVALUATE,
)
from src.models.agent_state import InstituteState, Subproblem, MentorVerdict
from src.models.archetype import Archetype, parse_archetypes_from_llm
from src.models.events import Event, EventType


class DirectorOutput(BaseModel):
    """Structured output from the Director's decomposition."""

    subproblems: list[dict[str, Any]] = Field(default_factory=list)
    pipeline_mode: str = Field(default="deep_research")
    pipeline_reasoning: str = ""
    open_questions: list[str] = Field(default_factory=list)
    key_assumptions: list[str] = Field(default_factory=list)
    reasoning_summary: str = ""

    @field_validator(
        "subproblems",
        "open_questions",
        "key_assumptions",
        mode="before",
    )
    @classmethod
    def _coerce_list_fields(cls, value: Any) -> list[Any]:
        return _coerce_json_list(value)

    @field_validator("pipeline_mode", mode="before")
    @classmethod
    def _coerce_pipeline_mode(cls, value: Any) -> str:
        if value in ("deep_research", "focused_extraction"):
            return value
        return "deep_research"


class ArchetypeOutput(BaseModel):
    """Structured output from the Director's archetype design."""

    archetypes: list[dict[str, Any]] = Field(default_factory=list)
    reasoning_summary: str = ""

    @field_validator("archetypes", mode="before")
    @classmethod
    def _coerce_archetypes(cls, value: Any) -> list[dict[str, Any]]:
        parsed = _coerce_json_list(value)
        return [item for item in parsed if isinstance(item, dict)]


def _coerce_json_list(value: Any) -> list[Any]:
    """Recover a list from common structured-output formatting mistakes."""
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    if not isinstance(value, str):
        return []

    text = value.strip()
    if not text:
        return []

    candidates = [text]
    if text.startswith(":"):
        candidates.append(text.lstrip(":").strip())
    if not text.startswith("[") and "[" in text and "]" in text:
        start = text.find("[")
        end = text.rfind("]")
        if end > start:
            candidates.append(text[start:end + 1])

    for candidate in candidates:
        try:
            parsed = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, list):
            return parsed

    return []


class DirectorAgent:
    """
    Decomposes a research question and designs agent archetypes.

    The Director runs once at the start of a research session. It:
    1. Breaks the question into focused subproblems
    2. Designs up to 20 agent archetypes with distinct research
       perspectives, methodologies, and trait profiles

    Together, these determine the structure and diversity of the
    entire investigation.

    Usage:
        director = DirectorAgent(llm_client, event_bus)
        updated_state = await director.run(state)
    """

    def __init__(
        self,
        llm: LLMClient,
        event_bus: EventBus,
        config: Settings | None = None,
    ) -> None:
        self._llm = llm
        self._bus = event_bus
        self._config = config or default_settings

    async def run(self, state: InstituteState) -> dict[str, Any]:
        """
        Decompose the research question and design agent archetypes.

        Two LLM calls:
        1. Decompose question → subproblems
        2. Design archetypes suited to the question and subproblems

        Args:
            state: Current institute state with research_question set.

        Returns:
            State update dict with subproblems and archetypes populated.
        """
        question = state["research_question"]
        director_start = time.time()

        await self._bus.publish(Event(
            event_type=EventType.AGENT_ACTION,
            agent_id="director",
            payload={
                "action": "decomposition_started",
                "question": question,
            },
        ))

        # Step 1: Decompose into subproblems
        step1_start = time.time()
        prompt = DIRECTOR_DECOMPOSE.format(research_question=question)
        result = await self._llm.complete_structured(
            prompt=prompt,
            response_model=DirectorOutput,
            system=DIRECTOR_SYSTEM,
            temperature=self._config.temperature_director,
        )
        #print(f"[TIMING] Director step 1 (decomposition): {time.time() - step1_start:.2f}s")

        # Convert to Subproblem TypedDicts
        subproblems: list[Subproblem] = []
        for sp in result.subproblems:
            subproblems.append(Subproblem(
                id=sp.get("id", f"sp-{len(subproblems)+1}"),
                question=sp.get("question", ""),
                priority=sp.get("priority", len(subproblems) + 1),
                success_criteria=sp.get("success_criteria", ""),
                research_strategy=sp.get("research_strategy", ""),
                assigned_agent=[],  # Filled in by spawn_squids
            ))

        await self._bus.publish(Event(
            event_type=EventType.AGENT_ACTION,
            agent_id="director",
            payload={
                "action": "decomposition_completed",
                "subproblems_count": len(subproblems),
                "open_questions_count": len(result.open_questions),
                "key_assumptions_count": len(result.key_assumptions),
                "reasoning_summary": result.reasoning_summary,
            },
        ))

        # Step 2: Design archetypes for the research question
        step2_start = time.time()
        num_agents = state.get("num_agents", len(subproblems))
        archetypes, archetype_reasoning = await self._design_archetypes(
            question, subproblems, num_agents
        )
        #print(f"[TIMING] Director step 2 (archetype design): {time.time() - step2_start:.2f}s")
        #print(f"[TIMING] Director total: {time.time() - director_start:.2f}s")

        await self._bus.publish(Event(
            event_type=EventType.AGENT_ACTION,
            agent_id="director",
            payload={
                "action": "decomposed_question",
                "subproblems_count": len(subproblems),
                "archetypes_count": len(archetypes),
                "open_questions": result.open_questions,
                "key_assumptions": result.key_assumptions,
                "reasoning_summary": result.reasoning_summary,
                "archetype_reasoning": archetype_reasoning,
                "archetype_names": [a.name for a in archetypes],
            },
        ))

        return {
            "subproblems": subproblems,
            "archetypes": [a.model_dump() for a in archetypes],
            "pipeline_mode": result.pipeline_mode,
            "open_questions": result.open_questions,
            "key_assumptions": result.key_assumptions,
        }

    async def _design_archetypes(
        self,
        question: str,
        subproblems: list[Subproblem],
        num_agents: int,
    ) -> tuple[list[Archetype], str]:
        """
        Design agent archetypes suited to the research question.

        The Director creates diverse research perspectives — skeptics,
        empiricists, theoreticians, cross-disciplinary thinkers — tuned
        to the specific domain being investigated.

        Args:
            question: The original research question.
            subproblems: Decomposed subproblems for context.
            num_agents: Target number of agents (informs archetype count).

        Returns:
            List of Archetype models, up to 20.
        """
        subproblems_text = "\n".join(
            f"- [{sp['id']}] {sp['question']}" for sp in subproblems
        )

        # Cap archetypes: at most 20, at most num_agents
        max_archetypes = min(
            self._config.max_archetypes,
            max(self._config.min_archetypes, num_agents),
        )

        prompt = DIRECTOR_DESIGN_ARCHETYPES.format(
            research_question=question,
            subproblems=subproblems_text,
            max_archetypes=max_archetypes,
        )

        await self._bus.publish(Event(
            event_type=EventType.AGENT_ACTION,
            agent_id="director",
            payload={
                "action": "archetype_design_started",
                "max_archetypes": max_archetypes,
                "subproblems_count": len(subproblems),
            },
        ))

        result = await self._llm.complete_structured(
            prompt=prompt,
            response_model=ArchetypeOutput,
            system=DIRECTOR_SYSTEM,
            temperature=self._config.temperature_archetype_design,
        )

        archetypes = parse_archetypes_from_llm(result.archetypes)

        # Ensure we have at least 3 archetypes
        if len(archetypes) < self._config.min_archetypes:
            archetypes = self._fallback_archetypes(archetypes)

        final_archetypes = archetypes[:max_archetypes]

        await self._bus.publish(Event(
            event_type=EventType.AGENT_ACTION,
            agent_id="director",
            payload={
                "action": "archetype_design_completed",
                "archetypes_count": len(final_archetypes),
                "archetype_names": [a.name for a in final_archetypes],
                "reasoning_summary": result.reasoning_summary,
            },
        ))

        return final_archetypes, result.reasoning_summary

    def _fallback_archetypes(
        self, existing: list[Archetype]
    ) -> list[Archetype]:
        """
        Provide minimum archetype diversity if the LLM under-produces.

        Ensures at least 3 archetypes: a skeptic, an empiricist, and
        a generalist. Only adds what's missing.
        """
        defaults = [
            Archetype(**data) for data in self._config.fallback_archetypes
        ]

        existing_names = {a.name for a in existing}
        for default in defaults:
            if (
                default.name not in existing_names
                and len(existing) < self._config.min_archetypes
            ):
                existing.append(default)

        return existing

    async def evaluate_progress(
        self,
        squid_id: str,
        subproblem: Subproblem,
        observations: list[dict],
        team_progress: str,
        hindsight_context: str,
        step_count: int,
        budget_spent: float,
        budget_total: float,
        research_question: str,
    ) -> MentorVerdict:
        """Evaluate a squid's research progress and decide: continue, redirect, or stop.

        Called every `mentor_check_interval` ReAct steps. Uses the fast model
        for cost efficiency. Has access to cross-agent progress and institutional
        memory via Hindsight.
        """
        obs_lines = []
        for i, o in enumerate(observations[-6:]):
            obs_lines.append(
                f"Step {i+1}: {o.get('action_type', '?')} "
                f"— {o.get('result_summary', 'No summary')}"
            )
        observations_text = "\n".join(obs_lines) if obs_lines else "No observations yet."

        prompt = MENTOR_EVALUATE.format(
            research_question=research_question,
            subproblem=subproblem.get("question", ""),
            success_criteria=subproblem.get("success_criteria", ""),
            research_strategy=subproblem.get("research_strategy", ""),
            step_count=step_count,
            budget_spent=budget_spent,
            budget_total=budget_total,
            observations_summary=observations_text,
            key_findings_summary="",
            team_progress_summary=team_progress or "No other researcher progress available.",
            hindsight_section=hindsight_context or "No session history available.",
        )

        try:
            result = await self._llm.complete_structured(
                prompt=prompt,
                response_model=MentorVerdict,
                system=DIRECTOR_SYSTEM,
                temperature=self._config.temperature_default_structured,
                model_override=self._config.fast_model or None,
            )
        except Exception as exc:
            logger.warning("Mentor evaluation failed, defaulting to continue: %s", exc)
            return MentorVerdict(
                action="continue",
                reasoning=f"Mentor evaluation failed: {exc}",
                direction="",
            )

        await self._bus.publish(Event(
            event_type=EventType.AGENT_ACTION,
            agent_id="director",
            payload={
                "action": "mentor_evaluate",
                "squid_id": squid_id,
                "verdict": result.action,
                "reasoning": result.reasoning[:200],
                "step_count": step_count,
            },
        ))

        return result
