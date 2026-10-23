from decimal import Decimal

import pytest
from pydantic import ValidationError

from app.models import Product
from app.scoring.base import CompanyFeatures, ScoringRequest, ScoringResult, risk_band_for_pd
from app.scoring.mock import MOCK_MODEL_VERSION, MockScorer
from app.services.checks import check_bki, check_data, check_fns
from app.services.mocks import bki_by_inn, fns_by_inn
from app.services.policy import AssessmentUnavailableError, evaluate_policy

PRODUCTS = {
    'working_capital': Product(
        code='working_capital',
        name='Демо',
        terms_version='demo-1',
        min_amount=300_000,
        max_amount=30_000_000,
        min_term_months=3,
        max_term_months=36,
        margin_pp=Decimal('4'),
        is_active=True,
    ),
    'investment': Product(
        code='investment',
        name='Демо',
        terms_version='demo-1',
        min_amount=1_000_000,
        max_amount=100_000_000,
        min_term_months=12,
        max_term_months=84,
        margin_pp=Decimal('3'),
        is_active=True,
    ),
}


@pytest.mark.parametrize(
    'pd,band',
    [
        ('0.049999', 'A'),
        ('0.05', 'B'),
        ('0.099999', 'B'),
        ('0.10', 'C'),
        ('0.199999', 'C'),
        ('0.20', 'D'),
    ],
)
def test_risk_band_boundaries(pd, band) -> None:
    assert risk_band_for_pd(Decimal(pd)) == band


PROFILES = {
    'client1': ('0000000000', 4_200_000, 3_300_000, 120_000, 'OOO', '47.11'),
    'client2': ('000000000001', 1_300_000, 1_050_000, 60_000, 'IP', '56.10'),
    'client3': ('0000000002', 2_000_000, 1_600_000, 180_000, 'OOO', '41.20'),
    'client4': ('0000000003', 1_500_000, 1_300_000, 200_000, 'OOO', '46.90'),
}


def company_for(profile: str) -> CompanyFeatures:
    _, revenue, expenses, payments, form, okved = PROFILES[profile]
    return CompanyFeatures(
        legal_form=form,
        okved=okved,
        region='Демо-регион',
        business_age_months=120,
        bank_client_months=48,
        avg_monthly_revenue=revenue,
        avg_monthly_expenses=expenses,
        revenue_trend_6m=Decimal('0'),
        existing_monthly_payments=payments,
    )


def case(
    profile='client1',
    amount=3_000_000,
    term=24,
    product='working_capital',
    purpose='закупка',
    **extra,
):
    inn = PROFILES[profile][0]
    application = {
        'company_id': 1,
        'product_code': product,
        'amount': amount,
        'term_months': term,
        'purpose': purpose,
        **extra,
    }
    checks = {
        'DATA': check_data(application, PRODUCTS[product]),
        'FNS': check_fns(fns_by_inn(inn)),
        'BKI': check_bki(bki_by_inn(inn)),
    }
    return application, company_for(profile), checks


def evaluate(application, company, checks, scorer=None):
    return evaluate_policy(
        application, company, checks, PRODUCTS, scorer or MockScorer(Decimal('16')), Decimal('16')
    )


def test_contract_is_deterministic_and_not_a_decision() -> None:
    application, company, _ = case()
    req = ScoringRequest(
        product_code='working_capital',
        amount=application['amount'],
        term_months=24,
        collateral_value=0,
        company=company,
        bki=bki_by_inn(PROFILES['client1'][0]),
        fns=fns_by_inn(PROFILES['client1'][0]),
    )
    scorer = MockScorer(Decimal('16'))
    assert scorer.source == 'mock' and scorer.is_test_result is True
    result = scorer.score(req)
    assert result == scorer.score(req)
    assert Decimal('0') <= result.pd <= Decimal('1')
    assert result.model_version == MOCK_MODEL_VERSION
    assert result.model_dump().keys() == {'pd', 'risk_band', 'model_version', 'reasons'}
    assert all(reason.text and reason.feature for reason in result.reasons)
    assert scorer.score(req.model_copy(update={'amount': 6_000_000})).pd != result.pd
    assert scorer.score(req.model_copy(update={'term_months': 36})).pd != result.pd
    native = req.model_dump(mode='json')
    native['company']['revenue_trend_6m'] = 0.04
    assert ScoringRequest.model_validate(native).company.revenue_trend_6m == Decimal('0.04')
    assert ScoringResult.model_validate(
        {'pd': 0.031, 'risk_band': 'A', 'model_version': 'synthetic-test', 'reasons': []}
    ).pd == Decimal('0.031')
    with pytest.raises(ValidationError):
        ScoringRequest.model_validate(req.model_dump() | {'decision': 'approve'})
    with pytest.raises(ValidationError):
        ScoringResult(pd=Decimal('0.12'), risk_band='A', model_version='test', reasons=[])


@pytest.mark.parametrize(
    'profile,amount,term,expected',
    [
        ('client1', 3_000_000, 24, 'approve'),
        ('client2', 5_000_000, 12, 'counter'),
        ('client3', 4_000_000, 24, 'manual_review'),
        ('client4', 3_000_000, 24, 'reject'),
    ],
)
def test_demo_decisions(profile, amount, term, expected) -> None:
    application, company, checks = case(profile, amount, term)
    result = evaluate(application, company, checks)
    assert result.decision == expected
    assert result.rationale and result.reason_codes
    assert result.rule_version.startswith('policy-demo-')
    if expected in ('approve', 'counter'):
        assert result.offer is not None
        product = PRODUCTS[result.offer.product_code]
        assert product.min_amount <= result.offer.amount <= product.max_amount
        assert product.min_term_months <= result.offer.term_months <= product.max_term_months
    else:
        assert result.offer is None


def test_missing_investment_data_and_stop_precedence() -> None:
    application, company, checks = case(
        product='investment', amount=8_000_000, term=36, purpose='investment'
    )
    assert evaluate(application, company, checks).decision == 'needs_info'
    assert 'объект инвестиций' in evaluate(application, company, checks).rationale[0]
    application['investment_object'] = 'Станок'
    checks['DATA'] = check_data(application, PRODUCTS['investment'])
    assert 'вид залога' in evaluate(application, company, checks).rationale[0]
    rejected, company4, checks4 = case(
        'client4', product='investment', amount=8_000_000, term=36, purpose='investment'
    )
    assert evaluate(rejected, company4, checks4).decision == 'reject'


def test_manual_overdue_and_amount_boundaries() -> None:
    application, company, checks = case()
    for overdue, expected in (
        (30, 'approve'),
        (31, 'manual_review'),
        (90, 'manual_review'),
        (91, 'reject'),
    ):
        bki = bki_by_inn(PROFILES['client1'][0]) | {'max_overdue_days_12m': overdue}
        checks['BKI'] = check_bki(bki)
        assert evaluate(application, company, checks).decision == expected
    application, company, checks = case(
        product='investment',
        amount=30_000_001,
        term=36,
        purpose='investment',
        investment_object='Станок',
        collateral_type='Оборудование',
        collateral_value=20_000_000,
    )
    assert evaluate(application, company, checks).decision == 'manual_review'
    application['amount'] = 30_000_000
    checks['DATA'] = check_data(application, PRODUCTS['investment'])
    assert evaluate(application, company, checks).reason_codes != ('AMOUNT_MANUAL',)


def test_independent_stop_factors() -> None:
    application, company, checks = case()
    checks['FNS'] = check_fns({'status': 'liquidating', 'tax_debt': False, 'mass_address': False})
    assert evaluate(application, company, checks).reason_codes == ('FNS_INACTIVE',)
    checks['FNS'] = check_fns(fns_by_inn(PROFILES['client1'][0]))
    checks['BKI'] = check_bki(bki_by_inn(PROFILES['client1'][0]) | {'defaults': 1})
    assert evaluate(application, company, checks).reason_codes == ('BKI_STOP',)


class FixedScorer:
    def __init__(self, pd: str):
        self.pd = Decimal(pd)
        self.seen = []

    def score(self, req):
        key = (req.product_code, req.amount, req.term_months)
        assert key not in self.seen
        self.seen.append(key)
        return ScoringResult(
            pd=self.pd,
            risk_band=risk_band_for_pd(self.pd),
            model_version='synthetic-test',
            reasons=[],
        )


def test_pd_boundaries_no_alternative_and_unique_variants() -> None:
    application, company, checks = case(amount=3_000_000, term=36)
    at_twelve = FixedScorer('0.12')
    assert evaluate(application, company, checks, at_twelve).decision == 'manual_review'
    assert len(at_twelve.seen) == len(set(at_twelve.seen))
    at_twenty_five = FixedScorer('0.25')
    assert evaluate(application, company, checks, at_twenty_five).decision == 'reject'
    below_twenty_five = FixedScorer('0.249999')
    assert evaluate(application, company, checks, below_twenty_five).decision == 'manual_review'


def test_dscr_threshold_is_inclusive() -> None:
    from app.services.pricing import credit_terms

    application, company, checks = case(amount=300_000, term=3)
    payment = credit_terms(
        PRODUCTS['working_capital'], 300_000, 3, Decimal('16'), 'C'
    ).monthly_payment
    company = company.model_copy(
        update={
            'avg_monthly_revenue': 1_250_000,
            'avg_monthly_expenses': 0,
            'existing_monthly_payments': 1_000_000 - payment,
        }
    )
    assert evaluate(application, company, checks, FixedScorer('0.119999')).decision == 'approve'
    below = company.model_copy(update={'avg_monthly_revenue': 1_249_999})
    assert evaluate(application, below, checks, FixedScorer('0.119999')).decision != 'approve'


def test_alternative_product_and_failure_are_safe() -> None:
    application, company, checks = case(
        product='working_capital',
        amount=5_000_000,
        term=12,
        purpose='equipment_purchase',
        investment_object='Станок',
        collateral_type='Оборудование',
        collateral_value=5_000_000,
    )
    company = company.model_copy(
        update={'avg_monthly_revenue': 1_300_000, 'avg_monthly_expenses': 1_000_000}
    )
    result = evaluate(application, company, checks)
    assert result.decision == 'counter'
    assert result.offer.kind == 'alternative_product'
    assert result.offer.product_code == 'investment'
    checks['BKI'] = check_bki(None)
    with pytest.raises(AssessmentUnavailableError):
        evaluate(application, company, checks)

    class BrokenScorer:
        def score(self, _req):
            raise TimeoutError('synthetic scorer timeout')

    checks['BKI'] = check_bki(bki_by_inn(PROFILES['client1'][0]))
    with pytest.raises(AssessmentUnavailableError):
        evaluate(application, company, checks, BrokenScorer())
    application['amount'] = 4_900_000
    with pytest.raises(AssessmentUnavailableError):
        evaluate(application, company, checks)
