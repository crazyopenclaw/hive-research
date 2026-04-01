"""Simulation backend — runs solver, parses output, evaluates constraints."""

from typing import Any, Dict

from squid.backends.base import BaseBackend
from squid.schema.experiment import ExperimentResult, ExperimentSpec


class SimulationBackend(BaseBackend):
    def validate_inputs(self, inputs: Dict[str, Any]) -> None:
        required = ["config_path", "solver_command", "validation_command"]
        for field in required:
            if field not in inputs:
                raise ValueError(f"Missing required field: {field}")

    async def run_experiment(self, spec: ExperimentSpec) -> ExperimentResult:
        try:
            self.validate_inputs(spec.inputs)
            return self.make_failed_result(spec, "Simulation backend not yet implemented")
        except Exception as e:
            return self.make_failed_result(spec, str(e))
