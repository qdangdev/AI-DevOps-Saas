"""Shallow git clone with token authentication.

We clone with --depth=1 because:
  - Builds only need the tree at HEAD.
  - It's drastically smaller for big monorepos (think node_modules history).
  - History is preserved on GitHub anyway; we never push back from the worker.

Authentication: GitHub accepts the token as the username over HTTPS, so the
URL is rewritten to embed it. We never log the rewritten URL.

The clone happens inside a tempdir owned by the calling task. The caller is
responsible for cleanup (use the context manager below).
"""
from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator
from urllib.parse import urlparse, urlunparse

import structlog

log = structlog.get_logger(__name__)


class GitError(RuntimeError):
    pass


def _build_authenticated_url(clone_url: str, token: str) -> str:
    """Embed an OAuth token into an https clone URL.

    Format: https://x-access-token:<token>@github.com/owner/repo.git
    The ``x-access-token`` username is the GitHub-recommended literal for
    OAuth/PAT authentication.
    """
    parsed = urlparse(clone_url)
    if parsed.scheme != "https":
        raise GitError(f"only https clone URLs are supported, got {parsed.scheme}")
    netloc = f"x-access-token:{token}@{parsed.hostname}"
    if parsed.port:
        netloc += f":{parsed.port}"
    return urlunparse(parsed._replace(netloc=netloc))


def _redact(url: str) -> str:
    """Strip credentials before logging."""
    parsed = urlparse(url)
    if parsed.username or parsed.password:
        host = parsed.hostname or ""
        if parsed.port:
            host += f":{parsed.port}"
        return urlunparse(parsed._replace(netloc=host))
    return url


@contextmanager
def shallow_clone(
    *,
    clone_url: str,
    token: str,
    branch: str = "main",
    depth: int = 1,
) -> Iterator[Path]:
    """Clone the repo into a tempdir and yield the path. Always cleaned up.

    Usage:
        with shallow_clone(clone_url=url, token=t, branch="main") as repo_dir:
            ... # build inside repo_dir
    """
    workdir = Path(tempfile.mkdtemp(prefix="ai-devops-clone-"))
    auth_url = _build_authenticated_url(clone_url, token)
    log.info("git.clone_start", url=_redact(clone_url), branch=branch, depth=depth, dest=str(workdir))
    try:
        # We pass auth_url via argv (not via env) and disable askpass to fail
        # fast if the token is rejected. Output goes to PIPE so the token
        # doesn't end up in worker stdout if git ever echoed it.
        env = {**os.environ, "GIT_TERMINAL_PROMPT": "0"}
        result = subprocess.run(
            [
                "git", "clone",
                "--depth", str(depth),
                "--branch", branch,
                "--single-branch",
                auth_url,
                str(workdir),
            ],
            check=False,
            capture_output=True,
            text=True,
            env=env,
            timeout=180,
        )
        if result.returncode != 0:
            # Redact the token from any error trace before raising/logging.
            stderr = result.stderr.replace(token, "***")
            raise GitError(f"git clone failed (exit {result.returncode}): {stderr.strip()}")

        # Capture HEAD SHA so we can record it on the deployment row.
        sha_result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=workdir,
            check=False,
            capture_output=True,
            text=True,
        )
        head_sha = sha_result.stdout.strip() if sha_result.returncode == 0 else ""
        log.info("git.clone_done", branch=branch, head=head_sha)

        yield workdir
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


def head_sha(repo_dir: Path) -> str:
    """Return the commit SHA at HEAD inside an already-cloned repo. Empty on failure."""
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=repo_dir,
        check=False,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip() if result.returncode == 0 else ""
