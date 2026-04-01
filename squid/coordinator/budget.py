"""Budget enforcement — LLM + compute budget tracking."""

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Dict


class BudgetAction(str, Enum):
    NORMAL = "normal"
    DOWNGRADE_TO_HAIKU = "downgrade_to_haiku"
    SUSPEND_NON_CRITICAL = "suspend_non_critical"
    QUEUE_EXPERIMENTS = "queue_experiments"
    STOP_ALL = "stop_all"


@dataclass
class BudgetTracker:
    session_id: str
    llm_budget_usd: float
    compute_budget_usd: float

    llm_spent_usd: float = 0.0
    compute_spent_usd: float = 0.0
    last_updated: datetime = field(default_factory=datetime.utcnow)

    max_findings_per_hour: int = 20
    max_searches_per_cycle: int = 30
    hourly_finding_counts: Dict[str, int] = field(default_factory=dict)

    @property
    def llm_percent_used(self) -> float:
        if self.llm_budget_usd <= 0:
            return 100.0
        return (self.llm_spent_usd / self.llm_budget_usd) * 100

    @property
    def compute_percent_used(self) -> float:
        if self.compute_budget_usd <= 0:
            return 100.0
        return (self.compute_spent_usd / self.compute_budget_usd) * 100

    @property
    def total_percent_used(self) -> float:
        return max(self.llm_percent_used, self.compute_percent_used)

    def get_action(self) -> BudgetAction:
        pct = self.total_percent_used
        if pct >= 100:
            return BudgetAction.STOP_ALL
        elif pct >= 90:
            return BudgetAction.QUEUE_EXPERIMENTS
        elif pct >= 85:
            return BudgetAction.SUSPEND_NON_CRITICAL
        elif pct >= 70:
            return BudgetAction.DOWNGRADE_TO_HAIKU
        return BudgetAction.NORMAL

    def record_llm_spend(self, amount_usd: float):
        self.llm_spent_usd += amount_usd
        self.last_updated = datetime.utcnow()

    def record_compute_spend(self, amount_usd: float):
        self.compute_spent_usd += amount_usd
        self.last_updated = datetime.utcnow()

    def can_submit_experiment(self, estimated_cost: float) -> bool:
        if self.compute_spent_usd + estimated_cost > self.compute_budget_usd:
            return False
        return self.get_action() not in (BudgetAction.QUEUE_EXPERIMENTS, BudgetAction.STOP_ALL)

    def get_status(self) -> Dict:
        return {
            "session_id": self.session_id,
            "llm": f"${self.llm_spent_usd:.4f} / ${self.llm_budget_usd:.2f} ({self.llm_percent_used:.1f}%)",
            "compute": f"${self.compute_spent_usd:.4f} / ${self.compute_budget_usd:.2f} ({self.compute_percent_used:.1f}%)",
            "action": self.get_action().value,
        }
