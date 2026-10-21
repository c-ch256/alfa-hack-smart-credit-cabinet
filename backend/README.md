# Backend: этап 3

Минимальный FastAPI-сервис на Python 3.12. Пока есть только `GET /health` и локальный Swagger `/docs`. БД, авторизация, скоринг и кредитные правила не реализованы. Данные не используются.

## Локальный запуск

Из корня репозитория, в подготовленном окружении `craftors-backend`:

```bash
conda run -n craftors-backend python -m pip install -r backend/requirements-dev.lock
cd backend
conda run -n craftors-backend uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Проверка: `http://127.0.0.1:8000/health` возвращает `{"status":"ok"}`; Swagger доступен по `http://127.0.0.1:8000/docs`.

Проверки из каталога `backend`:

```bash
conda run -n craftors-backend python -m pytest -q
conda run -n craftors-backend ruff check app tests
conda run -n craftors-backend ruff format --check app tests
conda run -n craftors-backend python -m pip_audit --no-deps --disable-pip -r requirements-dev.lock
```

## Конфигурация

Параметры читаются из переменных окружения с префиксом `APP_`. По умолчанию: `APP_ENV=local`, отладка выключена, разрешены только хосты `localhost`, `127.0.0.1`, `testserver`, CORS выключен. Списки передаются как JSON-массивы, например `APP_ALLOWED_HOSTS='["api.example.test"]'`.

Для `APP_ENV=production` обязательно явно задать `APP_DOCS_ENABLED=false` и `APP_ALLOWED_HOSTS` без локальных адресов. `APP_DEBUG=true`, wildcard-хосты и CORS без HTTPS отклоняются при старте. Если frontend размещён отдельно, задайте только конкретные HTTPS origin через `APP_CORS_ORIGINS`; по умолчанию CORS не включён. Публикуйте сервис за TLS-терминатором.

## Docker

Образ использует Python 3.12.14 и устанавливает только `requirements.lock` с точными версиями всех зависимостей:

```bash
docker build -t smart-credit-backend:local backend
docker run --rm -p 127.0.0.1:8000:8000 smart-credit-backend:local
```

Контейнер запускается непривилегированным пользователем. Для публичного размещения задайте строгие production-переменные и TLS на внешней границе; локальный запуск выше предназначен только для разработки.

Зависимости объявлены в `pyproject.toml`, точные транзитивные версии закреплены в `requirements.lock` и `requirements-dev.lock`. После изменения зависимостей обновляйте оба файла в Python 3.12 через `pip-compile` в `craftors-backend`, затем запускайте все проверки. Хеши пакетов пока не закреплены; доверенная загрузка из индекса и аудит зависимостей остаются обязательными.
