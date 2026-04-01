"""Tier-1 research agent — LangGraph with strict 4-tool allowlist (5 including propose)."""

import os
from datetime import datetime
from typing import Dict, List, Literal, Optional, TypedDict

from squid.agents.tools.search import web_search
from squid.agents.tools.fetch import fetch_url
from squid.agents.tools.sandbox import python_exec_sandbox
from squid.agents.tools.propose import propose_experiment
from squid.agents.tools.finding import post_finding


class AgentState(TypedDict):
    agent_id: str
    session_id: str
    research_question: str
    current_hypothesis: str
    cluster_id: Optional[str]
    findings_read: List[str]
    findings_posted: List[str]
    experiments_submitted: List[str]
    tool_calls_made: int
    confidence: float
    cycles_completed: int
    last_active: datetime
    status: Literal["researching", "sleeping", "stopped"]
    current_cycle: int


# Tier-1 tool allowlist — EXACTLY 5 tools
TIER1_TOOLS = [
    web_search,              # Brave API, max 30/cycle
    fetch_url,               # httpx + source taxonomy
    python_exec_sandbox,     # isolated Docker, no network, 60s
    propose_experiment,      # submits ExperimentSpec (does NOT run it)
    post_finding,            # writes to DAG via coordinator API
]


def create_tier1_agent(
    model_name: str = "claude-haiku-4-5",
    checkpointer=None,
):
    """Create a Tier-1 research agent with strict tool allowlist."""
    from langchain_anthropic import ChatAnthropic
    from langgraph.prebuilt import create_react_agent

    model = ChatAnthropic(model=model_name, max_tokens=4096)
    return create_react_agent(
        model=model,
        tools=TIER1_TOOLS,
        checkpointer=checkpointer,
    )


def build_research_loop():
    """Build the Tier-1 research loop as a StateGraph."""
    from langgraph.graph import END, StateGraph

    graph = StateGraph(AgentState)

    graph.add_node("read_context", lambda s: {**s, "current_cycle": s.get("current_cycle", 0) + 1})
    graph.add_node("form_hypothesis", lambda s: s)
    graph.add_node("find_evidence", lambda s: s)
    graph.add_node("decide_action", lambda s: s)
    graph.add_node("read_others", lambda s: s)
    graph.add_node("agree_or_disagree", lambda s: s)
    graph.add_node("sleep", lambda s: {**s, "status": "sleeping"})

    graph.set_entry_point("read_context")
    graph.add_edge("read_context", "form_hypothesis")
    graph.add_edge("form_hypothesis", "find_evidence")
    graph.add_edge("find_evidence", "decide_action")
    graph.add_edge("decide_action", "read_others")
    graph.add_edge("read_others", "agree_or_disagree")
    graph.add_edge("agree_or_disagree", "sleep")
    graph.add_edge("sleep", END)

    return graph.compile()


def run_worker():
    """CLI entry point for running a Tier-1 worker."""
    import asyncio

    async def main():
        session_id = os.environ.get("SESSION_ID", "")
        agent_count = int(os.environ.get("AGENT_COUNT", "5"))
        print(f"Starting {agent_count} Tier-1 agents for session {session_id}")
        # TODO: Implement full worker loop

    asyncio.run(main())
