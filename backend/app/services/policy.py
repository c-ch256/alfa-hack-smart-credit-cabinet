"""Версионированные демонстрационные правила MVP, не кредитная политика банка."""

from collections.abc import Mapping
from dataclasses import dataclass
from decimal import ROUND_FLOOR, Decimal
from typing import Any, Literal

from pydantic import ValidationError
from sqlalchemy.exc import SQLAlchemyError

from app.models import Product
from app.scoring.base import (
    BkiFeatures,
    CompanyFeatures,
    FnsFeatures,
    Scorer,
    ScoringRequest,
    ScoringResult,
)
from app.services.checks import COLLATERAL_THRESHOLD, CheckOutcome, check_data
from app.services.pricing import credit_terms, dscr

RULE_VERSION = 'policy-demo-2026-1'
AUTO_LIMIT = 30_000_000  # Демонстрационное допущение MVP.
AUTO_PD_LIMIT = Decimal('0.12')
MANUAL_PD_LIMIT = Decimal('0.25')
MIN_DSCR = Decimal('1.25')
MANUAL_OVERDUE_FROM = 31
STOP_OVERDUE_ABOVE = 90
AMOUNT_STEP_RATIO = Decimal('0.10')
AMOUNT_ROUNDING = 10_000
TERM_STEP_MONTHS = 12

# Альтернатива допускается только для явно распознанной цели.
PURPOSE_PRODUCTS = {
    'equipment_purchase': frozenset({'working_capital', 'investment'}),
    'закупка оборудования': frozenset({'working_capital', 'investment'}),
    'purchase': frozenset({'working_capital'}),
    'закупка': frozenset({'working_capital'}),
    'suppliers': frozenset({'working_capital'}),
    'rent': frozenset({'working_capital'}),
    'contract': frozenset({'working_capital'}),
    'investment': frozenset({'investment'}),
}
FIELD_LABELS = {
    'investment_object': 'объект инвестиций',
    'collateral_type': 'вид залога',
    'collateral_value': 'стоимость залога',
}


class AssessmentUnavailableError(Exception):
    """Не формировать кредитное решение до успешного получения/скоринга данных."""


@dataclass(frozen=True)
class PolicyOffer:
    kind: Literal['original', 'counter', 'alternative_product']
    product_code: str
    amount: int
    term_months: int
    rate: Decimal
    monthly_payment: int


@dataclass(frozen=True)
class PolicyResult:
    decision: Literal['approve', 'counter', 'manual_review', 'reject', 'needs_info']
    rationale: tuple[str, ...]
    reason_codes: tuple[str, ...]
    offer: PolicyOffer | None
    rule_version: str = RULE_VERSION


def _result(decision: str, code: str, text: str, offer: PolicyOffer | None = None) -> PolicyResult:
    return PolicyResult(decision, (text,), (code,), offer)


def _payload(checks: Mapping[str, CheckOutcome], source: str) -> dict[str, Any]:
    check = checks.get(source)
    if check is None or check.payload.get('available') is not True:
        raise AssessmentUnavailableError(f'{source} check unavailable')
    return check.payload


def _score(scorer: Scorer, request: ScoringRequest) -> ScoringResult:
    try:
        result = scorer.score(request)
    except SQLAlchemyError:
        raise
    except Exception as exc:
        raise AssessmentUnavailableError('scoring failed') from exc
    if not isinstance(result, ScoringResult):
        raise AssessmentUnavailableError('invalid scoring result')
    return result


def _amounts(requested: int, product: Product, original: bool) -> list[int]:
    start = min(requested, product.max_amount)
    if start < product.min_amount:
        return []
    amounts = [requested] if original else []
    step = Decimal(requested) * AMOUNT_STEP_RATIO
    index = 0
    while True:
        raw = Decimal(start) - step * index
        if raw < product.min_amount:
            break
        rounded = int((raw / AMOUNT_ROUNDING).to_integral_value(rounding=ROUND_FLOOR))
        value = rounded * AMOUNT_ROUNDING
        if product.min_amount <= value <= product.max_amount and value not in amounts:
            amounts.append(value)
        index += 1
    if product.min_amount % AMOUNT_ROUNDING == 0 and product.min_amount not in amounts:
        amounts.append(product.min_amount)
    return amounts


def _terms(requested: int, product: Product) -> list[int]:
    if requested > product.max_term_months:
        return []
    first = max(requested, product.min_term_months)
    terms = list(range(first, product.max_term_months + 1, TERM_STEP_MONTHS))
    if product.max_term_months not in terms:
        terms.append(product.max_term_months)
    return terms


def _can_offer_investment(application: Mapping[str, Any], amount: int) -> bool:
    if not str(application.get('investment_object') or '').strip():
        return False
    if amount >= COLLATERAL_THRESHOLD and (
        not str(application.get('collateral_type') or '').strip()
        or type(application.get('collateral_value')) is not int
        or application['collateral_value'] <= 0
    ):
        return False
    return True


def precheck_policy(
    application: Mapping[str, Any],
    checks: Mapping[str, CheckOutcome],
    products: Mapping[str, Product],
) -> PolicyResult | None:
    """Проверяет первые три правила до скоринга; workflow применяет только ранние исходы."""
    product_code = application.get('product_code')
    product = products.get(product_code)
    if product is None or not product.is_active:
        raise AssessmentUnavailableError('product unavailable')
    fns_data = _payload(checks, 'FNS')
    bki_data = _payload(checks, 'BKI')
    try:
        fns = FnsFeatures.model_validate(
            {name: fns_data[name] for name in FnsFeatures.model_fields}
        )
        bki = BkiFeatures.model_validate(
            {name: bki_data[name] for name in BkiFeatures.model_fields}
        )
    except (KeyError, ValidationError) as exc:
        raise AssessmentUnavailableError('invalid check data') from exc

    # Приоритет 1: известные стоп-факторы, до вызова скорера.
    if fns.status != 'active':
        return _result('reject', 'FNS_INACTIVE', 'Организация неактивна по синтетическим данным')
    if bki.max_overdue_days_12m > STOP_OVERDUE_ABOVE or bki.defaults > 0:
        return _result('reject', 'BKI_STOP', 'Синтетическая кредитная история содержит стоп-фактор')

    data = checks.get('DATA')
    if data is None:
        raise AssessmentUnavailableError('DATA check unavailable')
    if data != check_data(dict(application), product):
        raise AssessmentUnavailableError('DATA check does not match application')
    missing = data.payload.get('missing_fields')
    if missing:
        fields = ', '.join(FIELD_LABELS.get(name, name) for name in missing)
        return _result('needs_info', 'MISSING_DATA', f'Дослать сведения: {fields}')
    if data.status != 'ok' or data.payload.get('complete') is not True:
        raise AssessmentUnavailableError('DATA check incomplete or invalid')
    amount, term = application.get('amount'), application.get('term_months')
    if (
        type(amount) is not int
        or type(term) is not int
        or not product.min_amount <= amount <= product.max_amount
        or not product.min_term_months <= term <= product.max_term_months
    ):
        raise AssessmentUnavailableError('application terms invalid')
    if product_code == 'investment' and not _can_offer_investment(application, amount):
        return _result(
            'needs_info',
            'MISSING_INVESTMENT_DATA',
            'Дослать объект инвестиций и необходимые сведения о залоге',
        )

    # Приоритет 3: пограничные заявки остаются у сотрудника.
    if MANUAL_OVERDUE_FROM <= bki.max_overdue_days_12m <= STOP_OVERDUE_ABOVE:
        return _result('manual_review', 'OVERDUE_MANUAL', 'Нужно рассмотрение сотрудником')
    if amount > AUTO_LIMIT:
        return _result('manual_review', 'AMOUNT_MANUAL', 'Нужно рассмотрение сотрудником')
    return None


def build_scoring_request(
    application: Mapping[str, Any],
    company: CompanyFeatures,
    checks: Mapping[str, CheckOutcome],
) -> ScoringRequest:
    fns_data = _payload(checks, 'FNS')
    bki_data = _payload(checks, 'BKI')
    try:
        fns = FnsFeatures.model_validate(
            {name: fns_data[name] for name in FnsFeatures.model_fields}
        )
        bki = BkiFeatures.model_validate(
            {name: bki_data[name] for name in BkiFeatures.model_fields}
        )
        return ScoringRequest(
            product_code=application['product_code'],
            amount=application['amount'],
            term_months=application['term_months'],
            collateral_value=application.get('collateral_value') or 0,
            company=company,
            bki=bki,
            fns=fns,
        )
    except (KeyError, ValidationError) as exc:
        raise AssessmentUnavailableError('scoring input invalid') from exc


def evaluate_policy(
    application: Mapping[str, Any],
    company: CompanyFeatures,
    checks: Mapping[str, CheckOutcome],
    products: Mapping[str, Product],
    scorer: Scorer,
    key_rate: Decimal,
) -> PolicyResult:
    """Чистое решение по снимку; без API, БД, переходов и доступа клиента."""
    preliminary = precheck_policy(application, checks, products)
    if preliminary is not None:
        return preliminary
    product_code = application['product_code']
    product = products[product_code]
    amount, term = application['amount'], application['term_months']
    request = build_scoring_request(application, company, checks)
    original_score = _score(scorer, request)
    original_terms = credit_terms(product, amount, term, key_rate, original_score.risk_band)
    original_dscr = dscr(
        company.avg_monthly_revenue,
        company.avg_monthly_expenses,
        company.existing_monthly_payments,
        original_terms.monthly_payment,
    )
    if original_score.pd < AUTO_PD_LIMIT and original_dscr >= MIN_DSCR:
        return _result(
            'approve',
            'ORIGINAL_ELIGIBLE',
            'Запрошенные условия проходят демо-правила',
            PolicyOffer(
                'original',
                product_code,
                amount,
                term,
                original_terms.rate,
                original_terms.monthly_payment,
            ),
        )

    # Приоритет 5: уникальные варианты. Пересчёт скоринга для каждого нового запроса.
    seen = {(product_code, amount, term)}
    best_pd = original_score.pd
    best: PolicyOffer | None = None
    allowed = PURPOSE_PRODUCTS.get(
        str(application.get('purpose') or '').strip().lower(), frozenset({product_code})
    )
    for code in (product_code, *sorted(allowed - {product_code})):
        candidate_product = products.get(code)
        if candidate_product is None or not candidate_product.is_active:
            continue
        for candidate_amount in _amounts(amount, candidate_product, code == product_code):
            if candidate_amount > AUTO_LIMIT:
                continue
            if code == 'investment' and not _can_offer_investment(application, candidate_amount):
                continue
            for candidate_term in _terms(term, candidate_product):
                key = (code, candidate_amount, candidate_term)
                if key in seen:
                    continue
                seen.add(key)
                candidate = request.model_copy(
                    update={
                        'product_code': code,
                        'amount': candidate_amount,
                        'term_months': candidate_term,
                        'collateral_value': application.get('collateral_value') or 0
                        if code == 'investment'
                        else 0,
                    }
                )
                score = _score(scorer, candidate)
                best_pd = min(best_pd, score.pd)
                terms = credit_terms(
                    candidate_product, candidate_amount, candidate_term, key_rate, score.risk_band
                )
                coverage = dscr(
                    company.avg_monthly_revenue,
                    company.avg_monthly_expenses,
                    company.existing_monthly_payments,
                    terms.monthly_payment,
                )
                if score.pd >= AUTO_PD_LIMIT or coverage < MIN_DSCR:
                    continue
                offer = PolicyOffer(
                    'alternative_product' if code != product_code else 'counter',
                    code,
                    candidate_amount,
                    candidate_term,
                    terms.rate,
                    terms.monthly_payment,
                )
                if best is None or (offer.amount, -offer.monthly_payment) > (
                    best.amount,
                    -best.monthly_payment,
                ):
                    best = offer
    if best is not None:
        return _result(
            'counter', 'ALTERNATIVE_ELIGIBLE', 'Найдены допустимые демонстрационные условия', best
        )
    if best_pd < MANUAL_PD_LIMIT:
        return _result(
            'manual_review',
            'NO_AUTOMATIC_OFFER',
            'Автоматических условий нет; требуется рассмотрение сотрудником',
        )
    return _result('reject', 'HIGH_RISK_NO_OFFER', 'Не найдены допустимые демонстрационные условия')
