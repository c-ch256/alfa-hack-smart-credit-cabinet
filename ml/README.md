# Интерпретируемый скоринг МСБ

Реализованы автономные LogisticRegression, учебная кредитная политика и подбор
предложений. Нет API, базы, авторизации или Frontend. Для работы после установки
библиотек сеть не требуется. История прежнего кода: коммит 795ba63.

## Windows: команды из корня репозитория

Выполнять по одной строке. Активация окружения не обязательна.

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r ml/requirements-lock.txt
.\.venv\Scripts\python.exe -m pip check
.\.venv\Scripts\python.exe -m ml.train
.\.venv\Scripts\python.exe -m ml.evaluate
.\.venv\Scripts\python.exe -m pytest -q --junitxml=ml/reports/pytest.xml
.\.venv\Scripts\python.exe -m ml.cli score
.\.venv\Scripts\python.exe -m ml.cli offers --purpose working_capital
.\.venv\Scripts\python.exe -m ml.cli offers --purpose working_capital --public
.\.venv\Scripts\python.exe -m ml.benchmark
```

Свой запрос: добавить --input path/to/request.json.
Для инвестиций: --purpose equipment --investment-object-confirmed.
Это подтверждение объекта доверенным Backend, не выдуманные сведения модели.
Для генерации исходного CSV: python ml/generate_data.py --n_samples 15000 --seed 42 --output data/synthetic_data.csv.
requirements.txt содержит прямые зависимости, requirements-lock.txt — весь фиксированный набор.

## Архитектура

Валидация → стоп-факторы → ModelScorer → PD → credit_policy → CreditDecision
→ offer_optimizer → LoanOffer[] → явный PublicResult.

Pipeline: InputValidator → FeatureBuilder → Imputer/Scaler/Encoder → LogisticRegression.
Преобразования обучены только на train. Класс 1 — дефолт.
Модель использует 12 числовых фич и категории продукта/полного ОКВЭД.
Регион, ОПФ и статус не входят в регрессию. principal_coverage не является DSCR.
Все 20 внешних полей обязательны: пропуски/NaN/Infinity отклоняются.
Imputer — защитный внутренний компонент, не разрешение подменять неизвестные финансы.
Неизвестный ОКВЭД поддерживается encoder, неизвестный продукт нет.
Техническое ограничение величины чисел: 10^15, не банковский норматив.

Исходный CSV не менялся: 15000 строк, 978 дефолтов, seed 42.
Выдачи 24.10.2023–23.10.2025, наблюдение до 23.10.2026.
Метка — 90+ дней просрочки в следующие 12 месяцев, не отказ банка.
Латентная формула генератора отделена от фич модели.
Последние 6 месяцев — временной test; ранние записи разделены стратифицированно
70/15/15: train/validation/calibration. Только test отделён по времени.
C выбирается среди .1/1/10 по validation LogLoss, затем Brier и меньшему C.
Основной seed 42; 43/44 — устойчивость, не выбор лучшего test.
Class weights не используются; меньшинство оценивается AP/recall/F1.

PD — исходный predict_proba. Отдельного калибратора нет: его необходимость не доказана.
На calibration средняя PD 6.24%, частота 6.42%; сохраняются LogLoss, Brier и кривая.
Локальная калибровка не идеальна и не гарантируется общей близостью средних.
Test: AUC .9512, Brier .02854, LogLoss .10745. Это только синтетическая демонстрация.

## Политика и предложения

Все параметры policy_config.json — учебные допущения, не действующие правила банка
или актуальные рыночные ставки. Группы A <5%, B [5,10)%, C [10,20)%, D >=20% сохранены.
Группа и диагностический порог классификации 12% не являются одобрением.

Неактивность/defaults>0/просрочка>90 — стоп-фактор. Недостаток объекта/залога инвестиций
→ INSUFFICIENT_DATA. Просрочка 31–90 либо сумма >30 млн → MANUAL_REVIEW.
Затем PD >=25% → DECLINED, PD >=12% → MANUAL_REVIEW.
Для ELIGIBLE нужны продуктовые лимиты, корректная цель и DSCR >=1.25.
Низкая PD при недостатке денежного потока не делает заявку допустимой.

Ставка сценария = 14% + маржа 8/6 п.п. + надбавка A/B/C/D: 0/1.5/3/5 п.п.
По ней после PD считается аннуитет, затем DSCR = поток/(текущие платежи + аннуитет).
Цели: working_capital → оборотный, equipment → инвестиционный, expansion → оба.
Неизвестная/отсутствующая цель не даёт предложений.
Исходная заявка должна быть ELIGIBLE; этот прототип не спасает отклонённую заявку
автоматическим встречным предложением. Остальные статусы не поступают в перебор.

Конечная сетка: доли суммы 1/.9/.8/.7/.6/.5 с округлением вниз до 10000, плюс точная
исходная сумма; сроки 12/24/36/60/84 плюс исходный и максимум продукта.
Каждый кандидат заново скорится и полностью проверяется политикой.
Ранг [0,1] = .35*цель + .35*доля суммы + .20*доступность + .10*близость срока.
Цель: 1 для исходного продукта, .9 для другого разрешённого целью.
Доступность = clip(1 - платёж/бюджет,0,1), бюджет = поток/1.25 - текущие платежи.
Близость срока = 1/(1+|срок-исходный|/84).
Ничья: сумма, платёж, продукт, срок. Возвращаются до трёх вариантов.
Это открытые учебные предпочтения, без модели лимита или выдуманной прибыльности.

## Backend

```python
from ml.dto import ScoringRequest
from ml.inference import ModelScorer
from ml.offer_optimizer import optimize

scorer = ModelScorer("ml/artifacts")
req = ScoringRequest("app-123", request_dict)
internal = scorer.score(req)
v2 = internal.to_dict()
v1 = internal.to_v1()
result = optimize(req, "working_capital", scorer)
public = result.public()
```

V1 не изменён, v2 добавляет обёртку schema_version/application_id/request.
ScoringResult, CreditDecision и LoanOffer разделены. Вклады фич × коэффициенты
вместе с intercept воспроизводят log-odds. Это внутренние объяснения, не причинность
и не юридические тексты отказа. PublicResult — закрытый список полей без PD,
вкладов, групп, DSCR, ранга и правил; лишние поля запрещены, в том числе вложенные.

Ошибки: InputValidationError, SchemaVersionError, OutOfDomainError, ArtifactError.
Скорер сам отклоняет стоп-факторы. MODEL_DIR поддерживается; скрытого fallback нет.
Backend должен хранить отказы и статусы, реализовать аудит, RBAC и уведомления.

Pipeline хранится целиком в pipeline.joblib. Единственный источник описания
артефакта — metadata.json: версии, параметры, признаки и хэши. Загрузчик проверяет
совместимость; joblib должен происходить из доверенного источника.
Текущее состояние и результаты проверок: reports/final_summary.md.
Отказанные заявки, временной дрейф и реальные банковские зависимости не моделируются.
Метрики не подтверждают пригодность для реального кредитования.
