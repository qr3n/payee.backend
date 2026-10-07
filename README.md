# 💳 Payee — Telegram Payment Orchestrator & Gateway

Высокопроизводительный асинхронный платёжный шлюз и оркестратор сценариев оплаты через Telegram MTProto (Starslly, HelperStars, StarShoppik) с автоматической генерацией платёжных ссылок, конвертацией в СБП (НСПК), реактивным подтверждением оплаты и исходящими вебхуками для мерчантов.

---

## ⚡ Ключевые возможности

- 🚀 **Мультисценарная генерация ссылок («Race»):** Одновременный параллельный запуск сценариев оплаты через разные Telegram-боты с моментальной доставкой первой готовой ссылки клиенту через Server-Sent Events (SSE).
- 🔗 **Автоматическое извлечение СБП (НСПК):** Автоматический резолвинг и нормализация банковских ссылок СБП из веб-эквайрингов (Cardlink, Antilopay).
- 🤖 **Реактивный MTProto Listener:** Автоматический перехват входящих сообщений об оплате от ботов (@StarShoppik_bot, @HelperStars_Robot, @starslly_bot) в реальном времени с переводом платежа в статус `PAID`.
- 📬 **Исходящие вебхуки (Outbound Callbacks):** Моментальная отправка HTTP POST уведомлений на `callback_url` мерчанта при подтверждении оплаты (с криптографической подписью HMAC-SHA256 и настраиваемыми ретраями).
- 🛡️ **Финансовая безопасность и строгая идемпотентность:** Предварительная регистрация операций в БД, двухуровневые распределённые блокировки в Redis, изолированные транзакции финализации и автоматический статус `RECONCILIATION_REQUIRED` при нестандартных сбоях.
- 📱 **Telegram Bot Management BFF:** Полнофункциональный бот-клиент для администраторов на базе `aiogram 3` и `aiogram-dialog` для мониторинга аккаунтов, проверки сессий и управления платежами.
- 🐳 **Production-Ready контейнеризация:** Готовый стек с Traefik v3 (автоматический Let's Encrypt SSL), PostgreSQL 17, Redis 7, Taskiq асинхронными воркерами и Granian (Rust ASGI сервер).

---

## 🏗️ Архитектура системы

Проект построен по принципам **Vertical Slice Architecture (VSA / Modular Monolith)** и **BFF (Backend for Frontend)**:

```text
payee/
├── infra/
│   └── traefik/                # Edge Reverse Proxy (TLS termination, Let's Encrypt, Rate Limiting)
├── services/
│   ├── api/                    # Основной бэкенд шлюза (FastAPI + SQLModel + Taskiq + Telethon)
│   │   ├── app/
│   │   │   ├── core/           # Конфигурация, БД, Redis, брокер Taskiq, логирование, лимиты
│   │   │   ├── shared/         # BaseUUIDModel (UUIDv7), пагинация, RFC 9457 ошибки, кэш
│   │   │   └── modules/        # Вертикальные слайсы (бизнес-домены):
│   │   │       ├── health/     # Healthchecks & Readiness probes (/health, /ready)
│   │   │       ├── accounts/   # Управление и мониторинг пула Telegram MTProto аккаунтов
│   │   │       └── payments/   # Платёжные сценарии, гонка ссылок, резолверы СБП, вебхуки
│   │   └── alembic/            # Асинхронные миграции PostgreSQL
│   └── bot/                    # Telegram-бот администрирования (aiogram 3 + aiogram-dialog)
├── docker-compose.prod.yml     # Продакшн-оркестрация (Traefik, Postgres, Redis, API, Worker, Bot)
├── docker-compose.yml          # Окружение разработки (hot-reload, volume mounts)
├── Makefile                    # Команды сборки, тестирования и форматирования
└── .env.example                # Шаблон конфигурации переменных окружения
```

---

## 🚀 Пошаговое руководство по деплою на сервере

### 1. Требования к серверу
- **ОС:** Ubuntu 22.04+ / Debian 12 / AlmaLinux 9.
- **Установленное ПО:** Docker Engine 24+ и Docker Compose v2.
- **Сеть:** Открытые входящие порты `80/TCP` и `443/TCP` (UFW / Security Groups).
- **DNS:** Привязанные A-записи доменов к публичному IP сервера:
  - `api.yourdomain.com` (для шлюза API).
  - `bot.yourdomain.com` (для вебхука Telegram-бота).

### 2. Клонирование репозитория
```bash
git clone https://github.com/qr3n/payee.backend.git /opt/payee
cd /opt/payee
```

### 3. Настройка окружения (`.env`)
Скопируйте пример конфигурации и задайте боевые секреты:
```bash
cp .env.example .env
nano .env
```

**Обязательные переменные для Production:**
```ini
# Домены для маршрутизации Traefik и сертификатов
API_HOST=api.yourdomain.com
BOT_HOST=bot.yourdomain.com
ACME_EMAIL=admin@yourdomain.com

# База данных PostgreSQL (ОБЯЗАТЕЛЬНО измените пароль!)
POSTGRES_SERVER=postgres
POSTGRES_USER=postgres
POSTGRES_PASSWORD=your_super_strong_postgres_password_here
POSTGRES_DB=payee_prod

# Безопасность API
ADMIN_API_KEY="your_random_admin_api_token"
API_KEY="your_random_admin_api_token"
ADMIN_CHAT_IDS="[123456789]"     # Telegram ID администраторов для оповещений

# Исходящие вебхуки мерчантам (HMAC-SHA256 подпись)
PAYMENT_WEBHOOK_SECRET="your_webhook_signing_secret"

# Telegram Bot (BFF)
TELEGRAM_BOT_TOKEN="1234567890:AA...токен_от_BotFather"
TELEGRAM_BOT_MODE=webhook
TELEGRAM_WEBHOOK_URL="https://bot.yourdomain.com/webhook"
TELEGRAM_WEBHOOK_SECRET="your_random_webhook_secret_token"
```

### 4. Запуск контейнеров
Запустите сборку и старт сервисов:
```bash
docker compose -f docker-compose.prod.yml up -d --build
```

### 5. Проверка статуса запуска
Убедитесь, что все контейнеры запущены и healthy:
```bash
docker compose -f docker-compose.prod.yml ps
```

Проверьте логи применения миграций и старта API:
```bash
docker compose -f docker-compose.prod.yml logs migration
docker compose -f docker-compose.prod.yml logs api
```

Проверьте readiness probe:
```bash
curl -f https://api.yourdomain.com/ready
# Ожидаемый ответ: {"status":"ready","database":true,"redis":true,"timestamp":"..."}
```

---

## 🔑 Первоначальная инициализация аккаунтов

На чистом сервере база данных пуста. Для того чтобы генерация платёжных ссылок работала, в системе должны быть зарегистрированы **активные рабочие Telegram MTProto аккаунты**.

### Способ 1: Через Telegram-бота администратора
1. Откройте вашего бота в Telegram и отправьте `/start`.
2. Перейдите в раздел **«Управление аккаунтами»**.
3. Нажмите **«Добавить аккаунт»** и следуйте инструкциям ввода номера телефона и кода подтверждения.

### Способ 2: Загрузка сессий через Admin API
Используйте эндпоинт `POST /api/v1/accounts/upload`:
```bash
curl -X POST https://api.yourdomain.com/api/v1/accounts/upload \
  -H "X-API-Key: your_random_admin_api_token" \
  -F "file=@session_archive.zip"
```

После добавления аккаунтов прогрев сценариев и фоновый мониторинг сессий запустятся автоматически.

---

## 📖 Сценарий интеграции: «Создание → Оплата → Callback»

### 1. Создание платежа (Одиночный сценарий)

**Запрос:** `POST /api/v1/payments/`
```bash
curl -X POST https://api.yourdomain.com/api/v1/payments/ \
  -H "X-API-Key: your_random_admin_api_token" \
  -H "Content-Type: application/json" \
  -d '{
    "client_user_id": "merchant_user_1001",
    "scenario_id": "helperstars_bot",
    "amount": "300.00",
    "currency": "RUB",
    "idempotency_key": "order_uuid_9999",
    "callback_url": "https://merchant.example.com/api/payment-callback",
    "meta": {
      "order_id": "INV-102938"
    }
  }'
```

**Ответ (201 Created):**
```json
{
  "id": "0192e210-2b10-7e10-91ab-6d9b01234567",
  "client_user_id": "merchant_user_1001",
  "scenario_id": "helperstars_bot",
  "amount": "300.00",
  "currency": "RUB",
  "status": "pending",
  "payment_link": "https://qr.nspk.ru/...",
  "callback_url": "https://merchant.example.com/api/payment-callback",
  "expires_at": "2026-10-07T14:30:00Z",
  "paid_at": null,
  "cancelled_at": null,
  "meta": {
    "is_sbp_resolved": true,
    "generation_time_sec": 3.42
  }
}
```

---

### 2. Гонка сценариев («Race») со стримингом через SSE

Для максимальной скорости генерации ссылок можно запустить параллельную гонку по всем активным сценариям:

**Запуск:** `POST /api/v1/payments/race`
```bash
curl -X POST https://api.yourdomain.com/api/v1/payments/race \
  -H "X-API-Key: your_random_admin_api_token" \
  -H "Content-Type: application/json" \
  -d '{
    "client_user_id": "merchant_user_1001",
    "amount": "300.00",
    "currency": "RUB",
    "idempotency_key": "race_order_123",
    "callback_url": "https://merchant.example.com/api/payment-callback"
  }'
```

**Ответ:** Возвращает `batch_id`. Клиент подключается к стриму:
```bash
curl -N https://api.yourdomain.com/api/v1/payments/race/{batch_id}/stream
```
Первая готовая ссылка отдаётся клиенту мгновенно по мере генерации в фоне.

---

### 3. Исходящий Callback мерчанту (Webhook)

Когда покупатель оплачивает счёт, бот присылает подтверждение. Шлюз перехватывает сообщение, переводит статус в `PAID` и отправляет HTTP POST запрос на ваш `callback_url`:

**Заголовки запроса:**
```http
POST /api/payment-callback HTTP/1.1
Host: merchant.example.com
Content-Type: application/json
User-Agent: Payee-Webhook/0.1.0
X-Payee-Timestamp: 1791388800
X-Payee-Signature: 8f4a13c9a633... (HMAC-SHA256)
```

**Тело запроса (JSON):**
```json
{
  "event": "payment.status_changed",
  "payment_id": "0192e210-2b10-7e10-91ab-6d9b01234567",
  "client_user_id": "merchant_user_1001",
  "scenario_id": "helperstars_bot",
  "amount": "300.00",
  "currency": "RUB",
  "status": "paid",
  "idempotency_key": "order_uuid_9999",
  "batch_id": null,
  "payment_link": "https://qr.nspk.ru/...",
  "created_at": "2026-10-07T14:00:00Z",
  "paid_at": "2026-10-07T14:02:15Z",
  "cancelled_at": null,
  "external_transaction_id": "232384",
  "meta": {
    "order_id": "232384"
  }
}
```

#### Проверка подписи на стороне мерчанта (Python пример):
```python
import hashlib, hmac

def verify_payee_webhook(body_bytes: bytes, timestamp: str, signature: str, secret: str) -> bool:
    expected = hmac.new(
        secret.encode("utf-8"),
        f"{timestamp}.{body_bytes.decode('utf-8')}".encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    return hmac.compare_digest(expected, signature)
```

---

### 4. Ручной повтор вебхука (Resend)

Если сервер мерчанта временно не отвечал:
```bash
curl -X POST https://api.yourdomain.com/api/v1/payments/{payment_id}/webhook/resend \
  -H "X-API-Key: your_random_admin_api_token"
```

---

## 🛠️ Эксплуатация и обслуживание

### Регулярные бэкапы базы данных
Создайте скрипт резервного копирования в `crontab`:
```bash
# Ежедневный бэкап в 03:00 ночи
0 3 * * * docker compose -f /opt/payee/docker-compose.prod.yml exec -T postgres pg_dump -U postgres app | gzip > /opt/backups/payee_$(date +\%F).sql.gz
```

### Мониторинг и метрики
- **Prometheus метрики:** `GET https://api.yourdomain.com/metrics`
- **Readiness probe:** `GET https://api.yourdomain.com/ready`
- **Liveness probe:** `GET https://api.yourdomain.com/health`

### Локальная разработка и тестирование
```bash
# Запуск локального окружения
make dev

# Запуск тестов API и бота
make test

# Проверка стилей и линтеров
make check

# Применение миграций
make migrate
```

---

## 🔒 Безопасность в Production

1. **База данных и Redis изолированы:** Порты `5432` и `6379` не проброшены во внешнюю сеть хоста (закрыты в сети `gateway`).
2. **Непривилегированные пользователи:** Контейнеры запускаются под пользователем `appuser` (UID 10001).
3. **Ротация логов:** Для всех контейнеров в `docker-compose.prod.yml` установлен лимит `max-size: 50m`, `max-file: 3` для предотвращения переполнения диска.
4. **Rate Limiting:** Traefik настроен на ограничение аномальной частоты запросов к API.
