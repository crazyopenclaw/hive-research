"""Tests for Coordinator API endpoints (with mocked backends)."""

import pytest


def test_health_endpoint_exists():
    """Coordinator should have a /health endpoint."""
    from squid.coordinator.app import create_app
    app = create_app()
    routes = [r.path for r in app.routes]
    assert "/health" in routes


def test_research_endpoint_exists():
    """Coordinator should have POST /research."""
    from squid.coordinator.app import create_app
    app = create_app()
    routes = [r.path for r in app.routes]
    assert "/research" in routes


def test_internal_endpoints_exist():
    """Coordinator should have internal endpoints."""
    from squid.coordinator.app import create_app
    app = create_app()
    routes = [r.path for r in app.routes]
    assert "/internal/finding" in routes
    assert "/internal/experiment" in routes
    assert "/internal/context/{agent_id}" in routes
    assert "/internal/experiments/queue" in routes
