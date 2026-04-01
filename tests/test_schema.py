"""Tests for canonical schemas — ExperimentSpec and ExperimentResult validation."""

import pytest
from datetime import datetime

from squid.schema.finding import Finding, EvidenceNode
from squid.schema.experiment import BackendJudgment, ExperimentResult, ExperimentSpec


class TestFindingValidation:
    def test_valid_finding(self):
        f = Finding(
            id="f_abc12345",
            session_id="session_001",
            agent_id="agent_001",
            claim="Naproxen has a longer half-life than ibuprofen",
            confidence=0.85,
            confidence_rationale="Two independent tier-1 sources",
            evidence_type="empirical",
        )
        assert f.id == "f_abc12345"

    def test_claim_max_length(self):
        with pytest.raises(ValueError, match="500 characters"):
            Finding(
                id="f_test",
                session_id="s",
                agent_id="a",
                claim="x" * 501,
                confidence=0.5,
                confidence_rationale="test",
                evidence_type="theoretical",
            )

    def test_contradicts_needs_counter_claim(self):
        with pytest.raises(ValueError, match="counter_claim"):
            Finding(
                id="f_test",
                session_id="s",
                agent_id="a",
                claim="Ibuprofen is better",
                confidence=0.5,
                confidence_rationale="test",
                evidence_type="theoretical",
                relates_to="f_other",
                relation_type="CONTRADICTS",
                counter_claim=None,
            )

    def test_numerical_claim_needs_verification(self):
        with pytest.raises(ValueError, match="numerical"):
            Finding(
                id="f_test",
                session_id="s",
                agent_id="a",
                claim="Water boils at 90C",
                confidence=0.5,
                confidence_rationale="test",
                evidence_type="theoretical",
                has_numerical_verification=False,
            )

    def test_numerical_claim_with_verification_passes(self):
        f = Finding(
            id="f_test",
            session_id="s",
            agent_id="a",
            claim="Water boils at 90C at 3000m",
            confidence=0.5,
            confidence_rationale="Verified via python_exec",
            evidence_type="computational",
            has_numerical_verification=True,
        )
        assert f.has_numerical_verification

    def test_confidence_rationale_not_empty(self):
        with pytest.raises(ValueError, match="confidence_rationale"):
            Finding(
                id="f_test",
                session_id="s",
                agent_id="a",
                claim="Test",
                confidence=0.5,
                confidence_rationale="",
                evidence_type="theoretical",
            )


class TestExperimentSpec:
    def test_valid_spec(self):
        spec = ExperimentSpec(
            id="spec_abc12345",
            session_id="session_001",
            hypothesis_finding_id="f_001",
            backend_type="sandbox_python",
            goal="Verify boiling point calculation",
            inputs={"code": "print(100)", "description": "test"},
            success_metrics=["result"],
            constraints={},
            stop_conditions=[],
            artifacts_expected=[],
            max_compute_cost_usd=0.0,
            max_wall_clock_seconds=60,
            submitted_by="agent_001",
        )
        assert spec.backend_type == "sandbox_python"

    def test_invalid_backend_type(self):
        with pytest.raises(ValueError):
            ExperimentSpec(
                id="spec_test",
                session_id="s",
                hypothesis_finding_id="f_001",
                backend_type="invalid_backend",  # type: ignore
                goal="test",
                inputs={},
                success_metrics=[],
                constraints={},
                stop_conditions=[],
                artifacts_expected=[],
                max_compute_cost_usd=0.0,
                max_wall_clock_seconds=60,
                submitted_by="agent",
            )


class TestExperimentResult:
    def test_valid_result(self):
        result = ExperimentResult(
            id="run_abc12345",
            spec_id="spec_001",
            session_id="session_001",
            hypothesis_finding_id="f_001",
            backend_type="sandbox_python",
            status="completed",
            summary="Calculation verified",
            metrics={"result": 100.0},
            judgment=BackendJudgment(
                outcome="supports",
                confidence="high",
                reason="Numerical result matches claim",
            ),
            artifacts=[],
            environment={"version": "1.0"},
            cost={},
        )
        assert result.judgment.outcome == "supports"
