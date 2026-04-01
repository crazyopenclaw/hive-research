"""ResearchSquid — two-tier autonomous research system.

Tier 1 proposes. Tier 2 validates. DAG remembers.
"""

from squid.schema.finding import EvidenceNode, Finding
from squid.schema.experiment import BackendJudgment, ExperimentResult, ExperimentSpec
from squid.schema.session import Session, SessionConfig, SessionWorkflowStep

# Persona imports available but not eagerly loaded
# from squid.agents.persona import AgentPersona, PERSONA_TEMPLATES, create_persona

__all__ = [
    "EvidenceNode",
    "Finding",
    "BackendJudgment",
    "ExperimentResult",
    "ExperimentSpec",
    "Session",
    "SessionConfig",
    "SessionWorkflowStep",
]
