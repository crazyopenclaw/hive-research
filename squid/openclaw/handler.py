"""OpenClaw skill adapter — parses messages, calls coordinator HTTP API."""

import re
import os
from typing import Optional

import httpx


COORDINATOR_URL = os.getenv("SQUID_API_URL", "http://coordinator:8000")
API_KEY = os.getenv("SQUID_API_KEY", "")


async def handle_message(message: str, user_id: str) -> str:
    """Parse OpenClaw message and call the appropriate coordinator endpoint."""
    message = message.strip().lower()

    if message.startswith("research "):
        question = message[9:].strip()
        resp = await _post("/research", {"question": question, "modality": "general", "user_id": user_id})
        return f"Research started. Session: {resp.get('session_id', 'unknown')}\nI'll update you with findings."

    elif message == "status":
        resp = await _get("/session/latest")  # TODO: track active session per user
        return str(resp)

    elif message == "summary":
        resp = await _get("/session/latest/summary")
        return resp.get("summary", "No summary available")

    elif message == "stop":
        resp = await _post("/session/latest/stop", {})
        return f"Research stopped.\n\n{resp}"

    elif message in ("pause", "resume"):
        await _post(f"/session/latest/{message}", {})
        return f"Session {message}d."

    else:
        return "Commands: 'research [question]' | 'status' | 'summary' | 'stop' | 'pause' | 'resume'"


async def _post(path: str, data: dict) -> dict:
    headers = {"Authorization": f"Bearer {API_KEY}"} if API_KEY else {}
    async with httpx.AsyncClient() as client:
        resp = await client.post(f"{COORDINATOR_URL}{path}", json=data, headers=headers, timeout=30.0)
        resp.raise_for_status()
        return resp.json()


async def _get(path: str) -> dict:
    headers = {"Authorization": f"Bearer {API_KEY}"} if API_KEY else {}
    async with httpx.AsyncClient() as client:
        resp = await client.get(f"{COORDINATOR_URL}{path}", headers=headers, timeout=30.0)
        resp.raise_for_status()
        return resp.json()
