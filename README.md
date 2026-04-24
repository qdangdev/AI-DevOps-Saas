# AI DevOps SaaS

Connect a GitHub repo → analyze it → generate a production Dockerfile → deploy to AWS.

## Stack

- **Frontend:** React 18 + TypeScript + Vite + Tailwind + TanStack Query
- **Backend:** FastAPI (Python 3.12), SQLAlchemy 2.x async, Alembic, Pydantic v2
- **Workers:** Celery + Redis
- **Database:** PostgreSQL 16
- **LLM:** Anthropic Claude (provider-abstract interface)
- **Infra:** Docker, AWS ECS Fargate, GitHub Actions CI/CD

## Layout

```
ai-devops-saas/
├── backend/              # FastAPI app + Celery workers
│   ├── app/
│   │   ├── api/v1/       # HTTP routers
│   │   ├── core/         # config, db, logging, security
│   │   ├── models/       # SQLAlchemy ORM models
│   │   ├── schemas/      # Pydantic request/response schemas
│   │   ├── services/     # github, llm, analyzer, dockerfile_gen, deployer
│   │   ├── workers/      # Celery app + tasks
│   │   └── main.py
│   ├── alembic/          # DB migrations
│   ├── tests/
│   ├── Dockerfile
│   └── pyproject.toml
├── frontend/             # React SPA
│   ├── src/
│   │   ├── api/          # typed API client
│   │   ├── pages/
│   │   ├── components/
│   │   ├── hooks/
│   │   └── stores/
│   ├── Dockerfile
│   └── package.json
├── infra/
│   ├── ecs/              # ECS task definitions, terraform stubs
│   └── nginx/            # reverse proxy config (prod)
├── .github/workflows/    # CI/CD
├── docker-compose.yml    # local dev: api, worker, db, redis, frontend
├── Makefile
└── .env.example
```

## Local dev

```bash
cp .env.example .env
# fill in GITHUB_CLIENT_ID, GITHUB_CLIENT_SECRET, ANTHROPIC_API_KEY
make up         # docker-compose up
make migrate    # alembic upgrade head
```

API: http://localhost:8000 · Frontend: http://localhost:5173 · Docs: http://localhost:8000/docs

## Production deploy

CI pushes images to ECR on merge to `main`. ECS service rolls. See `.github/workflows/deploy.yml`.
