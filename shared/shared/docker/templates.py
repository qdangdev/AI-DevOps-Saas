"""Per-framework Dockerfile templates.

Each `render_*` function takes a small `Spec` dataclass (the bits we care
about from the AnalysisResult) and returns a Dockerfile string. We *don't*
hand the AnalysisResult itself to these functions — the caller in
`generate.py` is responsible for normalizing nulls/defaults into the Spec
before dispatching. This keeps each template body short and focused on the
framework's actual needs.

Common conventions across all templates:

  - **Multi-stage when there's a build step.** Cuts final image size by
    leaving build tools / dev deps behind.
  - **Non-root user.** Final stage runs as ``app`` (uid 1001) by default.
    ECS doesn't enforce non-root, but it's hygiene.
  - **PORT env var.** We set `ENV PORT=<port>` so apps that read it
    (Express, Flask, etc.) get the right value. We also EXPOSE it.
  - **Cache-friendly layer order.** Manifests copied first, deps installed,
    then app source — so editing source doesn't bust the deps cache.
  - **HEALTHCHECK.** Container-level health (separate from the ALB target
    group health). Cheap and helps `docker ps` show real state.

We intentionally do NOT use the user's exact build/start command verbatim
when a safer default is available. Example: a npm `start` script that runs
``next start`` is usually fine, but ``next start -p 3000`` ignores PORT.
We bind explicitly to `0.0.0.0:$PORT` where the framework lets us.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Spec:
    """Normalized inputs for a template — all required, no Nones.

    `generate.py` is responsible for filling in framework defaults so the
    template body never has to think about ``runtime_version is None``.
    """

    runtime_version: str          # e.g. "20", "3.12"
    package_manager: str          # "npm" | "yarn" | "pnpm" | "pip" | "poetry" | "uv" | ...
    build_command: str            # "" if no build step
    start_command: str            # required — empty string is invalid
    port: int
    env_vars: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _node_install_cmd(pm: str) -> str:
    """The deterministic install command for a node package manager.

    Each PM has a "respect the lockfile, don't update it" mode. We use it so
    builds are reproducible.
    """
    return {
        "npm":  "npm ci",
        "yarn": "yarn install --frozen-lockfile",
        "pnpm": "corepack enable && pnpm install --frozen-lockfile",
    }.get(pm, "npm ci")


def _python_install_block(pm: str) -> str:
    """Returns the lines to install Python deps for a given PM.

    Indented to fit inside a Dockerfile RUN block; the caller appends them
    after a COPY of the manifest file(s).
    """
    if pm == "poetry":
        return (
            "RUN pip install --no-cache-dir poetry==1.8.3 \\\n"
            "    && poetry config virtualenvs.create false \\\n"
            "    && poetry install --no-interaction --no-ansi --only main"
        )
    if pm == "uv":
        # uv is dramatically faster than pip; if the analyzer detected it
        # (uv.lock present) we honor that.
        return (
            "RUN pip install --no-cache-dir uv \\\n"
            "    && uv pip install --system --no-cache -r requirements.txt"
        )
    # default: pip
    return "RUN pip install --no-cache-dir -r requirements.txt"


def _python_manifest_copy(pm: str) -> str:
    """Which file(s) to COPY in the deps stage for this PM."""
    if pm == "poetry":
        return "COPY pyproject.toml poetry.lock* ./"
    # pip / uv both read requirements.txt
    return "COPY requirements.txt ./"


def _env_block(env_vars: list[str], port: int) -> str:
    """Standard ENV declarations: PORT first, then any names from analysis as empty.

    Setting names with empty values gives developers a hint of what to
    configure in ECS task definition env, without committing secrets.
    """
    lines = [f"ENV PORT={port}"]
    for name in env_vars:
        # Skip PORT if the analyzer picked it up — we already set it.
        if name.upper() == "PORT":
            continue
        lines.append(f"ENV {name}=")
    return "\n".join(lines)


def _user_block() -> str:
    """Add a non-root user. Same uid across templates so volume perms are
    predictable if anyone ever bind-mounts something."""
    return (
        "RUN addgroup --system --gid 1001 app \\\n"
        "    && adduser --system --uid 1001 --ingroup app app\n"
        "USER app"
    )


# ---------------------------------------------------------------------------
# Node-family templates (Next.js, Vite, CRA, Express, NestJS)
# ---------------------------------------------------------------------------


def render_nextjs(spec: Spec) -> str:
    """Next.js — multi-stage with `output: standalone`.

    The standalone build is the whole point: it tree-shakes node_modules
    down to ~50MB instead of ~500MB. We assume the user has it on; if they
    don't, `next start` from the full app still works but the image is fat.
    """
    install = _node_install_cmd(spec.package_manager)
    build = spec.build_command or "npm run build"
    return f"""\
# syntax=docker/dockerfile:1.7
FROM node:{spec.runtime_version}-alpine AS deps
WORKDIR /app
RUN apk add --no-cache libc6-compat
COPY package.json package-lock.json* yarn.lock* pnpm-lock.yaml* ./
RUN {install}

FROM node:{spec.runtime_version}-alpine AS builder
WORKDIR /app
COPY --from=deps /app/node_modules ./node_modules
COPY . .
ENV NEXT_TELEMETRY_DISABLED=1
RUN {build}

FROM node:{spec.runtime_version}-alpine AS runner
WORKDIR /app
ENV NODE_ENV=production NEXT_TELEMETRY_DISABLED=1
{_env_block(spec.env_vars, spec.port)}
{_user_block()}
COPY --from=builder --chown=app:app /app/public ./public
COPY --from=builder --chown=app:app /app/.next/standalone ./
COPY --from=builder --chown=app:app /app/.next/static ./.next/static
EXPOSE {spec.port}
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \\
  CMD node -e "fetch('http://127.0.0.1:'+process.env.PORT).then(r=>process.exit(r.ok?0:1)).catch(()=>process.exit(1))"
CMD ["node", "server.js"]
"""


def render_vite_or_cra(spec: Spec) -> str:
    """Vite / CRA — multi-stage, nginx serves the static build output.

    The build output dir is conventionally `dist` for Vite and `build` for
    CRA; we hand the user-detected `build_artifact_dir` in via build_command
    or fall back to `dist`. (Generator passes the right one.)
    """
    install = _node_install_cmd(spec.package_manager)
    build = spec.build_command or "npm run build"
    # We use start_command as a vehicle for the output directory name —
    # generate.py packs the artifact dir into spec.start_command for static
    # frameworks. This is a small contract internal to this module.
    artifact_dir = spec.start_command or "dist"
    return f"""\
# syntax=docker/dockerfile:1.7
FROM node:{spec.runtime_version}-alpine AS builder
WORKDIR /app
COPY package.json package-lock.json* yarn.lock* pnpm-lock.yaml* ./
RUN {install}
COPY . .
RUN {build}

FROM nginx:1.27-alpine AS runner
# Listen on the env-provided port. nginx doesn't read env directly, so we
# template the conf at container start.
COPY --from=builder /app/{artifact_dir} /usr/share/nginx/html
RUN printf 'server {{\\n  listen ${{PORT}};\\n  server_name _;\\n  root /usr/share/nginx/html;\\n  index index.html;\\n  location / {{ try_files $uri $uri/ /index.html; }}\\n}}\\n' \\
    > /etc/nginx/templates/default.conf.template
ENV PORT={spec.port}
EXPOSE {spec.port}
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \\
  CMD wget -qO- http://127.0.0.1:$PORT/ >/dev/null || exit 1
CMD ["nginx", "-g", "daemon off;"]
"""


def render_express(spec: Spec) -> str:
    """Express — single image, deterministic install, run as non-root."""
    install = _node_install_cmd(spec.package_manager)
    start = spec.start_command or "node index.js"
    return f"""\
# syntax=docker/dockerfile:1.7
FROM node:{spec.runtime_version}-alpine AS deps
WORKDIR /app
COPY package.json package-lock.json* yarn.lock* pnpm-lock.yaml* ./
RUN {install} --omit=dev || {install}

FROM node:{spec.runtime_version}-alpine AS runner
WORKDIR /app
ENV NODE_ENV=production
{_env_block(spec.env_vars, spec.port)}
COPY --from=deps /app/node_modules ./node_modules
COPY . .
{_user_block()}
EXPOSE {spec.port}
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \\
  CMD wget -qO- http://127.0.0.1:$PORT/ >/dev/null || exit 1
CMD {_shell_to_exec_form(start)}
"""


def render_nestjs(spec: Spec) -> str:
    """NestJS — multi-stage, builds to dist/, runs the bundle."""
    install = _node_install_cmd(spec.package_manager)
    build = spec.build_command or "npm run build"
    start = spec.start_command or "node dist/main.js"
    return f"""\
# syntax=docker/dockerfile:1.7
FROM node:{spec.runtime_version}-alpine AS deps
WORKDIR /app
COPY package.json package-lock.json* yarn.lock* pnpm-lock.yaml* ./
RUN {install}

FROM node:{spec.runtime_version}-alpine AS builder
WORKDIR /app
COPY --from=deps /app/node_modules ./node_modules
COPY . .
RUN {build}

FROM node:{spec.runtime_version}-alpine AS runner
WORKDIR /app
ENV NODE_ENV=production
{_env_block(spec.env_vars, spec.port)}
COPY --from=builder /app/node_modules ./node_modules
COPY --from=builder /app/dist ./dist
COPY --from=builder /app/package.json ./
{_user_block()}
EXPOSE {spec.port}
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \\
  CMD wget -qO- http://127.0.0.1:$PORT/ >/dev/null || exit 1
CMD {_shell_to_exec_form(start)}
"""


# ---------------------------------------------------------------------------
# Python-family templates
# ---------------------------------------------------------------------------


def render_fastapi(spec: Spec) -> str:
    """FastAPI — pip/uv/poetry, uvicorn or gunicorn+uvicorn workers in prod."""
    return _python_app(spec, app_kind="fastapi")


def render_flask(spec: Spec) -> str:
    return _python_app(spec, app_kind="flask")


def render_django(spec: Spec) -> str:
    """Django — gunicorn, run migrations at container start."""
    return _python_app(spec, app_kind="django")


def _python_app(spec: Spec, *, app_kind: str) -> str:
    """Shared body for FastAPI/Flask/Django — only the CMD differs.

    Defaults if the analyzer didn't pin a start command:
      fastapi → uvicorn app.main:app
      flask   → gunicorn app:app
      django  → gunicorn <project>.wsgi
    """
    manifest_copy = _python_manifest_copy(spec.package_manager)
    install = _python_install_block(spec.package_manager)
    default_cmds = {
        "fastapi": f"uvicorn app.main:app --host 0.0.0.0 --port {spec.port}",
        "flask":   f"gunicorn -b 0.0.0.0:{spec.port} app:app",
        # Django needs ``manage.py migrate`` before serving. We do it at
        # container start (cheap if already applied) so the user doesn't have
        # to wire up a one-shot migration job.
        "django":  f"sh -c 'python manage.py migrate --noinput && gunicorn -b 0.0.0.0:{spec.port} ${{DJANGO_WSGI_MODULE:-config.wsgi}}'",
    }
    start = spec.start_command or default_cmds[app_kind]
    return f"""\
# syntax=docker/dockerfile:1.7
FROM python:{spec.runtime_version}-slim AS runner
WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
{_env_block(spec.env_vars, spec.port)}

# System libs most Python web stacks end up pulling. Kept minimal — psycopg2
# binary wheels handle libpq for us; if the user is on psycopg-c they'll need
# to extend this image.
RUN apt-get update \\
    && apt-get install -y --no-install-recommends curl ca-certificates \\
    && rm -rf /var/lib/apt/lists/*

{manifest_copy}
{install}

COPY . .
{_user_block()}
EXPOSE {spec.port}
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \\
  CMD curl -fsS http://127.0.0.1:$PORT/ >/dev/null || exit 1
CMD {_shell_to_exec_form(start)}
"""


# ---------------------------------------------------------------------------
# Compiled-language templates
# ---------------------------------------------------------------------------


def render_go(spec: Spec) -> str:
    """Go — multi-stage, static binary copied into a ~5MB alpine.

    `CGO_ENABLED=0` and `-tags netgo` make the binary fully static so we can
    run it on alpine without glibc. The user's start_command is treated as
    the binary path inside the final image.
    """
    binary = (spec.start_command or "/app/server").lstrip("./")
    return f"""\
# syntax=docker/dockerfile:1.7
FROM golang:{spec.runtime_version}-alpine AS builder
WORKDIR /src
COPY go.mod go.sum ./
RUN go mod download
COPY . .
ENV CGO_ENABLED=0 GOOS=linux
RUN go build -ldflags='-s -w' -tags netgo -o /out/app ./...

FROM alpine:3.20 AS runner
WORKDIR /app
RUN apk add --no-cache ca-certificates wget \\
    && addgroup --system --gid 1001 app \\
    && adduser --system --uid 1001 --ingroup app app
{_env_block(spec.env_vars, spec.port)}
COPY --from=builder --chown=app:app /out/app /app/{binary}
USER app
EXPOSE {spec.port}
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \\
  CMD wget -qO- http://127.0.0.1:$PORT/ >/dev/null || exit 1
CMD ["/app/{binary}"]
"""


def render_rust(spec: Spec) -> str:
    """Rust — cargo build --release in a builder stage, copy to debian-slim.

    We deliberately avoid musl/alpine for the final image. axum + sqlx + most
    crates link cleanly against glibc and produce smaller-and-faster binaries
    than alpine; debian-slim is ~28MB.
    """
    return f"""\
# syntax=docker/dockerfile:1.7
FROM rust:{spec.runtime_version}-slim AS builder
WORKDIR /src
RUN apt-get update && apt-get install -y --no-install-recommends pkg-config libssl-dev \\
    && rm -rf /var/lib/apt/lists/*
COPY Cargo.toml Cargo.lock ./
# Cache deps: build a stub so ``cargo fetch`` resolves the lockfile
# without compiling the user's code yet.
RUN mkdir -p src && echo 'fn main(){{}}' > src/main.rs && cargo build --release \\
    && rm -rf src target/release/deps/$(basename $(pwd))*
COPY . .
RUN cargo build --release --locked

FROM debian:bookworm-slim AS runner
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates curl \\
    && rm -rf /var/lib/apt/lists/* \\
    && groupadd --system --gid 1001 app \\
    && useradd --system --uid 1001 --gid 1001 app
{_env_block(spec.env_vars, spec.port)}
COPY --from=builder --chown=app:app /src/target/release/* /app/
USER app
EXPOSE {spec.port}
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \\
  CMD curl -fsS http://127.0.0.1:$PORT/ >/dev/null || exit 1
# Many axum projects produce a single binary; if there are several, the
# user can override the CMD by committing their own Dockerfile.
CMD ["/bin/sh", "-c", "exec $(ls /app/* | head -1)"]
"""


# ---------------------------------------------------------------------------
# Static
# ---------------------------------------------------------------------------


def render_static_html(spec: Spec) -> str:
    """Plain-static — nginx serves the repo root.

    For the multi-tenant ECS deploy we still wrap static sites in nginx
    rather than S3+CloudFront; the trade-off is an extra ~5MB image vs. a
    second deployment path. ECS-only keeps the worker simple.
    """
    return f"""\
# syntax=docker/dockerfile:1.7
FROM nginx:1.27-alpine AS runner
COPY . /usr/share/nginx/html
RUN printf 'server {{\\n  listen ${{PORT}};\\n  server_name _;\\n  root /usr/share/nginx/html;\\n  index index.html;\\n}}\\n' \\
    > /etc/nginx/templates/default.conf.template
ENV PORT={spec.port}
EXPOSE {spec.port}
HEALTHCHECK --interval=30s --timeout=5s --start-period=5s --retries=3 \\
  CMD wget -qO- http://127.0.0.1:$PORT/ >/dev/null || exit 1
CMD ["nginx", "-g", "daemon off;"]
"""


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------


def _shell_to_exec_form(cmd: str) -> str:
    """Convert "foo --bar baz" → ["foo", "--bar", "baz"] for Dockerfile CMD.

    Exec form is preferred over shell form because:
      - SIGTERM goes to the binary, not /bin/sh -c → graceful shutdown works.
      - No subshell means no quoting surprises with $PORT etc.

    For commands with shell metacharacters (``&&``, ``;``, ``$( )``, ``|``),
    we wrap in ``sh -c`` because exec form can't handle them. The Django
    default uses this on purpose (migrate then serve).
    """
    if any(meta in cmd for meta in ("&&", "||", ";", "|", "$(", "${", ">", "<")):
        # Render as JSON-style array with sh -c.
        escaped = cmd.replace("\\", "\\\\").replace('"', '\\"')
        return f'["sh", "-c", "{escaped}"]'

    parts = cmd.split()
    rendered = ", ".join(f'"{p}"' for p in parts)
    return f"[{rendered}]"
