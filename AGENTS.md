# AI Agent Guidelines & Architecture Rules

This repository is a modern, production-grade backend template built with **Vertical Slice Architecture (VSA / Modular Monolith)**.
These guidelines help AI coding assistants (Cursor, Claude Code, Antigravity, Windsurf, Copilot, etc.) navigate the codebase efficiently, prevent hallucinations, avoid context sprawl, and produce compliant code during vibecoding sessions.

---

## 🏛️ System Architecture: AI-Native Vertical Slice (Modular Monolith)

Code is organized **by feature/business domain** rather than horizontal technical layers. Everything related to a single business capability lives co-located in a single directory.

```text
services/api/app/
├── core/                        # Global infrastructure & cross-cutting components
│   ├── config.py                # Environment & settings (Pydantic BaseSettings)
│   ├── db.py                    # Async SQLAlchemy/SQLModel engine & session factory
│   ├── redis.py                 # Async Redis connection pool & client
│   ├── broker.py                # Taskiq async worker broker & correlation tracing
│   ├── logging.py               # Structured logging (structlog, JSON in prod)
│   ├── middleware.py            # Correlation ID (X-Request-ID) & access logging
│   ├── rate_limit.py            # Redis sliding-window RateLimiter dependency
│   ├── exceptions.py            # Domain exceptions (AppException, NotFound, 429, etc.)
│   └── exception_handlers.py    # RFC 9457 unified Problem Details error responses
│
├── shared/                      # Reusable cross-domain building blocks
│   ├── models.py                # BaseUUIDModel (UUIDv7 primary keys, UTC timestamps)
│   ├── pagination.py            # PageParams, CursorParams, PaginatedResponse envelopes
│   ├── errors.py                # ErrorResponse, ErrorDetail schemas (RFC 9457)
│   └── cache.py                 # CacheService (Redis with orjson serialization)
│
└── modules/                     # 🚀 VERTICAL SLICES (Feature / Domain Modules)
    ├── __init__.py              # Central domain registry (imports all domain models for Alembic)
    ├── health/                  # Health & readiness probes module
    │   ├── router.py            # /health and /ready routes
    │   └── schemas.py           # HealthCheckResponse, ReadinessResponse
    │
    └── items/                   # Example Domain: Items management
        ├── __init__.py          # Module public API & re-exports
        ├── router.py            # FastAPI endpoints (APIRouter)
        ├── models.py            # SQLModel database tables
        ├── schemas.py           # Pydantic DTO schemas (Create, Update, Read)
        ├── service.py           # Pure business logic & CRUD (uses session.flush, NO commit)
        └── tasks.py             # Taskiq background async tasks
```

---

## 🤖 Golden Rules for AI Agents (Vibecoding Instructions)

When instructed to **create a new feature or domain `X`** (e.g., `users`, `billing`, `orders`):
1. **Directory Structure:** Create `services/api/app/modules/<X>/` containing:
   - `models.py`: Database entities inheriting from `app.shared.models.BaseUUIDModel`.
   - `schemas.py`: Input/output Pydantic schemas (e.g. `XCreate`, `XUpdate`, `XRead`).
   - `service.py`: Business logic and database operations.
   - `router.py`: FastAPI `APIRouter(prefix="/<X>", tags=["<X>"])`.
   - `tasks.py`: Background tasks decorated with `@broker.task` (if async jobs are needed).
   - `__init__.py`: Clean re-exports of models, schemas, and router.

2. **Register the New Module:**
   - Register the router in [`app/api/v1/router.py`](file:///home/qr3n/PycharmProjects/fastapi-backend-template/services/api/app/api/v1/router.py):
     ```python
     from app.modules.<X>.router import router as x_router
     api_v1_router.include_router(x_router)
     ```
   - Import the models in [`app/modules/__init__.py`](file:///home/qr3n/PycharmProjects/fastapi-backend-template/services/api/app/modules/__init__.py) so Alembic auto-generates migrations.

3. **Database Transactions (Unit of Work):**
   - **NEVER** call `await session.commit()` inside domain services (`service.py`).
   - Use `session.add(item)` and `await session.flush()` in services.
   - Transactions are committed automatically by the `get_db` dependency upon successful response, or rolled back on unhandled error.

4. **Background Tasks Auto-Discovery:**
   - Background workers use Taskiq with `--fs-discover`, which automatically scans all `**/tasks.py` across modules. No manual worker configuration is needed when adding new tasks.

5. **Cross-Module Communication (Bounded Contexts):**
   - Module `A` must NEVER mutate Module `B`'s database models directly.
   - Cross-module operations must go through Module `B`'s public `service.py` functions or background tasks.

6. **Atomic Git Commits per Milestone:**
   - Commit changes incrementally upon completing each discrete milestone (Schema -> Service -> Router -> Tasks).
   - Never accumulate hours of multi-stage work into a single uncommitted blob.
   - Follow the **Git & Atomic Commits Protocol** below.

---

## ⚡ Execution Context & Commands (Crucial)

Always run commands using `--directory services/api` from root or inside `services/api`:

### Common Commands:
- **Run tests:** `make test` (or `uv run --directory services/api pytest`)
- **Lint:** `make lint` (or `uv run --directory services/api ruff check . && uv run --directory services/api mypy app tests`)
- **Format code:** `make format` (or `uv run --directory services/api ruff format . && uv run --directory services/api ruff check --fix .`)
- **Full quality check:** `make check` (runs lint + tests)
- **Apply migrations:** `make migrate` (or `uv run --directory services/api alembic upgrade head`)
- **Create migration:** `make migration m="name"`
- **Run local server:** `make dev`
- **Run background worker:** `make worker`
- **Docker services:** `make up` / `make down` / `make logs`

---

## 🐍 Python & FastAPI Standards

1. **Python 3.12+ Standards:**
   - Use built-in generics: `list[str]`, `dict[str, Any]` (avoid `typing.List`, `typing.Dict`).
   - Use union syntax: `str | None`, `int | float` (avoid `typing.Optional`, `typing.Union`).
   - Use `collections.abc.AsyncGenerator` instead of `typing.AsyncGenerator`.
   - Use `datetime.UTC` for timezones.

2. **HTTP Server, Database ORM & Cache:**
   - Server: **Granian** (Rust-based ASGI server), **NOT** Uvicorn.
   - ORM: **SQLModel** with **asyncpg**.
   - Cache / Broker: **Redis** using `redis.asyncio` with C-extension `hiredis` and connection pooling (`redis_pool`).
   - Background Tasks / Workers: **Taskiq** (`taskiq-redis` + `taskiq-fastapi`) with dependency injection via `TaskiqDepends`.
   - Primary Keys: **UUIDv7** via `uuid6` (time-ordered, zero B-Tree index fragmentation).
   - Observability: **Prometheus** metrics at `/metrics` + structured logging via `structlog`.
   - Error Format: RFC 9457 unified error envelope (`ErrorResponse`).

---

## 🔄 Self-Correction & Verification Workflow

Before committing ANY code or finishing a coding task, execute this sequence:
1. `uv run --directory services/api ruff check --fix .`
2. `uv run --directory services/api ruff format .`
3. `uv run --directory services/api mypy app tests`
4. `uv run --directory services/api pytest`
*(Or simply: `make format && make check`)*

> [!CAUTION]
> If any test, linter, or typecheck error occurs, the agent **MUST fix it first**. Never commit failing code. Never use `--no-verify`.

---

## 📦 Git & Atomic Commits Protocol (Mandatory for AI Agents)

AI agents must adhere to the **Plan → Implement → Verify → Commit** cycle for every logical milestone. This creates atomic checkpoints, prevents technical debt, simplifies code reviews, and guarantees effortless rollbacks if an agent goes off track.

### 1. What Constitutes a "Logical Milestone" (Checkpoints):
When developing features, refactoring, or fixing bugs, create a separate commit at each of these discrete boundaries:
- **Milestone 1 — Database & Schema:**
  - Definition of entities in `models.py` + Alembic migration generated and verified.
  - *Example message:* `feat(items): add item model and initial alembic migration`
- **Milestone 2 — Domain Service & Business Logic:**
  - Pure business logic / CRUD in `service.py` + unit tests in `tests/test_<feature>.py`.
  - *Example message:* `feat(items): implement item service with pagination and business rules`
- **Milestone 3 — HTTP API & Endpoints:**
  - DTO schemas in `schemas.py`, FastAPI `router.py`, registered in `v1/router.py` + API tests.
  - *Example message:* `feat(items): add rest api endpoints for item management`
- **Milestone 4 — Background Tasks & Workers (if applicable):**
  - Taskiq background jobs in `tasks.py` + integration test.
  - *Example message:* `feat(items): add background task for item analysis`
- **Bugfixes:**
  - Reproducing test written + fix implemented + full suite green.
  - *Example message:* `fix(db): prevent dropping tables during postgres test runs`
- **Refactoring & Infra:**
  - Code restructuring, performance tuning, or CI/tooling updates without behavior break.
  - *Example message:* `refactor(core): extract redis sliding window rate limiter`

### 2. Pre-Commit Quality Gate (Strictly Enforced):
Before running `git commit`, the agent **MUST** run the verification suite:
```bash
make format && make check
# Equivalent to:
# uv run --directory services/api ruff check --fix .
# uv run --directory services/api ruff format .
# uv run --directory services/api mypy app tests
# uv run --directory services/api pytest
```
No commit is permitted unless all checks pass with exit code `0`.

### 3. Conventional Commit Format:
Format: `<type>(<scope>): <imperative summary>`

- **Types:**
  - `feat`: New feature or capability.
  - `fix`: Bug fix.
  - `refactor`: Code change that neither fixes a bug nor adds a feature.
  - `test`: Adding missing tests or correcting existing tests.
  - `docs`: Documentation or guideline updates.
  - `perf`: Performance improvements.
  - `chore`: Tooling, build config, package dependencies, migrations.
- **Scope:** Name of the domain module or architectural layer:
  - `items`, `health`, `users`, `billing`, `core`, `db`, `deps`, `docker`, `ci`, `infra`.
- **Description Rules:**
  - Use imperative, present tense ("add", "fix", "refactor" — not "added", "fixing", "fixes").
  - Lowercase, maximum 72 characters, no trailing period.
  - **No AI fluff:** NEVER write "AI generated", "Modified files per user prompt", or vague "wip" / "update".
  - Add body bullet points only if non-obvious architectural rationale or breaking changes need explanation.

### 4. Git Staging & Safety Rules:
1. **Always inspect before staging:** Run `git status` and `git diff --stat` to verify modified files.
2. **Targeted staging:** Stage files intentionally for the current milestone (e.g. `git add services/api/app/modules/<domain>/models.py services/api/alembic/versions/`).
3. **Never commit secrets or caches:** Ensure `.env`, credentials, `.pytest_cache`, and `__pycache__` are never staged.
4. **Never run `git push` autonomously:** Pushing to remote repositories must be explicitly initiated by the user.
5. **Never use `--no-verify` or rewrite published history:** Do not bypass hooks or force-push.

