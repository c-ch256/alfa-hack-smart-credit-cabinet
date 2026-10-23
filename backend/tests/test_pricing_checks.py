from decimal import Decimal

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from app.config import Settings
from app.db import Base
from app.models import Application, ApplicationSnapshot, CheckResult, Company, Product, User
from app.seed import seed_demo_catalog, seed_demo_users
from app.services.checks import check_bki, check_data, check_fns, run_checks
from app.services.mocks import MockUnavailableError, bki_by_inn, fns_by_inn
from app.services.pricing import annual_rate, annuity_payment, credit_terms, dscr, payment_totals


def test_pricing_and_dscr() -> None:
    assert annual_rate(Decimal('16'), Decimal('4'), 'B') == Decimal('21.5')
    assert annuity_payment(3_000_000, Decimal('21.5'), 24) == 154_895
    assert payment_totals(3_000_000, 154_895, 24) == (3_717_480, 717_480)
    assert annuity_payment(300_000, Decimal('0'), 3) == 100_000
    assert annuity_payment(1, Decimal('0'), 2) == 1  # Полрубля округляется вверх.
    assert dscr(4_200_000, 3_300_000, 120_000, 180_000) == Decimal('3')
    assert dscr(100, 200, 10, 10) == Decimal('-5')
    assert dscr(125, 0, 0, 100) == Decimal('1.25')


@pytest.mark.parametrize(
    'amount,rate,term',
    [
        (0, Decimal('5'), 12),
        (100, Decimal('-1'), 12),
        (100, Decimal('NaN'), 12),
        (100, Decimal('5'), 0),
        (True, Decimal('5'), 12),
    ],
)
def test_pricing_rejects_invalid_inputs(amount, rate, term) -> None:
    with pytest.raises(ValueError):
        annuity_payment(amount, rate, term)


def test_pricing_product_boundaries_and_rate_config(monkeypatch) -> None:
    product = Product(
        code='investment',
        name='Демо',
        terms_version='demo-1',
        min_amount=1_000_000,
        max_amount=100_000_000,
        min_term_months=12,
        max_term_months=84,
        margin_pp=Decimal('3'),
        is_active=True,
    )
    assert credit_terms(product, 1_000_000, 12, Decimal('16'), 'A').rate == Decimal('19')
    assert credit_terms(product, 100_000_000, 84, Decimal('16'), 'D').rate == Decimal('24')
    with pytest.raises(ValueError):
        credit_terms(product, 999_999, 12, Decimal('16'), 'A')
    with pytest.raises(ValueError):
        credit_terms(product, 1_000_000, 85, Decimal('16'), 'A')
    with pytest.raises(ValueError):
        dscr(0, 0, 0, 0)
    with pytest.raises(ValueError):
        annual_rate(Decimal('16'), Decimal('3'), 'E')
    monkeypatch.setenv('KEY_RATE', '17.25')
    assert Settings().key_rate == Decimal('17.25')


@pytest.mark.parametrize(
    'inn,fns_status,bki_status,overdue',
    [
        ('0000000000', 'ok', 'ok', 0),
        ('000000000001', 'ok', 'warn', 5),
        ('0000000002', 'warn', 'warn', 45),
        ('0000000003', 'fail', 'fail', 120),
    ],
)
def test_all_demo_profiles(inn, fns_status, bki_status, overdue) -> None:
    fns = fns_by_inn(inn)
    bki = bki_by_inn(inn)
    assert check_fns(fns).status == fns_status
    assert check_bki(bki).status == bki_status
    assert bki['max_overdue_days_12m'] == overdue
    assert fns_by_inn(inn) == fns
    assert bki_by_inn(inn) == bki
    fns['status'] = 'inactive'
    assert fns_by_inn(inn)['status'] != 'inactive'


def test_incomplete_unavailable_and_stop_boundaries() -> None:
    assert check_fns(None).payload['error_code'] == 'INVALID_FNS_RESPONSE'
    assert check_fns({'status': 'active'}).status == 'warn'
    assert check_fns({'status': 'active', 'tax_debt': False, 'mass_address': True}).status == 'warn'
    assert check_bki(None).status == 'warn'
    assert check_bki({'active_loans': True}).status == 'warn'
    base = {'active_loans': 1, 'total_debt': 100, 'max_overdue_days_12m': 90, 'defaults': 0}
    assert check_bki(base).status == 'warn'
    assert check_bki(base | {'max_overdue_days_12m': 91}).status == 'fail'
    assert check_bki(base | {'defaults': 1, 'max_overdue_days_12m': 0}).status == 'fail'
    with pytest.raises(MockUnavailableError):
        fns_by_inn('9999999999')


def test_data_completeness_and_collateral_boundary() -> None:
    product = Product(
        code='investment',
        name='Демо',
        terms_version='demo-1',
        min_amount=1_000_000,
        max_amount=100_000_000,
        min_term_months=12,
        max_term_months=84,
        margin_pp=Decimal('3'),
        is_active=True,
    )
    data = {
        'company_id': 1,
        'product_code': 'investment',
        'amount': 4_999_999,
        'term_months': 12,
        'purpose': 'Оборудование',
        'investment_object': 'Станок',
    }
    assert check_data(data, product).status == 'ok'
    assert check_data(data | {'amount': 5_000_000}, product).payload['missing_fields'] == [
        'collateral_type',
        'collateral_value',
    ]
    assert (
        check_data(
            data | {'amount': 5_000_000, 'collateral_type': 'Оборудование', 'collateral_value': 1},
            product,
        ).status
        == 'ok'
    )
    assert check_data(data | {'amount': 100_000_001}, product).status == 'warn'
    assert check_data(data | {'amount': 999_999}, product).status == 'fail'
    assert check_data(data | {'investment_object': '  '}, product).status == 'warn'


def test_runs_append_history_and_keep_mock_failures_distinct(tmp_path) -> None:
    def unavailable_fns(_inn: str) -> dict:
        raise MockUnavailableError('synthetic fixture unavailable')

    engine = create_engine(f'sqlite+pysqlite:///{tmp_path / "checks.sqlite"}')
    Base.metadata.create_all(engine)
    settings = Settings(env='test', demo_password='synthetic-demo-password-only')
    seed_demo_users(engine, settings)
    seed_demo_catalog(engine)
    try:
        with Session(engine) as session:
            user = session.scalar(select(User).where(User.login == 'client1'))
            company = session.scalar(select(Company).where(Company.owner_user_id == user.id))
            application = Application(
                number='SYN-CHECK-1',
                company_id=company.id,
                created_by_user_id=user.id,
                product_code='working_capital',
                amount=3_000_000,
                term_months=24,
                purpose='Закупка',
                idempotency_key='syn-check-1',
                version=1,
            )
            session.add(application)
            session.flush()
            snapshot = ApplicationSnapshot(
                application_id=application.id,
                version=1,
                data={
                    'company_id': company.id,
                    'product_code': 'working_capital',
                    'amount': 3_000_000,
                    'term_months': 24,
                    'purpose': 'Закупка',
                },
            )
            session.add(snapshot)
            session.flush()
            first = run_checks(session, application, snapshot)
            second = run_checks(session, application, snapshot, bki_provider=lambda _inn: None)
            third = run_checks(session, application, snapshot, fns_provider=unavailable_fns)
            assert [row.source for row in first] == ['DATA', 'FNS', 'BKI']
            assert [row.status for row in first] == ['ok', 'ok', 'ok']
            assert second[2].status == 'warn'
            assert second[2].payload['error_code'] == 'INVALID_BKI_RESPONSE'
            assert third[1].status == 'warn'
            assert third[1].payload['error_code'] == 'MOCK_UNAVAILABLE'
            session.commit()
            assert session.scalar(select(func.count()).select_from(CheckResult)) == 9
            assert (
                session.scalars(
                    select(CheckResult).where(CheckResult.source == 'BKI').order_by(CheckResult.id)
                )
                .all()[0]
                .status
                == 'ok'
            )
            other = ApplicationSnapshot(
                application_id=application.id + 1, version=2, data=snapshot.data
            )
            with pytest.raises(ValueError):
                run_checks(session, application, other)
            assert session.scalar(select(func.count()).select_from(CheckResult)) == 9
    finally:
        engine.dispose()
