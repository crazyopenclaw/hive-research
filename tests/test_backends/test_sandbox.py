"""Tests for sandbox Python executor."""

import pytest

from squid.backends.sandbox_python.executor import SandboxPythonBackend
from squid.schema.experiment import ExperimentSpec


def test_sandbox_backend_exists():
    backend = SandboxPythonBackend()
    assert backend is not None


def test_sandbox_validate_inputs():
    backend = SandboxPythonBackend()
    with pytest.raises(ValueError, match="code"):
        backend.validate_inputs({"description": "test"})


def test_sandbox_validate_inputs_passes():
    backend = SandboxPythonBackend()
    backend.validate_inputs({"code": "print(1)", "description": "test"})


@pytest.mark.asyncio
async def test_sandbox_run_returns_result():
    backend = SandboxPythonBackend()
    spec = ExperimentSpec(
        id="spec_test",
        session_id="s",
        hypothesis_finding_id="f_001",
        backend_type="sandbox_python",
        goal="test",
        inputs={"code": "print(1)", "description": "test"},
        success_metrics=[],
        constraints={},
        stop_conditions=[],
        artifacts_expected=[],
        max_compute_cost_usd=0.0,
        max_wall_clock_seconds=60,
        submitted_by="agent",
    )
    result = await backend.run_experiment(spec)
    assert result.status in ("completed", "failed")
    assert result.judgment is not None
