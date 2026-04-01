"""web_search — Brave API wrapper for Tier-1 agents."""

import os
from typing import Dict, List, Optional

import httpx


BRAVE_API_KEY = os.getenv("BRAVE_API_KEY", "")
BRAVE_SEARCH_URL = "https://api.search.brave.com/res/v1/web/search"

_search_count = 0
MAX_SEARCHES_PER_CYCLE = 30


def reset_search_counter():
    global _search_count
    _search_count = 0


async def web_search(
    query: str,
    count: int = 5,
    freshness: Optional[str] = None,
) -> List[Dict]:
    """
    Search the web using Brave Search API.

    Returns: List of {title, url, snippet}
    Raises: RuntimeError if rate limit exceeded
    """
    global _search_count

    if _search_count >= MAX_SEARCHES_PER_CYCLE:
        raise RuntimeError(f"Search rate limit reached ({MAX_SEARCHES_PER_CYCLE}/cycle)")

    if not BRAVE_API_KEY:
        raise RuntimeError("BRAVE_API_KEY not set")

    params = {"q": query, "count": min(count, 10)}
    if freshness:
        params["freshness"] = freshness

    headers = {
        "Accept": "application/json",
        "X-Subscription-Token": BRAVE_API_KEY,
    }

    async with httpx.AsyncClient() as client:
        response = await client.get(BRAVE_SEARCH_URL, params=params, headers=headers, timeout=10.0)
        response.raise_for_status()
        data = response.json()

    _search_count += 1

    return [
        {"title": item.get("title", ""), "url": item.get("url", ""), "snippet": item.get("description", "")}
        for item in data.get("web", {}).get("results", [])
    ]
