"""BaseBackend — abstract class all Tier-2 backends implement."""

from abc import ABC, abstractmethod
from typing import Any, Dict

from squid.schema.experiment import BackendJudgment, ExperimentResult, ExperimentSpec


class BaseBackend(ABC):
    @abstractmethod
    def validate_inputs(self, inputs: Dict[str, Any]) -> None:
        """Raise ValueError if inputs don't match this backend's schema."""
        ...

    @abstractmethod
    async def run_experiment(self, spec: ExperimentSpec) -> ExperimentResult:
        """
        Run the experiment. Return a structured result.
        Must respect spec.max_wall_clock_seconds and spec.max_compute_cost_usd.
        Must return ExperimentResult even on failure (status="failed").
        Must never raise — catch all exceptions and return failed result.
        """
        ...

    def make_failed_result(self, spec: ExperimentSpec, reason: str) -> ExperimentResult:
        """Convenience method for backends to return a clean failure."""
        from uuid import uuid4
        return ExperimentResult(
            id=f"run_{uuid4().hex[:8]}",
            spec_id=spec.id,
            session_id=spec.session_id,
            hypothesis_finding_id=spec.hypothesis_finding_id,
            backend_type=spec.backend_type,
            status="failed",
            summary=f"Experiment failed: {reason}",
            metrics={},
            judgment=BackendJudgment(
                outcome="failed",
                confidence="high",
                reason=reason,
            ),
            artifacts=[],
            environment={},
            cost={},
        )
