"""Tests for DAG write/read cycle — validates post_finding, reader, taxonomy against real Neo4j.

These tests require a running Neo4j instance (docker-compose up neo4j).
Skipped by default; run with: pytest tests/test_dag.py --run-dag
"""

import os
import pytest
import uuid
from datetime import datetime

# Skip unless explicitly requested (needs running Neo4j)
pytestmark = pytest.mark.skipif(
    os.getenv("NEO4J_URI") is None and not os.path.exists("/.dockerenv"),
    reason="Requires running Neo4j instance",
)


def gen_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:8]}"


@pytest.fixture
def dag_client():
    """Create a test DAG client."""
    from hive.dag.client import DAGClient
    client = DAGClient(
        uri=os.getenv("NEO4J_URI", "bolt://localhost:7687"),
        user=os.getenv("NEO4J_USER", "neo4j"),
        password=os.getenv("NEO4J_PASSWORD", "hiveresearch"),
    )
    return client


@pytest.mark.asyncio
async def test_write_and_read_finding(dag_client):
    """Write a finding, read it back, verify all fields."""
    from hive.dag.writer import post_finding
    from hive.dag.reader import get_frontier
    from hive.schema.finding import Finding

    await dag_client.connect()
    test_session = gen_id("test_session")

    finding = Finding(
        id=gen_id("f"),
        session_id=test_session,
        agent_id="test_agent",
        claim="Naproxen has a longer half-life than ibuprofen",
        confidence=0.85,
        confidence_rationale="Two independent tier-1 sources",
        evidence_type="empirical",
        source_urls=["https://pubmed.ncbi.nlm.nih.gov/12345/"],
        source_tiers=[1],
        min_source_tier=1,
    )

    fid = await post_finding(dag_client, finding)
    assert fid == finding.id

    frontier = await get_frontier(dag_client, test_session)
    assert len(frontier) >= 1
    assert any(f["claim"] == finding.claim for f in frontier)

    await dag_client.close()


@pytest.mark.asyncio
async def test_write_finding_with_edge(dag_client):
    """Write a finding that SUPPORTS another finding."""
    from hive.dag.writer import post_finding
    from hive.dag.reader import get_frontier
    from hive.schema.finding import Finding

    await dag_client.connect()
    test_session = gen_id("test_session")

    # Write parent finding
    parent = Finding(
        id=gen_id("f"),
        session_id=test_session,
        agent_id="test_agent",
        claim="Ibuprofen has shorter half-life",
        confidence=0.90,
        confidence_rationale="Well-established pharmacology",
        evidence_type="empirical",
    )
    await post_finding(dag_client, parent)

    # Write child finding that SUPPORTS parent
    child = Finding(
        id=gen_id("f"),
        session_id=test_session,
        agent_id="test_agent",
        claim="Naproxen has longer half-life than ibuprofen",
        confidence=0.85,
        confidence_rationale="PubMed comparison study",
        evidence_type="empirical",
        relates_to=parent.id,
        relation_type="SUPPORTS",
    )
    await post_finding(dag_client, child)

    await dag_client.close()


@pytest.mark.asyncio
async def test_contradicts_requires_counter_claim(dag_client):
    """CONTRADICTS edge without counter_claim should fail."""
    from hive.dag.writer import post_finding
    from hive.schema.finding import Finding

    await dag_client.connect()
    test_session = gen_id("test_session")

    with pytest.raises(ValueError, match="counter_claim"):
        Finding(
            id=gen_id("f"),
            session_id=test_session,
            agent_id="test_agent",
            claim="Ibuprofen is better",
            confidence=0.5,
            confidence_rationale="test",
            evidence_type="theoretical",
            relates_to="f_other",
            relation_type="CONTRADICTS",
            counter_claim=None,  # Missing!
        )

    await dag_client.close()


@pytest.mark.asyncio
async def test_numerical_verification_required():
    """Finding with numbers but no verification should fail validation."""
    from hive.schema.finding import Finding

    with pytest.raises(ValueError, match="numerical"):
        Finding(
            id=gen_id("f"),
            session_id="s",
            agent_id="a",
            claim="Water boils at 90C at altitude 3000m",
            confidence=0.5,
            confidence_rationale="test",
            evidence_type="theoretical",
            has_numerical_verification=False,
        )


@pytest.mark.asyncio
async def test_taxonomy_classification():
    """Verify source tier classification."""
    from hive.dag.taxonomy import classify_source_tier

    assert classify_source_tier("https://pubmed.ncbi.nlm.nih.gov/12345/")[0] == 1
    assert classify_source_tier("https://arxiv.org/abs/2301.00001")[0] == 2
    assert classify_source_tier("https://en.wikipedia.org/wiki/Test")[0] == 3
    assert classify_source_tier("https://random-blog.com/post")[0] == 4
