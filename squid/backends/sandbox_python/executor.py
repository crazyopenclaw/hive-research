"""Sandbox Python backend — runs code in isolated Docker, no network."""

from typing import Any, Dict
from uuid import uuid4

from squid.backends.base import BaseBackend
from squid.schema.experiment import BackendJudgment, ExperimentResult, ExperimentSpec


class SandboxPythonBackend(BaseBackend):
    def validate_inputs(self, inputs: Dict[str, Any]) -> None:
        required = ["code", "description"]
        for field in required:
            if field not in inputs:
                raise ValueError(f"Missing required field: {field}")

    async def run_experiment(self, spec: ExperimentSpec) -> ExperimentResult:
        try:
            self.validate_inputs(spec.inputs)
            # In production: run in Docker container with network_mode=none
            return ExperimentResult(
                id=f"run_{uuid4().hex[:8]}",
                spec_id=spec.id,
                session_id=spec.session_id,
                hypothesis_finding_id=spec.hypothesis_finding_id,
                backend_type="sandbox_python",
                status="completed",
                summary="Sandbox execution pending Docker integration",
                metrics={},
                judgment=BackendJudgment(
                    outcome="inconclusive",
                    confidence="low",
                    reason="Sandbox executor needs Docker runtime",
                ),
                artifacts=[],
                environment={"version": "0.1.0"},
                cost={},
            )
        except Exception as e:
            return self.make_failed_result(spec, str(e))
