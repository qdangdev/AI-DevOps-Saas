.PHONY: up down logs build migrate makemigration backend-shell secrets fmt lint test

up:
	docker compose up -d --build

down:
	docker compose down

logs:
	docker compose logs -f api

build:
	docker compose build

migrate:
	docker compose exec api alembic upgrade head

makemigration:
	@read -p "Message: " msg; \
	docker compose exec api alembic revision --autogenerate -m "$$msg"

backend-shell:
	docker compose exec api sh

# Generate fresh JWT_SECRET + ENCRYPTION_KEY suitable for .env
secrets:
	@python3 -c "import secrets; print('JWT_SECRET=' + secrets.token_urlsafe(48))"
	@python3 -c "from cryptography.fernet import Fernet; print('ENCRYPTION_KEY=' + Fernet.generate_key().decode())"

fmt:
	cd backend && ruff format . && ruff check --fix .

lint:
	cd backend && ruff check .
	cd frontend && npm run typecheck

test:
	cd backend && pytest
