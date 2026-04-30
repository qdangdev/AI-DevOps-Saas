"""docker buildx — build the user's image and push to ECR.

Why ``buildx`` instead of plain ``docker build``:
  - First-class linux/amd64 cross-compile when the worker host happens to be arm64.
  - Inline cache (``--cache-to=type=inline``) baked into the image, so subsequent
    builds of the same repo are much faster without needing an external cache.
  - One command for build *and* push (``--push``) instead of build → tag → push.

We log build output line-by-line via the deployment events stream so the
frontend can tail it live.
"""
from __future__ import annotations

import subprocess
from collections.abc import Callable
from pathlib import Path

import structlog

log = structlog.get_logger(__name__)

LineCallback = Callable[[str], None]


class DockerBuildError(RuntimeError):
    pass


def docker_login(*, registry: str, username: str, password: str) -> None:
    """Run ``docker login`` against an ECR registry.

    The password is fed via stdin (--password-stdin) so it never lands in
    process listings. The ECR token expires every 12h; we always log in
    fresh per build.
    """
    log.info("docker.login", registry=registry)
    proc = subprocess.run(
        ["docker", "login", "--username", username, "--password-stdin", registry],
        input=password,
        text=True,
        capture_output=True,
        timeout=30,
    )
    if proc.returncode != 0:
        raise DockerBuildError(f"docker login failed: {proc.stderr.strip()}")


def build_and_push(
    *,
    context_dir: Path,
    dockerfile: Path,
    image_uri: str,
    platform: str = "linux/amd64",
    on_log_line: LineCallback | None = None,
    timeout_seconds: int = 1800,
) -> None:
    """Build with buildx and push in one step.

    ``image_uri`` is the *fully-qualified* tag (registry/repo:tag) — we hand
    that straight to ``docker buildx --tag`` so push targets it.
    """
    if not dockerfile.exists():
        raise DockerBuildError(f"dockerfile not found: {dockerfile}")
    if not context_dir.is_dir():
        raise DockerBuildError(f"build context not a directory: {context_dir}")

    cmd = [
        "docker", "buildx", "build",
        "--platform", platform,
        "--file", str(dockerfile),
        "--tag", image_uri,
        # Inline cache: writes cache manifest into the image so next build
        # of this repo can pull it for free.
        "--cache-to=type=inline",
        "--cache-from", f"type=registry,ref={image_uri}",
        "--push",
        # Don't dump the entire BuildKit log on success — only on error.
        "--progress=plain",
        str(context_dir),
    ]
    log.info("docker.build_start", image=image_uri, platform=platform, context=str(context_dir))

    # Stream stderr because buildx writes progress there. stdout gets the
    # build summary (image digest etc.) on success.
    with subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    ) as proc:
        assert proc.stdout is not None
        try:
            for line in proc.stdout:
                line = line.rstrip()
                if on_log_line is not None:
                    on_log_line(line)
            ret = proc.wait(timeout=timeout_seconds)
        except subprocess.TimeoutExpired:
            proc.kill()
            raise DockerBuildError(f"docker build timed out after {timeout_seconds}s") from None

    if ret != 0:
        raise DockerBuildError(f"docker build/push failed (exit {ret})")
    log.info("docker.build_done", image=image_uri)
