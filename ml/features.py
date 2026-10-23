"""Единый расчёт признаков для обучения и инференса, без ставки и будущих данных."""
import json
from pathlib import Path

import numpy as np
import pandas as pd
from jsonschema import Draft202012Validator

CONTRACT_DIR = Path(__file__).parent / 'contracts'
REQUEST_SCHEMA = json.loads((CONTRACT_DIR / 'scoring_request.schema.json').read_text(encoding='utf-8'))
VALIDATOR = Draft202012Validator(REQUEST_SCHEMA)
GROUPS = {key: list(REQUEST_SCHEMA['properties'][key]['properties']) for key in ('company', 'bki', 'fns')}
ROOT_FIELDS = ['product_code', 'amount', 'term_months', 'collateral_value']
INPUT_FIELDS = ROOT_FIELDS + [field for fields in GROUPS.values() for field in fields]
CAT_FEATURES = ['product_code', 'legal_form', 'okved', 'region', 'status']


def request_to_row(request):
    """Принять dict или Pydantic v2-модель backend; проверить официальный JSON Schema."""
    if hasattr(request, 'model_dump'):
        request = request.model_dump(mode='json')
    VALIDATOR.validate(request)
    row = {key: request[key] for key in ROOT_FIELDS}
    for group in GROUPS:
        row.update(request[group])
    if row['bank_client_months'] > row['business_age_months']:
        raise ValueError('bank_client_months exceeds business_age_months')
    if not all(np.isfinite(value) for value in row.values() if isinstance(value, (int, float))):
        raise ValueError('Non-finite input')
    return row


def row_to_request(row):
    """CSV хранит 20 полей плоско; API использует вложенный контракт."""
    def native(value):
        return value.item() if isinstance(value, np.generic) else value
    result = {key: native(row[key]) for key in ROOT_FIELDS}
    for group, fields in GROUPS.items():
        result[group] = {key: native(row[key]) for key in fields}
    return result


def build_features(rows: pd.DataFrame) -> pd.DataFrame:
    """Явный whitelist исключает default_12m и application_date из модели.

    Вход обязан соответствовать контракту; пропуски не заменяются выдуманными
    значениями. Нулевые знаменатели ограничиваются единицей; отношения —
    диапазоном [-100, 100], чтобы поддерживать нулевую выручку.
    """
    x = rows[INPUT_FIELDS].copy()
    if x.isna().any().any():
        raise ValueError('Missing required input')
    for column in CAT_FEATURES:
        x[column] = x[column].astype(str)
    for column in ('tax_debt', 'mass_address'):
        x[column] = x[column].astype(int)
    revenue = x.avg_monthly_revenue.clip(lower=1)
    amount = x.amount.clip(lower=1)
    cash_flow = x.avg_monthly_revenue - x.avg_monthly_expenses
    x['net_cash_flow'] = cash_flow
    x['net_margin'] = (cash_flow / revenue).clip(-100, 100)
    x['principal_monthly'] = amount / x.term_months.clip(lower=1)
    x['principal_coverage'] = (cash_flow / (x.existing_monthly_payments + x.principal_monthly).clip(lower=1)).clip(-100, 100)
    x['loan_to_annual_revenue'] = (amount / (12 * revenue)).clip(0, 100)
    x['debt_to_annual_revenue'] = (x.total_debt / (12 * revenue)).clip(0, 100)
    x['collateral_coverage'] = (x.collateral_value / amount).clip(0, 100)
    x['log_revenue'] = np.log1p(x.avg_monthly_revenue)
    if not np.isfinite(x.drop(columns=CAT_FEATURES).to_numpy(dtype=float)).all():
        raise ValueError('Non-finite features')
    return x
