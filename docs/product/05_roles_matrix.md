# Матрица ролей и прав

Роли: **CLIENT**, **STAFF**, **SYSTEM** (конвейер). Маршруты — как в API команды (`docs/openapi.json`); фронтенд обращается к ним через `/api/…`.
Коды: 401 — нет или просрочен токен · 403 — не та роль · 404 — чужая или несуществующая сущность (существование не раскрываем) · 409 — недопустимый переход или устаревшая версия · 422 — валидация · 429 — лимит попыток входа.

| Действие | API | CLIENT | STAFF | Если нельзя |
|---|---|---|---|---|
| Войти | `POST /auth/login` | ✓ | ✓ | 401; 429 — 5 попыток в минуту на логин (включая успешные), 30 на IP |
| Профиль | `GET /auth/me` | ✓ | ✓ | 401 |
| Компании и реквизиты | `GET /me/companies`, `GET /me/companies/{id}/requisites` | ✓ свои | ✗ | 403; 404 чужая |
| Каталог со ставкой «от» | `GET /products` | ✓ | ✓ | 401 |
| Заявки: список, просмотр, история | `GET /applications`, `GET /applications/{id}`, `GET /applications/{id}/history` | ✓ свои | ✗ | 403; 404 чужая |
| Создать черновик | `POST /applications` + `Idempotency-Key` | ✓ своя компания | ✗ | 404; 409 тот же ключ с другим телом; 422 |
| Править черновик | `PATCH /applications/{id}` | ✓ только `draft` | ✗ | 409; 422 |
| Отправить | `POST /applications/{id}/submit` | ✓ из `draft` | ✗ | 409 повторно |
| Дослать сведения | `POST /applications/{id}/additional-info` | ✓ из `needs_info`, с `version` | ✗ | 409 устаревшая версия |
| Отозвать | `POST /applications/{id}/withdraw` | ✓ из `draft`, `needs_info`, `manual_review` (П10), `approved`, `counter_offer` | ✗ | 409 |
| Предложения | `GET /applications/{id}/offers` | ✓ своя | ✗ | 404 |
| Принять / отклонить | `POST /offers/{id}/accept`, `/decline` | ✓ своё, `proposed` | ✗ | 404; 409 |
| Договор, PDF, график | `GET /offers/{id}/contract`, `/contract.pdf`, `/schedule` | ✓ своё | ✗ | 404 чужое |
| Демо-код и подпись | `POST /applications/{id}/sign-demo/code`, `/sign-demo` | ✓ из `accepted` | ✗ | 409; 422 неверный код |
| Очередь | `GET /staff/applications` | ✗ | ✓ **только назначенные** | 403 |
| Карточка (пишет `staff.view`) | `GET /staff/applications/{id}` | ✗ | ✓ назначенная | 403; 404 |
| Предварительный расчёт условий | `POST /staff/loan-preview`, `GET /staff/products` | ✗ | ✓ | 403 |
| Внутренние комментарии | `GET`/`POST /staff/applications/{id}/comments` | ✗ | ✓ | 403 |
| Решение | `POST /staff/applications/{id}/decision` | ✗ | ✓ из `manual_review`, комментарий обязателен | 403; 409; 422 |
| Запрос сведений | `POST /staff/applications/{id}/request-info` | ✗ | ✓ из `manual_review` | 403; 409; 422 |
| Уведомления | `GET /staff/notifications` | ✗ | ✓ | 403 |
| Журнал действий | `GET /staff/audit` | ✗ | ✓ | 403 |
| Шаги конвейера | — | ✗ | ✗ | только SYSTEM |
| Health / Ready | `GET /health`, `GET /ready` | публично | публично | — |

Проверено 26.10.2026 на сборке команды: TC-09…TC-14, TC-19…TC-22 в [13_test_scenarios.md](13_test_scenarios.md).
