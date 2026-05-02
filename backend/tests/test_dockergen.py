"""Dockerfile generator tests — pure functions, no I/O.

Each test verifies the rendered Dockerfile contains the *contract* signals
the build step needs: correct base image, EXPOSE port, and the start command
the analyzer detected. We don't pin exact bytes — that would brick the suite
on every cosmetic change to the templates.
"""
from __future__ import annotations

import pytest

from shared.analysis.schemas import (
    AnalysisResult,
    DeploymentStrategy,
    DeploymentTarget,
    Framework,
)
from shared.docker import UnsupportedFrameworkError, generate_dockerfile


def _make(
    *,
    framework: Framework,
    language: str = "python",
    runtime_version: str | None = None,
    package_manager: str | None = None,
    build_command: str | None = None,
    start_command: str | None = None,
    default_port: int | None = None,
    env_vars: list[str] | None = None,
    target: DeploymentTarget = DeploymentTarget.ECS_FARGATE,
    expose_port: int | None = None,
    needs_build: bool = True,
) -> AnalysisResult:
    return AnalysisResult(
        framework=framework,
        language=language,
        runtime_version=runtime_version,
        package_manager=package_manager,
        build_command=build_command,
        start_command=start_command,
        default_port=default_port,
        env_vars=env_vars or [],
        deployment_strategy=DeploymentStrategy(
            target=target,
            expose_port=expose_port,
            needs_build_step=needs_build,
        ),
        confidence=0.9,
    )


# ---------------------------------------------------------------------------
# Node family
# ---------------------------------------------------------------------------


def test_nextjs_uses_node_alpine_and_standalone_server():
    df = generate_dockerfile(_make(
        framework=Framework.NEXT_JS,
        language="typescript",
        runtime_version="20.11.0",
        package_manager="npm",
        default_port=3000,
    ))
    assert "FROM node:20-alpine" in df
    assert "EXPOSE 3000" in df
    # standalone output ships as server.js — that's what we exec.
    assert 'CMD ["node", "server.js"]' in df


def test_vite_uses_nginx_and_dist_artifact():
    df = generate_dockerfile(_make(
        framework=Framework.REACT_VITE,
        language="typescript",
        package_manager="pnpm",
        default_port=8080,
    ))
    assert "nginx:1.27-alpine" in df
    assert "/app/dist /usr/share/nginx/html" in df
    assert "EXPOSE 8080" in df


def test_cra_uses_build_artifact():
    df = generate_dockerfile(_make(
        framework=Framework.REACT_CRA,
        language="javascript",
        default_port=8080,
    ))
    assert "/app/build /usr/share/nginx/html" in df


def test_express_runs_user_start_command():
    df = generate_dockerfile(_make(
        framework=Framework.EXPRESS,
        language="javascript",
        runtime_version="18",
        start_command="node src/server.js",
        default_port=3000,
    ))
    assert "FROM node:18-alpine" in df
    assert '["node", "src/server.js"]' in df


def test_nestjs_builds_and_runs_dist():
    df = generate_dockerfile(_make(
        framework=Framework.NEST_JS,
        language="typescript",
        runtime_version="20",
        build_command="npm run build",
        default_port=3000,
    ))
    assert "RUN npm run build" in df
    assert '["node", "dist/main.js"]' in df


# ---------------------------------------------------------------------------
# Python family
# ---------------------------------------------------------------------------


def test_fastapi_uses_uvicorn_default_when_no_start_command():
    df = generate_dockerfile(_make(
        framework=Framework.FASTAPI,
        language="python",
        runtime_version="3.12",
        default_port=8000,
    ))
    assert "FROM python:3.12-slim" in df
    assert "uvicorn app.main:app" in df
    assert "EXPOSE 8000" in df


def test_flask_uses_gunicorn_default():
    df = generate_dockerfile(_make(
        framework=Framework.FLASK,
        language="python",
        default_port=8000,
    ))
    assert "gunicorn" in df


def test_django_runs_migrate_before_serve():
    df = generate_dockerfile(_make(
        framework=Framework.DJANGO,
        language="python",
        default_port=8000,
    ))
    # Migrate-then-serve has shell metacharacters → renders as sh -c.
    assert "manage.py migrate" in df
    assert "gunicorn" in df
    assert '"sh", "-c"' in df


def test_python_poetry_install_block():
    df = generate_dockerfile(_make(
        framework=Framework.FASTAPI,
        package_manager="poetry",
    ))
    assert "pip install --no-cache-dir poetry" in df
    assert "COPY pyproject.toml" in df


# ---------------------------------------------------------------------------
# Compiled
# ---------------------------------------------------------------------------


def test_go_builds_static_and_uses_alpine_runtime():
    df = generate_dockerfile(_make(
        framework=Framework.GO_NET_HTTP,
        language="go",
        runtime_version="1.22",
        default_port=8080,
        needs_build=True,
    ))
    assert "FROM golang:1.22-alpine" in df
    assert "FROM alpine:3.20" in df
    assert "CGO_ENABLED=0" in df
    assert "EXPOSE 8080" in df


def test_rust_uses_debian_slim_runtime():
    df = generate_dockerfile(_make(
        framework=Framework.RUST_AXUM,
        language="rust",
        runtime_version="1.78",
        default_port=8080,
    ))
    assert "FROM rust:1.78-slim" in df
    assert "FROM debian:bookworm-slim" in df


# ---------------------------------------------------------------------------
# Static
# ---------------------------------------------------------------------------


def test_static_html_serves_with_nginx():
    df = generate_dockerfile(_make(
        framework=Framework.STATIC_HTML,
        language="html",
        target=DeploymentTarget.STATIC_S3_CLOUDFRONT,
        default_port=8080,
        needs_build=False,
    ))
    assert "nginx:1.27-alpine" in df
    assert "/usr/share/nginx/html" in df


# ---------------------------------------------------------------------------
# Defaults / errors
# ---------------------------------------------------------------------------


def test_node_runtime_falls_back_to_default_when_unset():
    df = generate_dockerfile(_make(framework=Framework.NEXT_JS))
    # 20 is our pinned default for node.
    assert "FROM node:20-alpine" in df


def test_python_runtime_strips_patch_version():
    df = generate_dockerfile(_make(
        framework=Framework.FASTAPI,
        runtime_version="3.12.4",
    ))
    assert "FROM python:3.12-slim" in df


def test_unknown_framework_raises():
    with pytest.raises(UnsupportedFrameworkError):
        generate_dockerfile(_make(framework=Framework.UNKNOWN, language="unknown"))


def test_rails_raises_unsupported_for_now():
    with pytest.raises(UnsupportedFrameworkError):
        generate_dockerfile(_make(framework=Framework.RAILS, language="ruby"))


def test_env_vars_emitted_as_empty_envs():
    df = generate_dockerfile(_make(
        framework=Framework.FASTAPI,
        env_vars=["DATABASE_URL", "REDIS_URL"],
        default_port=8000,
    ))
    assert "ENV DATABASE_URL=" in df
    assert "ENV REDIS_URL=" in df


def test_accepts_dict_input():
    """The Deployment row stores analysis as JSONB → dict; generator must accept that."""
    analysis = _make(framework=Framework.FASTAPI, default_port=8000).model_dump(mode="json")
    df = generate_dockerfile(analysis)
    assert "uvicorn app.main:app" in df
