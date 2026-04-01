"""MCP server — exposes ResearchSquid as 5 MCP tools via FastMCP."""

import os

from squid.coordinator.session import create_session, stop_session
from squid.dag.reader import get_session_summary
from squid.dag.client import DAGClient


def run():
    """CLI entry point for MCP server."""
    transport = os.getenv("MCP_TRANSPORT", "stdio")
    port = int(os.getenv("MCP_PORT", "8001"))
    print(f"Starting ResearchSquid MCP server (transport={transport}, port={port})")
    # TODO: Implement FastMCP server with 5 tools:
    # squid_start_session, squid_get_status, squid_get_summary,
    # squid_submit_experiment, squid_stop_session


# MCP tool definitions (to be wired with FastMCP)
TOOLS = [
    {
        "name": "squid_start_session",
        "description": "Start a new research session. Returns session_id.",
        "parameters": ["question", "modality", "llm_budget_usd", "compute_budget_usd", "agent_count"],
    },
    {
        "name": "squid_get_status",
        "description": "Get current state of a research session: clusters, findings count, spend.",
        "parameters": ["session_id"],
    },
    {
        "name": "squid_get_summary",
        "description": "Get a detailed natural-language summary of current research findings.",
        "parameters": ["session_id"],
    },
    {
        "name": "squid_submit_experiment",
        "description": "Submit an ExperimentSpec. Returns spec_id. Result appears in DAG when complete.",
        "parameters": ["session_id", "hypothesis_finding_id", "backend_type", "goal", "inputs", "max_compute_cost_usd"],
    },
    {
        "name": "squid_stop_session",
        "description": "Stop a research session and generate final report.",
        "parameters": ["session_id"],
    },
]
