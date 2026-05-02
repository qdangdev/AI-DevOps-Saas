"""Public dispatch: AnalysisResult → Dockerfile string.

Sole external function:

    generate_dockerfile(analysis) -> str

How it handles missing fields:
  The analyzer is allowed to return null for runtime_version, package_manager,
  build_command, and start_command (it's specifically prompted *not* to guess).
  This module fills those gaps with framework-specific safe defaults — same
  defaults every well-behaved Next.js / FastAPI / Go project would use.

Errors:
  We only raise on UNKNOWN. Callers should already have guarded against that
  via the deployment confidence cap, but this is a hard backstop:
  shipping an unconfident image is worse than refusing.

The module is pure — no I/O, no DB, no settings. It's safe to import from the
API for "preview Dockerfile" routes.
"""
from __future__ import annotations

from shared.analysis.schemas import AnalysisResult, Framework
from shared.docker import templates
from shared.docker.templates import Spec


class UnsupportedFrameworkError(ValueError):
    """Raised when no template exists for the analyzed framework.

    Today this only fires for ``Framework.UNKNOWN``. If a future framework
    is added to the enum but not given a template, it'll fire for that too —
    catch in the worker and mark the deployment failed with a clear message.
    """


# ---------------------------------------------------------------------------
# Default-fillers — keep all "what does this framework typically use" knowledge
# in one place so templates stay short.
# ---------------------------------------------------------------------------


_DEFAULT_NODE_VERSION = "20"
_DEFAULT_PYTHON_VERSION = "3.12"
_DEFAULT_GO_VERSION = "1.22"
_DEFAULT_RUST_VERSION = "1.78"

_DEFAULT_NODE_PM = "npm"
_DEFAULT_PYTHON_PM = "pip"

_DEFAULT_PORTS: dict[Framework, int] = {
    Framework.NEXT_JS: 3000,
    Framework.NUXT: 3000,
    Framework.REACT_VITE: 8080,
    Framework.REACT_CRA: 8080,
    Framework.EXPRESS: 3000,
    Framework.NEST_JS: 3000,
    Framework.FASTAPI: 8000,
    Framework.FLASK: 8000,
    Framework.DJANGO: 8000,
    Framework.RAILS: 3000,
    Framework.SPRING_BOOT: 8080,
    Framework.GO_NET_HTTP: 8080,
    Framework.RUST_AXUM: 8080,
    Framework.STATIC_HTML: 8080,
}


def _node_runtime(version: str | None) -> str:
    """Coerce a node version pin to a major-only tag for the alpine image.

    "20.11.0" → "20", ">=18 <20" → "20" (default, we don't try to satisfy ranges),
    None → "20".
    """
    if not version:
        return _DEFAULT_NODE_VERSION
    # Strip leading 'v', take the major.
    v = version.lstrip("v ").split(".")[0]
    return v if v.isdigit() else _DEFAULT_NODE_VERSION


def _python_runtime(version: str | None) -> str:
    """Python 'X.Y' for the slim image. '3.12.1' → '3.12', None → '3.12'."""
    if not version:
        return _DEFAULT_PYTHON_VERSION
    parts = version.lstrip("v ").split(".")
    if len(parts) >= 2 and parts[0].isdigit() and parts[1].isdigit():
        return f"{parts[0]}.{parts[1]}"
    return _DEFAULT_PYTHON_VERSION


def _go_runtime(version: str | None) -> str:
    if not version:
        return _DEFAULT_GO_VERSION
    parts = version.lstrip("v ").split(".")
    if len(parts) >= 2 and parts[0].isdigit() and parts[1].isdigit():
        return f"{parts[0]}.{parts[1]}"
    return _DEFAULT_GO_VERSION


def _rust_runtime(version: str | None) -> str:
    return version.lstrip("v ") if version else _DEFAULT_RUST_VERSION


# ---------------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------------


_NODE_FRAMEWORKS = {
    Framework.NEXT_JS,
    Framework.NUXT,
    Framework.REACT_VITE,
    Framework.REACT_CRA,
    Framework.EXPRESS,
    Framework.NEST_JS,
}
_PYTHON_FRAMEWORKS = {Framework.FASTAPI, Framework.FLASK, Framework.DJANGO}


def generate_dockerfile(analysis: AnalysisResult | dict) -> str:
    """Produce a Dockerfile string for an AnalysisResult.

    Accepts either the typed model or its serialized dict (which is what we
    persist on the Deployment row). Validates+coerces dicts for safety.
    """
    if isinstance(analysis, dict):
        analysis = AnalysisResult.model_validate(analysis)

    fw = analysis.framework
    port = analysis.default_port or analysis.deployment_strategy.expose_port or _DEFAULT_PORTS.get(fw, 8080)
    env_vars = list(analysis.env_vars)
    build = analysis.build_command or ""
    start = analysis.start_command or ""

    if fw in _NODE_FRAMEWORKS:
        spec = Spec(
            runtime_version=_node_runtime(analysis.runtime_version),
            package_manager=analysis.package_manager or _DEFAULT_NODE_PM,
            build_command=build,
            start_command=start,
            port=port,
            env_vars=env_vars,
        )
        if fw == Framework.NEXT_JS:
            return templates.render_nextjs(spec)
        if fw == Framework.NUXT:
            # Nuxt 3 "nitro preset = node" produces a server.mjs at .output/.
            # Templating identically to Next handles 95% of cases; if a user's
            # Nuxt config diverges they can commit a Dockerfile.
            return templates.render_nextjs(spec)
        if fw in (Framework.REACT_VITE, Framework.REACT_CRA):
            # Pack the artifact dir into start_command for the static template.
            artifact = analysis.deployment_strategy.build_artifact_dir or (
                "dist" if fw == Framework.REACT_VITE else "build"
            )
            return templates.render_vite_or_cra(
                Spec(
                    runtime_version=spec.runtime_version,
                    package_manager=spec.package_manager,
                    build_command=spec.build_command,
                    start_command=artifact,  # contract internal to the template
                    port=spec.port,
                    env_vars=spec.env_vars,
                )
            )
        if fw == Framework.EXPRESS:
            return templates.render_express(spec)
        if fw == Framework.NEST_JS:
            return templates.render_nestjs(spec)

    if fw in _PYTHON_FRAMEWORKS:
        spec = Spec(
            runtime_version=_python_runtime(analysis.runtime_version),
            package_manager=analysis.package_manager or _DEFAULT_PYTHON_PM,
            build_command=build,
            start_command=start,
            port=port,
            env_vars=env_vars,
        )
        if fw == Framework.FASTAPI:
            return templates.render_fastapi(spec)
        if fw == Framework.FLASK:
            return templates.render_flask(spec)
        if fw == Framework.DJANGO:
            return templates.render_django(spec)

    if fw == Framework.GO_NET_HTTP:
        return templates.render_go(
            Spec(
                runtime_version=_go_runtime(analysis.runtime_version),
                package_manager="go-modules",
                build_command="",
                start_command=start or "/app/server",
                port=port,
                env_vars=env_vars,
            )
        )

    if fw == Framework.RUST_AXUM:
        return templates.render_rust(
            Spec(
                runtime_version=_rust_runtime(analysis.runtime_version),
                package_manager="cargo",
                build_command="",
                start_command="",
                port=port,
                env_vars=env_vars,
            )
        )

    if fw == Framework.STATIC_HTML:
        return templates.render_static_html(
            Spec(
                runtime_version="",
                package_manager="",
                build_command="",
                start_command="",
                port=port,
                env_vars=env_vars,
            )
        )

    # Rails, Spring Boot, Unknown — not yet supported by the generator.
    # Rails/Spring would each be ~30 lines but need careful base-image choices
    # we haven't done yet. UNKNOWN is the explicit "we don't know what this is"
    # case from the analyzer.
    raise UnsupportedFrameworkError(
        f"no Dockerfile template for framework={fw.value}. "
        "Commit a Dockerfile to the repo root to bypass auto-generation."
    )
