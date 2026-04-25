"""Orchestrator: GitHub fetch → deterministic detect → LLM confirm → AnalysisResult.

Public function:

    await analyze_repo(access_token, owner, repo, branch="main")

Failure mode:

    The LLM call is the only step that can realistically blow up at runtime
    (network, rate limit, model unavailable). If it fails we fall back to a
    `_result_from_heuristic_only(...)` build — never let the caller crash on
    an LLM hiccup. The caller can detect the fallback by `confidence <= 0.5`.
"""
from __future__ import annotations

import asyncio

import structlog

from shared.analysis.detector import detect
from shared.analysis.manifests import MANIFEST_FILES, all_paths
from shared.analysis.prompts import (
    SYSTEM_PROMPT,
    TOOL_DESCRIPTION,
    TOOL_NAME,
    build_user_prompt,
)
from shared.analysis.schemas import (
    AnalysisResult,
    DeploymentStrategy,
    DeploymentTarget,
    DetectionCandidate,
    Framework,
    tool_schema_for_anthropic,
)
from shared.services import github as gh
from shared.services.anthropic import LLMError, complete_with_tool

log = structlog.get_logger(__name__)


async def analyze_repo(
    access_token: str,
    owner: str,
    repo: str,
    branch: str = "main",
) -> AnalysisResult:
    """End-to-end analysis. Always returns a result — never raises on LLM failure."""
    # 1. Fetch file tree + manifests in parallel. Tree is one call;
    #    manifests are N calls but most return 404 (missing files), which is
    #    fine and cheap.
    tree_task = gh.get_tree(access_token, owner, repo, branch=branch)
    manifest_tasks = [
        gh.get_file(access_token, owner, repo, m.path, branch=branch, max_bytes=m.max_bytes)
        for m in MANIFEST_FILES
    ]
    tree_entries, *manifest_contents = await asyncio.gather(tree_task, *manifest_tasks)

    paths = [e["path"] for e in tree_entries if e.get("type") == "blob"]
    manifests = {
        m.path: c for m, c in zip(MANIFEST_FILES, manifest_contents, strict=True) if c is not None
    }

    log.info(
        "analyze.fetched",
        owner=owner,
        repo=repo,
        branch=branch,
        tree_size=len(paths),
        manifests_found=list(manifests.keys()),
    )

    # 2. Deterministic detector. Always runs, always fast.
    candidates = detect(paths, manifests)
    top = candidates[0]
    log.info(
        "analyze.heuristic",
        framework=top.framework.value,
        confidence=top.confidence,
        evidence=top.evidence,
    )

    # 3. LLM analysis. If it fails, fall back to heuristic-only.
    try:
        return await _analyze_with_llm(
            owner=owner, repo=repo, branch=branch,
            file_tree=paths, manifests=manifests, top_candidate=top,
        )
    except (LLMError, Exception) as e:  # noqa: BLE001 — intentional broad catch around external call
        log.warning("analyze.llm_failed_falling_back_to_heuristic", error=str(e))
        return _result_from_heuristic_only(top, manifests)


async def _analyze_with_llm(
    *,
    owner: str,
    repo: str,
    branch: str,
    file_tree: list[str],
    manifests: dict[str, str],
    top_candidate: DetectionCandidate,
) -> AnalysisResult:
    user_prompt = build_user_prompt(
        owner=owner, repo=repo, branch=branch,
        file_tree=file_tree, manifests=manifests, top_candidate=top_candidate,
    )
    raw = await complete_with_tool(
        system=SYSTEM_PROMPT,
        user=user_prompt,
        tool_name=TOOL_NAME,
        tool_description=TOOL_DESCRIPTION,
        tool_schema=tool_schema_for_anthropic(),
    )
    # Pydantic re-validates — protects against schema drift if a model version
    # ever returns extra/missing fields despite the tool definition.
    return AnalysisResult.model_validate(raw)


def _result_from_heuristic_only(
    top: DetectionCandidate,
    manifests: dict[str, str],
) -> AnalysisResult:
    """Last-resort result when the LLM is unavailable.

    We don't try to invent versions or commands here — return nulls and let
    the user see the low confidence. The Dockerfile generator downstream
    should refuse to run on `confidence < 0.6` and surface the issue to the
    user instead of producing a guess that won't boot.
    """
    fw = top.framework
    is_static = fw == Framework.STATIC_HTML
    return AnalysisResult(
        framework=fw,
        language=_default_language_for(fw),
        runtime_version=None,
        package_manager=None,
        build_command=None,
        start_command=None,
        default_port=None,
        env_vars=[],
        needs_postgres=False,
        needs_redis=False,
        deployment_strategy=DeploymentStrategy(
            target=DeploymentTarget.STATIC_S3_CLOUDFRONT if is_static else DeploymentTarget.ECS_FARGATE,
            dockerfile_base_image=None,
            expose_port=None,
            health_check_path="/",
            needs_build_step=False,
            build_artifact_dir=None,
        ),
        confidence=min(top.confidence, 0.5),  # cap — we didn't get LLM confirmation
        notes=(
            f"Heuristic-only analysis (LLM unavailable). Detected {fw.value} from "
            f"{', '.join(top.evidence) or 'no evidence'}. Manual review recommended."
        ),
    )


def _default_language_for(fw: Framework) -> str:
    return {
        Framework.NEXT_JS: "typescript",
        Framework.NUXT: "typescript",
        Framework.REACT_VITE: "typescript",
        Framework.REACT_CRA: "javascript",
        Framework.EXPRESS: "javascript",
        Framework.NEST_JS: "typescript",
        Framework.FASTAPI: "python",
        Framework.FLASK: "python",
        Framework.DJANGO: "python",
        Framework.RAILS: "ruby",
        Framework.SPRING_BOOT: "java",
        Framework.GO_NET_HTTP: "go",
        Framework.RUST_AXUM: "rust",
        Framework.STATIC_HTML: "html",
        Framework.UNKNOWN: "unknown",
    }[fw]


# Re-export for convenience: callers tend to want all_paths() too when
# constructing test fixtures.
__all__ = ["analyze_repo", "all_paths"]
