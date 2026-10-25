# ruff: noqa: F811 -- pytest fixtures imported for parametrized API tests

import asyncio
from datetime import date
from decimal import Decimal

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.orm import Session
from test_applications import api, auth  # noqa: F401 -- pytest fixture
from test_completion import submitted_offer

from app.models import Company, CreditContract, Offer, User
from app.services.pricing import annual_rate, annuity_payment, payment_schedule


def test_company_requisites_and_base_rate(api):
    async def check():
        async with AsyncClient(transport=ASGITransport(app=api), base_url='http://testserver') as c:
            for login, field, missing in (
                ('client1', 'ogrn', 'ogrnip'),
                ('client2', 'ogrnip', 'ogrn'),
            ):
                headers = await auth(c, login)
                company = (await c.get('/me/companies', headers=headers)).json()[0]
                result = await c.get(f'/me/companies/{company["id"]}/requisites', headers=headers)
                assert result.status_code == 200
                assert result.json()[field] and result.json()[missing] is None
                assert result.json()['demonstration'] is True
            assert (
                await c.get(f'/me/companies/{company["id"]}/requisites', headers=await auth(c))
            ).status_code == 404

    asyncio.run(check())
    assert api.state.settings.key_rate == Decimal('14.00')
    assert annual_rate(Decimal('14'), Decimal('4'), 'A') == Decimal('18')
    assert annual_rate(Decimal('14'), Decimal('3'), 'A') == Decimal('17')


@pytest.mark.parametrize(
    'login,fields,expected_status',
    [
        ('client1', None, 'approved'),
        ('client2', {'amount': 5_000_000, 'term_months': 12}, 'counter_offer'),
    ],
)
def test_contract_preview_accept_pdf_and_immutable_snapshot(api, login, fields, expected_status):
    async def check():
        async with AsyncClient(transport=ASGITransport(app=api), base_url='http://testserver') as c:
            application_id, offer_id, owner, status = await submitted_offer(
                c, login=login, fields=fields, key=f'contract-{login}'
            )
            assert status == expected_status
            outsider = await auth(c, 'client4')
            staff = await auth(c, 'staff1')
            for suffix in ('contract', 'contract.pdf', 'schedule'):
                assert (
                    await c.get(f'/offers/{offer_id}/{suffix}', headers=outsider)
                ).status_code == 404
                assert (
                    await c.get(f'/offers/{offer_id}/{suffix}', headers=staff)
                ).status_code == 404
            preview = (await c.get(f'/offers/{offer_id}/contract', headers=owner)).json()
            assert preview['fixed'] is False
            assert preview['borrower']['inn']
            assert preview['notice'].startswith('ДЕМОНСТРАЦИОННЫЙ ДОГОВОР')
            with Session(api.state.db_engine) as session:
                offer = session.get(Offer, offer_id)
                assert preview['amount_rub'] == offer.amount
                assert Decimal(preview['annual_rate_percent']) == offer.rate
                if expected_status == 'counter_offer':
                    assert offer.amount != session.get(Offer, offer_id).application.amount
            assert (await c.post(f'/offers/{offer_id}/accept', headers=owner)).status_code == 200
            fixed = (await c.get(f'/offers/{offer_id}/contract', headers=owner)).json()
            assert fixed['fixed'] is True
            assert fixed['amount_rub'] == preview['amount_rub']
            schedule = (await c.get(f'/offers/{offer_id}/schedule', headers=owner)).json()
            rows = schedule['payments']
            assert rows == fixed['schedule']
            assert rows[-1]['balance_rub'] == 0
            assert sum(row['principal_rub'] for row in rows) == fixed['amount_rub']
            assert sum(row['payment_rub'] for row in rows) == fixed['total_payments_rub']
            pdf = await c.get(f'/offers/{offer_id}/contract.pdf', headers=owner)
            assert pdf.status_code == 200
            assert pdf.headers['content-type'] == 'application/pdf'
            assert pdf.content.startswith(b'%PDF-')
            with Session(api.state.db_engine) as session:
                snapshot = session.scalar(
                    select(CreditContract).where(CreditContract.offer_id == offer_id)
                )
                assert snapshot is not None
                original = snapshot.terms.copy()
                session.get(
                    Company, session.get(Offer, offer_id).application.company_id
                ).name = 'Изменённое имя'
                session.commit()
            repeated = (await c.get(f'/offers/{offer_id}/contract', headers=owner)).json()
            assert repeated['borrower'] == fixed['borrower']
            assert repeated['schedule'] == fixed['schedule']
            with Session(api.state.db_engine) as session:
                offer = session.get(Offer, offer_id)
                offer.application.assigned_staff_user_id = session.scalar(
                    select(User.id).where(User.login == 'staff1')
                )
                session.commit()
            assert (await c.get(f'/offers/{offer_id}/contract', headers=staff)).status_code == 200
            issued = await c.post(f'/applications/{application_id}/sign-demo/code', headers=owner)
            assert issued.status_code == 200
            signed = await c.post(
                f'/applications/{application_id}/sign-demo',
                headers=owner,
                json={'code': issued.json()['code']},
            )
            assert signed.status_code == 200
            after_sign = (await c.get(f'/offers/{offer_id}/contract', headers=owner)).json()
            assert after_sign['signing_status'] == 'signed_demo'
            assert after_sign['schedule'] == fixed['schedule']
            assert after_sign['annual_rate_percent'] == fixed['annual_rate_percent']
            with Session(api.state.db_engine) as session:
                assert (
                    session.scalar(
                        select(CreditContract).where(CreditContract.offer_id == offer_id)
                    ).terms
                    == original
                )
            return application_id

    asyncio.run(check())


@pytest.mark.parametrize(
    'amount,rate,months',
    [(300_000, '18', 3), (3_000_000, '17', 24), (99_999_999, '20.5', 84), (1_000_000, '0', 12)],
)
def test_schedule_closes_balance(amount, rate, months):
    rows = payment_schedule(amount, Decimal(rate), months, date(2027, 1, 31))
    assert len(rows) == months
    assert rows[1]['payment_date'] == '2027-02-28'
    assert all(row['balance_rub'] >= 0 for row in rows)
    assert rows[-1]['balance_rub'] == 0
    assert sum(row['principal_rub'] for row in rows) == amount
    assert all(row['payment_rub'] == row['principal_rub'] + row['interest_rub'] for row in rows)


def test_historical_offer_rate_is_preserved(api):
    async def check():
        async with AsyncClient(transport=ASGITransport(app=api), base_url='http://testserver') as c:
            _, offer_id, owner, _ = await submitted_offer(c, key='historical-rate')
            with Session(api.state.db_engine) as session:
                offer = session.get(Offer, offer_id)
                offer.rate = Decimal('20.0000')
                offer.monthly_payment = annuity_payment(offer.amount, offer.rate, offer.term_months)
                session.commit()
            assert (await c.post(f'/offers/{offer_id}/accept', headers=owner)).status_code == 200
            document = (await c.get(f'/offers/{offer_id}/contract', headers=owner)).json()
            assert Decimal(document['annual_rate_percent']) == Decimal('20')
            assert document['monthly_payment_rub'] == annuity_payment(
                document['amount_rub'], Decimal('20'), document['term_months']
            )

    asyncio.run(check())
