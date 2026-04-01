"""Session lifecycle — create, pause, resume, stop."""

import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional

from squid.dag.client import DAGClient
from squid.schema.session import Session, SessionConfig


def gen_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:8]}"


async def create_session(
    driver: DAGClient,
    question: str,
    modality: str = "general",
    available_backends: List[str] = None,
    backend_config: Dict[str, Any] = None,
    llm_budget_usd: float = 20.0,
    compute_budget_usd: float = 20.0,
    agent_count: int = 10,
    user_id: Optional[str] = None,
) -> Session:
    """Create a new research session."""
    session = Session(
        id=gen_id("session"),
        question=question,
        modality=modality,
        llm_budget_usd=llm_budget_usd,
        compute_budget_usd=compute_budget_usd,
        agent_count=agent_count,
        created_at=datetime.utcnow(),
    )
    await driver.run(
        """
        MERGE (s:Session {id: $id})
        SET s.question = $question, s.modality = $modality, s.status = 'active',
            s.created_at = $created_at, s.llm_budget_usd = $llm_budget_usd,
            s.compute_budget_usd = $compute_budget_usd, s.llm_spent_usd = 0,
            s.compute_spent_usd = 0
        """,
        id=session.id, question=session.question, modality=session.modality,
        created_at=session.created_at.isoformat(),
        llm_budget_usd=session.llm_budget_usd,
        compute_budget_usd=session.compute_budget_usd,
    )
    return session


async def pause_session(driver: DAGClient, session_id: str) -> None:
    await driver.run("MATCH (s:Session {id: $id}) SET s.status = 'paused'", id=session_id)


async def resume_session(driver: DAGClient, session_id: str) -> None:
    await driver.run("MATCH (s:Session {id: $id}) SET s.status = 'active'", id=session_id)


async def stop_session(driver: DAGClient, session_id: str) -> Dict:
    await driver.run("MATCH (s:Session {id: $id}) SET s.status = 'stopped'", id=session_id)
    from squid.dag.reader import get_frontier
    findings = await get_frontier(driver, session_id)
    return {"session_id": session_id, "status": "stopped", "finding_count": len(findings)}
