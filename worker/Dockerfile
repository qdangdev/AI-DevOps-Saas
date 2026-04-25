# syntax=docker/dockerfile:1.7
# Worker image — has docker CLI + AWS CLI baked in for build & deploy tasks.
# Build context is the *monorepo root* (so we can COPY shared/ + worker/).

FROM python:3.12-slim AS builder
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /build

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
 && rm -rf /var/lib/apt/lists/*

# Install both packages editable into a single prefix.
COPY shared/  ./shared/
COPY worker/  ./worker/
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir --prefix=/install ./shared && \
    pip install --no-cache-dir --prefix=/install ./worker

# --- runtime ---
FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/install/bin:$PATH"

# docker CLI for `docker build`, awscli for ECR auth + ECS calls
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq5 ca-certificates curl gnupg \
 && install -m 0755 -d /etc/apt/keyrings \
 && curl -fsSL https://download.docker.com/linux/debian/gpg | gpg --dearmor -o /etc/apt/keyrings/docker.gpg \
 && echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/debian bookworm stable" > /etc/apt/sources.list.d/docker.list \
 && apt-get update && apt-get install -y --no-install-recommends docker-ce-cli docker-buildx-plugin \
 && pip install --no-cache-dir awscli \
 && apt-get purge -y curl gnupg && apt-get autoremove -y \
 && rm -rf /var/lib/apt/lists/* \
 && useradd -m -u 1000 worker

COPY --from=builder /install /install
WORKDIR /app

USER worker

# Subscribe to all four queues by default; in prod each ECS service overrides -Q to its own pool.
CMD ["celery", "-A", "worker.celery_app:app", "worker", \
     "-Q", "analyzer,dockergen,builder,deployer", \
     "--loglevel=info", "--concurrency=2"]
