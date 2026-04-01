"""classify_source_tier() — rule-based, no LLM."""

import re
from typing import Tuple


# Tier 1: Peer-reviewed / primary
TIER1_PATTERNS = [
    r"pubmed\.ncbi\.nlm\.nih\.gov",
    r"nature\.com",
    r"cochranelibrary\.com",
    r"doi\.org",
    r"sciencedirect\.com",
    r"springer\.com",
    r"wiley\.com/doi",
    r"cell\.com",
    r"thelancet\.com",
    r"nejm\.org",
    r"bmj\.com",
    r"jamanetwork\.com",
    r"plos\.org",
    r"pnas\.org",
    r"science\.org",
    r"chembl\.ebi\.ac\.uk",
]

# Tier 2: Preprint / institutional
TIER2_PATTERNS = [
    r"arxiv\.org",
    r"biorxiv\.org",
    r"medrxiv\.org",
    r"ssrn\.com",
    r"nih\.gov",
    r"\.gov/",
    r"\.edu/",
    r"\.ac\.(uk|jp|de|fr|au)/",
    r"who\.int",
    r"fda\.gov",
    r"ema\.europa\.eu",
    r"cdc\.gov",
    r"nasa\.gov",
    r"nist\.gov",
]

# Tier 3: Secondary / aggregated
TIER3_PATTERNS = [
    r"wikipedia\.org",
    r"webmd\.com",
    r"mayoclinic\.org",
    r"healthline\.com",
    r"medlineplus\.gov",
    r"bbc\.com",
    r"reuters\.com",
    r"apnews\.com",
    r"nytimes\.com",
    r"theguardian\.com",
    r"medium\.com",
    r"stackoverflow\.com",
]

TIER_LABELS = {
    1: "Peer-reviewed / primary",
    2: "Preprint / institutional",
    3: "Secondary / aggregated",
    4: "Unclassified",
}


def classify_source_tier(url: str) -> Tuple[int, str]:
    """
    Classify a URL into a source tier. Rule-based — no LLM.

    Returns:
        (tier_number, tier_basis)
    """
    url_lower = url.lower()

    for pattern in TIER1_PATTERNS:
        if re.search(pattern, url_lower):
            return (1, f"matched_tier1:{pattern}")

    for pattern in TIER2_PATTERNS:
        if re.search(pattern, url_lower):
            return (2, f"matched_tier2:{pattern}")

    for pattern in TIER3_PATTERNS:
        if re.search(pattern, url_lower):
            return (3, f"matched_tier3:{pattern}")

    return (4, "no_pattern_match")


def get_tier_label(tier: int) -> str:
    return TIER_LABELS.get(tier, "Unknown")
