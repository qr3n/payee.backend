# DeepSeek Stateful Wrapper

Неофициальный локальный OpenAI-совместимый proxy для web-чата DeepSeek.

В отличие от исходного `smkttl/deepseek-api`, обёртка сохраняет один объект
`DeepSeekChat` для каждого `conversation_id`. Последовательные сообщения
продолжают один настоящий чат DeepSeek и используют его серверный контекст.

## Возможности

- многоходовый контекст через `conversation_id`;
- один чат в web-интерфейсе DeepSeek вместо нового чата на каждый запрос;
- исправленный парсер первого фрагмента ответа;
- актуализированные browser-заголовки;
- повтор временной ошибки `MISSING_HEADER`;
- интерактивный консольный клиент;
- OpenAI-подобный endpoint `/v1/chat/completions`.
- загрузка изображений и документов с привязкой к диалогу;
- интернет-поиск с источниками в поле `citations`.

## Предупреждение

Проект использует неофициальный web API и браузерную сессию DeepSeek. Это может
нарушать правила сервиса и привести к завершению или блокировке аккаунта.
Используйте только локально и на свой риск. Proxy не имеет входящей авторизации
и по умолчанию слушает только `127.0.0.1`.

## Требования

- Docker Desktop или Docker Engine с Compose;
- Python 3 для интерактивного клиента;
- авторизованный аккаунт на `https://chat.deepseek.com`.

## Получение токенов

1. Войдите на `https://chat.deepseek.com`.
2. Откройте DevTools → Network.
3. Отправьте любое сообщение.
4. Найдите POST-запрос `completion`.
5. Скопируйте:
   - cookie `ds_session_id`;
   - заголовок `authorization` целиком, включая префикс `Bearer `.

Не публикуйте эти значения: они предоставляют доступ к вашей web-сессии.

## Запуск

```bash
cp .env.example .env
```

Заполните `.env`:

```dotenv
DS_SESSION_ID=ваше_значение
AUTHORIZATION_TOKEN="Bearer ваш_токен"
```

Соберите и запустите proxy:

```bash
make up
make ps
make smoke
```

По умолчанию API доступен только локально:

```text
http://127.0.0.1:28080/v1
```

Если порт `28080` уже занят другим тестовым проектом, запустите proxy на другом
порту:

```bash
PROXY_PORT=28081 make up
PROXY_PORT=28081 make chat
```

## Интерактивный диалог с памятью

```bash
make chat
```

Команды клиента:

- `/reset` — удалить текущий контекст и начать новый web-чат;
- `/file "/полный/путь/к/фото.png"` — загрузить файл и прикрепить его к следующему сообщению;
- `/files` — показать загруженные в текущий разговор файлы;
- `/search on` и `/search off` — включить или выключить интернет-поиск;
- `/exit` — выйти;
- обычный текст — отправить следующее сообщение в текущий чат.

Путь к существующему локальному файлу можно также вставить отдельной строкой:

```text
Вы: '/Users/alexsho/Desktop/Снимок экрана.png'
Файл загружен: upload.png [VISION], id=file-...
Файл будет прикреплён к следующему сообщению.
Вы: Что изображено на скриншоте?
```

Или загрузить файл и сразу задать вопрос одной командой:

```bash
python3 deepseek_chat.py \
  --conversation-id manual \
  --file "/Users/alexsho/Desktop/photo.png" \
  "Что изображено на фотографии?"
```

Запрос с включённым интернет-поиском:

```bash
python3 deepseek_chat.py --search "Какие сегодня главные новости?"
```

Создать отдельный разговор:

```bash
CONVERSATION_ID=work make chat
```

Если используете нестандартный порт:

```bash
PROXY_PORT=28081 CONVERSATION_ID=work make chat
```

Пока proxy не перезапущен, повторный запуск с тем же `conversation_id`
продолжает тот же чат:

```bash
python3 deepseek_chat.py --conversation-id work "Запомни слово велосипед"
python3 deepseek_chat.py --conversation-id work "Какое слово я назвал?"
```

## Как работать с контекстом, поиском и файлами

### Контекст диалога

Контекст держится по `conversation_id`. Пока контейнер работает, все сообщения с
одинаковым `conversation_id` идут в один web-чат DeepSeek:

```bash
python3 deepseek_chat.py --conversation-id client-1 "Меня зовут Миша"
python3 deepseek_chat.py --conversation-id client-1 "Как меня зовут?"
```

Чтобы начать чистый диалог:

```text
Вы: /reset
```

или через HTTP:

```bash
curl -X DELETE http://127.0.0.1:28080/v1/conversations/client-1
```

Важный нюанс: если вы переключаетесь между обычным/search-режимом и картинками
(`VISION`), wrapper может создать новую внутреннюю web-сессию DeepSeek. Это
нужно, чтобы DeepSeek не зависал в старом режиме. Внешний `conversation_id`
остаётся тем же, но часть серверного контекста DeepSeek после такого
переключения может не сохраниться.

### Интернет-поиск

В интерактивном клиенте поиск включается и выключается командой:

```text
Вы: /search on
Интернет-поиск включён.
Вы: найди погоду на эту неделю в Санкт-Петербурге

Вы: /search off
Интернет-поиск выключен.
```

One-shot запрос:

```bash
python3 deepseek_chat.py \
  --search \
  "Найди в интернете погоду на эту неделю в Санкт-Петербурге"
```

HTTP API:

```bash
curl http://127.0.0.1:28080/v1/chat/completions \
  -H 'Content-Type: application/json' \
  -d '{
    "model": "deepseek-v3",
    "conversation_id": "weather",
    "search_enabled": true,
    "messages": [{"role": "user", "content": "Погода в Санкт-Петербурге на неделю"}]
  }'
```

Если поиск сработал, в ответе будет `search_enabled: true`, а найденные источники
попадут в поле `citations`.

### Файлы и фотографии

Файл загружается отдельно и прикрепляется только к следующему сообщению. Это
сделано специально: старый скриншот не должен мешать следующему обычному
сообщению или интернет-поиску.

Интерактивно:

```text
Вы: /file "/Users/alexsho/Desktop/photo.png"
Файл загружен: photo.png [VISION], id=file-...
Файл будет прикреплён к следующему сообщению.
Вы: Что изображено на фото?
```

Можно просто вставить путь отдельной строкой — клиент сам поймёт, что это файл:

```text
Вы: "/Users/alexsho/Desktop/photo.png"
Вы: Что изображено на фото?
```

One-shot:

```bash
python3 deepseek_chat.py \
  --file "/Users/alexsho/Desktop/photo.png" \
  "Что изображено на фото?"
```

Через HTTP API сначала загрузите файл:

```bash
curl -X POST \
  'http://127.0.0.1:28080/v1/files?conversation_id=demo' \
  -F 'file=@/Users/alexsho/Desktop/photo.png'
```

Затем передайте полученный `id` в `file_ids`:

```bash
curl http://127.0.0.1:28080/v1/chat/completions \
  -H 'Content-Type: application/json' \
  -d '{
    "model": "deepseek-v3",
    "conversation_id": "demo",
    "file_ids": ["file-..."],
    "messages": [{"role": "user", "content": "Что изображено на фото?"}]
  }'
```

Посмотреть загруженные файлы в текущем `conversation_id`:

```text
Вы: /files
```

или через HTTP:

```bash
curl http://127.0.0.1:28080/v1/conversations/demo/files
```

### Как не смешивать поиск и старые файлы

После `/file` следующий текстовый запрос уйдёт вместе с файлом. После ответа
клиент очищает список ожидающих файлов. Поэтому такой сценарий работает
корректно:

```text
Вы: /file "/Users/alexsho/Desktop/screen.png"
Вы: Что на скриншоте?
Вы: /search on
Вы: найди погоду на эту неделю в Санкт-Петербурге
```

Если нужно снова спросить именно по этому файлу, прикрепите его ещё раз через
`/file` или передайте его `file_id` через HTTP `file_ids`.

## HTTP API

Новый разговор с заданным идентификатором:

```bash
curl http://127.0.0.1:28080/v1/chat/completions \
  -H 'Content-Type: application/json' \
  -d '{
    "model": "deepseek-v3",
    "conversation_id": "demo",
    "messages": [{"role": "user", "content": "Меня зовут Миша"}]
  }'
```

Продолжение разговора — повторите запрос с `conversation_id: "demo"`.

Загрузка файла через HTTP API:

```bash
curl -X POST \
  'http://127.0.0.1:28080/v1/files?conversation_id=demo' \
  -F 'file=@/Users/alexsho/Desktop/photo.png'
```

После успешной загрузки передайте нужные `file_ids` в следующем запросе.
Это важно: файлы не прикрепляются ко всем последующим сообщениям автоматически,
чтобы старый скриншот не мешал обычному интернет-поиску:

```bash
curl http://127.0.0.1:28080/v1/chat/completions \
  -H 'Content-Type: application/json' \
  -d '{
    "model": "deepseek-v3",
    "conversation_id": "demo",
    "file_ids": ["file-..."],
    "messages": [{"role": "user", "content": "Что изображено на фото?"}]
  }'
```

Сброс контекста:

```bash
curl -X DELETE http://127.0.0.1:28080/v1/conversations/demo
```

Проверка состояния:

```bash
curl http://127.0.0.1:28080/health
```

## Модели

- `deepseek-v3` — быстрый режим;
- `deepseek-r1` — режим рассуждения;
- `deepseek-v4` — expert без рассуждения;
- `deepseek-r4` — expert с рассуждением.

Названия преобразуются во внутренние режимы web-интерфейса и могут перестать
работать после обновлений DeepSeek.

## Ограничения

- соответствие `conversation_id` → web-сессия хранится в памяти процесса;
- метаданные загруженных `file_id` хранятся только в памяти процесса;
- максимальный размер загрузки по умолчанию — 50 МиБ;
- после перезапуска контейнера локальное соответствие теряется, хотя созданный
  чат остаётся в аккаунте DeepSeek;
- при переключении между обычным/search-режимом и vision-файлами wrapper
  создаёт новую внутреннюю DeepSeek web-сессию, потому что сам DeepSeek плохо
  переключает эти режимы внутри одного web-чата;
- неактивные разговоры удаляются из памяти через 3600 секунд;
- параллельные сообщения одного разговора выполняются последовательно;
- web API и формат SSE могут измениться без предупреждения.

## Управление

```bash
make logs
make down
```

## Лицензия

GNU Affero General Public License v3.0. См. `LICENSE` и `NOTICE.md`.
