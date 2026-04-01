---
name: research-squid
description: >
  Launch a research squad on any question. Real AI agents search the web,
  read papers, run experiments, and debate each other.
  Usage: "research [question]" | "status" | "stop" | "summary"
metadata:
  openclaw:
    emoji: " "
    requires:
      env: ["SQUID_API_URL", "SQUID_API_KEY"]
---
# ResearchSquid

## Commands
- "research [question]" → start general research
- "research [question] with gpu backend" → start with compute backend
- "status" → active clusters, finding count, spend
- "summary" → full research summary
- "stop" → final report and stop
- "pause" / "resume" → session control

## What to expect
Results are calibrated. The system will say "insufficient evidence" if it cannot find adequate support. Source tier is shown on every finding.
