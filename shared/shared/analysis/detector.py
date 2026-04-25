"""Deterministic framework detection — no LLM, no I/O.

Given a file tree (list of paths) and a dict of fetched manifest contents,
return ranked `DetectionCandidate`s. The orchestrator uses the top candidate
as a *hint* for the LLM, not as the final answer.

The rules are intentionally conservative — we'd rather return `unknown` with
0.5 confidence and let the LLM figure it out than confidently mis-detect.
A wrong heuristic that the LLM "trusts" is the worst failure mode.
"""
from __future__ import annotations

import json
import re
from collections.abc import Iterable

from shared.analysis.schemas import DetectionCandidate, Framework


def _has(paths: set[str], path: str) -> bool:
    return path in paths


def _has_any(paths: set[str], candidates: Iterable[str]) -> bool:
    return any(c in paths for c in candidates)


def _package_json_deps(manifests: dict[str, str]) -> set[str]:
    """Return the union of dependencies + devDependencies from package.json."""
    raw = manifests.get("package.json")
    if not raw:
        return set()
    try:
        pkg = json.loads(raw)
    except json.JSONDecodeError:
        return set()
    deps: set[str] = set()
    for key in ("dependencies", "devDependencies", "peerDependencies"):
        d = pkg.get(key) or {}
        if isinstance(d, dict):
            deps.update(d.keys())
    return deps


def _pyproject_or_requirements_deps(manifests: dict[str, str]) -> set[str]:
    """Best-effort Python dependency names. Doesn't attempt full TOML parsing —
    we only care about presence of well-known packages."""
    deps: set[str] = set()
    if pyproj := manifests.get("pyproject.toml"):
        # Match any dep name appearing in a dependencies array. Cheap regex
        # is fine here — we don't need versions.
        deps.update(re.findall(r'"([a-zA-Z0-9_\-]+)(?:\[[^\]]*\])?(?:[<>=!~][^"]+)?"', pyproj))
    if reqs := manifests.get("requirements.txt"):
        for line in reqs.splitlines():
            line = line.strip()
            if not line or line.startswith("#") or line.startswith("-"):
                continue
            name = re.split(r"[<>=!~\[]", line, 1)[0].strip()
            if name:
                deps.add(name.lower())
    return {d.lower() for d in deps}


def detect(paths: list[str], manifests: dict[str, str]) -> list[DetectionCandidate]:
    """Return candidates ranked by confidence (highest first). Always non-empty:
    if nothing matches, returns [(UNKNOWN, 0.0)]."""
    pset = set(paths)
    candidates: list[DetectionCandidate] = []

    # --- Node ecosystem ---
    if _has(pset, "package.json"):
        npm_deps = _package_json_deps(manifests)

        # Next.js: config file is a near-100% signal.
        if _has_any(pset, ("next.config.js", "next.config.mjs", "next.config.ts")) or "next" in npm_deps:
            candidates.append(
                DetectionCandidate(
                    framework=Framework.NEXT_JS,
                    confidence=0.95 if "next" in npm_deps else 0.9,
                    evidence=[p for p in pset if p.startswith("next.config")] + ["package.json:next"],
                )
            )
        # Nuxt
        elif _has_any(pset, ("nuxt.config.ts", "nuxt.config.js")) or "nuxt" in npm_deps:
            candidates.append(
                DetectionCandidate(framework=Framework.NUXT, confidence=0.9, evidence=["nuxt.config"])
            )
        # NestJS — test before generic Express; nest depends on express but the framing differs.
        elif "@nestjs/core" in npm_deps or _has(pset, "nest-cli.json"):
            candidates.append(
                DetectionCandidate(
                    framework=Framework.NEST_JS,
                    confidence=0.9,
                    evidence=["@nestjs/core" if "@nestjs/core" in npm_deps else "nest-cli.json"],
                )
            )
        # Vite (often React, sometimes Vue/Svelte — LLM disambiguates)
        elif _has_any(pset, ("vite.config.ts", "vite.config.js")) or "vite" in npm_deps:
            candidates.append(
                DetectionCandidate(framework=Framework.REACT_VITE, confidence=0.75, evidence=["vite.config"])
            )
        # CRA — react-scripts is the giveaway
        elif "react-scripts" in npm_deps:
            candidates.append(
                DetectionCandidate(
                    framework=Framework.REACT_CRA,
                    confidence=0.9,
                    evidence=["package.json:react-scripts"],
                )
            )
        # Plain Express
        elif "express" in npm_deps:
            candidates.append(
                DetectionCandidate(
                    framework=Framework.EXPRESS,
                    confidence=0.85,
                    evidence=["package.json:express"],
                )
            )

    # --- Python ecosystem ---
    py_deps = _pyproject_or_requirements_deps(manifests)
    if py_deps or _has_any(pset, ("pyproject.toml", "requirements.txt", "Pipfile")):
        if _has(pset, "manage.py") or "django" in py_deps:
            candidates.append(
                DetectionCandidate(
                    framework=Framework.DJANGO,
                    confidence=0.95 if _has(pset, "manage.py") else 0.85,
                    evidence=[p for p in ("manage.py", "pyproject.toml:django") if p.startswith("manage") or "django" in py_deps],
                )
            )
        elif "fastapi" in py_deps:
            candidates.append(
                DetectionCandidate(
                    framework=Framework.FASTAPI,
                    confidence=0.9,
                    evidence=["pyproject.toml:fastapi"],
                )
            )
        elif "flask" in py_deps:
            candidates.append(
                DetectionCandidate(
                    framework=Framework.FLASK,
                    confidence=0.85,
                    evidence=["pyproject.toml:flask"],
                )
            )

    # --- Other languages ---
    if _has(pset, "Gemfile") and "rails" in (manifests.get("Gemfile", "")):
        candidates.append(
            DetectionCandidate(framework=Framework.RAILS, confidence=0.9, evidence=["Gemfile:rails"])
        )
    if _has(pset, "go.mod"):
        candidates.append(
            DetectionCandidate(framework=Framework.GO_NET_HTTP, confidence=0.7, evidence=["go.mod"])
        )
    if _has(pset, "Cargo.toml"):
        candidates.append(
            DetectionCandidate(framework=Framework.RUST_AXUM, confidence=0.6, evidence=["Cargo.toml"])
        )
    if _has_any(pset, ("pom.xml", "build.gradle", "build.gradle.kts")):
        candidates.append(
            DetectionCandidate(framework=Framework.SPRING_BOOT, confidence=0.7, evidence=["pom.xml/gradle"])
        )

    # --- Static-only fallback ---
    if not candidates:
        if _has(pset, "index.html") and not _has(pset, "package.json"):
            candidates.append(
                DetectionCandidate(
                    framework=Framework.STATIC_HTML, confidence=0.6, evidence=["index.html"]
                )
            )

    if not candidates:
        candidates.append(DetectionCandidate(framework=Framework.UNKNOWN, confidence=0.0, evidence=[]))

    candidates.sort(key=lambda c: c.confidence, reverse=True)
    return candidates
