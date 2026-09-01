# Africlay Server — Setup Guide

This guide provides step-by-step instructions to set up and run the **Africlay Server** Django backend locally using [**Docker & Docker Compose**](https://docs.docker.com/) (recommended with PostgreSQL) or locally using [**uv**](https://docs.astral.sh/uv/).

---

## 1. Prerequisites

Depending on how you plan to run the app:

- **Git**
- **Docker & Docker Compose** (Recommended for full-stack containerized development with PostgreSQL)
- **uv** (For local Python environment & package management)

### Installing `uv` (Local Development)

#### macOS / Linux
```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```
*Or with Homebrew (macOS):*
```bash
brew install uv
```

#### Windows
```powershell
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
```

Verify installation:
```bash
uv --version
docker compose version
```

---

## 2. Environment Configuration & Multi-Environment Settings

The application uses a modular settings architecture under `africlay_server/settings/`:
- **`base.py`**: Common settings (apps, middleware, templates, rest framework, swagger, auth).
- **`local.py`**: Local development using locally spun PostgreSQL container via Docker.
- **`dev.py`**: Development environment connecting to a Google Cloud SQL PostgreSQL instance using its Public IP address.
- **`prod.py`**: Production deployment with production database instance, debug disabled, and security hardening.

Switch environments by setting `DEV_ENVIRONMENT` in your `.env` (or shell environment):
- `DEV_ENVIRONMENT=local` (Default)
- `DEV_ENVIRONMENT=dev`
- `DEV_ENVIRONMENT=prod`

1. Copy `.env-example` to create your local `.env` file:
   ```bash
   cp .env-example .env
   ```

2. Review the `.env` settings:
   ```env
   # Environment Mode: 'local', 'dev', or 'prod'
   DEV_ENVIRONMENT=local

   # Local PostgreSQL Configuration (Docker)
   DB_ENGINE=django.db.backends.postgresql
   DB_NAME=africlay_db
   DB_USER=africlay
   DB_PASSWORD=africlay
   DB_HOST=localhost
   DB_PORT=5432

   # Cloud SQL Dev Configuration (Public IP)
   DEV_DB_NAME=africlaydev
   DEV_DB_USER=africlaydev
   DEV_DB_PASSWORD=your_dev_password
   DEV_DB_HOST=34.x.x.x
   DEV_DB_PORT=5432
   ```

> **Note:**
> - `DB_HOST=localhost` in `.env` enables running commands locally on your Mac (e.g. `make migrate` / `make runserver`) while connecting to the exposed database port `5432`.
> - Inside Docker, `docker-compose.yml` automatically sets `DB_HOST=db` for container-to-container networking.
> - When `DEV_ENVIRONMENT=dev`, the app automatically connects to `DEV_DB_HOST` (Cloud SQL Public IP).
> - When `DEV_ENVIRONMENT=prod`, the app connects to `PROD_DB_HOST` with production security headers.


---

## 3. Option A: Running with Docker Compose (Recommended)

Docker Compose runs both the PostgreSQL database (`postgres:17-alpine`) and the Django backend application in isolated containers with volume live-reloading.

### 1. Build and Start Containers
```bash
# Start and build in foreground
docker compose up --build
# or using Makefile:
make docker-up-build

# Start in background (detached)
docker compose up -d
# or using Makefile:
make docker-up-d
```

### 2. Apply Migrations inside Docker
```bash
docker compose exec backend python manage.py migrate
# or using Makefile:
make docker-migrate
```

### 3. Create Admin Superuser
```bash
docker compose exec backend python manage.py createsuperuser
# or using Makefile:
make docker-createsuperuser
```

### 4. View Logs & Stop Containers
```bash
# Follow live logs
docker compose logs -f
# or using Makefile:
make docker-logs

# Stop containers
docker compose down
# or using Makefile:
make docker-down

# Stop containers and remove volumes (clean database)
docker compose down -v
# or using Makefile:
make docker-down-v
```

---

## 4. Option B: Running Locally with `uv`

If you prefer running Django locally on your host machine:

### 1. Install & Sync Dependencies
```bash
uv sync
# or using Makefile:
make sync
```

### 2. Activate the Virtual Environment (Optional)
`uv run` automatically uses the virtual environment, but you can activate it if you prefer:

#### macOS / Linux
```bash
source .venv/bin/activate
```
#### Windows
```powershell
.venv\Scripts\activate
```

### 3. Start PostgreSQL Database
You can start just the database container using Docker Compose:
```bash
docker compose up -d db
# or using Makefile:
make docker-db
```
*(Ensure `DB_HOST=localhost` in your `.env` for local host connection)*

### 4. Run Migrations & Start Server
```bash
# Generate migrations
uv run python manage.py makemigrations
# or using Makefile:
make makemigrations

# Apply migrations
uv run python manage.py migrate
# or using Makefile:
make migrate

# Create superuser
uv run python manage.py createsuperuser
# or using Makefile:
make createsuperuser

# Start development server
uv run python manage.py runserver
# or using Makefile:
make runserver
```

---

## 5. Managing Dependencies with `uv`

- **Add a dependency:**
  ```bash
  uv add <package-name>
  # Example: uv add djangorestframework
  ```

- **Add a development dependency:**
  ```bash
  uv add --dev <package-name>
  # Example: uv add --dev pytest black flake8
  ```

- **Remove a dependency:**
  ```bash
  uv remove <package-name>
  ```

- **Update lockfile / all dependencies:**
  ```bash
  uv lock --upgrade
  ```

---

## 6. Accessing the Application

- **Django Server:** `http://localhost:8000/`
- **Django Admin Interface:** `http://localhost:8000/admin/`
- **pgAdmin 4 (Database GUI):** `http://localhost:5050/`
  - **Login Email:** `admin@africlay.com` (or value of `PGADMIN_DEFAULT_EMAIL` in `.env`)
  - **Login Password:** `admin` (or value of `PGADMIN_DEFAULT_PASSWORD` in `.env`)

### Connecting pgAdmin to PostgreSQL Database
Once logged into pgAdmin:
1. Right-click **Servers** > **Register** > **Server...**
2. In the **General** tab:
   - **Name:** `Africlay DB` (or any name)
3. In the **Connection** tab:
   - **Host name/address:** `db` (the service name on the shared docker network)
   - **Port:** `5432`
   - **Maintenance database:** `africlay_db`
   - **Username:** `africlay`
   - **Password:** `africlay`
4. Click **Save**.

---

## 7. Makefile Command Reference

To see all available shortcuts, run:
```bash
make help
```

### Docker Compose Commands
| Make Target | Equivalent Docker Command | Description |
| :--- | :--- | :--- |
| `make docker-up` | `docker compose up` | Start containers (attached) |
| `make docker-up-build` | `docker compose up --build` | Rebuild and start containers |
| `make docker-up-d` | `docker compose up -d` | Start containers in background |
| `make docker-down` | `docker compose down` | Stop and remove containers |
| `make docker-down-v` | `docker compose down -v` | Stop containers and clear volumes |
| `make docker-build` | `docker compose build` | Rebuild container images |
| `make docker-logs` | `docker compose logs -f` | Follow live container logs |
| `make docker-db` | `docker compose up -d db` | Start only PostgreSQL container |
| `make docker-pgadmin` | `docker compose up -d db pgadmin` | Start PostgreSQL and pgAdmin containers |
| `make docker-migrate` | `docker compose exec backend python manage.py migrate` | Run migrations inside backend container |
| `make docker-makemigrations` | `docker compose exec backend python manage.py makemigrations` | Generate migrations inside backend container |
| `make docker-createsuperuser` | `docker compose exec backend python manage.py createsuperuser` | Create admin superuser in backend container |
| `make docker-shell` | `docker compose exec backend python manage.py shell` | Open Django shell inside container |


### Local Development Commands (`uv`)
| Make Target | Equivalent Local Command | Description |
| :--- | :--- | :--- |
| `make sync` | `uv sync` | Install / sync project dependencies |
| `make runserver` | `uv run python manage.py runserver` | Start Django local dev server |
| `make makemigrations` | `uv run python manage.py makemigrations` | Generate new database migrations |
| `make migrate` | `uv run python manage.py migrate` | Apply database migrations |
| `make migrate-all` | `uv run python manage.py migrate --merge` | Apply database migrations with merge |
| `make createsuperuser` | `uv run python manage.py createsuperuser` | Create an admin superuser |
| `make shell` | `uv run python manage.py shell` | Open Django interactive shell |
| `make test` | `uv run python manage.py test` | Run project test suite |
