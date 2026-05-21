"""
Knowledge graph repository — the single gateway for all artifact CRUD.

Every read/write to the Neo4j knowledge graph goes through this module.
This ensures consistent handling of:
  - Node creation with proper labels and properties
  - Relationship creation (both direct edges and Relation nodes)
  - Event emission on every mutation
  - Embedding triggers for text-bearing artifacts

Design: One method per artifact type for creation, plus generic methods
for updates, queries, and relationship management.
"""

from typing import Any

from neo4j import AsyncSession

from src.graph.connection import Neo4jConnection
from src.events.bus import EventBus
from src.models.events import Event, EventType
from src.models.base import BaseArtifact
from src.models.source import Source, SourceChunk
from src.models.note import Note
from src.models.claim import Assumption, Hypothesis, Finding
from src.models.relation import Relation
from src.models.experiment import Experiment, ExperimentResult
from src.models.message import Message
from src.session_context import get_current_session_id


# Maps Python model classes to Neo4j node labels
_LABEL_MAP: dict[type, str] = {
    Source: "Source",
    SourceChunk: "SourceChunk",
    Note: "Note",
    Assumption: "Assumption",
    Hypothesis: "Hypothesis",
    Finding: "Finding",
    Relation: "Relation",
    Experiment: "Experiment",
    ExperimentResult: "ExperimentResult",
    Message: "Message",
}


class GraphRepository:
    """
    CRUD operations for all knowledge graph artifact types.

    All mutations emit events on the event bus. The repository
    does NOT handle embedding — that's the RAG layer's job.
    The repository focuses purely on graph structure.

    Usage:
        repo = GraphRepository(neo4j_conn, event_bus)
        source = Source(uri="paper.pdf", source_type="pdf", created_by="agent-1")
        await repo.create(source)
    """

    def __init__(
        self,
        connection: Neo4jConnection,
        event_bus: EventBus,
    ) -> None:
        self._conn = connection
        self._bus = event_bus

    # ── Generic CRUD ─────────────────────────────────────────────────

    async def create(self, artifact: BaseArtifact) -> str:
        """
        Create a node in Neo4j for any artifact type.

        Determines the correct label from the artifact's class,
        serializes properties, creates the node, and emits an event.

        Returns:
            The artifact's ID.
        """
        label = _LABEL_MAP.get(type(artifact))
        if not label:
            raise ValueError(f"Unknown artifact type: {type(artifact)}")

        props = artifact.neo4j_properties()
        if not props.get("session_id"):
            artifact.session_id = get_current_session_id()
            props["session_id"] = artifact.session_id

        async with self._conn.session() as session:
            await session.run(
                f"CREATE (n:{label} $props)",
                props=props,
            )

        await self._bus.publish(Event(
            event_type=EventType.ARTIFACT_CREATED,
            agent_id=artifact.created_by,
            artifact_id=artifact.id,
            artifact_type=label.lower(),
            payload={
                "label": label,
                **self._artifact_event_payload(artifact),
            },
        ))

        return artifact.id

    async def get(self, artifact_id: str) -> dict[str, Any] | None:
        """
        Fetch a single node by ID, regardless of label.

        Returns:
            Node properties as a dict, or None if not found.
        """
        async with self._conn.session() as session:
            result = await session.run(
                "MATCH (n {id: $id}) RETURN n, labels(n) AS labels",
                id=artifact_id,
            )
            record = await result.single()
            if not record:
                return None
            node_data = dict(record["n"])
            node_data["_labels"] = record["labels"]
            return node_data

    async def update(
        self,
        artifact_id: str,
        properties: dict[str, Any],
    ) -> None:
        """
        Update properties on an existing node.

        Only the provided properties are updated; others are preserved.
        """
        async with self._conn.session() as session:
            await session.run(
                "MATCH (n {id: $id}) SET n += $props",
                id=artifact_id,
                props=properties,
            )

        updated_node = await self.get(artifact_id)

        await self._bus.publish(Event(
            event_type=EventType.ARTIFACT_UPDATED,
            artifact_id=artifact_id,
            payload={
                "label": self._node_label(updated_node),
                "updated_fields": list(properties.keys()),
                "properties": properties,
            },
        ))

    async def update_status(
        self,
        artifact_id: str,
        status: str,
        agent_id: str = "",
    ) -> None:
        """
        Update the lifecycle status of an artifact.

        Emits a specific event for refutation since it's a significant
        research event that other agents should know about.
        """
        await self.update(artifact_id, {"status": status})

        if status == "refuted":
            await self._bus.publish(Event(
                event_type=EventType.ARTIFACT_REFUTED,
                agent_id=agent_id,
                artifact_id=artifact_id,
            ))

    # ── Relationship Management ──────────────────────────────────────

    async def create_edge(
        self,
        from_id: str,
        to_id: str,
        edge_type: str,
        properties: dict[str, Any] | None = None,
    ) -> None:
        """
        Create a direct Neo4j relationship between two nodes.

        This creates the structural edge used for fast traversal.
        For rich metadata, also create a Relation node.
        """
        props_clause = ""
        params: dict[str, Any] = {"from_id": from_id, "to_id": to_id}
        edge_props = dict(properties or {})
        if "session_id" not in edge_props:
            session_id = get_current_session_id()
            if session_id:
                edge_props["session_id"] = session_id
        if edge_props:
            props_clause = " $props"
            params["props"] = edge_props

        query = (
            f"MATCH (a {{id: $from_id}}), (b {{id: $to_id}}) "
            f"CREATE (a)-[:{edge_type}{props_clause}]->(b)"
        )
        async with self._conn.session() as session:
            await session.run(query, **params)

    async def create_relation(self, relation: Relation) -> str:
        """
        Create a Relation node AND direct edges to its source and target.

        A Relation is a first-class node that carries metadata about
        the connection (type, weight, reasoning, who created it).
        Additionally, direct edges are created for fast graph traversal.

        Returns:
            The Relation's ID.
        """
        # Create the Relation node
        await self.create(relation)

        # Link it to source and target artifacts
        await self.create_edge(
            relation.id, relation.source_artifact_id, "FROM_ARTIFACT"
        )
        await self.create_edge(
            relation.id, relation.target_artifact_id, "TO_ARTIFACT"
        )

        # Also create a direct traversal edge between the artifacts
        edge_type = relation.relation_type.value.upper()
        await self.create_edge(
            relation.source_artifact_id,
            relation.target_artifact_id,
            edge_type,
            {"relation_id": relation.id, "weight": relation.weight},
        )

        source_preview = await self.get(relation.source_artifact_id)
        target_preview = await self.get(relation.target_artifact_id)

        await self._bus.publish(Event(
            event_type=EventType.RELATION_CREATED,
            agent_id=relation.created_by,
            artifact_id=relation.id,
            payload={
                "relation_type": relation.relation_type.value,
                "source_id": relation.source_artifact_id,
                "target_id": relation.target_artifact_id,
                "source_type": self._node_label(source_preview),
                "target_type": self._node_label(target_preview),
                "source_text": self._node_text_preview(source_preview),
                "target_text": self._node_text_preview(target_preview),
                "source_preview": self._node_preview(
                    source_preview, relation.source_artifact_id
                ),
                "target_preview": self._node_preview(
                    target_preview, relation.target_artifact_id
                ),
                "reasoning": relation.reasoning,
                "weight": relation.weight,
            },
        ))

        return relation.id

    # ── Graph traversal ───────────────────────────────────────────────

    async def get_neighbors(
        self,
        node_id: str,
        edge_type: str,
        direction: str = "outgoing",
        limit: int = 10,
    ) -> list[dict[str, Any]]:
        """
        Fetch neighboring nodes connected by a specific edge type.

        Args:
            node_id: The starting node ID.
            edge_type: The relationship type to traverse.
            direction: "outgoing", "incoming", or "both".
            limit: Max number of neighbors to return.

        Returns:
            List of neighbor node property dicts.
        """
        if direction == "incoming":
            pattern = f"(neighbor)-[:{edge_type}]->(n)"
        elif direction == "both":
            pattern = f"(neighbor)-[:{edge_type}]-(n)"
        else:
            pattern = f"(n)-[:{edge_type}]->(neighbor)"

        query = (
            f"MATCH {pattern} WHERE n.id = $id "
            f"RETURN neighbor LIMIT $limit"
        )
        async with self._conn.session() as session:
            result = await session.run(query, id=node_id, limit=limit)
            records = await result.data()
            return [dict(r["neighbor"]) for r in records]

    # ── Source-specific helpers ───────────────────────────────────────

    async def link_chunk_to_source(
        self, chunk_id: str, source_id: str
    ) -> None:
        """Create a HAS_CHUNK edge from Source to SourceChunk."""
        await self.create_edge(source_id, chunk_id, "HAS_CHUNK")

    async def link_note_to_chunks(
        self, note_id: str, chunk_ids: list[str]
    ) -> None:
        """Create INFORMED edges from SourceChunks to a Note."""
        for chunk_id in chunk_ids:
            await self.create_edge(chunk_id, note_id, "INFORMED")

    async def link_hypothesis_to_experiment(
        self, hypothesis_id: str, experiment_id: str
    ) -> None:
        """Create a TESTED_BY edge from Hypothesis to Experiment."""
        await self.create_edge(hypothesis_id, experiment_id, "TESTED_BY")

    async def link_experiment_to_result(
        self, experiment_id: str, result_id: str
    ) -> None:
        """Create a PRODUCED edge from Experiment to Result."""
        await self.create_edge(experiment_id, result_id, "PRODUCED")

    async def link_finding_to_hypothesis(
        self, finding_id: str, hypothesis_id: str
    ) -> None:
        """Create an UPDATES edge from Finding to Hypothesis."""
        await self.create_edge(finding_id, hypothesis_id, "UPDATES")

    # ── Message helpers ──────────────────────────────────────────────

    async def create_message(self, message: Message) -> str:
        """
        Create a Message node and link it to the referenced artifact.

        Also emits a MESSAGE_SENT event so the target agent knows
        to check their inbox.
        """
        await self.create(message)

        if message.regarding_artifact_id:
            await self.create_edge(
                message.id, message.regarding_artifact_id, "REGARDING"
            )

        await self._bus.publish(Event(
            event_type=EventType.MESSAGE_SENT,
            agent_id=message.from_agent,
            payload={
                "to_agent": message.to_agent,
                "regarding": message.regarding_artifact_id,
                "text": message.text,
                "message_type": message.message_type.value,
            },
        ))

        return message.id

    # ── Batch operations ─────────────────────────────────────────────

    async def get_by_label(
        self,
        label: str,
        filters: dict[str, Any] | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """
        Fetch all nodes of a given label, optionally filtered.

        Args:
            label: Neo4j node label (e.g., "Hypothesis").
            filters: Property filters (exact match).
            limit: Max results to return.

        Returns:
            List of node property dicts.
        """
        where_clause = ""
        params: dict[str, Any] = {"limit": limit}

        if filters:
            conditions = [f"n.{k} = ${k}" for k in filters]
            where_clause = "WHERE " + " AND ".join(conditions)
            params.update(filters)

        query = f"MATCH (n:{label}) {where_clause} RETURN n LIMIT $limit"

        async with self._conn.session() as session:
            result = await session.run(query, **params)
            records = await result.data()
            return [dict(r["n"]) for r in records]

    async def get_unread_messages(self, agent_id: str) -> list[dict[str, Any]]:
        """Fetch all unread messages addressed to a specific agent."""
        return await self.get_by_label(
            "Message",
            filters={"to_agent": agent_id, "read": False},
        )

    async def mark_message_read(self, message_id: str) -> None:
        """Mark a message as read by the target agent."""
        await self.update(message_id, {"read": True})

    def _artifact_event_payload(self, artifact: BaseArtifact) -> dict[str, Any]:
        payload: dict[str, Any] = {"created_by": artifact.created_by}

        if isinstance(artifact, Note):
            payload.update({
                "text": artifact.text,
                "source_chunk_ids": artifact.source_chunk_ids,
                "confidence": artifact.confidence,
            })
        elif isinstance(artifact, Assumption):
            payload.update({
                "text": artifact.text,
                "basis": artifact.basis,
                "strength": artifact.strength,
            })
        elif isinstance(artifact, Hypothesis):
            payload.update({
                "text": artifact.text,
                "confidence": artifact.confidence,
                "testable": artifact.testable,
                "supporting_evidence": artifact.supporting_evidence,
            })
        elif isinstance(artifact, Finding):
            payload.update({
                "text": artifact.text,
                "hypothesis_id": artifact.hypothesis_id,
                "experiment_id": artifact.experiment_id,
                "conclusion_type": artifact.conclusion_type,
                "confidence": artifact.confidence,
            })
        elif isinstance(artifact, Experiment):
            payload.update({
                "hypothesis_id": artifact.hypothesis_id,
                "expected_outcome": artifact.spec.expected_outcome,
                "code_preview": artifact.spec.code[:240],
                "timeout_seconds": artifact.spec.timeout_seconds,
                "requirements": artifact.spec.requirements,
                "input_data": artifact.spec.input_data,
            })
        elif isinstance(artifact, ExperimentResult):
            payload.update({
                "experiment_id": artifact.experiment_id,
                "exit_code": artifact.exit_code,
                "stdout_preview": artifact.stdout[:240],
                "stderr_preview": artifact.stderr[:240],
                "artifacts": artifact.artifacts,
                "interpretation": artifact.interpretation,
            })
        elif isinstance(artifact, Message):
            payload.update({
                "from_agent": artifact.from_agent,
                "to_agent": artifact.to_agent,
                "text": artifact.text,
                "message_type": artifact.message_type.value,
                "regarding_artifact_id": artifact.regarding_artifact_id,
            })
        elif isinstance(artifact, Relation):
            payload.update({
                "source_artifact_id": artifact.source_artifact_id,
                "target_artifact_id": artifact.target_artifact_id,
                "relation_type": artifact.relation_type.value,
                "reasoning": artifact.reasoning,
                "weight": artifact.weight,
            })
        elif isinstance(artifact, Source):
            payload.update({
                "title": getattr(artifact, "title", ""),
                "uri": getattr(artifact, "uri", ""),
                "source_type": getattr(artifact, "source_type", ""),
            })
        elif isinstance(artifact, SourceChunk):
            payload.update({
                "text": getattr(artifact, "text", ""),
                "chunk_index": getattr(artifact, "chunk_index", None),
            })

        return payload

    @staticmethod
    def _node_label(node: dict[str, Any] | None) -> str:
        if not node:
            return ""
        labels = node.get("_labels", [])
        return labels[0] if labels else ""

    @staticmethod
    def _node_text_preview(node: dict[str, Any] | None) -> str:
        if not node:
            return ""
        for field in ("text", "title", "spec_expected_outcome", "spec_code"):
            value = node.get(field)
            if isinstance(value, str) and value.strip():
                return value.strip()[:160]
        return ""

    @classmethod
    def _node_preview(
        cls,
        node: dict[str, Any] | None,
        artifact_id: str,
    ) -> str:
        text = cls._node_text_preview(node)
        if text:
            return text

        label = cls._node_label(node)
        short_id = artifact_id[:12] + "..." if artifact_id else ""
        if label and short_id:
            return f"{label} {short_id}"
        return short_id
