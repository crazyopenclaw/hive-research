#!/bin/bash
set -e
echo "Starting ResearchSquid Coordinator..."
uvicorn squid.coordinator.app:app --host 0.0.0.0 --port 8000
