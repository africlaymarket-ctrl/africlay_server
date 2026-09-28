.PHONY: help sync runserver makemigrations makemigrations-all migrate migrate-all createsuperuser shell test smoke-api \
        docker-up docker-up-build docker-up-d docker-down docker-down-v docker-build docker-logs \
        docker-migrate docker-makemigrations docker-createsuperuser docker-shell docker-db docker-pgadmin

help:
	@echo "Available commands:"
	@echo "  Local (uv):"
	@echo "    make sync                  - Install/sync dependencies with uv"
	@echo "    make runserver             - Start Django local dev server"
	@echo "    make makemigrations        - Create new database migrations"
	@echo "    make migrate               - Apply database migrations"
	@echo "    make migrate-all           - Apply database migrations (with merge)"
	@echo "    make createsuperuser       - Create an admin superuser"
	@echo "    make shell                 - Open Django interactive shell"
	@echo "    make test                  - Run Django test suite"
	@echo "    make smoke-api             - Exercise core API endpoints against the persistent database"
	@echo ""
	@echo "  Docker Compose:"
	@echo "    make docker-up             - Start all containers (attached)"
	@echo "    make docker-up-build       - Rebuild and start all containers"
	@echo "    make docker-up-d           - Start all containers in background"
	@echo "    make docker-down           - Stop and remove containers"
	@echo "    make docker-down-v         - Stop containers and remove volumes"
	@echo "    make docker-build          - Build docker images"
	@echo "    make docker-logs           - Follow live container logs"
	@echo "    make docker-db             - Start only PostgreSQL database container"
	@echo "    make docker-pgadmin        - Start PostgreSQL and pgAdmin containers"
	@echo "    make docker-migrate        - Run migrations inside backend container"
	@echo "    make docker-makemigrations - Make migrations inside backend container"
	@echo "    make docker-createsuperuser- Create superuser inside backend container"
	@echo "    make docker-shell          - Open Django shell inside backend container"


# ==============================================================================
# Local Development Commands (uv)
# ==============================================================================

sync:
	uv sync

runserver:
	uv run python manage.py runserver

makemigrations:
	uv run python manage.py makemigrations

makemigrations-all:
	uv run python manage.py makemigrations --all

migrate:
	uv run python manage.py migrate

migrate-all:
	uv run python manage.py migrate --merge

createsuperuser:
	uv run python manage.py createsuperuser

shell:
	uv run python manage.py shell

test:
	uv run python manage.py test

smoke-api:
	uv run python manage.py smoke_api

# ==============================================================================
# Docker Compose Commands
# ==============================================================================

docker-up:
	docker compose up

docker-up-build:
	docker compose up --build

docker-up-d:
	docker compose up -d

docker-down:
	docker compose down

docker-down-v:
	docker compose down -v

docker-build:
	docker compose build

docker-logs:
	docker compose logs -f

docker-db:
	docker compose up -d db

docker-pgadmin:
	docker compose up -d db pgadmin


docker-migrate:
	docker compose exec backend python manage.py migrate

docker-makemigrations:
	docker compose exec backend python manage.py makemigrations

docker-createsuperuser:
	docker compose exec backend python manage.py createsuperuser

docker-shell:
	docker compose exec backend python manage.py shell
