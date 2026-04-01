"""propose_experiment — submits ExperimentSpec to Coordinator (does NOT run it)."""

import os
from typing import Any, Dict, List

import httpx

from squid.schema.experiment import ExperimentSpec


COORDINATOR_URL = os.getenv("COORDINATOR_URL", "http://coordinator:8000")


async def propose_experiment(
    hypothesis_finding_id: str,
    backend_type: str,
    goal: str,
    inputs: Dict[str, Any],
    success_metrics: List[str],
    constraints: Dict[str, Any],
    max_compute_cost_usd: float,
    max_wall_clock_seconds: int,
    session_id: str,
    agent_id: str,
) -> str:
    """
    Submit a structured experiment request to the Coordinator.

    You are proposing a test — you do not control how it runs.
    The Coordinator will route it to the correct backend.
    Results come back as a new Finding linked to an ExperimentRun node.

    Returns: spec_id
    """
    spec = ExperimentSpec(
        id="",  # assigned by Coordinator
        session_id=session_id,
        hypothesis_finding_id=hypothesis_finding_id,
        backend_type=backend_type,  # type: ignore
        goal=goal,
        inputs=inputs,
        success_metrics=success_metrics,
        constraints=constraints,
        stop_conditions=[],
        artifacts_expected=[],
        max_compute_cost_usd=max_compute_cost_usd,
        max_wall_clock_seconds=max_wall_clock_seconds,
        submitted_by=agent_id,
    )

    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{COORDINATOR_URL}/internal/experiment",
                json=spec.model_dump(),
                params={"session_id": session_id, "agent_id": agent_id},
                timeout=10.0,
            )
            response.raise_for_status()
            data = response.json()
            return f"Experiment proposed: {data.get('spec_id', 'unknown')}. Result will appear in your next context cycle."
    except httpx.HTTPError as e:
        return f"Failed to propose experiment: {str(e)}"
