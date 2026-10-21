# Backend: FastAPI и PostgreSQL

Python 3.12, FastAPI, PostgreSQL 16, SQLAlchemy 2.0 и Alembic. Реализованы `/health`, `/ready`, конфигурация БД и схема кредитного кабинета. Авторизации, API заявок и кредитной логики пока нет. `/health` проверяет процесс API; `/ready` делает `SELECT 1` в БД и возвращает 503 при недоступности без деталей подключения.

## Запуск через Docker Compose

Из корня репозитория:

```bash
cp .env.example .env
# Задайте в .env собственный DB_PASSWORD; оставлять его пустым нельзя.
docker compose up --build
```

После первого запуска: `http://127.0.0.1:8000/health`, `http://127.0.0.1:8000/ready`, Swagger `http://127.0.0.1:8000/docs`. Compose не публикует порт PostgreSQL, API доступен только на localhost. Данные БД сохраняются в именованном volume `postgres_data`; обычный `docker compose down` его не удаляет. API работает без root и запускает `alembic upgrade head` перед Uvicorn. При ошибке миграции контейнер API не стартует.

Проверка состояния и текущей миграции:

```bash
docker compose ps
docker compose exec api alembic -c alembic.ini current
```

## Локальная разработка в Conda

Все установки и проверки выполняйте только в окружении `craftors-backend`:

```bash
conda run -n craftors-backend python -m pip install -r backend/requirements-dev.lock
```

Настройки читаются из env. `DB_HOST`, `DB_PORT`, `DB_NAME`, `DB_USER`, `DB_PASSWORD` собираются в PostgreSQL URL с драйвером psycopg; полный `DATABASE_URL` имеет приоритет. Для локального запуска вне Compose экспортируйте нужные переменные из своего `.env` и запустите команды из `backend/`:

```bash
conda run -n craftors-backend alembic -c alembic.ini upgrade head
conda run -n craftors-backend uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Ревизия `0002_credit_schema` создаёт `users`, `companies`, `products`, `applications`, `application_snapshots`, `check_results`, `scores`, `decisions`, `offers`, `status_events` и `audit_events`. Миграция не создаёт демо-пользователей и не содержит реальных данных. `companies.owner_user_id` связывает компанию с владельцем; проверки роли и принадлежности при запросах будут реализованы в сервисе авторизации. Снимки, результаты проверок, оценки, решения и события хранятся отдельными записями без каскадного удаления. Схема не применяет FSM и не фильтрует содержимое JSON аудита: это обязанности будущих сервисов.

Текущую ревизию показывает `conda run -n craftors-backend alembic -c alembic.ini current`. Откат `alembic downgrade 0001_initial_empty` удаляет прикладные таблицы и допустим только на одноразовой тестовой БД; перед изменениями долговременной БД нужна резервная копия.

Без работающей БД `/health` остаётся доступным, а `/ready` возвращает 503. Текущий локальный Swagger — `/docs`; публичный префикс `/api` через nginx относится к следующему этапу. В production дополнительно обязательны `APP_ENV=production`, `APP_DOCS_ENABLED=false`, явный `APP_ALLOWED_HOSTS` и непустые учётные данные БД. Не публикуйте PostgreSQL наружу и не печатайте переменные с паролем или полный `DATABASE_URL`.

## Проверки

Из `backend/`:

```bash
conda run -n craftors-backend python -m pytest -q
conda run -n craftors-backend ruff check app tests alembic
conda run -n craftors-backend ruff format --check app tests alembic
conda run -n craftors-backend alembic -c alembic.ini heads
conda run -n craftors-backend alembic -c alembic.ini upgrade head --sql
conda run -n craftors-backend python -m pip check
conda run -n craftors-backend python -m pip_audit --no-deps --disable-pip -r requirements-dev.lock
```

Первая миграция создаёт только служебную таблицу `alembic_version`; вторая — прикладную схему. Проверка `--sql` не требует БД. Тесты схемы применяют и откатывают миграции на изолированной SQLite с включёнными внешними ключами; окончательная проверка PostgreSQL выполняется через Compose.

Зависимости объявлены в `pyproject.toml`; runtime и dev версии зафиксированы в `requirements.lock` и `requirements-dev.lock`. После изменения зависимостей пересобирайте оба lock-файла через `pip-compile` под Python 3.12 в `craftors-backend` и повторяйте проверки. Хеши пакетов пока не закреплены.
