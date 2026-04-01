"""Tests for source taxonomy — classify_source_tier() edge cases."""

import pytest

from squid.dag.taxonomy import classify_source_tier, get_tier_label


def test_tier1_pubmed():
    assert classify_source_tier("https://pubmed.ncbi.nlm.nih.gov/12345678/")[0] == 1

def test_tier1_nature():
    assert classify_source_tier("https://www.nature.com/articles/s41586-023-06735-9")[0] == 1

def test_tier1_doi():
    assert classify_source_tier("https://doi.org/10.1038/s41586-023-06735-9")[0] == 1

def test_tier1_chembl():
    assert classify_source_tier("https://www.ebi.ac.uk/chembl/")[0] == 1

def test_tier2_arxiv():
    assert classify_source_tier("https://arxiv.org/abs/2301.00001")[0] == 2

def test_tier2_biorxiv():
    assert classify_source_tier("https://www.biorxiv.org/content/10.1101/2023.01.01")[0] == 2

def test_tier2_nih():
    assert classify_source_tier("https://www.nih.gov/some-page")[0] == 2

def test_tier2_edu():
    assert classify_source_tier("https://stanford.edu/research/paper")[0] == 2

def test_tier3_wikipedia():
    assert classify_source_tier("https://en.wikipedia.org/wiki/Aspirin")[0] == 3

def test_tier3_bbc():
    assert classify_source_tier("https://www.bbc.com/news/health-123456")[0] == 3

def test_tier4_unknown():
    assert classify_source_tier("https://some-random-blog.com/article")[0] == 4

def test_tier_labels():
    assert get_tier_label(1) == "Peer-reviewed / primary"
    assert get_tier_label(2) == "Preprint / institutional"
    assert get_tier_label(3) == "Secondary / aggregated"
    assert get_tier_label(4) == "Unclassified"
    assert get_tier_label(99) == "Unknown"
