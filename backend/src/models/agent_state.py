"""
LangGraph state definitions for the research institute orchestration.

InstituteState is the top-level state that flows through the LangGraph
state machine. It holds coordination data (IDs, counters, flags) — all
actual artifact data lives in Neo4j and Postgres.

SquidState tracks an individual line of inquiry assigned to a
Squid agent, including its persona for behavioral customization.
"""

from typing import Annotated, Any, Literal, TypedDict

from langgraph.graph import add_messages

from pydantic import BaseModel, Field


class Subproblem(TypedDict):
    """A decomposed piece of the research question."""

    id: str
    question: str
    priority: int  # 1 = highest
    success_criteria: str
    research_strategy: str  # LLM-generated guidance on how to research this subproblem
    assigned_agent: list[str]  # agent_ids


class AgentInfo(TypedDict):
    """
    Metadata about a spawned squid agent.

    Includes persona reference, budget tracking, and performance
    metrics used by the controller for pause/reallocate decisions.
    """

    agent_id: str
    name: str
    line_of_inquiry: str
    subproblem_id: str
    status: str  # "active", "paused", "done"

    # Persona — serialized dict from AgentPersona.model_dump()
    persona: dict[str, Any]

    # Per-agent budget tracking
    budget_allocated_usd: float
    budget_used_usd: float

    # Performance tracking (used by controller + reputation)
    consecutive_empty_iterations: int

    # Workspace path (absolute string) — set by _spawn_squids() when workspace layer is active.
    # Always access with agent_info.get("workspace_path", "") — TypedDict keys may be absent.
    workspace_path: str


class BeliefCluster(TypedDict):
    """
    A group of agents with similar epistemic stances.

    Clusters are formed by computing belief vectors (agent stance on
    each hypothesis) and grouping by cosine similarity. Debate happens
    between clusters, not within.
    """

    cluster_id: str
    agent_ids: list[str]
    shared_hypotheses: list[str]   # Hypotheses this cluster broadly supports
    contested_hypotheses: list[str]  # Hypotheses with mixed support within cluster


class InstituteState(TypedDict, total=False):
    """
    Top-level state flowing through the LangGraph institute graph.

    Design principle: only IDs and coordination metadata here.
    Full artifact content lives in Neo4j / Postgres and is fetched
    on demand by agents.
    """

    # Research question
    research_question: str
    session_id: str

    # Agent count requested by user
    num_agents: int

    # Director output
    subproblems: list[Subproblem]
    archetypes: list[dict[str, Any]]  # Serialized Archetype dicts
    open_questions: list[str]
    key_assumptions: list[str]

    # Active agents
    agents: list[AgentInfo]

    # Iteration tracking
    iteration: int
    max_iterations: int

    # Budget management — decremented on every LLM call
    budget_remaining_usd: float
    budget_total_usd: float
    
    # Enhanced budget tracking
    tokens_used: int
    dollars_used: float
    llm_budget_usd: float
    budget_warning: bool  # True when at 90% threshold

    # Artifacts created in the current iteration (IDs only)
    artifacts_this_iteration: list[str]

    # Experiments waiting to run
    pending_experiments: list[str]  # ExperimentSpec IDs

    # Debate queue — claims to be challenged
    debate_queue: list[dict[str, Any]]

    # Belief-based clustering for debate routing
    belief_clusters: list[BeliefCluster]
    last_recluster_iteration: int  # When clusters were last recomputed

    # Coverage tracking — how much of each subproblem is addressed
    coverage: dict[str, float]  # subproblem_id → 0.0–1.0

    # Pipeline mode — set by Director
    pipeline_mode: str  # "deep_research" or "focused_extraction"

    # Controller decisions
    should_stop: bool
    controller_directives: list[str]

    # Per-iteration briefing (injected into all agent prompts)
    iteration_summary: str

    # Sources provided by user + discovered by agents
    source_ids: list[str]

    # Event log for this iteration (cleared each cycle)
    events: list[dict[str, Any]]

    # Messages for LangGraph's built-in message handling
    messages: Annotated[list, add_messages]


class SquidState(TypedDict, total=False):
    """
    Per-squid state used when Send dispatches parallel research.

    Each squid gets a slice of the problem and works independently,
    writing results to the shared knowledge graph. The persona dict
    controls the agent's behavioral traits and model selection.
    """

    agent_id: str
    agent_name: str
    subproblem: Subproblem
    session_id: str
    iteration: int
    budget_remaining_usd: float

    # Persona — serialized dict from AgentPersona.model_dump()
    persona: dict[str, Any]

    # Institute briefing from previous iteration
    iteration_summary: str

    # Workspace path (absolute string) — mirrors AgentInfo.workspace_path.
    # Access with state.get("workspace_path", "") — may be absent if workspace is disabled.
    workspace_path: str

    # What this squid produced (IDs)
    notes_created: list[str]
    assumptions_created: list[str]
    hypotheses_created: list[str]
    experiments_proposed: list[str]
    findings_created: list[str]
    relations_created: list[str]
    messages_sent: list[str]

    # ReAct loop state
    research_question: str  # Full research question for mentor context
    observations: list[dict[str, Any]]  # Accumulated observations from ReAct steps
    step_count: int  # Current ReAct step number within this invocation


class SquidAction(BaseModel):
    """Single action decision from a ReAct think step."""

    action_type: Literal["search", "fetch", "read", "produce"] = Field(
        description="What to do next: search for information, fetch a specific URL, read an artifact in detail, or produce final findings"
    )
    reasoning: str = Field(
        description="Brief explanation of why this action was chosen"
    )
    query: str = Field(
        default="",
        description="Search query if action_type=search"
    )
    source: Literal["tavily", "arxiv"] = Field(
        default="tavily",
        description="Search source if action_type=search"
    )
    url: str = Field(
        default="",
        description="URL to fetch if action_type=fetch"
    )
    artifact_id: str = Field(
        default="",
        description="Artifact ID to read in detail if action_type=read"
    )
    has_critical_gaps: bool = Field(
        default=False,
        description="Only for produce: are there gaps you couldn't fill?"
    )
    gap_description: str = Field(
        default="",
        description="Only for produce: description of remaining gaps"
    )


class SquidObservation(BaseModel):
    """Result of executing a ReAct action."""

    action_type: str = Field(
        description="The action type that was executed"
    )
    query_or_url: str = Field(
        description="The search query or URL that was used"
    )
    result_summary: str = Field(
        description="LLM-readable summary of what was found"
    )
    source_ids: list[str] = Field(
        default_factory=list,
        description="Source node IDs created by this action"
    )
    key_data_points: list[str] = Field(
        default_factory=list,
        description="Specific facts or data points extracted"
    )


class MentorVerdict(BaseModel):
    """Director's evaluation of a squid's research progress."""

    action: Literal["continue", "redirect", "stop"] = Field(
        description="Whether the researcher should continue, be redirected, or stop and produce findings"
    )
    reasoning: str = Field(
        description="Brief explanation of the verdict"
    )
    direction: str = Field(
        default="",
        description="Specific new direction if action=redirect (empty otherwise)"
    )
