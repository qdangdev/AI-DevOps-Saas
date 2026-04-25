"""Prompt engineering for the deployment-analyzer LLM call.

Strategy
========

We send Claude:

  1. **A persona / role** in the system prompt. "Senior DevOps engineer"
     anchors the model in the right domain (deployment, not code review).
  2. **A short, hard-coded set of constraints**: what the output must be,
     what to do when uncertain. We say "return null if you can't infer"
     more than once on purpose — without it Claude tends to fabricate
     plausible-sounding versions and ports.
  3. **The deterministic detector's top guess** as a "current hypothesis".
     This is a head start, not an instruction; the system prompt says
     explicitly to override it if the manifests disagree.
  4. **The actual evidence** — file tree (paths only) + manifest contents,
     each clearly delimited so Claude doesn't conflate them.

We do NOT use few-shot examples. They cost ~1k tokens each and on this
domain Claude doesn't need them; the schema descriptions in
`AnalysisResult` carry the same signal more cheaply.

Output enforcement
==================

The actual JSON shape is enforced by tool-use, not prose instructions.
`shared.services.anthropic.complete_with_tool` forces
`tool_choice={"type": "tool", "name": "report_analysis"}`, so the model
*cannot* respond with anything other than a tool call against our schema.
This is more reliable than asking for JSON in the prompt and parsing it.

Tuning notes
============

- temperature=0 (set in the wrapper). This task has one right answer per
  repo; sampling diversity only adds noise.
- We pre-truncate file tree to 200 entries. Beyond that the per-token cost
  outweighs the marginal information.
- Manifest contents are individually capped (see manifests.py). A monorepo
  whose root README is 500KB shouldn't drown out a 1KB pyproject.toml.
"""
from __future__ import annotations

from shared.analysis.schemas import DetectionCandidate

SYSTEM_PROMPT = """\
You are a senior DevOps engineer analyzing a GitHub repository to design
its production deployment.

You will receive:
  1. The repository's file tree (directory structure only, no contents).
  2. The contents of a small set of manifest files (package.json,
     pyproject.toml, Dockerfile, etc.) that we fetched verbatim.
  3. A heuristic guess about which framework this is, with a confidence
     score. The heuristic is a starting point — override it if the
     manifests clearly contradict it.

Rules:
  - Be conservative. If you cannot determine a value from the evidence,
    return null. Do NOT guess plausible-looking versions, ports, or
    commands.
  - Pin runtime versions only if the repo declares them (in a .nvmrc,
    .python-version, engines field, etc.). If the repo says "node >=18"
    return "18", not "20".
  - Detect environment variables by *reference* in the manifests / Dockerfile
    / .env.example. Don't list every var the framework *could* take.
  - The deployment_strategy field shapes our generated Dockerfile and ECS
    task definition. For pure-static front-ends prefer
    static-s3-cloudfront. For long-poll / websocket apps prefer
    ecs-fargate-nlb. Otherwise ecs-fargate.

You will respond by calling the `report_analysis` tool exactly once.
"""


def build_user_prompt(
    *,
    owner: str,
    repo: str,
    branch: str,
    file_tree: list[str],
    manifests: dict[str, str],
    top_candidate: DetectionCandidate,
    max_tree_entries: int = 200,
) -> str:
    """Assemble the user message Claude will see.

    Sections are delimited with `=== HEADER ===` lines. Claude handles those
    well as soft section markers and they're easy to debug-grep in logs.
    """
    truncated = file_tree[:max_tree_entries]
    tree_block = "\n".join(truncated)
    if len(file_tree) > max_tree_entries:
        tree_block += f"\n... ({len(file_tree) - max_tree_entries} more entries truncated)"

    manifest_blocks: list[str] = []
    for path, contents in manifests.items():
        manifest_blocks.append(f"--- {path} ---\n{contents.rstrip()}")
    manifests_section = "\n\n".join(manifest_blocks) if manifest_blocks else "(no manifest files found)"

    evidence = ", ".join(top_candidate.evidence) if top_candidate.evidence else "(none)"

    return f"""\
Repository: {owner}/{repo}@{branch}

=== HEURISTIC GUESS ===
framework: {top_candidate.framework.value}
confidence: {top_candidate.confidence:.2f}
evidence:  {evidence}

(This is a starting point. If the manifests below disagree, override it.)

=== FILE TREE ({len(truncated)} of {len(file_tree)}) ===
{tree_block}

=== MANIFEST FILES ===
{manifests_section}

Now call the `report_analysis` tool with your final analysis. Remember:
return null for any field you can't determine from the evidence above.
"""


# --- Tool advertisement -----------------------------------------------------
# Description Claude sees when deciding when to call the tool. Kept terse —
# the schema does the heavy lifting, this is just the "what is this for".
TOOL_NAME = "report_analysis"
TOOL_DESCRIPTION = (
    "Submit the final deployment analysis for the repository. Call this "
    "exactly once, after you've reviewed the file tree and manifests."
)
