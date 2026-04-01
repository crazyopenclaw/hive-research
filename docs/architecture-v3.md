# ResearchSquid — Full Architecture Document (v3)

See the full architecture document provided separately.

## Key Design Decisions

1. **NOT OpenClaw for agent execution** — agents run in LangGraph with explicit tool allowlists
2. **Tier-1 / Tier-2 split** — Tier-1 proposes, Tier-2 validates
3. **Typed execution backends** — sandbox_python, gpu_training, bio_pipeline, simulation
4. **ExperimentSpec** — structured hypothesis-as-experiment, not arbitrary code
5. **Honest Contract** — calibrated confidence, insufficient-evidence as valid output
