"""Deployment slug generation — shared between API (creator) and worker (consumer).

A slug is the per-deployment name that appears everywhere AWS needs a stable
identifier (and as a subdomain): ECR repo, ECS service, target group, listener
rule, and Route53 record.

Constraints:
  - DNS subdomain: ≤63 chars, [a-z0-9-], no leading/trailing hyphen.
  - ALB target group name: ≤32 chars, [a-zA-Z0-9-].
  - ECS service name: ≤255 chars but we keep ≤32 for symmetry with the TG.

So we cap at 32 effective chars and use only [a-z0-9-]. The format is:

    <repo-stem>-<short-id>

where short-id is a 6 hex chars to keep slugs unique even across re-deploys
of the same repo.
"""
from __future__ import annotations

import re
import secrets


_SLUG_MAX_LEN = 32
_REPO_STEM_MAX = _SLUG_MAX_LEN - 1 - 6  # leave room for "-<6-char>"


def _short_id() -> str:
    """6 hex chars; ~24 bits of randomness — plenty for our scale."""
    return secrets.token_hex(3)


def _slugify(s: str) -> str:
    """Lowercase, [a-z0-9-] only, collapse runs, strip edges."""
    s = s.lower()
    s = re.sub(r"[^a-z0-9]+", "-", s)
    s = re.sub(r"-+", "-", s)
    return s.strip("-")


def make_slug(*, owner_login: str, repo_name: str) -> str:
    """Build a deployment slug. Caller stores the result on the row.

    Inputs are sanitized: e.g. ``("Octocat", "Hello-World!!")`` →
    ``"octocat-hello-world-a1b2c3"``.
    """
    stem = _slugify(f"{owner_login}-{repo_name}")
    if not stem:
        stem = "app"
    if len(stem) > _REPO_STEM_MAX:
        stem = stem[:_REPO_STEM_MAX].rstrip("-")
    return f"{stem}-{_short_id()}"


def is_valid_slug(slug: str) -> bool:
    """Validate the rules above. Used in API serializers + tests."""
    if not (1 <= len(slug) <= _SLUG_MAX_LEN):
        return False
    if not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", slug):
        return False
    return True
