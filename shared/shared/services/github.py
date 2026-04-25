"""GitHub OAuth + REST API client.

Used by both backend (auth, list repos) and worker (clone, fetch metadata at deploy time).
This module never touches the DB; persistence is the caller's job.
"""
from __future__ import annotations

import secrets
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlencode

import httpx
import structlog

from shared.core.config import get_settings

log = structlog.get_logger(__name__)
settings = get_settings()

GITHUB_AUTHORIZE_URL = "https://github.com/login/oauth/authorize"
GITHUB_TOKEN_URL = "https://github.com/login/oauth/access_token"
GITHUB_API_BASE = "https://api.github.com"

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


# --- repo introspection (used by the analyzer, no clone required) ----------


async def get_tree(
    access_token: str,
    owner: str,
    repo: str,
    *,
    branch: str = "main",
    max_entries: int = 1000,
) -> list[dict[str, Any]]:
    """Recursive file tree for a repo (paths + types only, no contents).

    The git/trees endpoint returns up to ~100k entries in one shot; we cap
    here so a monorepo with 50k files doesn't blow up the analyzer's prompt.
    """
    client = get_client()
    resp = await client.get(
        f"{GITHUB_API_BASE}/repos/{owner}/{repo}/git/trees/{branch}",
        headers={"Authorization": f"Bearer {access_token}"},
        params={"recursive": "1"},
    )
    if resp.status_code == 404:
        raise GitHubError(f"tree not found: {owner}/{repo}@{branch}")
    if resp.status_code != 200:
        log.error("github.get_tree_failed", status=resp.status_code, body=resp.text)
        raise GitHubError(f"get tree failed: {resp.status_code}")

    payload = resp.json()
    entries = payload.get("tree", [])
    if payload.get("truncated"):
        log.warning("github.tree_truncated", owner=owner, repo=repo, branch=branch)
    return entries[:max_entries]


async def get_file(
    access_token: str,
    owner: str,
    repo: str,
    path: str,
    *,
    branch: str = "main",
    max_bytes: int = 64_000,
) -> str | None:
    """Fetch a file's text contents, or None if not found / not text / too large.

    Uses the raw media type to skip the base64 dance. We never raise on 404 —
    "manifest doesn't exist" is a normal signal for the analyzer (e.g. a
    Python repo legitimately has no package.json).
    """
    client = get_client()
    resp = await client.get(
        f"{GITHUB_API_BASE}/repos/{owner}/{repo}/contents/{path}",
        headers={
            "Authorization": f"Bearer {access_token}",
            "Accept": "application/vnd.github.raw",
        },
        params={"ref": branch},
    )
    if resp.status_code == 404:
        return None
    if resp.status_code != 200:
        log.warning("github.get_file_failed", path=path, status=resp.status_code)
        return None

    body = resp.content[:max_bytes]
    try:
        return body.decode("utf-8")
    except UnicodeDecodeError:
        return None
