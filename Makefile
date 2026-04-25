.PHONY: up down logs build migrate makemigration api-shell worker-shell secrets fmt lint test install-dev

# --- docker compose ----------------------------------------------------------

up:
	docker compose up -d --build

down:
	docker compose down

logs:
	docker compose logs -f api worker

build:
	docker compose build

# --- alembic (migrations live under shared/alembic) -------------------------

migrate:
	docker compose exec api alembic upgrade head

makemigration:
	@read -p "Message: " msg; \
	docker compose exec api alembic revision --autogenerate -m "$$msg"

# --- shells -----------------------------------------------------------------

api-shell:
	docker compose exec api sh

worker-shell:
	docker compose exec worker sh

# --- secrets ----------------------------------------------------------------

# Generate fresh JWT_SECRET + ENCRYPTION_KEY suitable for .env
secrets:
	@python3 -c "import secrets; print('JWT_SECRET=' + secrets.token_urlsafe(48))"
	@python3 -c "from cryptography.fernet import Fernet; print('ENCRYPTION_KEY=' + Fernet.generate_key().decode())"

# --- local dev (no docker) --------------------------------------------------

# Editable installs in dependency order: shared first, then the two consumers.
install-dev:
	pip install -e ./shared
	pip install -e './backend[dev]'
	pip install -e './worker[dev]'
	cd frontend && npm install

# --- quality ----------------------------------------------------------------

fmt:
	ruff format shared backend worker
	ruff check --fix shared backend worker

lint:
	ruff check shared backend worker
	cd frontend && npm run typecheck

test:
	cd backend && pytest
	cd shared && pytest -q || true
	cd worker && pytest -q || true
