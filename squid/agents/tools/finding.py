"""post_finding — writes to DAG via Coordinator API."""

import os
from typing import Dict, List, Optional

import httpx

from squid.schema.finding import Finding


COORDINATOR_URL = os.getenv("COORDINATOR_URL", "http://coordinator:8000")


async def post_finding(
    claim: str,
    confidence: float,
    confidence_rationale: str,
    evidence_type: str,
    source_urls: List[str],
    numerical_verification_ran: bool,
    session_id: str,
    agent_id: str,
    experiment_run_id: Optional[str] = None,
    relates_to: Optional[str] = None,
    relation_type: Optional[str] = None,
    counter_claim: Optional[str] = None,
) -> str:
    """
    Post a finding to the knowledge DAG.

    Validations: CONTRADICTS requires counter_claim. Numbers require verification.

    Returns: finding_id
    """
    payload = {
        "claim": claim,
        "confidence": confidence,
        "confidence_rationale": confidence_rationale,
        "evidence_type": evidence_type,
        "source_urls": source_urls,
        "numerical_verification_ran": numerical_verification_ran,
        "experiment_run_id": experiment_run_id,
        "relates_to": relates_to,
        "relation_type": relation_type,
        "counter_claim": counter_claim,
        "session_id": session_id,
        "agent_id": agent_id,
    }

    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{COORDINATOR_URL}/internal/finding",
                json=payload,
                timeout=10.0,
            )
            response.raise_for_status()
            data = response.json()
            return f"Finding posted: {data.get('finding_id', 'unknown')}"
    except httpx.HTTPStatusError as e:
        if e.response.status_code == 400:
            return f"Validation error: {e.response.json().get('detail', 'Unknown')}"
        raise
    except httpx.HTTPError as e:
        return f"Failed to post finding: {str(e)}"
