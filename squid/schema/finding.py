"""Finding — a specific claim with provenance, confidence, and DAG position."""

import re
from datetime import datetime
from typing import List, Literal, Optional

from pydantic import BaseModel, field_validator


class Finding(BaseModel):
    # Identity
    id: str  # "f_" + 8-char hex
    session_id: str
    agent_id: str
    cycle_posted: int = 0

    # The claim
    claim: str  # max 500 chars
    confidence: float  # 0.0–1.0
    confidence_rationale: str  # required — cannot be empty
    evidence_type: Literal["empirical", "theoretical", "computational", "insufficient"]

    # Source quality
    source_urls: List[str] = []
    source_tiers: List[int] = []  # one per URL, 1–4
    min_source_tier: int = 4  # computed from source_tiers
    has_numerical_verification: bool = False

    # DAG position
    cluster_id: Optional[str] = None
    status: Literal["active", "superseded", "retracted", "insufficient"] = "active"

    # Links
    experiment_run_id: Optional[str] = None  # if this finding came from a run
    relates_to: Optional[str] = None  # Finding ID
    relation_type: Optional[Literal["SUPPORTS", "CONTRADICTS", "EXTENDS", "REFUTES"]] = None
    counter_claim: Optional[str] = None  # required if relation_type == CONTRADICTS

    created_at: datetime = None

    @field_validator("claim")
    @classmethod
    def claim_max_length(cls, v):
        if len(v) > 500:
            raise ValueError("Claim must be 500 characters or fewer")
        return v

    @field_validator("counter_claim")
    @classmethod
    def contradicts_needs_counter(cls, v, info):
        if info.data.get("relation_type") == "CONTRADICTS" and not v:
            raise ValueError("counter_claim is required when relation_type is CONTRADICTS")
        return v

    @field_validator("has_numerical_verification")
    @classmethod
    def numbers_need_verification(cls, v, info):
        claim = info.data.get("claim", "")
        if re.search(r'\d', claim) and not v:
            raise ValueError(
                "Claim contains numbers. Set has_numerical_verification=True only "
                "after running python_exec or linking an ExperimentRun."
            )
        return v

    @field_validator("confidence_rationale")
    @classmethod
    def rationale_not_empty(cls, v):
        if not v or not v.strip():
            raise ValueError("confidence_rationale cannot be empty")
        return v


class EvidenceNode(BaseModel):
    """DAG node created from an ExperimentResult. Thin wrapper."""
    finding_id: str  # the Finding this evidence supports or refutes
    experiment_run_id: str  # the ExperimentRun that produced this
    judgment_outcome: Literal["supports", "refutes", "inconclusive", "failed"]
    judgment_confidence: Literal["high", "moderate", "low"]
    summary: str
    created_at: datetime = None
