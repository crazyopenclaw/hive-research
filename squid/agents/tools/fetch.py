"""fetch_url — httpx wrapper with source taxonomy integration."""

import re
from typing import Dict

import httpx

from squid.dag.taxonomy import classify_source_tier, get_tier_label

MAX_CONTENT_LENGTH = 50000


async def fetch_url(url: str, max_chars: int = MAX_CONTENT_LENGTH) -> Dict:
    """
    Fetch a URL. Content wrapped in [FETCHED CONTENT] markers (untrusted).
    Returns: {url, title, content, tier, tier_label, tier_warning}
    """
    tier, tier_basis = classify_source_tier(url)

    try:
        async with httpx.AsyncClient(follow_redirects=True) as client:
            response = await client.get(url, timeout=15.0)
            response.raise_for_status()

            content = response.text
            content = re.sub(r'<script[^>]*>.*?</script>', '', content, flags=re.DOTALL)
            content = re.sub(r'<style[^>]*>.*?</style>', '', content, flags=re.DOTALL)
            content = re.sub(r'<[^>]+>', ' ', content)
            content = re.sub(r'\s+', ' ', content).strip()

            if len(content) > max_chars:
                content = content[:max_chars] + "... [TRUNCATED]"

            title_match = re.search(r'<title[^>]*>(.*?)</title>', response.text, re.IGNORECASE | re.DOTALL)
            title = title_match.group(1).strip() if title_match else ""

            tier_warning = ""
            if tier >= 3:
                tier_warning = f"WARNING: Tier {tier} source ({get_tier_label(tier)}) — low reliability"

            return {
                "url": url,
                "title": title,
                "content": f"[FETCHED CONTENT]\n{content}\n[/FETCHED CONTENT]",
                "tier": tier,
                "tier_label": get_tier_label(tier),
                "tier_basis": tier_basis,
                "tier_warning": tier_warning,
            }

    except httpx.HTTPError as e:
        return {
            "url": url,
            "title": "",
            "content": f"[FETCHED CONTENT]\nError: {str(e)}\n[/FETCHED CONTENT]",
            "tier": tier,
            "tier_label": get_tier_label(tier),
            "tier_warning": "Fetch failed",
        }
