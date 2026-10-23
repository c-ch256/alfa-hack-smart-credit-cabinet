# ruff: noqa: F811 -- импорт pytest fixture используется как параметр тестов

import asyncio

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import event, func, select
from sqlalchemy.orm import Session
from test_applications import api, auth, company_id, create  # noqa: F401 -- pytest fixture

from app.errors import ApiError
from app.models import (
    Application,
    ApplicationSnapshot,
    AuditEvent,
    CheckResult,
    Decision,
    Offer,
    Score,
    StatusEvent,
    User,
)
from app.scoring.mock import MockScorer
from app.services.workflow import submit_and_process


@pytest.mark.parametrize(
    'login,fields,expected,route',
    [
        (
            'client1',
            {},
            'approved',
            ['draft', 'submitted', 'checking', 'scoring', 'decision', 'approved'],
        ),
        (
            'client2',
            {'amount': 5_000_000, 'term_months': 12},
            'counter_offer',
            ['draft', 'submitted', 'checking', 'scoring', 'decision', 'counter_offer'],
        ),
        (
            'client3',
            {'amount': 4_000_000},
            'manual_review',
            ['draft', 'submitted', 'checking', 'scoring', 'decision', 'manual_review'],
        ),
        ('client4', {}, 'rejected', ['draft', 'submitted', 'checking', 'rejected']),
    ],
)
def test_demo_workflow(api: FastAPI, login, fields, expected, route) -> None:
    async def check():
        async with AsyncClient(
            transport=ASGITransport(app=api), base_url='http://testserver'
        ) as client:
            headers = await auth(client, login)
            company = await company_id(client, headers)
            created = await create(client, headers, company, fields=fields)
            assert created.status_code == 201, created.text
            app_id = created.json()['id']
            response = await client.post(f'/applications/{app_id}/submit', headers=headers)
            assert response.status_code == 200, response.text
            assert response.json()['status'] == expected
            history = (await client.get(f'/applications/{app_id}/history', headers=headers)).json()
            assert [event['to_status'] for event in history] == route
            assert [event['sequence'] for event in history] == list(range(1, len(route) + 1))
            assert all(event['actor_role'] == 'SYSTEM' for event in history[2:])
            assert (
                await client.post(f'/applications/{app_id}/submit', headers=headers)
            ).status_code == 409
            offers = (await client.get(f'/applications/{app_id}/offers', headers=headers)).json()
            if expected in ('approved', 'counter_offer'):
                assert len(offers) == 1
                assert offers[0]['monthly_payment'] > 0
                assert offers[0]['kind'] == ('original' if expected == 'approved' else 'counter')
            else:
                assert offers == []
            for payload in (response.json(), history, offers):

                def keys(value):
                    if isinstance(value, dict):
                        for key, nested in value.items():
                            yield key
                            yield from keys(nested)
                    elif isinstance(value, list):
                        for nested in value:
                            yield from keys(nested)

                assert set(keys(payload)).isdisjoint(
                    {
                        'pd',
                        'risk_band',
                        'reasons',
                        'max_overdue_days_12m',
                        'tax_debt',
                        'mass_address',
                        'total_debt',
                    }
                )
            other = await auth(client, 'client2' if login != 'client2' else 'client1')
            assert (
                await client.get(f'/applications/{app_id}/offers', headers=other)
            ).status_code == 404
            return app_id

    app_id = asyncio.run(check())
    with Session(api.state.db_engine) as session:
        snapshot = session.scalar(
            select(ApplicationSnapshot)
            .where(ApplicationSnapshot.application_id == app_id)
            .order_by(ApplicationSnapshot.version.desc())
        )
        assert snapshot.data['company']['avg_monthly_revenue'] > 0
        assert snapshot.data['company_profile']['inn'].startswith('0000')
        assert (
            session.scalar(
                select(func.count())
                .select_from(CheckResult)
                .where(CheckResult.snapshot_id == snapshot.id)
            )
            == 3
        )
        scores = list(session.scalars(select(Score).where(Score.snapshot_id == snapshot.id)))
        decisions = list(
            session.scalars(select(Decision).where(Decision.snapshot_id == snapshot.id))
        )
        if expected == 'rejected':
            assert not scores
            assert len(decisions) == 1 and decisions[0].decision == 'reject'
        elif expected == 'manual_review':
            assert len(scores) == 1 and not decisions
            audit = session.scalar(
                select(AuditEvent).where(
                    AuditEvent.application_id == app_id, AuditEvent.action == 'workflow.policy'
                )
            )
            assert audit.details['reason_codes'] == ['OVERDUE_MANUAL']
        else:
            assert scores and all(score.is_test_result for score in scores)
            assert len(decisions) == 1 and decisions[0].score_id is not None
            offer = session.scalar(select(Offer).where(Offer.application_id == app_id))
            assert offer.decision_id == decisions[0].id


def test_missing_investment_information(api: FastAPI) -> None:
    async def check():
        async with AsyncClient(
            transport=ASGITransport(app=api), base_url='http://testserver'
        ) as client:
            headers = await auth(client)
            company = await company_id(client, headers)
            created = await create(
                client,
                headers,
                company,
                fields={
                    'product_code': 'investment',
                    'amount': 8_000_000,
                },
            )
            assert created.status_code == 201
            app_id = created.json()['id']
            response = await client.post(f'/applications/{app_id}/submit', headers=headers)
            assert response.status_code == 200
            assert response.json()['status'] == 'needs_info'
            assert 'объект инвестиций' in response.json()['info_request']
            assert 'вид залога' in response.json()['info_request']
            history = (await client.get(f'/applications/{app_id}/history', headers=headers)).json()
            assert [item['to_status'] for item in history] == [
                'draft',
                'submitted',
                'checking',
                'needs_info',
            ]
            return app_id

    app_id = asyncio.run(check())
    with Session(api.state.db_engine) as session:
        assert (
            session.scalar(
                select(func.count()).select_from(Score).where(Score.application_id == app_id)
            )
            == 0
        )
        assert (
            session.scalar(
                select(func.count()).select_from(Decision).where(Decision.application_id == app_id)
            )
            == 0
        )


def test_alternative_investment_without_documents_is_not_offered(api: FastAPI) -> None:
    async def check():
        async with AsyncClient(
            transport=ASGITransport(app=api), base_url='http://testserver'
        ) as client:
            headers = await auth(client, 'client2')
            company = await company_id(client, headers)
            created = await create(
                client,
                headers,
                company,
                fields={
                    'amount': 5_000_000,
                    'term_months': 12,
                    'purpose': 'equipment_purchase',
                },
            )
            assert created.status_code == 201
            app_id = created.json()['id']
            response = await client.post(f'/applications/{app_id}/submit', headers=headers)
            assert response.status_code == 200
            assert response.json()['status'] == 'counter_offer'
            offers = (await client.get(f'/applications/{app_id}/offers', headers=headers)).json()
            assert len(offers) == 1
            assert offers[0]['product_code'] == 'working_capital'

    asyncio.run(check())


def test_unavailable_mock_can_be_retried_without_duplicate_transitions(api: FastAPI) -> None:
    def broken_fns(_inn):
        raise RuntimeError('synthetic mock error')

    api.state.fns_provider = broken_fns

    async def check():
        async with AsyncClient(
            transport=ASGITransport(app=api), base_url='http://testserver'
        ) as client:
            headers = await auth(client)
            company = await company_id(client, headers)
            created = await create(client, headers, company)
            app_id = created.json()['id']
            first = await client.post(f'/applications/{app_id}/submit', headers=headers)
            assert first.status_code == 200 and first.json()['status'] == 'checking'
            del api.state.fns_provider
            second = await client.post(f'/applications/{app_id}/submit', headers=headers)
            assert second.status_code == 200 and second.json()['status'] == 'approved'
            return app_id

    app_id = asyncio.run(check())
    with Session(api.state.db_engine) as session:
        assert (
            session.scalar(
                select(func.count())
                .select_from(CheckResult)
                .where(CheckResult.application_id == app_id)
            )
            == 6
        )
        assert (
            session.scalar(
                select(func.count())
                .select_from(ApplicationSnapshot)
                .where(ApplicationSnapshot.application_id == app_id)
            )
            == 2
        )
        history = list(
            session.scalars(
                select(StatusEvent)
                .where(StatusEvent.application_id == app_id)
                .order_by(StatusEvent.sequence)
            )
        )
        assert [event.to_status for event in history] == [
            'draft',
            'submitted',
            'checking',
            'scoring',
            'decision',
            'approved',
        ]


def test_scoring_failure_is_recorded_and_retryable(api: FastAPI) -> None:
    class BrokenScorer:
        source = 'mock'
        is_test_result = True
        model_version = 'mock-broken-test'

        def score(self, _request):
            raise TimeoutError('synthetic scoring timeout')

    api.state.workflow_scorer = BrokenScorer()

    async def check():
        async with AsyncClient(
            transport=ASGITransport(app=api), base_url='http://testserver'
        ) as client:
            headers = await auth(client)
            company = await company_id(client, headers)
            created = await create(client, headers, company)
            app_id = created.json()['id']
            first = await client.post(f'/applications/{app_id}/submit', headers=headers)
            assert first.status_code == 200 and first.json()['status'] == 'scoring'
            api.state.workflow_scorer = MockScorer(api.state.settings.key_rate)
            second = await client.post(f'/applications/{app_id}/submit', headers=headers)
            assert second.status_code == 200 and second.json()['status'] == 'approved'
            return app_id

    app_id = asyncio.run(check())
    with Session(api.state.db_engine) as session:
        scores = list(
            session.scalars(select(Score).where(Score.application_id == app_id).order_by(Score.id))
        )
        assert [score.result_status for score in scores] == ['error', 'success']
        assert scores[0].pd is None and scores[0].error_code == 'SCORER_FAILED'
        assert (
            session.scalar(
                select(func.count()).select_from(Decision).where(Decision.application_id == app_id)
            )
            == 1
        )


def test_concurrent_stale_submit_is_rejected(api: FastAPI) -> None:
    async def setup():
        async with AsyncClient(
            transport=ASGITransport(app=api), base_url='http://testserver'
        ) as client:
            headers = await auth(client)
            company = await company_id(client, headers)
            return (await create(client, headers, company)).json()['id']

    app_id = asyncio.run(setup())
    with Session(api.state.db_engine) as first, Session(api.state.db_engine) as second:
        actor_one = first.scalar(select(User).where(User.login == 'client1'))
        actor_two = second.scalar(select(User).where(User.login == 'client1'))
        second.get(Application, app_id)
        submit_and_process(first, app_id, actor_one, api.state.settings)
        first.commit()
        with pytest.raises(ApiError) as error:
            submit_and_process(second, app_id, actor_two, api.state.settings)
        assert error.value.status_code == 409
        second.rollback()
    with Session(api.state.db_engine) as session:
        assert (
            session.scalar(
                select(func.count()).select_from(Decision).where(Decision.application_id == app_id)
            )
            == 1
        )
        assert (
            session.scalar(
                select(func.count())
                .select_from(StatusEvent)
                .where(StatusEvent.application_id == app_id)
            )
            == 6
        )


def test_failed_persistence_rolls_back_entire_submission(api: FastAPI) -> None:
    async def setup():
        async with AsyncClient(
            transport=ASGITransport(app=api), base_url='http://testserver'
        ) as client:
            headers = await auth(client)
            company = await company_id(client, headers)
            return (await create(client, headers, company)).json()['id'], headers

    app_id, headers = asyncio.run(setup())

    def fail_offer(_mapper, _connection, _target):
        raise RuntimeError('synthetic persistence failure')

    event.listen(Offer, 'before_insert', fail_offer)
    try:

        async def submit():
            async with AsyncClient(
                transport=ASGITransport(app=api), base_url='http://testserver'
            ) as client:
                await client.post(f'/applications/{app_id}/submit', headers=headers)

        with pytest.raises(RuntimeError, match='synthetic persistence failure'):
            asyncio.run(submit())
    finally:
        event.remove(Offer, 'before_insert', fail_offer)

    with Session(api.state.db_engine) as session:
        assert session.get(Application, app_id).status == 'draft'
        assert (
            session.scalar(
                select(func.count())
                .select_from(ApplicationSnapshot)
                .where(ApplicationSnapshot.application_id == app_id)
            )
            == 1
        )
        assert (
            session.scalar(
                select(func.count())
                .select_from(CheckResult)
                .where(CheckResult.application_id == app_id)
            )
            == 0
        )
        assert (
            session.scalar(
                select(func.count()).select_from(Score).where(Score.application_id == app_id)
            )
            == 0
        )
        assert (
            session.scalar(
                select(func.count())
                .select_from(StatusEvent)
                .where(StatusEvent.application_id == app_id)
            )
            == 1
        )
