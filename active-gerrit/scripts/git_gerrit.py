#!/usr/bin/env python3
"""Gerrit-specific Git ref helpers for active-gerrit local Git commands."""

from __future__ import annotations

from typing import Mapping, Optional
from urllib.parse import quote

REVIEW_REF_PREFIX = "refs/for"
CHANGE_REF_PREFIX = "refs/changes"


def build_change_ref(change_number: int, patch_set: int) -> str:
    if change_number <= 0:
        raise ValueError("change_number must be greater than zero.")
    if patch_set <= 0:
        raise ValueError("patch_set must be greater than zero.")
    return f"{CHANGE_REF_PREFIX}/{change_number % 100:02d}/{change_number}/{patch_set}"


def build_review_ref(branch: str, options: Optional[Mapping[str, str]] = None) -> str:
    normalized_branch = branch.strip().lstrip("/")
    if not normalized_branch:
        raise ValueError("branch is required to build a Gerrit review ref.")

    ref = f"{REVIEW_REF_PREFIX}/{normalized_branch}"
    if not options:
        return ref

    encoded_options = []
    for key, value in sorted(options.items()):
        clean_key = key.strip()
        clean_value = str(value).strip()
        if not clean_key or not clean_value:
            continue
        encoded_options.append(f"{quote(clean_key, safe='')}={quote(clean_value, safe='@._+-')}")
    if not encoded_options:
        return ref
    return f"{ref}%{','.join(encoded_options)}"
