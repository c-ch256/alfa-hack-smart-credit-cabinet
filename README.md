# alfa-hack-smart-credit-cabinet

Автономный ML-прототип: LogisticRegression, PD, учебная кредитная политика и подбор предложений.
Инструкции: [ml/README.md](ml/README.md).
Фактические итоги: [ml/reports/final_summary.md](ml/reports/final_summary.md).
Backend реализует FastAPI, PostgreSQL, ML-скоринг, кредитный workflow и демонстрационные документы; frontend развивается отдельно.

Для локального запуска установите `KEY_RATE=14.00` (демонстрационная базовая ставка). Подробности API договора и графика: [backend/README.md](backend/README.md).
Backend и режимы скоринга `SCORER=mock|model` описаны в [backend/README.md](backend/README.md).
Для Docker: заполните `.env` по `.env.example`, затем `docker compose up --build`.
Модель использует только доверенный локальный синтетический артефакт; реальные банковские
решения и интеграции не выполняются.
