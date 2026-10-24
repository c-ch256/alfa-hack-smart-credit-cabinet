# Матрица ролей и прав (D1)

Роли: **CLIENT**, **STAFF**, **SYSTEM** (конвейер, без входа). Без токена любой эндпоинт, кроме входа и `/health`, → **401**.
Коды: 401 — нет или просрочен токен · 403 — не та роль · 404 — чужая или несуществующая сущность (существование не раскрываем) · 409 — недопустимый переход · 422 — валидация · 429 — лимит попыток входа.

| Действие | API | CLIENT | STAFF | Код ошибки, если нельзя |
|---|---|---|---|---|
| Войти | `POST /auth/login` | ✓ | ✓ | 401 неверный пароль · 429 после 5 неудачных попыток за минуту |
| Мой профиль | `GET /auth/me` | ✓ | ✓ | 401 |
| Каталог продуктов | `GET /products` | ✓ | ✓ | 401 |
| Мои компании | `GET /me/companies` | ✓ свои | ✗ | 403 |
| Подбор кредита | `POST /recommendations` | ✓ своя компания | ✗ | 403 · 404 чужая компания · 422 |
| Список своих заявок | `GET /applications` | ✓ свои | ✗ | 403 |
| Создать черновик | `POST /applications` | ✓ своя компания | ✗ | 403 · 404 чужая компания · 422 лимиты/цель · 409 ключ идемпотентности чужой |
| Посмотреть заявку и историю | `GET /applications/{id}`, `/history` | ✓ своя | ✗ | 403 · 404 чужая |
| Редактировать | `PATCH /applications/{id}` | ✓ своя, только `draft` | ✗ | 404 · 409 не черновик · 422 |
| Отправить | `POST /applications/{id}/submit` | ✓ из `draft` | ✗ | 404 · 409 повторная отправка |
| Дослать сведения | `POST /applications/{id}/additional-info` | ✓ из `needs_info` | ✗ | 404 · 409 · 422 |
| Отозвать / закрыть | `POST /applications/{id}/withdraw` | ✓ из `draft`, `needs_info`, `approved`, `counter_offer` | ✗ | 404 · 409 |
| Предложения | `GET /applications/{id}/offers` | ✓ своя | ✗ | 404 |
| Принять / отказаться от оффера | `POST /offers/{id}/accept`, `/decline` | ✓ своя, оффер `proposed` | ✗ | 404 чужой · 409 неактуален |
| Получить демо-код | `POST /applications/{id}/sign-demo/code` | ✓ из `accepted` | ✗ | 404 · 409 |
| Подписать | `POST /applications/{id}/sign-demo` | ✓ из `accepted`, код действует 5 мин | ✗ | 404 · 409 код истёк · 422 неверный код |
| Очередь заявок | `GET /staff/applications?status=` | ✗ | ✓ все, кроме черновиков | 403 |
| Карточка заявки (пишет аудит `staff.view`) | `GET /staff/applications/{id}` | ✗ | ✓ | 403 · 404 |
| Решение | `POST /staff/applications/{id}/decision` | ✗ | ✓ только из `manual_review`, комментарий обязателен | 403 · 404 · 409 · 422 |
| Запросить сведения | `POST /staff/applications/{id}/request-info` | ✗ | ✓ только из `manual_review` | 403 · 404 · 409 · 422 |
| Журнал аудита | `GET /staff/audit?application_id=` | ✗ | ✓ | 403 |
| Шаги конвейера (`submitted→…→решение`, `signed_demo→disbursed_demo`) | — | ✗ | ✗ | только SYSTEM; попытка через API невозможна |
| Health | `GET /health` | публично | публично | — |

Проверено на прототипе: 401 на каждом роуте, 403, 404, 409, 422, 429. Для командного backend — сценарии TC-09…TC-13, TC-19, TC-20 в [13_test_scenarios.md](13_test_scenarios.md).
