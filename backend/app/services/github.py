"""GitHub OAuth + REST API client.

Handles:
  - Generating the authorize URL (with CSRF state token)
  - Exchanging the OAuth code for an access token
  - Fetching the authenticated user
  - Listing repos the user has access to

This module never touches the DB. It returns plain dicts/dataclasses; persistence is the
caller's job. That keeps it easy to test and easy to swap (e.g., for GitHub App auth later).
"""
from __future__ import annotations

import secrets
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlencode

import httpx
import structlog

from app.core.config import get_settings

log = structlog.get_logger(__name__)
settings = get_settings()

GITHUB_AUTHORIZE_URL = "https://github.com/login/oauth/authorize"
GITHUB_TOKEN_URL = "https://github.com/login/oauth/access_token"
GITHUB_API_BASE = "https://api.github.com"

# Module-level client — connection pooling, reused across requests.
# Closed via lifespan in app/main.py.
_client: httpx.AsyncClient | None = None


def get_client() -> httpx.AsyncClient:
    global _client
    if _client is None:
        _client = httpx.AsyncClient(
            timeout=httpx.Timeout(10.0, connect=5.0),
            headers={"Accept": "application/vnd.github+json", "User-Agent": "ai-devops-saas"},
        )
    return _client


async def close_client() -> None:
    global _client
    if _client is not None:
        await _client.aclose()
        _client = None


@dataclass(frozen=True)
class GitHubUser:
    id: int
    login: str
    email: str | None
    avatar_url: str | None


class GitHubError(Exception):
    """Raised for any GitHub API failure."""


def build_authorize_url(state: str | None = None) -> tuple[str, str]:
    """Return (authorize_url, state). Caller stores state and validates on callback."""
    state = state or secrets.token_urlsafe(32)
    params = {
        "client_id": settings.github_client_id,
        "redirect_uri": settings.github_redirect_uri,
        "scope": settings.github_oauth_scopes,
        "state": state,
        "allow_signup": "true",
    }
    return f"{GITHUB_AUTHORIZE_URL}?{urlencode(params)}", state


async def exchange_code_for_token(code: str) -> str:
    """Trade an OAuth code for a user access token."""
    client = get_client()
    resp = await client.post(
        GITHUB_TOKEN_URL,
        data={
            "client_id": settings.github_client_id,
            "client_secret": settings.github_client_secret,
            "code": code,
            "redirect_uri": settings.github_redirect_uri,
        },
        headers={"Accept": "application/json"},
    )
    if resp.status_code != 200:
        log.error("github.token_exchange_failed", status=resp.status_code, body=resp.text)
        raise GitHubError(f"token exchange failed: {resp.status_code}")

    payload = resp.json()
    if "access_token" not in payload:
        log.error("github.token_exchange_missing_token", payload=payload)
        raise GitHubError(payload.get("error_description") or "no access_token in response")
    return payload["access_token"]


async def fetch_user(access_token: str) -> GitHubUser:
    """Fetch the authenticated user. Tries /user/emails to surface a primary email if /user hides it."""
    client = get_client()
    headers = {"Authorization": f"Bearer {access_token}"}

    user_resp = await client.get(f"{GITHUB_API_BASE}/user", headers=headers)
    if user_resp.status_code != 200:
        log.error("github.fetch_user_failed", status=user_resp.status_code, body=user_resp.text)
        raise GitHubError(f"fetch user failed: {user_resp.status_code}")
    u = user_resp.json()

    email = u.get("email")
    if not email:
        emails_resp = await client.get(f"{GITHUB_API_BASE}/user/emails", headers=headers)
        if emails_resp.status_code == 200:
            primaries = [e for e in emails_resp.json() if e.get("primary") and e.get("verified")]
            if primaries:
                email = primaries[0]["email"]

    return GitHubUser(
        id=u["id"],
        login=u["login"],
        email=email,
        avatar_url=u.get("avatar_url"),
    )


async def list_repos(
    access_token: str,
    *,
    visibility: str = "all",
    affiliation: str = "owner,collaborator,organization_member",
    per_page: int = 50,
    max_pages: int = 4,
) -> list[dict[str, Any]]:
    """List repos the user has access to. Paginates up to max_pages."""
    client = get_client()
    headers = {"Authorization": f"Bearer {access_token}"}
    repos: list[dict[str, Any]] = []

    for page in range(1, max_pages + 1):
        resp = await client.get(
            f"{GITHUB_API_BASE}/user/repos",
            headers=headers,
            params={
                "visibility": visibility,
                "affiliation": affiliation,
                "per_page": per_page,
                "page": page,
                "sort": "pushed",
                "direction": "desc",
            },
        )
        if resp.status_code != 200:
            log.error("github.list_repos_failed", status=resp.status_code, body=resp.text)
            raise GitHubError(f"list repos failed: {resp.status_code}")
        batch = resp.json()
        repos.extend(batch)
        if len(batch) < per_page:
            break
    return repos


async def get_repo(access_token: str, github_repo_id: int) -> dict[str, Any]:
    """Fetch a single repo by its numeric id. Used when connecting a repo."""
    client = get_client()
    resp = await client.get(
        f"{GITHUB_API_BASE}/repositories/{github_repo_id}",
        headers={"Authorization": f"Bearer {access_token}"},
    )
    if resp.status_code == 404:
        raise GitHubError("repo not found or token lacks access")
    if resp.status_code != 200:
        log.error("github.get_repo_failed", status=resp.status_code, body=resp.text)
        raise GitHubError(f"get repo failed: {resp.status_code}")
    return resp.json()
