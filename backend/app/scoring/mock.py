"""Прозрачная синтетическая скоркарта, не обученная ML-модель."""

from collections.abc import Mapping
from decimal import ROUND_HALF_UP, Decimal, localcontext

from app.scoring.base import ScoreReason, ScoringRequest, ScoringResult, risk_band_for_pd
from app.services.pricing import annual_rate, annuity_payment, dscr

MOCK_MODEL_VERSION = 'mock-demo-2026-1'
_DEFAULT_MARGINS = {'working_capital': Decimal('4'), 'investment': Decimal('3')}


class MockScorer:
    source = 'mock'
    is_test_result = True
    model_version = MOCK_MODEL_VERSION

    def __init__(
        self,
        key_rate: Decimal,
        product_margins: Mapping[str, Decimal] | None = None,
    ) -> None:
        self.key_rate = key_rate
        self.product_margins = dict(product_margins or _DEFAULT_MARGINS)

    def score(self, req: ScoringRequest) -> ScoringResult:
        if not isinstance(req, ScoringRequest):
            raise TypeError('ScoringRequest required')
        if req.product_code not in self.product_margins:
            raise ValueError('product margin unavailable')
        company, bki, fns = req.company, req.bki, req.fns
        with localcontext() as context:
            context.prec = 50
            assumed_rate = annual_rate(self.key_rate, self.product_margins[req.product_code], 'B')
            payment = annuity_payment(req.amount, assumed_rate, req.term_months)
            coverage = dscr(
                company.avg_monthly_revenue,
                company.avg_monthly_expenses,
                company.existing_monthly_payments,
                payment,
            )
            annual_revenue = max(company.avg_monthly_revenue * 12, 1)
            loan_ratio = Decimal(req.amount) / annual_revenue
            debt_ratio = Decimal(bki.total_debt) / annual_revenue
            pd = Decimal('0.025')
            reasons: list[ScoreReason] = []

            def add(feature: str, description: str, contribution: Decimal) -> None:
                nonlocal pd
                pd += contribution
                if contribution:
                    reasons.append(
                        ScoreReason(
                            feature=feature,
                            text=description,
                            effect='increases_risk' if contribution > 0 else 'decreases_risk',
                            contribution=contribution,
                        )
                    )

            if bki.max_overdue_days_12m:
                add(
                    'max_overdue_days_12m',
                    'В синтетической истории есть просрочка',
                    min(Decimal(bki.max_overdue_days_12m) * Decimal('0.003'), Decimal('0.30')),
                )
            else:
                add('max_overdue_days_12m', 'Нет просрочек за 12 месяцев', Decimal('-0.003'))
            if bki.defaults:
                add('defaults', 'В синтетической истории есть дефолт', Decimal('0.30'))
            if fns.status != 'active':
                add('fns_status', 'Организация неактивна по синтетическим данным', Decimal('0.30'))
            if fns.tax_debt:
                add('tax_debt', 'Есть синтетическая налоговая задолженность', Decimal('0.025'))
            if fns.mass_address:
                add('mass_address', 'Синтетический массовый адрес', Decimal('0.01'))
            if company.legal_form == 'IP':
                add('legal_form', 'Форма организации учтена в демо-оценке', Decimal('0.005'))
            if company.okved.startswith('41.'):
                add('okved', 'Отрасль учтена в демо-оценке', Decimal('0.01'))
            if company.region != 'Демо-регион':
                add('region', 'Регион учтён в демо-оценке', Decimal('0.002'))
            if company.business_age_months < 24:
                add('business_age_months', 'Небольшой срок работы бизнеса', Decimal('0.02'))
            if company.bank_client_months < 12:
                add('bank_client_months', 'Небольшой срок обслуживания', Decimal('0.01'))
            if company.revenue_trend_6m < 0:
                add(
                    'revenue_trend_6m',
                    'Снижение выручки',
                    min(-company.revenue_trend_6m * Decimal('0.1'), Decimal('0.05')),
                )
            if bki.active_loans > 2:
                add('active_loans', 'Несколько действующих кредитов', Decimal('0.005'))
            add(
                'total_debt',
                'Учтена текущая задолженность',
                min(debt_ratio * Decimal('0.01'), Decimal('0.03')),
            )
            add(
                'amount',
                'Учтена запрошенная сумма',
                min(loan_ratio * Decimal('0.02'), Decimal('0.05')),
            )
            add(
                'term_months',
                'Учтён срок кредита',
                Decimal(req.term_months) / Decimal(84) * Decimal('0.01'),
            )
            if coverage < Decimal('1.25'):
                add('dscr', 'Платёжная нагрузка выше демо-порога', Decimal('0.04'))
            elif coverage < Decimal('2'):
                add('dscr', 'Запас платёжеспособности невелик', Decimal('0.01'))
            if req.collateral_value:
                coverage_ratio = Decimal(req.collateral_value) / req.amount
                add(
                    'collateral_value',
                    'Учтено обеспечение',
                    -min(coverage_ratio * Decimal('0.01'), Decimal('0.01')),
                )
            pd = max(Decimal('0'), min(pd, Decimal('1'))).quantize(
                Decimal('0.000001'), rounding=ROUND_HALF_UP
            )
            return ScoringResult(
                pd=pd,
                risk_band=risk_band_for_pd(pd),
                model_version=MOCK_MODEL_VERSION,
                reasons=reasons,
            )
