"""Pydantic models for analysis input/output.

`AnalysisResult` doubles as the LLM tool schema — Pydantic emits a JSON Schema
that Anthropic accepts directly via `Model.model_json_schema()`. That means
the schema lives in exactly one place and the LLM physically cannot return
a shape we can't parse.
"""
from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class Framework(str, Enum):
    """Supported deployment targets.

    Kept deliberately small — each value implies a known Dockerfile shape and
    ECS task definition template downstream. `unknown` is the explicit
    fallback when neither the heuristic nor the LLM is confident.
    """

    NEXT_JS = "next.js"
    NUXT = "nuxt"
    REACT_VITE = "react-vite"
    REACT_CRA = "react-cra"
    EXPRESS = "express"
    NEST_JS = "nest.js"
    FASTAPI = "fastapi"
    FLASK = "flask"
    DJANGO = "django"
    RAILS = "rails"
    SPRING_BOOT = "spring-boot"
    GO_NET_HTTP = "go-net-http"
    RUST_AXUM = "rust-axum"
    STATIC_HTML = "static-html"
    UNKNOWN = "unknown"


class DeploymentTarget(str, Enum):
    """Where the app should run."""

    ECS_FARGATE = "ecs-fargate"
    ECS_FARGATE_NLB = "ecs-fargate-nlb"  # for non-HTTP / sticky / websockets
    STATIC_S3_CLOUDFRONT = "static-s3-cloudfront"
    UNKNOWN = "unknown"


class DeploymentStrategy(BaseModel):
    """How to actually ship the app."""

    target: DeploymentTarget = Field(
        description="Which AWS deployment shape this app fits."
    )
    dockerfile_base_image: str | None = Field(
        default=None,
        description="Base image we'll use in the generated Dockerfile, e.g. 'node:20-slim'. Null for static-only apps.",
    )
    expose_port: int | None = Field(
        default=None,
        description="Port the container will listen on. Null for static sites.",
    )
    health_check_path: str = Field(
        default="/",
        description="HTTP path the load balancer will probe.",
    )
    needs_build_step: bool = Field(
        description="True if the framework needs a `build` step (Next.js, Vite, etc.) before serve.",
    )
    build_artifact_dir: str | None = Field(
        default=None,
        description="For static targets only: which directory holds the built assets (e.g. 'dist', 'build', '.next/static').",
    )


class AnalysisResult(BaseModel):
    """Final analyzer output — the LLM tool schema.

    Conservative-by-default: any field the model can't infer must be returned
    as null rather than guessed. Downstream Dockerfile generation will treat
    nulls as "use the framework's documented default", not "the user said so".
    """

    framework: Framework = Field(description="Detected web framework.")
    language: str = Field(
        description="Primary language: 'python', 'javascript', 'typescript', 'go', 'rust', 'ruby', 'java'.",
    )
    runtime_version: str | None = Field(
        default=None,
        description="Pinned runtime version if declared (e.g. '20.11.0', '3.12'). Null if not specified.",
    )
    package_manager: str | None = Field(
        default=None,
        description="'npm' | 'yarn' | 'pnpm' | 'pip' | 'poetry' | 'uv' | 'bundler' | 'go-modules' | 'cargo'.",
    )
    build_command: str | None = Field(
        default=None,
        description="Shell command that builds the app, e.g. 'npm run build'. Null if no build step.",
    )
    start_command: str | None = Field(
        default=None,
        description="Shell command that starts the server in production, e.g. 'npm start' or 'uvicorn app.main:app --host 0.0.0.0'.",
    )
    default_port: int | None = Field(
        default=None,
        description="Port the app listens on by default.",
    )
    env_vars: list[str] = Field(
        default_factory=list,
        description="Names of environment variables the app appears to require (no values).",
    )
    needs_postgres: bool = Field(
        default=False,
        description="True if the app imports a Postgres client / has DATABASE_URL with postgres scheme.",
    )
    needs_redis: bool = Field(
        default=False,
        description="True if the app imports a Redis client / has REDIS_URL.",
    )
    deployment_strategy: DeploymentStrategy
    confidence: float = Field(
        ge=0.0,
        le=1.0,
        description="0.0–1.0 — how confident the analyzer is overall. <0.5 means the user should review before deploy.",
    )
    notes: str = Field(
        default="",
        description="One- or two-sentence human-readable summary of what was detected and why.",
    )


class DetectionCandidate(BaseModel):
    """One framework guess from the deterministic detector."""

    framework: Framework
    confidence: float = Field(ge=0.0, le=1.0)
    evidence: list[str] = Field(
        default_factory=list,
        description="File paths that matched, e.g. ['next.config.js', 'pages/index.tsx'].",
    )


def tool_schema_for_anthropic() -> dict[str, Any]:
    """Return AnalysisResult's JSON schema, flattened so Anthropic accepts it.

    Pydantic v2 emits `$defs` for nested models; Anthropic accepts that, but
    inlining keeps the schema portable to other tool-use backends and avoids
    surprises if a provider only supports JSON Schema draft 7.
    """
    return AnalysisResult.model_json_schema(mode="serialization")
