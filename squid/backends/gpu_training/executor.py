"""GPU Training backend — applies patch, runs train/eval, captures metrics."""

from typing import Any, Dict

from squid.backends.base import BaseBackend
from squid.schema.experiment import ExperimentResult, ExperimentSpec


class GPUTrainingBackend(BaseBackend):
    def validate_inputs(self, inputs: Dict[str, Any]) -> None:
        required = ["repo_path", "base_commit", "patch", "train_command", "eval_command"]
        for field in required:
            if field not in inputs:
                raise ValueError(f"Missing required field: {field}")

    async def run_experiment(self, spec: ExperimentSpec) -> ExperimentResult:
        try:
            self.validate_inputs(spec.inputs)
            return self.make_failed_result(spec, "GPU backend not yet implemented")
        except Exception as e:
            return self.make_failed_result(spec, str(e))
