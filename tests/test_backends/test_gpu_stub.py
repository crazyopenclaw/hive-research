"""Tests for GPU backend with stub trainer."""

import pytest

from squid.backends.gpu_training.executor import GPUTrainingBackend


def test_gpu_backend_exists():
    backend = GPUTrainingBackend()
    assert backend is not None


def test_gpu_validate_inputs():
    backend = GPUTrainingBackend()
    with pytest.raises(ValueError, match="repo_path"):
        backend.validate_inputs({"base_commit": "abc"})
