"""Sends updates to user via OpenClaw webhook."""

import os
from typing import Optional

import httpx


OPENCLAW_WEBHOOK_URL = os.getenv("OPENCLAW_WEBHOOK_URL", "")


async def dispatch_update(
    session_id: str,
    message: str,
    channel: Optional[str] = None,
) -> None:
    """Send a research update to the user via OpenClaw webhook."""
    if not OPENCLAW_WEBHOOK_URL:
        return

    try:
        async with httpx.AsyncClient() as client:
            await client.post(
                OPENCLAW_WEBHOOK_URL,
                json={
                    "session_id": session_id,
                    "message": message,
                    "channel": channel,
                },
                timeout=10.0,
            )
    except Exception:
        pass  # Best-effort dispatch
