# FastAPI Backend Template

Современный production-ready шаблон backend-микросервиса на базе **FastAPI**, ориентированный на максимальную производительность, микросервисную архитектуру и контейнеризацию.

## 🚀 Стек технологий

- **Фреймворк:** [FastAPI](https://fastapi.tiangolo.com/) (с нативной сериализацией Pydantic v2 в Rust)
- **HTTP-сервер:** [Granian](https://github.com/emmett-framework/granian) — высокопроизводительный HTTP-сервер для Python, написанный на Rust
- **Event Loop:** [uvloop](https://github.com/MagicStack/uvloop) — быстрый асинхронный цикл событий на базе libuv
- **База данных:** [PostgreSQL 17](https://www.postgresql.org/) (с healthcheck и персистентными томами)
- **ORM:** [SQLModel](https://sqlmodel.tiangolo.com/) (SQLAlchemy 2.0 + Pydantic v2) с асинхронным драйвером [asyncpg](https://github.com/MagicStack/asyncpg)
- **Кэш & In-memory хранилище:** [Redis 7](https://redis.io/) с официальным клиентом `redis.asyncio`, C-парсером [hiredis](https://github.com/redis/hiredis-py) и встроенным Connection Pool
- **Миграции БД:** [Alembic](https://alembic.sqlalchemy.org/) (асинхронная автогенерация миграций)
- **Валидация и конфиг:** [Pydantic v2](https://docs.pydantic.dev/) & [pydantic-settings](https://docs.pydantic.dev/latest/concepts/pydantic_settings/)
- **Telegram Bot:** [aiogram 3](https://docs.aiogram.dev/) & [aiogram-dialog](https://aiogram-dialog.readthedocs.io/) — современный декларативный бот-фронтенд (BFF-паттерн, FSM в Redis, Polling в Dev, Webhook через Traefik в Prod)
- **Управление зависимостями:** [uv](https://docs.astral.sh/uv/) — сверхбыстрый пакетный менеджер нового поколения на Rust (заменяет Poetry/pip)
- **Качество кода и тесты:** [pytest](https://docs.pytest.org/), [httpx](https://www.python-httpx.org/), [fakeredis](https://github.com/cunla/fakeredis-py), [Ruff](https://docs.astral.sh/ruff/) (линтер и форматтер), [Mypy](https://mypy-lang.org/)
- **Контейнеризация:** Docker (Multi-stage build с кэшированием uv) & Docker Compose (dev и prod профили)
- **CI/CD:** [GitHub Actions](https://github.com/features/actions) & [GitLab CI/CD](https://docs.gitlab.com/ee/ci/) (линтеры, Mypy, тесты, валидация Docker сборок)

---

## 📁 Структура проекта

Структура следует принципам **микросервисной архитектуры** и **Separation of Concerns (SoC)**: все микросервисы располагаются в директории `services/`, каждый сервис изолирован, имеет собственный `pyproject.toml`, зависимости `uv.lock` и `Dockerfile`. В корне проекта находятся конфигурации оркестрации (`docker-compose.yml`, `docker-compose.prod.yml`).

```text
fastapi-backend-template/
├── Makefile                    # Удобные шорткаты для разработки, тестов, миграций и Docker
├── .github/
│   └── workflows/
│       └── ci.yml              # CI пайплайн для GitHub Actions
├── .gitlab-ci.yml              # CI пайплайн для GitLab CI/CD
├── docker-compose.yml          # Оркестрация для разработки (hot-reload, volume mount)
├── docker-compose.prod.yml     # Оркестрация для production (multi-worker, no root, restart: always)
├── .env.example                # Пример переменных окружения
├── .gitignore                  # Исключения версионного контроля
├── README.md                   # Документация проекта
├── AGENTS.md                   # Правила архитектуры для ИИ-агентов
├── infra/
│   └── traefik/                # Edge Reverse Proxy (Traefik v3)
│       ├── traefik.yml         # Конфигурация для локальной разработки (порт 80, дашборд 8080)
│       ├── traefik.prod.yml    # Конфигурация для production (HTTPS / Let's Encrypt ACME)
│       └── dynamic/
│           └── middlewares.yml # Динамические middleware (security headers, gzip compression)
└── services/
    └── api/                    # Микросервис API (FastAPI)
        ├── Dockerfile          # Многоэтапный Dockerfile (base -> builder -> dev / prod с uv)
        ├── .dockerignore       # Исключения для сборки контейнера
        ├── pyproject.toml      # Зависимости и конфигурация сервиса (uv / PEP 621)
        ├── uv.lock             # Зафиксированные версии пакетов (uv)
        ├── alembic.ini         # Конфигурация миграций Alembic
        ├── alembic/            # Директория миграций базы данных
        │   ├── env.py          # Асинхронный запуск миграций (SQLModel metadata)
        │   └── versions/       # Файлы версий миграций
        ├── tests/              # Набор тестов (pytest + httpx + SQLite in-memory)
        │   ├── __init__.py
        │   ├── conftest.py     # Фикстуры pytest (AsyncClient, in-memory DB & fake_redis)
        │   ├── test_health.py  # Тесты эндпоинтов здоровья и readiness probe
        │   ├── test_items.py   # Тесты CRUD операций над Items
        │   ├── test_middleware_and_errors.py # Тесты Request-ID, ошибок и пагинации
        │   └── test_redis.py   # Тесты операций Redis и CacheService
        └── app/                # Исходный код приложения (Vertical Slice Architecture)
            ├── __init__.py
            ├── main.py         # Точка входа FastAPI, CORS, middleware, lifespan, /metrics
            ├── core/           # Инфраструктура и кросс-функциональные компоненты
            │   ├── __init__.py
            │   ├── config.py   # Конфигурация через pydantic-settings & DSN
            │   ├── db.py       # AsyncEngine и AsyncSessionMaker
            │   ├── redis.py    # Redis ConnectionPool, клиент и хелперы
            │   ├── broker.py   # Taskiq broker & correlation tracing middleware
            │   ├── logging.py  # Структурированное логирование (structlog)
            │   ├── middleware.py # RequestIDMiddleware (Correlation ID, latency, access logs)
            │   ├── rate_limit.py # Redis sliding-window RateLimiter dependency
            │   ├── exceptions.py # Базовые исключения (AppException, NotFound, RateLimit)
            │   └── exception_handlers.py # Обработка ошибок (RFC 9457 Problem Details)
            ├── shared/         # Общие примитивы и переиспользуемые строительные блоки
            │   ├── __init__.py
            │   ├── models.py   # BaseUUIDModel (UUIDv7, UTC timestamps)
            │   ├── pagination.py # Generic-схемы PaginatedResponse[T], CursorParams
            │   ├── errors.py   # Схемы ErrorDetail и ErrorResponse (RFC 9457)
            │   └── cache.py    # Сервис кэширования через Redis с orjson (CacheService)
            ├── modules/        # Вертикальные слайсы (Feature / Domain Modules)
            │   ├── __init__.py # Реестр доменных моделей (для автогенерации Alembic)
            │   ├── health/     # Срез мониторинга здоровья и readiness probe
            │   │   ├── __init__.py
            │   │   ├── router.py # /health и /ready
            │   │   └── schemas.py # HealthCheckResponse, ReadinessResponse
            │   └── items/      # Доменный срез сущности Items
            │       ├── __init__.py
            │       ├── router.py  # Эндпоинты FastAPI (/api/v1/items/)
            │       ├── models.py  # SQLModel сущности БД
            │       ├── schemas.py # Pydantic DTO (ItemCreate, ItemUpdate, ItemRead)
            │       ├── service.py # Бизнес-логика (Unit of Work, session.flush)
            │       └── tasks.py   # Асинхронные задачи Taskiq
            └── api/
                ├── __init__.py
                ├── deps.py     # FastAPI Depends провайдеры (get_db с commit, get_redis)
                └── v1/
                    ├── __init__.py
                    └── router.py # Агрегатор роутеров доменных модулей
    └── bot/                    # 🤖 Микросервис Telegram-бота (aiogram 3 + aiogram-dialog)
        ├── Dockerfile          # Многоэтапный Dockerfile (base -> builder -> dev / prod с uv)
        ├── .dockerignore       # Исключения для сборки контейнера
        ├── pyproject.toml      # Зависимости и конфигурация сервиса (uv / PEP 621)
        ├── uv.lock             # Зафиксированные версии пакетов (uv)
        ├── scripts/
        │   └── entrypoint.sh   # Скрипт точки входа контейнера
        ├── tests/              # Набор тестов (pytest + mock client + fakedialogs)
        │   ├── __init__.py
        │   ├── conftest.py     # Фикстуры
        │   ├── test_client.py  # Тесты типизированного HTTP-клиента к API
        │   ├── test_dialogs.py # Тесты геттеров и стейтов aiogram-dialog
        │   ├── test_handlers.py# Тесты команд (/start, /menu, /help)
        │   └── test_webhook.py # Тесты вебхук-сервера и healthcheck
        └── bot/                # Исходный код бота
            ├── __init__.py
            ├── main.py         # Единая точка входа (Polling в Dev / Webhook в Prod)
            ├── core/           # Конфигурация (pydantic-settings), логирование, RedisStorage
            ├── client/         # Асинхронный HTTP-клиент (httpx) к FastAPI
            ├── dialogs/        # Интерактивные меню и формы aiogram-dialog
            ├── handlers/       # Обработчики команд Telegram
            ├── middlewares/    # Внедрение ApiClientMiddleware и LoggingMiddleware
            └── webhook/        # Webhook-сервер aiohttp для production
```

---

## ⚡ Быстрые команды (Makefile)

В корне репозитория настроен `Makefile` для ускорения повседневной разработки:

| Команда | Описание |
|---|---|
| `make help` | Справка по всем доступным командам |
| `make dev` | Запуск локального сервера API Granian (`--reload`) |
| `make worker` | Запуск фонового воркера Taskiq (`--reload`) |
| `make bot-dev` | Запуск локального Telegram-бота в режиме polling |
| `make test` | Запуск тестов для всех сервисов (`test-api` + `test-bot`) |
| `make lint` | Проверка линтером Ruff и типами Mypy для всех сервисов |
| `make format` | Форматирование кода и автоисправление во всех сервисах |
| `make check` | Полная проверка качества кода (`lint` + `test`) для всех сервисов |
| `make migrate` | Применение миграций базы данных (`alembic upgrade head`) |
| `make migration m="msg"` | Создание новой автогенерируемой миграции Alembic |
| `make downgrade` | Откат базы данных на 1 ревизию назад |
| `make up` | Запуск всех сервисов в фоне через Docker Compose |
| `make down` | Остановка всех контейнеров Docker Compose |
| `make logs` | Просмотр логов всех сервисов в реальном времени |
| `make build` | Пересборка Docker-контейнеров |
| `make clean` | Очистка временных файлов кэша (`__pycache__`, `.pytest_cache`) |

---

## 🛠️ Запуск через Docker Compose

### 1. Локальная разработка (Traefik + API)

```bash
docker compose up --build
```

Благодаря **Traefik v3** трафик маршрутизируется по доменам (RFC 6761: `*.localhost` резолвятся локально автоматически без изменения `/etc/hosts`):

| Сервис | URL | Описание |
|---|---|---|
| **Backend API (Docs)** | [http://api.localhost/docs](http://api.localhost/docs) | Swagger UI документация |
| **Backend API (Health)** | [http://api.localhost/health](http://api.localhost/health) | Проверка здоровья API |
| **Traefik Dashboard** | [http://localhost:8080](http://localhost:8080) | Дашборд роутера Traefik в реальном времени |
| **API Direct Port** | [http://localhost:8000/docs](http://localhost:8000/docs) | Прямой доступ к API в обход прокси (для отладки) |

---

## 🌐 Подключение внешнего фронтенда (Multi-repo)

Фронтенд разрабатывается в отдельном репозитории и подключается к Traefik через общую Docker-сеть **`gateway`**.

> [!TIP]
> Полная документация по интеграции для фронтенд-разработчика или AI-агента, форматы контракта и примеры генерации TypeScript-типов описаны в файле [**`FRONTEND_INTEGRATION.md`**](file:///home/qr3n/PycharmProjects/fastapi-backend-template/FRONTEND_INTEGRATION.md).
> Для экспорта машиночитаемой спецификации API используйте команду `make openapi` (создаёт [`openapi.json`](file:///home/qr3n/PycharmProjects/fastapi-backend-template/openapi.json)).

### Вариант 1: Запуск фронтенда в Docker (из своего репозитория)
В `docker-compose.yml` репозитория фронтенда достаточно указать сеть `gateway` как внешнюю:

```yaml
services:
  frontend:
    build: .
    labels:
      - "traefik.enable=true"
      - "traefik.http.routers.frontend.rule=Host(`localhost`) || Host(`app.localhost`)"
      - "traefik.http.routers.frontend.entrypoints=web"
      - "traefik.http.services.frontend.loadbalancer.server.port=3000" # Порт вашего приложения
    networks:
      - gateway

networks:
  gateway:
    external: true
```

Traefik автоматически обнаружит запущенный контейнер фронтенда и направит на него запросы с `http://localhost` и `http://app.localhost`.

### Вариант 2: Локальная разработка без Docker (Vite / Next.js / Nuxt)
Если фронтенд запускается локально командой `npm run dev` на порту 3000/5173, он отправляет запросы напрямую к API по адресу `http://api.localhost`. В бэкенде уже настроен CORS для `http://localhost` и `http://app.localhost`.

### 2. Запуск в режиме Production

```bash
docker compose -f docker-compose.prod.yml up --build -d
```

- Traefik слушает порты 80 и 443, автоматически запрашивает бесплатные SSL-сертификаты **Let's Encrypt** через ACME HTTP-challenge.
- Настроен автоматический редирект с HTTP на HTTPS.
- Включены заголовки безопасности (HSTS, XSS Protection, Frame Options) и gzip-сжатие.
- Приложение API запускается из-под непривилегированного пользователя `appuser` (UID 10001).
- Granian запущен в многопроцессном режиме (`--workers 4`).

---

## 🤖 Микросервис Telegram-бота (`services/bot`)

В проект интегрирован production-ready шаблон Telegram-бота на базе **aiogram 3** и **aiogram-dialog**.

### 🏛️ Архитектурные принципы:
1. **API как единый источник правды (Single Source of Truth, SSOT):**
   - Бот **не обращается к базе данных PostgreSQL напрямую** и не дублирует доменные модели и миграции.
   - Все операции с данными (получение списка элементов, просмотр деталей, создание новых записей, запуск аналитических задач Taskiq) бот выполняет через асинхронный типизированный HTTP-клиент (`bot.client.ApiClient` на базе `httpx`) к FastAPI бэкенду (`API_BASE_URL`).
   - Это гарантирует, что бизнес-логика, валидация, аутентификация и аудит централизованы в одном месте.
2. **Изоляция хранилища Redis:**
   - **Для бота:** Redis используется **исключительно** для персистентности FSM-состояний и стеков окон диалогов (`RedisStorage` с изолированным индексом БД `REDIS_FSM_DB=1` и префиксом ключей `fsm:`).
   - **Для API:** Бизнес-кэш (`CacheService`) и очереди сообщений Taskiq живут в `REDIS_DB=0`.
3. **Два режима работы (Dev / Prod):**
   - **Development (`TELEGRAM_BOT_MODE=polling`):** Бот работает через long-polling. Не требуется белый IP-адрес, валидный SSL-сертификат или туннелирование через ngrok. Запуск: `make bot-dev` или через Docker Compose (`docker compose up bot`).
   - **Production (`TELEGRAM_BOT_MODE=webhook`):** Бот разворачивается как `aiohttp` веб-сервер за Traefik v3. Traefik обеспечивает SSL-терминацию (HTTPS Let's Encrypt), а запросы от Telegram защищены секретным токеном (`X-Telegram-Bot-Api-Secret-Token`). Сервис предоставляет эндпоинт `/health` для Docker/Traefik healthcheck.
4. **Реактивные диалоги (aiogram-dialog):**
   - Декларативное управление окнами интерфейса: каталог элементов с навигацией и постраничным скроллингом (`ScrollingGroup`), форма пошагового создания элемента (`TextInput`), карточка элемента и запуск фонового воркера Taskiq прямо из Telegram.

### 🚀 Запуск бота:
```bash
# 1. Задайте токен от @BotFather в .env
TELEGRAM_BOT_TOKEN="1234567890:your_actual_token"

# 2. Локальный запуск в режиме Polling (с хот-релоадом API):
make bot-dev

# 3. Или запуск всех сервисов в Docker (Dev):
make up
```

---

## 💻 Локальный запуск без Docker

### Требования:
- Python 3.12+
- [uv](https://docs.astral.sh/uv/)

```bash
# Перейдите в папку сервиса
cd services/api

# Установите зависимости (создаст .venv за доли секунды)
uv sync

# Запустите Granian сервер
uv run granian --interface asgi --loop uvloop --host 0.0.0.0 --port 8000 --reload app.main:app
```

### Добавление новых зависимостей:
```bash
# В директории services/api:
uv add httpx
```

### Тестирование и проверка качества кода:
```bash
# Запуск асинхронных тестов:
uv run pytest

# Проверка линтером и автоисправление:
uv run ruff check --fix .
uv run ruff format .

# Проверка статической типизации:
uv run mypy app tests
```

### 🗄️ Миграции базы данных (Alembic):
```bash
# Создание новой миграции на основе изменений моделей SQLModel:
uv run alembic revision --autogenerate -m "create_users_table"

# Применение миграций до актуальной версии:
uv run alembic upgrade head

# Откат последней миграции:
uv run alembic downgrade -1
```

---

## 📦 REST API CRUD эндпоинты

В шаблоне реализован полный production-grade CRUD пример на базе **SQLModel** и асинхронного PostgreSQL:

| Метод | Путь | Описание |
|---|---|---|
| `POST` | `/api/v1/items/` | Создание нового объекта (возвращает `201 Created` с UUID) |
| `GET` | `/api/v1/items/` | Постраничный список объектов (`?page=1&size=20`) -> `PaginatedResponse` |
| `GET` | `/api/v1/items/{id}` | Получение объекта по UUID (`404` с `ErrorResponse` если не найден) |
| `PATCH` | `/api/v1/items/{id}` | Частичное обновление полей объекта |
| `DELETE` | `/api/v1/items/{id}` | Удаление объекта (`204 No Content`) |

---

## 🛡️ Correlation ID (`X-Request-ID`) и единый формат ошибок

Каждый HTTP-запрос проходит через высокопроизводительный pure ASGI middleware [`RequestIDMiddleware`](file:///home/qr3n/PycharmProjects/fastapi-backend-template/services/api/app/core/middleware.py):
- Читает входящий заголовок `X-Request-ID` от клиента / Traefik или генерирует новый UUIDv4.
- Сохраняет его в асинхронный контекст `ContextVar` (функция `get_request_id()`) и добавляет в заголовки ответа.

Все ошибки приложения (ошибки валидации Pydantic 422, HTTP-исключения, доменные `AppException` и непредвиденные 500) форматируются через единые централизованные обработчики:

```json
{
  "error": {
    "code": "ITEM_NOT_FOUND",
    "message": "Item not found",
    "details": null,
    "request_id": "c1a2b3c4-d5e6-4f7a-8b9c-0d1e2f3a4b5c"
  }
}
```

---

## 📄 Универсальная пагинация (Generic Pagination)

Для любых коллекций в шаблоне реализован дженерик-класс [`PaginatedResponse[T]`](file:///home/qr3n/PycharmProjects/fastapi-backend-template/services/api/app/schemas/pagination.py) и параметры [`PageParams`](file:///home/qr3n/PycharmProjects/fastapi-backend-template/services/api/app/schemas/pagination.py) (`page`, `size`):

```json
{
  "items": [
    {
      "id": "c1a2b3c4-...",
      "title": "Item 1",
      "is_active": true,
      "created_at": "2026-09-23T20:50:00Z",
      "updated_at": "2026-09-23T20:50:00Z"
    }
  ],
  "total": 42,
  "page": 1,
  "size": 20,
  "pages": 3
}
```

---

## 🩺 Эндпоинты Health & Readiness Check

В шаблоне реализованы эндпоинты для мониторинга и оркестрации:

1. **Root Liveness Probe:** `GET /health`
   Быстрая проверка жизнеспособности процесса для балансировщиков нагрузки (Traefik, ALB, Kubernetes liveness probe).

2. **API v1 Liveness Probe:** `GET /api/v1/health`
   Версионированный эндпоинт проверки статуса приложения.

3. **Readiness Probe:** `GET /api/v1/ready`
   Глубокая проверка готовности сервиса к обработке трафика: выполняет пинг в **PostgreSQL** (`SELECT 1`) и **Redis** (`PING`).
   - Возвращает `200 OK`, если база данных и Redis доступны.
   - Возвращает `503 Service Unavailable`, если хотя бы один из сервисов недоступен.

### Пример ответа `/api/v1/ready`:
```json
{
  "status": "ready",
  "database": true,
  "redis": true,
  "timestamp": "2026-09-23T20:28:00.000000Z"
}
```

---

## ⚙️ Переменные окружения

Скопируйте пример:
```bash
cp .env.example .env
```

| Переменная | По умолчанию | Описание |
|---|---|---|
| `PROJECT_NAME` | `FastAPI Service` | Название сервиса |
| `VERSION` | `0.1.0` | Версия сервиса |
| `ENVIRONMENT` | `development` | Окружение (`development` / `production`) |
| `DEBUG` | `true` | Включение отладки и OpenAPI Docs (`/docs`) |
| `PORT` | `8000` | Внутренний порт приложения |
| `HOST` | `0.0.0.0` | Адрес прослушивания |
| `WORKERS` | `1` (dev) / `4` (prod) | Количество воркеров Granian |
| `POSTGRES_SERVER` | `postgres` | Хост PostgreSQL сервера |
| `POSTGRES_PORT` | `5432` | Порт PostgreSQL |
| `POSTGRES_USER` | `postgres` | Пользователь базы данных |
| `POSTGRES_PASSWORD` | `postgres` | Пароль базы данных |
| `POSTGRES_DB` | `app` | Имя базы данных |
| `DB_POOL_SIZE` | `10` | Размер пула постоянных соединений PostgreSQL |
| `DB_MAX_OVERFLOW` | `20` | Максимальное число временных соединений сверх пула |
| `REDIS_HOST` | `redis` | Хост сервера Redis |
| `REDIS_PORT` | `6379` | Порт сервера Redis |
| `REDIS_PASSWORD` | *(пусто)* | Пароль для доступа к Redis |
| `REDIS_DB` | `0` | Номер базы данных Redis |
| `REDIS_MAX_CONNECTIONS` | `20` | Максимальный размер пула соединений Redis |
| `API_HOST` | `api.localhost` | Домен бэкенда для Traefik (в проде: `api.domain.com`) |
| `FRONTEND_HOST` | `localhost` | Домен фронтенда для Traefik (в проде: `domain.com`) |
| `TRAEFIK_DASHBOARD_PORT`| `8080` | Порт веб-интерфейса Traefik |
| `ACME_EMAIL` | `admin@example.com` | Email для выпуска сертификатов Let's Encrypt |
| `BACKEND_CORS_ORIGINS` | `["http://localhost", ...]` | Разрешенные источники CORS |
