"""Внутренние проверки; результат и сырые данные не предназначены клиентскому API."""

import json
from collections.abc import Callable
from dataclasses import dataclass
from hashlib import sha256
from typing import Any

from sqlalchemy.orm import Session

from app.models import Application, ApplicationSnapshot, CheckResult, Company, Product
from app.services.mocks import MOCK_VERSION, bki_by_inn, fns_by_inn

CHECK_VERSION = 'checks-demo-2026-1'
COLLATERAL_THRESHOLD = 5_000_000  # Демонстрационное допущение MVP.


@dataclass(frozen=True)
class CheckOutcome:
    status: str
    summary: str
    payload: dict[str, Any]


def _unavailable(source: str, code: str) -> CheckOutcome:
    return CheckOutcome(
        'warn',
        f'Синтетические данные {source} недоступны или неполны; требуется повторная проверка',
        {'available': False, 'error_code': code},
    )


def check_data(snapshot: dict[str, Any], product: Product | None) -> CheckOutcome:
    if not isinstance(snapshot, dict):
        return _unavailable('заявки', 'INVALID_SNAPSHOT')
    try:
        canonical = json.dumps(
            snapshot, sort_keys=True, ensure_ascii=False, separators=(',', ':')
        ).encode('utf-8')
    except (TypeError, ValueError):
        return _unavailable('заявки', 'INVALID_SNAPSHOT')
    fingerprint = sha256(canonical).hexdigest()
    missing = [
        name
        for name in ('company_id', 'product_code', 'amount', 'term_months', 'purpose')
        if snapshot.get(name) is None
        or isinstance(snapshot.get(name), str)
        and not snapshot[name].strip()
    ]
    if product is None or snapshot.get('product_code') != product.code or not product.is_active:
        return _unavailable('продукта', 'PRODUCT_UNAVAILABLE')
    if product.code == 'investment':
        if (
            not isinstance(snapshot.get('investment_object'), str)
            or not snapshot['investment_object'].strip()
        ):
            missing.append('investment_object')
        if type(snapshot.get('amount')) is int and snapshot['amount'] >= COLLATERAL_THRESHOLD:
            if (
                not isinstance(snapshot.get('collateral_type'), str)
                or not snapshot['collateral_type'].strip()
            ):
                missing.append('collateral_type')
            if (
                type(snapshot.get('collateral_value')) is not int
                or snapshot['collateral_value'] <= 0
            ):
                missing.append('collateral_value')
    if missing:
        return CheckOutcome(
            'warn',
            'Нужно дополнить сведения заявки',
            {
                'complete': False,
                'missing_fields': missing,
                'product_code': product.code,
                'input_fingerprint': fingerprint,
            },
        )
    amount, term = snapshot['amount'], snapshot['term_months']
    if (
        type(snapshot['company_id']) is not int
        or snapshot['company_id'] <= 0
        or type(amount) is not int
        or not product.min_amount <= amount <= product.max_amount
        or type(term) is not int
        or not product.min_term_months <= term <= product.max_term_months
        or not isinstance(snapshot['purpose'], str)
    ):
        return CheckOutcome(
            'fail',
            'Параметры заявки вне ограничений продукта',
            {
                'complete': False,
                'error_code': 'INVALID_APPLICATION_TERMS',
                'input_fingerprint': fingerprint,
            },
        )
    return CheckOutcome(
        'ok',
        'Обязательные сведения заполнены',
        {
            'complete': True,
            'product_code': product.code,
            'terms_version': product.terms_version,
            'input_fingerprint': fingerprint,
        },
    )


def check_fns(data: dict[str, Any] | None) -> CheckOutcome:
    if not isinstance(data, dict) or (
        data.get('status') not in ('active', 'liquidating', 'inactive')
        or type(data.get('tax_debt')) is not bool
        or type(data.get('mass_address')) is not bool
    ):
        return _unavailable('ФНС', 'INVALID_FNS_RESPONSE')
    payload = {
        'available': True,
        'status': data['status'],
        'tax_debt': data['tax_debt'],
        'mass_address': data['mass_address'],
        'mock_version': MOCK_VERSION,
    }
    if data['status'] != 'active':
        return CheckOutcome('fail', 'Синтетический статус организации неактивен', payload)
    if data['tax_debt'] or data['mass_address']:
        return CheckOutcome(
            'warn', 'В синтетических сведениях ФНС есть признаки для проверки', payload
        )
    return CheckOutcome('ok', 'Синтетические сведения ФНС проверены', payload)


def check_bki(data: dict[str, Any] | None) -> CheckOutcome:
    fields = ('active_loans', 'total_debt', 'max_overdue_days_12m', 'defaults')
    if not isinstance(data, dict) or any(
        type(data.get(name)) is not int or data[name] < 0 for name in fields
    ):
        return _unavailable('БКИ', 'INVALID_BKI_RESPONSE')
    if (
        data['active_loans'] > 1_000
        or data['total_debt'] > 9_223_372_036_854_775_807
        or data['max_overdue_days_12m'] > 365
        or data['defaults'] > 1_000
        or (data['active_loans'] == 0 and data['total_debt'] > 0)
    ):
        return _unavailable('БКИ', 'INVALID_BKI_RESPONSE')
    payload = {
        'available': True,
        **{name: data[name] for name in fields},
        'mock_version': MOCK_VERSION,
    }
    if data['defaults'] > 0 or data['max_overdue_days_12m'] > 90:
        return CheckOutcome('fail', 'Синтетическая кредитная история содержит стоп-фактор', payload)
    if data['max_overdue_days_12m'] > 0:
        return CheckOutcome('warn', 'В синтетической кредитной истории есть просрочка', payload)
    return CheckOutcome('ok', 'Синтетическая кредитная история проверена', payload)


def run_checks(
    session: Session,
    application: Application,
    snapshot: ApplicationSnapshot,
    *,
    fns_provider: Callable[[str], dict] = fns_by_inn,
    bki_provider: Callable[[str], dict] = bki_by_inn,
) -> list[CheckResult]:
    """Добавляет три записи к снимку. Вызывающий управляет транзакцией и правами."""
    if snapshot.application_id != application.id or snapshot.id is None:
        raise ValueError('snapshot does not belong to application')
    company = session.get(Company, application.company_id)
    if company is None or snapshot.data.get('company_id') != company.id:
        raise ValueError('application company and snapshot disagree')
    frozen_company = snapshot.data.get('company_profile') or {}
    inn = frozen_company.get('inn', company.inn)
    product = session.get(Product, application.product_code)
    outcomes = {'DATA': check_data(snapshot.data, product)}
    for source, provider, checker in (
        ('FNS', fns_provider, check_fns),
        ('BKI', bki_provider, check_bki),
    ):
        try:
            response = provider(inn)
        except Exception:  # noqa: BLE001 -- граница локального адаптера: сбой не означает отказ
            outcomes[source] = _unavailable(source, 'MOCK_UNAVAILABLE')
        else:
            outcomes[source] = checker(response)
    rows = [
        CheckResult(
            application_id=application.id,
            snapshot_id=snapshot.id,
            source=source,
            status=outcome.status,
            summary=outcome.summary,
            payload=outcome.payload,
            adapter_version=CHECK_VERSION if source == 'DATA' else MOCK_VERSION,
        )
        for source, outcome in outcomes.items()
    ]
    session.add_all(rows)
    session.flush()
    return rows
