# Third-party notices

Проект «Умный кредитный кабинет предпринимателя» (команда Craftors, Альфа-Хак 2026) использует только компоненты с открытыми лицензиями. Платных API, закрытых библиотек и моделей с закрытыми весами нет. Версии зафиксированы в `backend/requirements.lock`, `ml/requirements-lock.txt` и `frontend/package-lock.json`. Полные тексты лицензий лежат в пакетах и на страницах проектов.

## 1. Моки и демонстрационные компоненты

Внешних интеграций в проекте нет. Все обращения к внешним системам заменены локальными заглушками на синтетических данных.

| Что имитируется | Где в коде | Как работает |
|---|---|---|
| ФНС (статус компании, налоговая задолженность, массовый адрес) | `backend/app/services/mocks.py` (`fns_by_inn`) | Локальные версионированные ответы для демо-ИНН |
| БКИ (кредитная история, просрочки, долговая нагрузка) | `backend/app/services/mocks.py` (`bki_by_inn`) | Локальные версионированные ответы для демо-ИНН |
| Скоринг | `backend/app/scoring/mock.py` (`SCORER=mock`); модель — `SCORER=model` | Демонстрационная скоркарта или логистическая регрессия на синтетических данных |
| Электронная подпись | `backend/app/services/completion.py` | Демо-код показывается в интерфейсе, СМС не отправляется; хранится только HMAC-хэш кода |
| Выдача кредита | `backend/app/services/completion.py` | Смена статуса на `disbursed_demo`, реальное перечисление средств не выполняется |
| Кредитный договор и график платежей | `backend/app/services/contracts.py`, `contract_pdf.py` | Помечены «Демонстрационный договор. Не является юридически значимым документом» |
| Реквизиты банка и компаний | демо-данные backend | Вымышленные («Демо-Банк (вымышленный)») |

## 2. Данные и модель

| Компонент | Происхождение | Лицензия |
|---|---|---|
| `data/synthetic_data.csv` (15 000 строк) | Сгенерирован командой: `ml/generate_data.py`, seed 42. Персональных и реальных данных нет | Собственная разработка команды |
| `ml/artifacts/pipeline.joblib` | Обучен командой на синтетических данных (scikit-learn, логистическая регрессия) | Собственная разработка команды |
| Демо-учётки и компании (client1–client4, staff1) | Вымышленные, создаются при старте backend | Собственная разработка команды |

## 3. Шрифты

| Компонент | Где используется | Лицензия |
|---|---|---|
| Inter (`@fontsource/inter` 5.3.0) | Интерфейс | SIL Open Font License 1.1 |
| Noto Sans (`backend/app/assets/NotoSans-Regular.ttf`) | PDF договора и графика | SIL Open Font License 1.1, текст — `backend/app/assets/LICENSE.txt` |
## 4. Backend и ML — Python

Используются в работающем приложении (`backend/requirements.lock`):

| Пакет | Версия | Лицензия |
|---|---|---|
| fastapi | 0.156.4 | MIT |
| starlette | 1.9.0 | BSD-3-Clause |
| uvicorn | 0.40.0 | BSD-3-Clause |
| pydantic / pydantic-core / pydantic-settings | 2.14.1 / 2.50.1 / 2.16.0 | MIT |
| sqlalchemy | 2.0.53 | MIT |
| alembic | 1.20.0 | MIT |
| mako | 1.4.3 | MIT |
| psycopg / psycopg-binary | 3.3.6 | LGPL-3.0-only (используется без изменений, как отдельная библиотека) |
| argon2-cffi / argon2-cffi-bindings | 25.1.0 / 26.1.0 | MIT |
| cffi | 2.1.1 | MIT-0 |
| pycparser | 3.12 | BSD-3-Clause |
| pyjwt | 2.15.1 | MIT |
| reportlab | 4.4.9 | BSD |
| pillow | 13.0.0 | MIT-CMU |
| scikit-learn | 1.9.1 | BSD-3-Clause |
| numpy | 2.5.4 | BSD-3-Clause (с компонентами 0BSD, MIT, Zlib, CC0-1.0) |
| scipy | 1.18.1 | BSD-3-Clause |
| pandas | 3.0.6 | BSD-3-Clause |
| joblib | 1.6.0 | BSD-3-Clause |
| threadpoolctl | 3.7.0 | BSD-3-Clause |
| cloudpickle | 3.1.2 | BSD-3-Clause |
| narwhals | 2.27.1 | MIT |
| jsonschema / jsonschema-specifications | 4.26.0 / 2025.9.1 | MIT |
| referencing | 0.37.0 | MIT |
| rpds-py | 2026.9.1 | MIT |
| attrs | 26.1.0 | MIT |
| anyio | 4.15.1 | MIT |
| h11 | 0.16.0 | MIT |
| idna | 3.20 | BSD-3-Clause |
| charset-normalizer | 3.5.2 | MIT |
| click | 8.5.0 | BSD-3-Clause |
| greenlet | 3.5.6 | MIT AND PSF-2.0 |
| markupsafe | 3.0.4 | BSD-3-Clause |
| annotated-doc / annotated-types | 0.0.5 / 0.8.0 | MIT |
| typing-extensions | 4.16.0 | PSF-2.0 |
| typing-inspection | 0.4.4 | MIT |
| python-dateutil | 3.0.0 | Apache-2.0 OR BSD-3-Clause |
| python-dotenv | 1.2.4 | BSD-3-Clause |
| six | 1.17.0 | MIT |
| tzdata | 2026.5 | Apache-2.0 |
| opentelemetry-api | 1.45.1 | Apache-2.0 |

Только для обучения, отчётов и тестов ML (`ml/requirements-lock.txt`, в контейнер приложения не входят):

| Пакет | Версия | Лицензия |
|---|---|---|
| matplotlib | 3.11.2 | Matplotlib License (на основе PSF) |
| contourpy | 1.4.0 | BSD-3-Clause |
| cycler | 0.12.1 | BSD-3-Clause |
| fonttools | 4.67.0 | MIT |
| kiwisolver | 1.5.1 | BSD-3-Clause |
| pyparsing | 3.3.3 | MIT |
| packaging | 26.3 | Apache-2.0 OR BSD-2-Clause |
| psutil | 7.2.2 | BSD-3-Clause |
| pytest | 9.1.1 | MIT |
| pluggy / iniconfig | 1.7.0 / 2.3.1 | MIT |
| pygments | 2.21.0 | BSD-2-Clause |
| colorama | 0.4.6 | BSD-3-Clause |

Инструменты разработки backend (`backend/requirements-dev.lock`): pytest 9.0.3 (MIT), httpx 0.28.1 (BSD-3-Clause), ruff 0.16.8 (MIT), pip-audit 2.10.1 (Apache-2.0).

## 5. Frontend — npm

Прямые зависимости (`frontend/package.json`):

| Пакет | Версия | Лицензия |
|---|---|---|
| @alfalab/core-components (+ `-config`, `-stack-context`) | 50.38.0 / 1.1.0 / 1.0.1 | MIT |
| react / react-dom | 18.3.1 | MIT |
| react-router-dom | 7.18.4 | MIT |
| @tanstack/react-query | 5.104.5 | MIT |
| react-hook-form | 7.91.0 | MIT |
| @hookform/resolvers | 5.2.2 | MIT |
| zod | 4.6.7 | MIT |
| @fontsource/inter | 5.3.0 | OFL-1.1 |

Всего с транзитивными зависимостями в рабочей сборке 381 пакет. Лицензии: MIT — 346, ISC — 13, Apache-2.0 — 13, BSD-3-Clause — 2, по одному OFL-1.1, CC0-1.0, Unlicense, 0BSD, «MIT AND ISC». У двух пакетов лицензия в `package.json` не указана: `react-canvas-pattern-lock` 2.0.7 и `@alfalab/react-canvas-pattern-lock` 2.0.8. Это транзитивные зависимости `@alfalab/core-components` (компонент графического ключа). В коде проекта они не используются и в собранный бандл не попадают.

Инструменты сборки и проверки (`devDependencies`, в рабочую сборку не входят): vite, @vitejs/plugin-react, typescript, eslint, typescript-eslint, eslint-plugin-react-hooks, eslint-plugin-react-refresh, globals, @eslint/js, @types/*. Лицензии: MIT, Apache-2.0, BSD-2/3-Clause, ISC, MPL-2.0 (lightningcss), CC-BY-4.0 (данные caniuse-lite), BlueOak-1.0.0.

## 6. Docker-образы

| Образ | Назначение | Лицензия |
|---|---|---|
| `postgres:16.15-alpine3.24` | База данных | PostgreSQL License |
| `python:3.12.14-slim-bookworm` | Backend | PSF-2.0 (Python); пакеты Debian — свободные лицензии |
| `node:24-alpine` | Только сборка фронтенда | MIT (Node.js) |
| `nginxinc/nginx-unprivileged:stable-alpine` | Раздача фронтенда | BSD-2-Clause (nginx) |

Образы закреплены по `sha256` в `backend/Dockerfile` и `frontend/Dockerfile`.

## 7. Товарные знаки

«Альфа-Банк» и связанные обозначения принадлежат их правообладателю. Проект — учебный прототип для хакатона, не является продуктом банка и не использует его реальные данные, реквизиты или системы.
