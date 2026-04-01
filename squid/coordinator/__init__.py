"""ResearchSquid coordinator — session lifecycle, routing, budget, telemetry."""

# Lazy imports to avoid dependency chain issues at import time.
# These are available via direct import:
#   from squid.coordinator.session import create_session
#   from squid.coordinator.app import create_app
#   from squid.coordinator.audit import AuditLogger, EventType
#   from squid.coordinator.telemetry import SessionTelemetry
#   from squid.coordinator.report_agent import ReportAgent
