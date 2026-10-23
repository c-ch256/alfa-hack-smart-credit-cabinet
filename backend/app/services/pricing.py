"""Демонстрационный расчёт условий MVP; ставки не являются тарифами банка."""

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal, localcontext

from app.models import Product

RISK_PREMIUM_PP = {
    'A': Decimal('0'),
    'B': Decimal('1.5'),
    'C': Decimal('3'),
    'D': Decimal('5'),
}
RUBLE = Decimal('1')


@dataclass(frozen=True)
class CreditTerms:
    rate: Decimal
    monthly_payment: int
    total_payments: int
    overpayment: int


def _nonnegative_decimal(value: Decimal, name: str) -> Decimal:
    if not isinstance(value, Decimal) or not value.is_finite() or value < 0:
        raise ValueError(f'{name} must be a finite nonnegative Decimal')
    return value


def annual_rate(key_rate: Decimal, margin_pp: Decimal, risk_band: str) -> Decimal:
    """Годовая ставка в процентах: KEY_RATE + маржа + надбавка за риск."""
    if risk_band not in RISK_PREMIUM_PP:
        raise ValueError('unknown risk band')
    return (
        _nonnegative_decimal(key_rate, 'key_rate')
        + _nonnegative_decimal(margin_pp, 'margin_pp')
        + RISK_PREMIUM_PP[risk_band]
    )


def annuity_payment(amount: int, rate: Decimal, term_months: int) -> int:
    """Аннуитет за месяц, округлённый до целого рубля по ROUND_HALF_UP."""
    if type(amount) is not int or amount <= 0:
        raise ValueError('amount must be a positive integer of rubles')
    if type(term_months) is not int or term_months <= 0:
        raise ValueError('term_months must be a positive integer')
    _nonnegative_decimal(rate, 'rate')
    with localcontext() as context:
        context.prec = 50
        monthly_rate = rate / Decimal('1200')
        if monthly_rate == 0:
            payment = Decimal(amount) / term_months
        else:
            payment = Decimal(amount) * monthly_rate / (1 - (1 + monthly_rate) ** -term_months)
        return int(payment.quantize(RUBLE, rounding=ROUND_HALF_UP))


def payment_totals(amount: int, monthly_payment: int, term_months: int) -> tuple[int, int]:
    if type(amount) is not int or amount <= 0:
        raise ValueError('amount must be a positive integer of rubles')
    if type(monthly_payment) is not int or monthly_payment < 0:
        raise ValueError('monthly_payment must be a nonnegative integer')
    if type(term_months) is not int or term_months <= 0:
        raise ValueError('term_months must be a positive integer')
    total = monthly_payment * term_months
    return total, total - amount


def dscr(
    monthly_revenue: int,
    monthly_expenses: int,
    existing_monthly_payments: int,
    new_monthly_payment: int,
) -> Decimal:
    """(Выручка − расходы) / (текущие платежи + новый платёж)."""
    values = (monthly_revenue, monthly_expenses, existing_monthly_payments, new_monthly_payment)
    if any(type(value) is not int or value < 0 for value in values):
        raise ValueError('DSCR inputs must be nonnegative integer rubles')
    denominator = existing_monthly_payments + new_monthly_payment
    if denominator == 0:
        raise ValueError('DSCR is undefined when total monthly payments are zero')
    with localcontext() as context:
        context.prec = 50
        return Decimal(monthly_revenue - monthly_expenses) / denominator


def credit_terms(
    product: Product, amount: int, term_months: int, key_rate: Decimal, risk_band: str
) -> CreditTerms:
    if not product.is_active or not product.min_amount <= amount <= product.max_amount:
        raise ValueError('amount is outside active product limits')
    if not product.min_term_months <= term_months <= product.max_term_months:
        raise ValueError('term is outside product limits')
    rate = annual_rate(key_rate, product.margin_pp, risk_band)
    payment = annuity_payment(amount, rate, term_months)
    total, overpayment = payment_totals(amount, payment, term_months)
    return CreditTerms(rate, payment, total, overpayment)
