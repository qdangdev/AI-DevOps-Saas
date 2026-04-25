"""Catalog of manifest files we fetch verbatim for the LLM.

We deliberately don't dump the *whole* repo into the prompt — token cost is
quadratic-ish on a long context and most files are noise. Instead we fetch a
small whitelist of "manifests": files that declare what the app *is* (which
framework, which deps, which scripts).

Two ordering hints matter:

  - Order in `MANIFEST_FILES` is also the order we present them to the LLM,
    so put the most disambiguating files first (next.config.js before
    package.json).
  - Per-file `max_bytes` keeps a single huge lockfile from eating the whole
    prompt budget.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Manifest:
    path: str
    max_bytes: int
    # Hint that this file alone strongly suggests a specific framework.
    # Used by the deterministic detector and surfaced to the LLM.
    fingerprint_for: str | None = None


MANIFEST_FILES: tuple[Manifest, ...] = (
    # --- Framework fingerprints (high signal, fetched first) ---
    Manifest("next.config.js", 4_000, fingerprint_for="next.js"),
    Manifest("next.config.mjs", 4_000, fingerprint_for="next.js"),
    Manifest("next.config.ts", 4_000, fingerprint_for="next.js"),
    Manifest("nuxt.config.ts", 4_000, fingerprint_for="nuxt"),
    Manifest("nuxt.config.js", 4_000, fingerprint_for="nuxt"),
    Manifest("vite.config.ts", 4_000, fingerprint_for="react-vite"),
    Manifest("vite.config.js", 4_000, fingerprint_for="react-vite"),
    Manifest("nest-cli.json", 2_000, fingerprint_for="nest.js"),
    Manifest("manage.py", 2_000, fingerprint_for="django"),
    Manifest("Gemfile", 4_000, fingerprint_for="rails"),
    Manifest("config/application.rb", 4_000, fingerprint_for="rails"),
    Manifest("Cargo.toml", 8_000, fingerprint_for="rust-axum"),
    Manifest("go.mod", 4_000, fingerprint_for="go-net-http"),

    # --- Universal manifests (lower signal but always informative) ---
    Manifest("package.json", 16_000),
    Manifest("pyproject.toml", 16_000),
    Manifest("requirements.txt", 8_000),
    Manifest("Pipfile", 4_000),
    Manifest("poetry.lock", 4_000),       # only first N bytes — we just want runtime hints
    Manifest("pom.xml", 8_000),
    Manifest("build.gradle", 8_000),
    Manifest("build.gradle.kts", 8_000),

    # --- Runtime version pins ---
    Manifest(".nvmrc", 64),
    Manifest(".node-version", 64),
    Manifest(".python-version", 64),
    Manifest(".tool-versions", 256),

    # --- Deployment-relevant ---
    Manifest("Dockerfile", 8_000),         # if they already have one we won't overwrite their intent
    Manifest("docker-compose.yml", 8_000),
    Manifest("Procfile", 1_000),
    Manifest(".env.example", 4_000),       # signals which env vars are needed
    Manifest("README.md", 16_000),         # last resort context
)


def all_paths() -> list[str]:
    return [m.path for m in MANIFEST_FILES]
