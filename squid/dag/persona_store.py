"""Persona persistence — Neo4j storage for agent personas and revision history."""

from datetime import datetime
from typing import Any, Dict, List, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from squid.dag.client import DAGClient
from squid.agents.persona import AgentPersona


async def save_persona(driver: "DAGClient", persona: AgentPersona) -> str:
    """Persist persona to Neo4j."""
    await driver.run(
        """
        MERGE (p:AgentPersona {id: $id})
        SET p.agent_id = $agent_id,
            p.session_id = $session_id,
            p.revision = $revision,
            p.specialty = $specialty,
            p.skepticism_level = $skepticism_level,
            p.preferred_evidence_types = $preferred_evidence_types,
            p.contradiction_aggressiveness = $contradiction_aggressiveness,
            p.source_strictness = $source_strictness,
            p.experiment_appetite = $experiment_appetite,
            p.reporting_style = $reporting_style,
            p.model_tier = $model_tier,
            p.model_name = $model_name,
            p.model_base_url = $model_base_url,
            p.model_api_key = $model_api_key,
            p.created_at = $created_at,
            p.updated_at = $updated_at,
            p.revision_history = $revision_history
        """,
        id=persona.id,
        agent_id=persona.agent_id,
        session_id=persona.session_id,
        revision=persona.revision,
        specialty=persona.specialty,
        skepticism_level=persona.skepticism_level,
        preferred_evidence_types=persona.preferred_evidence_types,
        contradiction_aggressiveness=persona.contradiction_aggressiveness,
        source_strictness=persona.source_strictness,
        experiment_appetite=persona.experiment_appetite,
        reporting_style=persona.reporting_style,
        model_tier=persona.model_tier,
        model_name=persona.model_name,
        model_base_url=persona.model_base_url,
        model_api_key=persona.model_api_key,
        created_at=(persona.created_at or datetime.utcnow()).isoformat(),
        updated_at=(persona.updated_at or datetime.utcnow()).isoformat(),
        revision_history=str(persona.revision_history),
    )

    # Link persona to agent if agent exists
    await driver.run(
        """
        MATCH (p:AgentPersona {id: $pid})
        OPTIONAL MATCH (a:Agent {id: $aid, session_id: $sid})
        FOREACH (_ IN CASE WHEN a IS NOT NULL THEN [1] ELSE [] END |
            MERGE (a)-[:HAS_PERSONA {revision: $revision, updated_at: $updated_at}]->(p)
        )
        """,
        pid=persona.id,
        aid=persona.agent_id,
        sid=persona.session_id,
        revision=persona.revision,
        updated_at=datetime.utcnow().isoformat(),
    )

    return persona.id


async def load_persona(
    driver: "DAGClient", session_id: str, agent_id: str
) -> Optional[AgentPersona]:
    """Load persona from Neo4j."""
    records = await driver.run(
        """
        MATCH (p:AgentPersona {agent_id: $aid, session_id: $sid})
        RETURN p ORDER BY p.revision DESC LIMIT 1
        """,
        aid=agent_id,
        sid=session_id,
    )
    if not records:
        return None

    props = dict(records[0]["p"])
    return AgentPersona(
        id=props["id"],
        agent_id=props["agent_id"],
        session_id=props["session_id"],
        revision=props.get("revision", 1),
        specialty=props.get("specialty", "general"),
        skepticism_level=props.get("skepticism_level", 0.5),
        preferred_evidence_types=props.get("preferred_evidence_types", ["empirical", "theoretical"]),
        contradiction_aggressiveness=props.get("contradiction_aggressiveness", 0.5),
        source_strictness=props.get("source_strictness", 0.7),
        experiment_appetite=props.get("experiment_appetite", 0.5),
        reporting_style=props.get("reporting_style", "concise"),
        model_tier=props.get("model_tier", "fast"),
        model_name=props.get("model_name"),
        model_base_url=props.get("model_base_url"),
        model_api_key=props.get("model_api_key"),
        created_at=props.get("created_at"),
        updated_at=props.get("updated_at"),
        revision_history=_parse_revision_history(props.get("revision_history", "[]")),
    )


async def load_session_personas(
    driver: "DAGClient", session_id: str
) -> List[AgentPersona]:
    """Load all personas for a session."""
    records = await driver.run(
        """
        MATCH (p:AgentPersona {session_id: $sid})
        RETURN p ORDER BY p.agent_id, p.revision DESC
        """,
        sid=session_id,
    )
    # Deduplicate: keep latest revision per agent
    seen = {}
    personas = []
    for rec in records:
        props = dict(rec["p"])
        agent_id = props["agent_id"]
        if agent_id not in seen:
            seen[agent_id] = True
            personas.append(AgentPersona(
                id=props["id"],
                agent_id=agent_id,
                session_id=props["session_id"],
                revision=props.get("revision", 1),
                specialty=props.get("specialty", "general"),
                skepticism_level=props.get("skepticism_level", 0.5),
                preferred_evidence_types=props.get("preferred_evidence_types", ["empirical", "theoretical"]),
                contradiction_aggressiveness=props.get("contradiction_aggressiveness", 0.5),
                source_strictness=props.get("source_strictness", 0.7),
                experiment_appetite=props.get("experiment_appetite", 0.5),
                reporting_style=props.get("reporting_style", "concise"),
                model_tier=props.get("model_tier", "fast"),
                model_name=props.get("model_name"),
                model_base_url=props.get("model_base_url"),
                model_api_key=props.get("model_api_key"),
                created_at=props.get("created_at"),
                updated_at=props.get("updated_at"),
                revision_history=_parse_revision_history(props.get("revision_history", "[]")),
            ))
    return personas


def _parse_revision_history(raw: Any) -> List[Dict[str, Any]]:
    """Parse revision history from Neo4j storage."""
    if isinstance(raw, list):
        return raw
    if isinstance(raw, str):
        try:
            import json
            return json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            return []
    return []
