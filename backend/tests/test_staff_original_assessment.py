# ruff: noqa: F811 -- imported fixtures are used as parameters
import asyncio
from decimal import Decimal
from types import SimpleNamespace

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session
from test_applications import api, auth, company_id, create  # noqa: F401
from test_model_workflow import model_api  # noqa: F401

from app.models import ApplicationSnapshot, Decision, Offer, Score
from app.services.pricing import credit_terms, dscr
from app.services.staff import original_request_score


def test_original_score_matches_snapshot_terms_and_collateral():
    snapshot = SimpleNamespace(
        id=10,
        application_id=1,
        data={
            'product_code': 'investment',
            'amount': 5_000_000,
            'term_months': 24,
            'collateral_value': 6_000_000,
        },
    )

    def score(id, **changes):
        return SimpleNamespace(
            id=id, application_id=1, snapshot_id=10, input_snapshot={**snapshot.data, **changes}
        )

    original = score(1)
    different_snapshot = score(5)
    different_snapshot.snapshot_id = 11
    assert (
        original_request_score(
            snapshot,
            [
                original,
                score(2, amount=300_000),
                score(3, term_months=36),
                score(4, collateral_value=0),
                different_snapshot,
            ],
        )
        is original
    )
    assert original_request_score(snapshot, [score(6, product_code='working_capital')]) is None


@pytest.mark.parametrize('api_fixture', ['api', 'model_api'])
def test_card_uses_original_assessment_before_and_after_offer_acceptance(
    api_fixture, request, monkeypatch
):
    configured = request.getfixturevalue(api_fixture)
    configured.state.settings.key_rate = Decimal('16')
    tracked = (Score, Decision, Offer, ApplicationSnapshot)

    def counts():
        with Session(configured.state.db_engine) as db:
            return tuple(db.scalar(select(func.count()).select_from(model)) for model in tracked)

    async def check():
        async with AsyncClient(
            transport=ASGITransport(app=configured), base_url='http://testserver'
        ) as client:
            owner = await auth(client, 'client2')
            company = await company_id(client, owner)
            created = await create(
                client, owner, company, fields={'amount': 5_000_000, 'term_months': 12}
            )
            app_id = created.json()['id']
            submitted = await client.post(f'/applications/{app_id}/submit', headers=owner)
            assert submitted.status_code == 200, submitted.text
            assert submitted.json()['status'] == 'counter_offer'
            staff = await auth(client, 'staff1')
            if configured.state.workflow_scorer is not None:

                def forbidden_inference(_request):
                    raise AssertionError('STAFF card must not rerun ML')

                monkeypatch.setattr(configured.state.workflow_scorer, 'score', forbidden_inference)
            before_counts = counts()
            before = (await client.get(f'/staff/applications/{app_id}', headers=staff)).json()
            assert counts() == before_counts
            original, offered = before['dscr']
            assert original['calculation_basis'] == 'current_formula'
            assert offered['calculation_basis'] == 'saved_offer'
            assert original['score_id'] == before['score']['id']
            assert (
                original['snapshot_id'] == before['snapshot_id'] == before['score']['snapshot_id']
            )
            assert original['snapshot_version'] == before['snapshot_version']
            assert (
                before['score']['product_code'],
                before['score']['amount'],
                before['score']['term_months'],
            ) == ('working_capital', 5_000_000, 12)
            assert (offered['amount'], offered['term_months']) != (
                original['amount'],
                original['term_months'],
            )
            assert original['value'] is not None and original['value'] != offered['value']
            with Session(configured.state.db_engine) as db:
                snapshot = db.get(ApplicationSnapshot, before['snapshot_id'])
                score = db.get(Score, before['score']['id'])
                from app.models import Product

                terms = credit_terms(
                    db.get(Product, original['product_code']),
                    original['amount'],
                    original['term_months'],
                    configured.state.settings.key_rate,
                    score.risk_band,
                )
                company = snapshot.data['company']
                assert Decimal(original['value']) == dscr(
                    company['avg_monthly_revenue'],
                    company['avg_monthly_expenses'],
                    company['existing_monthly_payments'],
                    terms.monthly_payment,
                )
                assert (
                    db.scalar(select(func.max(Score.id)).where(Score.application_id == app_id))
                    > score.id
                )
            accepted = await client.post(f'/offers/{offered["offer_id"]}/accept', headers=owner)
            assert accepted.status_code == 200, accepted.text
            after_counts = counts()
            after = (await client.get(f'/staff/applications/{app_id}', headers=staff)).json()
            assert after['application']['status'] == 'accepted'
            assert after['score'] == before['score']
            assert after['dscr'] == before['dscr']
            assert counts() == after_counts == before_counts

    asyncio.run(check())
