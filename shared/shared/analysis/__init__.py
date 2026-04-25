"""Repository analysis — framework detection + deployment recommendation.

Public surface:

    from shared.analysis import analyze_repo, AnalysisResult

    result = await analyze_repo(
        access_token=token,
        owner="qdangdev",
        repo="my-app",
        branch="main",
    )

Two stages under the hood:

    1. shared.analysis.detector — fast, deterministic, file-tree-based.
       No LLM, no I/O beyond what the orchestrator hands it.
    2. shared.analysis.analyzer — orchestrator that fetches manifests,
       runs the detector, then asks Claude to confirm + fill in details
       (versions, env vars, deployment strategy) via tool-use JSON.

The detector handles the easy 80% on its own; the LLM is the long tail.
"""
from shared.analysis.analyzer import analyze_repo
from shared.analysis.schemas import (
    AnalysisResult,
    DeploymentStrategy,
    DetectionCandidate,
    Framework,
)

__all__ = [
    "analyze_repo",
    "AnalysisResult",
    "DeploymentStrategy",
    "DetectionCandidate",
    "Framework",
]
