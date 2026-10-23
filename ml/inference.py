"""Локальный адаптер JSON-контракта; без обращений к внешним сервисам."""
import json
import os
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
from catboost import CatBoostClassifier, Pool

from ml.features import build_features, request_to_row


def risk_band(pd_value):
    return 'A' if pd_value < 0.05 else 'B' if pd_value < 0.10 else 'C' if pd_value < 0.20 else 'D'


class ModelScorer:
    """score(dict | Pydantic v2) -> dict по scoring_result.schema.json.

    Загрузка один раз при создании. Ошибки загрузки передаются backend;
    выбор MockScorer и логирование остаются ответственностью backend.
    """
    def __init__(self, model_dir=None):
        directory = Path(model_dir or os.getenv('MODEL_DIR', 'ml/artifacts'))
        self.metadata = json.loads((directory / 'metadata.json').read_text(encoding='utf-8'))
        self.model = CatBoostClassifier()
        self.model.load_model(str(directory / 'model.cbm'))
        self.calibrator = joblib.load(directory / 'calibration.joblib')
        if self.model.feature_names_ != self.metadata['features']:
            raise ValueError('Model feature schema mismatch')

    def score(self, req):
        return self.score_many([req])[0]

    def score_many(self, requests):
        if not requests:
            return []
        rows = pd.DataFrame([request_to_row(req) for req in requests])
        features = build_features(rows)[self.metadata['features']]
        pool = Pool(features, cat_features=self.metadata['categorical_features'])
        raw = np.asarray(self.model.predict(pool, prediction_type='RawFormulaVal', thread_count=1))
        probabilities = self.calibrator.predict_proba(raw.reshape(-1, 1))[:, 1]
        shap = self.model.get_feature_importance(pool, type='ShapValues', thread_count=1)[:, :-1]
        results = []
        for probability, contributions in zip(probabilities, shap):
            reasons = []
            for index in np.argsort(-np.abs(contributions))[:4]:
                contribution = float(contributions[index])
                if contribution == 0:
                    continue
                effect = 'increases_risk' if contribution > 0 else 'decreases_risk'
                name = features.columns[index]
                label = LABELS.get(name, name)
                text = f'{label}: значение повышает оценку риска' if contribution > 0 else f'{label}: значение снижает оценку риска'
                reasons.append({'feature': name, 'text': text, 'effect': effect, 'contribution': contribution})
            results.append({'pd': float(probability), 'risk_band': risk_band(probability),
                            'model_version': self.metadata['model_version'], 'reasons': reasons})
        return results


LABELS = {
    'amount': 'Сумма кредита', 'term_months': 'Срок кредита',
    'collateral_value': 'Стоимость залога', 'product_code': 'Продукт',
    'legal_form': 'Форма бизнеса', 'okved': 'Отрасль', 'region': 'Регион',
    'business_age_months': 'Возраст бизнеса', 'bank_client_months': 'Срок обслуживания в банке',
    'avg_monthly_revenue': 'Средняя выручка', 'avg_monthly_expenses': 'Средние расходы',
    'revenue_trend_6m': 'Динамика выручки', 'existing_monthly_payments': 'Текущие платежи',
    'active_loans': 'Число кредитов', 'max_overdue_days_12m': 'Просрочки за последний год',
    'total_debt': 'Общий долг', 'defaults': 'Дефолты в истории', 'status': 'Статус компании',
    'tax_debt': 'Налоговый долг', 'mass_address': 'Массовый адрес',
    'net_cash_flow': 'Чистый денежный поток', 'net_margin': 'Доля свободного денежного потока',
    'principal_monthly': 'Основной долг в расчёте на месяц',
    'principal_coverage': 'Покрытие текущих платежей и основного долга без процентов',
    'loan_to_annual_revenue': 'Кредит к годовой выручке',
    'debt_to_annual_revenue': 'Долг к годовой выручке',
    'collateral_coverage': 'Покрытие залогом', 'log_revenue': 'Масштаб выручки',
}
