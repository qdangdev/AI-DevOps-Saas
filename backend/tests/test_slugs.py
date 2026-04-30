"""Slug-generator tests — pure functions, no I/O.

Slugs feed DNS, ALB target groups (≤32 chars), and ECR repo paths, so the
constraints are non-negotiable. These tests pin them.
"""
from __future__ import annotations

import re

from shared.core.slugs import is_valid_slug, make_slug


def test_basic_slug_shape():
    s = make_slug(owner_login="octocat", repo_name="hello-world")
    assert is_valid_slug(s)
    assert s.startswith("octocat-hello-world-")


def test_strips_garbage_chars():
    s = make_slug(owner_login="My/Org!", repo_name="App@v2")
    assert is_valid_slug(s)
    # Junk got mapped to hyphens / dropped.
    assert "@" not in s and "!" not in s and "/" not in s


def test_length_capped_at_32():
    long_repo = "a" * 200
    s = make_slug(owner_login="o", repo_name=long_repo)
    assert is_valid_slug(s)
    assert len(s) <= 32


def test_no_leading_or_trailing_hyphen():
    s = make_slug(owner_login="-edges-", repo_name="-also-")
    assert is_valid_slug(s)
    assert not s.startswith("-")
    assert not s.endswith("-")


def test_unique_per_call():
    a = make_slug(owner_login="o", repo_name="r")
    b = make_slug(owner_login="o", repo_name="r")
    assert a != b  # short_id randomness should split them


def test_validator_rejects_bad_input():
    assert not is_valid_slug("")
    assert not is_valid_slug("UPPER")
    assert not is_valid_slug("has space")
    assert not is_valid_slug("-leading")
    assert not is_valid_slug("trailing-")
    assert not is_valid_slug("a" * 33)
    assert is_valid_slug("ok-app-123")


def test_short_id_is_hex():
    s = make_slug(owner_login="o", repo_name="r")
    short = s.rsplit("-", 1)[-1]
    assert re.fullmatch(r"[0-9a-f]{6}", short)
