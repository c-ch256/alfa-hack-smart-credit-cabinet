# Backend: FastAPI и PostgreSQL

Python 3.12, FastAPI, PostgreSQL 16, SQLAlchemy 2.0 и Alembic. Реализованы `/health`, `/ready`, авторизация, клиентский API черновиков и переходы `submit`/`withdraw`. `/health` проверяет процесс API; `/ready` делает `SELECT 1` в БД и возвращает 503 при недоступности без деталей подключения.

## Запуск через Docker Compose

Из корня репозитория:

```bash
cp .env.example .env
# Задайте в .env собственные DB_PASSWORD, JWT_SECRET и DEMO_PASSWORD.
docker compose up --build
```

После первого запуска: `http://127.0.0.1:8000/health`, `http://127.0.0.1:8000/ready`, Swagger `http://127.0.0.1:8000/docs`. Compose не публикует порт PostgreSQL, API доступен только на localhost. Данные БД сохраняются в именованном volume `postgres_data`; обычный `docker compose down` его не удаляет. API работает без root и запускает миграции, идемпотентный seed учётных записей и Uvicorn. При ошибке миграции или seed контейнер API не стартует.

`JWT_SECRET` должен содержать не менее 32 байт случайного значения, `DEMO_PASSWORD` — не менее 12 символов. Эти значения не коммитят и не передают в логи. Seed создаёт `client1`–`client4` (CLIENT), `staff1` (STAFF), четыре синтетические компании и два демонстрационных продукта. Повторный запуск не меняет существующие учётные записи, пароли и компании; смену пароля нужно выполнять отдельной процедурой. Заявки seed не создаёт.

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
conda run -n craftors-backend python -m app.seed
conda run -n craftors-backend uvicorn app.main:app --host 127.0.0.1 --port 8000
```

`POST /auth/login` принимает JSON с `login` и `password`, возвращает `access_token`, `token_type=bearer` и `expires_in`. Передавайте токен в `Authorization: Bearer <token>` для `GET /auth/me`; ответ содержит только `id`, `login`, `role`, `full_name`, `position`. JWT подписан HS256, содержит `sub`, `iss`, `aud`, `iat`, `nbf`, `exp`, `jti`, живёт 60 минут по умолчанию (`JWT_TTL_MINUTES`, максимум 480). Роль берётся из текущей записи пользователя в БД, а не из JWT. `/health`, `/ready` и локальный `/docs` остаются публичными.

## Клиентские заявки

Все маршруты ниже требуют `CLIENT`. `GET /me/companies` возвращает компании клиента из БД; `GET /products` показывает два демонстрационных продукта и их лимиты. `POST /applications` создаёт черновик и требует заголовок `Idempotency-Key` (1–128 символов: буквы, цифры, `.`, `_`, `~`, `-`). Тело: `company_id`, `product_code`, `amount` (целые рубли), `term_months`, `purpose`, необязательный `purpose_details`. Для `investment` обязателен `investment_object`; от 5 млн ₽ дополнительно требуются `collateral_type` и положительный `collateral_value`. Пределы суммы и срока берутся из каталога. Условия и пороги — только демонстрационные допущения из `docs/00_common.md`, не тарифы банка.

`GET /applications` и `GET /applications/{id}` дают текущие данные только по своим компаниям. `PATCH /applications/{id}` изменяет клиентские поля только у черновика; `POST /applications/{id}/submit` переводит `draft → submitted` и **не запускает** проверок, скоринга или решения. `POST /applications/{id}/withdraw` переводит заявку в `closed` только из `draft`, `needs_info`, `approved` или `counter_offer` согласно FSM. `GET /applications/{id}/history` возвращает фактические события с временем, ролью и ID инициатора, причиной и порядковым номером. Событие создания черновика имеет `from_status=null`. Списки заявок и событий принимают `limit` (1–100) и `offset` (0–1 000 000); для полной истории последовательно запрашивайте страницы.

Повтор `POST /applications` с тем же ключом и исходным телом возвращает текущую заявку с HTTP 200, даже если её позже отредактировали; первая попытка возвращает 201. Тот же ключ с другим телом возвращает 409. Чужая компания или заявка даёт 404, запрещённый переход — 409, неверные поля — 422. Статус, история и аудит перехода сохраняются в одной транзакции; конкурентные изменения защищены версией заявки. Детальные схемы запросов и ответов доступны в `/docs`.

**Допущение MVP:** лимит входа хранится в памяти процесса: 5 попыток в минуту на логин (включая попытки с разных IP) и на пару логин/IP, 30 на IP. При нескольких репликах лимит не общий; до публичного масштабирования нужен общий лимитер, а также механизм отзыва уже выданных JWT. Приложение не принимает cookie для авторизации. Ответы об ошибках входа не сообщают, существует ли логин.

Ревизия `0002_credit_schema` создаёт `users`, `companies`, `products`, `applications`, `application_snapshots`, `check_results`, `scores`, `decisions`, `offers`, `status_events` и `audit_events`. Миграция не создаёт демо-данных. `companies.owner_user_id` связывает компанию с владельцем; клиентский API проверяет роль и принадлежность для каждого обращения. Снимки, результаты проверок, оценки, решения и события хранятся отдельными записями без каскадного удаления. FSM и фильтрация содержимого аудита выполняются сервисами, а не ограничениями схемы.

Текущую ревизию показывает `conda run -n craftors-backend alembic -c alembic.ini current`. Откат `alembic downgrade 0001_initial_empty` удаляет прикладные таблицы и допустим только на одноразовой тестовой БД; перед изменениями долговременной БД нужна резервная копия.

Без работающей БД `/health` остаётся доступным, а `/ready` возвращает 503. Текущий локальный Swagger — `/docs`; публичный префикс `/api` через nginx относится к следующему этапу. В production дополнительно обязательны `APP_ENV=production`, `APP_DOCS_ENABLED=false`, явный `APP_ALLOWED_HOSTS`, непустые учётные данные БД и `JWT_SECRET`. Не публикуйте PostgreSQL наружу и не печатайте переменные с паролями, токенами или полный `DATABASE_URL`.

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
