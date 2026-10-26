# alfa-hack-smart-credit-cabinet

Автономный ML-прототип: LogisticRegression, PD, учебная кредитная политика и подбор предложений.
Инструкции: [ml/README.md](ml/README.md).
Фактические итоги: [ml/reports/final_summary.md](ml/reports/final_summary.md).
Backend реализует FastAPI, PostgreSQL, ML-скоринг, кредитный workflow и демонстрационные документы; frontend содержит кабинеты CLIENT и STAFF.

## Запуск демо

Требуется Docker с Compose. При первом запуске скопируйте `.env.example` в `.env`
и задайте `DB_PASSWORD`, `JWT_SECRET` (не менее 32 байт) и `DEMO_PASSWORD`
(не менее 12 символов).
Существующий `.env` сохраняйте: значения `KEY_RATE`, `SCORER` и остальные настройки
берутся из него. `KEY_RATE=14.00` — демонстрационное значение по умолчанию;
`SCORER=model` включает поставляемую ML-модель, `SCORER=mock` — демонстрационную скоркарту.

Из корня репозитория выполните одну команду:

```sh
docker compose up --build
```

Откройте **http://localhost:8080** — это единая точка входа в интерфейс CLIENT/STAFF.
Если проверяющий использует другой компьютер в той же сети, адрес:
`http://<IP компьютера с Docker>:8080` (разрешите доступ к порту 8080 в локальной сети).
Node.js, `npm ci` и отдельный запуск frontend на компьютере проверяющего не нужны.
Демо-логины: `client1`–`client4`, `staff1`; пароль — заданный вами `DEMO_PASSWORD`.

Compose запускает PostgreSQL, API и production-сборку Vite под nginx.
Frontend ждёт готовности API, API — базы; штатные миграции выполняются при старте API.
Обновление вложенных страниц React Router поддерживается. Запросы `/api/*`
идут через nginx в backend по Docker-сети; готовность API: **http://localhost:8080/api/ready**.
Прямой локальный адрес API `http://127.0.0.1:8000` также сохранён.

Данные PostgreSQL остаются в существующем volume `postgres_data` и переживают рестарт.
Для остановки без удаления данных используйте `docker compose stop`.
Подробности API договора и графика: [backend/README.md](backend/README.md).
