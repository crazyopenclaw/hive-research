"""Patch validator — validates patch only touches allowed_files."""

import re
from typing import List


def validate_patch_scope(patch: str, allowed_files: List[str]) -> bool:
    """
    Validate that a unified diff only touches files in allowed_files.

    Returns True if valid, raises ValueError if patch touches disallowed files.
    """
    # Extract files touched by the patch
    touched_files = set(re.findall(r'^\+\+\+ b/(.+)$', patch, re.MULTILINE))

    disallowed = touched_files - set(allowed_files)
    if disallowed:
        raise ValueError(
            f"Patch touches files outside allowed_files: {disallowed}. "
            f"Allowed: {allowed_files}"
        )

    return True
