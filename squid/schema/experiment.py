"""ExperimentSpec, ExperimentResult, BackendJudgment — experiment lifecycle objects."""

from datetime import datetime
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel


class ExperimentSpec(BaseModel):
    # Identity
    id: str  # "spec_" + 8-char hex
    session_id: str
    hypothesis_finding_id: str  # Finding ID that this tests

    # Routing
    backend_type: Literal[
        "sandbox_python",
        "gpu_training",
        "bio_pipeline",
        "simulation",
    ]
    priority: Literal["low", "normal", "high"] = "normal"

    # What this experiment is trying to find out
    goal: str  # one sentence, natural language

    # Budget caps — Coordinator enforces, backend cannot exceed
    max_compute_cost_usd: float
    max_wall_clock_seconds: int

    # Expected outputs
    success_metrics: List[str]  # metric names the backend must return
    constraints: Dict[str, Any]  # e.g. {"max_gpus": 1}
    stop_conditions: List[str]  # e.g. ["runtime_exceeded", "nan_loss"]
    artifacts_expected: List[str]  # e.g. ["train_log", "eval_metrics.json"]

    # Backend-specific inputs — only one populated
    inputs: Dict[str, Any]  # validated against backend schema on routing

    submitted_by: str  # agent_id
    submitted_at: datetime = None
    status: Literal["pending", "running", "completed", "failed", "cancelled"] = "pending"


class BackendJudgment(BaseModel):
    outcome: Literal["supports", "refutes", "inconclusive", "failed"]
    confidence: Literal["high", "moderate", "low"]
    reason: str  # one sentence — why this judgment


class ExperimentResult(BaseModel):
    # Identity
    id: str  # "run_" + 8-char hex
    spec_id: str  # ExperimentSpec this came from
    session_id: str
    hypothesis_finding_id: str

    # Status
    backend_type: str
    status: Literal["completed", "failed", "timeout", "error"]

    # What the backend found
    summary: str  # one paragraph, natural language — for human consumption
    metrics: Dict[str, float]  # raw numbers
    judgment: BackendJudgment  # machine-readable conclusion

    # Reproducibility
    artifacts: List[str]  # s3:// or local paths
    environment: Dict[str, str]  # {"image": "trainer:v1", "commit": "abc123"}

    # Cost
    cost: Dict[str, Any]  # {"gpu_minutes": 28} or {"api_calls": 5}
    completed_at: datetime = None
    wall_clock_seconds: int = 0
