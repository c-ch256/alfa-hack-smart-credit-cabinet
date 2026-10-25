# ruff: noqa: F811 -- импорт pytest fixture используется как параметр тестов

import asyncio
from decimal import Decimal

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session
from test_applications import api, auth, company_id, create  # noqa: F401 -- pytest fixture

from app.application_service import TRANSITIONS, transition
from app.errors import ApiError
from app.models import (
    Application,
    ApplicationSnapshot,
    AuditEvent,
    CheckResult,
    Decision,
    Offer,
    Product,
    Score,
    StaffComment,
    StatusEvent,
    User,
)
from app.scoring.mock import MockScorer
from app.security import hash_password
from app.services.pricing import annuity_payment, credit_terms, dscr
from app.services.staff import make_manual_decision
from app.staff_schemas import ManualDecisionInput


async def manual_application(
    client: AsyncClient, *, login='client3', fields: dict | None = None
) -> tuple[int, dict]:
    headers = await auth(client, login)
    company = await company_id(client, headers)
    created = await create(client, headers, company, fields=fields or {'amount': 4_000_000})
    assert created.status_code == 201, created.text
    app_id = created.json()['id']
    submitted = await client.post(f'/applications/{app_id}/submit', headers=headers)
    assert submitted.status_code == 200, submitted.text
    assert submitted.json()['status'] == 'manual_review'
    return app_id, headers


def test_staff_catalog_and_loan_preview_is_read_only(api: FastAPI) -> None:
    tracked = (Application, ApplicationSnapshot, Score, Decision, Offer, StatusEvent)

    def counts() -> tuple[int, ...]:
        with Session(api.state.db_engine) as session:
            return tuple(
                session.scalar(select(func.count()).select_from(model)) for model in tracked
            )

    async def check() -> None:
        async with AsyncClient(
            transport=ASGITransport(app=api), base_url='http://testserver'
        ) as client:
            staff = await auth(client, 'staff1')
            app_id, owner = await manual_application(client)
            before = counts()
            with Session(api.state.db_engine) as session:
                application = session.get(Application, app_id)
                initial = (
                    application.status,
                    application.version,
                    application.assigned_staff_user_id,
                )
            payload = {
                'application_id': app_id,
                'product_code': 'working_capital',
                'amount': 3_000_000,
                'term_months': 24,
            }
            for path in ('/staff/products', '/staff/loan-preview'):
                method = client.post if path.endswith('loan-preview') else client.get
                kwargs = {'json': payload} if path.endswith('loan-preview') else {}
                assert (await method(path, **kwargs)).status_code == 401
                assert (await method(path, headers=owner, **kwargs)).status_code == 403
            catalog = (await client.get('/staff/products', headers=staff)).json()
            assert catalog['key_rate'] == '14.00'
            assert catalog['rate_formula'] == 'key_rate + margin_pp + risk_premium_pp'
            assert catalog['risk_premium_pp']['B'] == '1.5'
            assert catalog['demonstration'] is True
            assert {item['code'] for item in catalog['products']} == {
                'working_capital',
                'investment',
            }
            response = await client.post('/staff/loan-preview', headers=staff, json=payload)
            assert response.status_code == 200, response.text
            preview = response.json()
            assert preview['product'] == next(
                item for item in catalog['products'] if item['code'] == payload['product_code']
            )
            assert preview['amount'] == payload['amount']
            assert preview['term_months'] == payload['term_months']
            assert preview['application_id'] == app_id
            assert preview['snapshot_id'] > 0
            assert preview['snapshot_version'] > 0
            assert preview['risk_band'] in 'ABCD'
            assert preview['pd'] is not None
            assert preview['model_version']
            assert preview['score_source'] == 'mock'
            assert preview['is_test_result'] is True
            assert preview['key_rate'] == '14.00'
            assert preview['monthly_payment'] > 0
            assert preview['total_payments'] == preview['monthly_payment'] * 24
            assert preview['overpayment'] == preview['total_payments'] - payload['amount']
            assert preview['indicative'] is True and preview['demonstration'] is True
            assert preview['applied_constraints'] == {
                'min_amount': 300_000,
                'max_amount': 30_000_000,
                'min_term_months': 3,
                'max_term_months': 36,
            }
            with Session(api.state.db_engine) as session:
                product = session.get(Product, payload['product_code'])
                expected = credit_terms(
                    product,
                    payload['amount'],
                    payload['term_months'],
                    Decimal('14.00'),
                    preview['risk_band'],
                )
                company = session.scalar(
                    select(ApplicationSnapshot).where(
                        ApplicationSnapshot.id == preview['snapshot_id']
                    )
                ).data['company']
            assert Decimal(preview['annual_rate']) == expected.rate
            assert preview['monthly_payment'] == expected.monthly_payment
            assert Decimal(preview['dscr']) == dscr(
                company['avg_monthly_revenue'],
                company['avg_monthly_expenses'],
                company['existing_monthly_payments'],
                expected.monthly_payment,
            )
            assert 'не является предложением' in preview['explanation']
            variants = []
            for amount, term in ((3_000_000, 24), (2_000_000, 24), (3_000_000, 36)):
                result = await client.post(
                    '/staff/loan-preview',
                    headers=staff,
                    json=payload | {'amount': amount, 'term_months': term},
                )
                assert result.status_code == 200, result.text
                variants.append(result.json())
            assert variants[0]['monthly_payment'] != variants[1]['monthly_payment']
            assert variants[0]['monthly_payment'] != variants[2]['monthly_payment']
            assert variants[0]['dscr'] != variants[1]['dscr']
            assert variants[0]['dscr'] != variants[2]['dscr']
            assert variants[0]['pd'] != variants[1]['pd']
            assert counts() == before
            with Session(api.state.db_engine) as session:
                application = session.get(Application, app_id)
                assert (
                    application.status,
                    application.version,
                    application.assigned_staff_user_id,
                ) == initial
                events = list(
                    session.scalars(
                        select(AuditEvent).where(
                            AuditEvent.application_id == app_id,
                            AuditEvent.action == 'staff.loan_preview',
                        )
                    )
                )
                assert len(events) == 4
                assert all(
                    'amount' not in row.details and 'pd' not in row.details for row in events
                )

    asyncio.run(check())


def test_staff_loan_preview_rejects_invalid_inputs_without_writes(api: FastAPI) -> None:
    async def check() -> None:
        async with AsyncClient(
            transport=ASGITransport(app=api), base_url='http://testserver'
        ) as client:
            staff = await auth(client, 'staff1')
            app_id, _ = await manual_application(client)
            with Session(api.state.db_engine) as session:
                before = tuple(
                    session.scalar(
                        select(func.count())
                        .select_from(model)
                        .where(model.application_id == app_id)
                    )
                    for model in (Score, Decision, Offer, StatusEvent, AuditEvent)
                )
            base = {
                'application_id': app_id,
                'product_code': 'working_capital',
                'amount': 3_000_000,
                'term_months': 24,
            }
            cases = (
                ({'amount': 299_999}, 'INVALID_AMOUNT'),
                ({'amount': 30_000_001}, 'INVALID_AMOUNT'),
                ({'term_months': 2}, 'INVALID_TERM'),
                ({'term_months': 37}, 'INVALID_TERM'),
                ({'product_code': 'missing'}, 'INVALID_PRODUCT'),
                ({'product_code': 'investment'}, 'INVALID_PRODUCT'),
                ({'amount': 0}, 'VALIDATION_ERROR'),
                ({'amount': True}, 'VALIDATION_ERROR'),
                ({'amount': 3_000_000.5}, 'VALIDATION_ERROR'),
            )
            for changes, code in cases:
                response = await client.post(
                    '/staff/loan-preview', headers=staff, json=base | changes
                )
                assert response.status_code == 422
                assert response.json()['code'] == code
            assert (
                await client.post(
                    '/staff/loan-preview',
                    headers=staff,
                    json={key: value for key, value in base.items() if key != 'application_id'},
                )
            ).status_code == 422
            assert (
                await client.post(
                    '/staff/loan-preview', headers=staff, json=base | {'risk_band': 'A'}
                )
            ).status_code == 422
            assert (
                await client.post(
                    '/staff/loan-preview', headers=staff, json=base | {'application_id': 999999}
                )
            ).status_code == 404
            with Session(api.state.db_engine) as session:
                after = tuple(
                    session.scalar(
                        select(func.count())
                        .select_from(model)
                        .where(model.application_id == app_id)
                    )
                    for model in (Score, Decision, Offer, StatusEvent, AuditEvent)
                )
            assert after == before

    asyncio.run(check())


def test_staff_loan_preview_uses_saved_snapshot_and_handles_scorer_failure(api: FastAPI) -> None:
    class BrokenScorer:
        source = 'mock'
        is_test_result = True

        def score(self, _request):
            raise RuntimeError('synthetic scorer failure')

    async def check() -> int:
        async with AsyncClient(
            transport=ASGITransport(app=api), base_url='http://testserver'
        ) as client:
            app_id, _ = await manual_application(client)
            staff = await auth(client, 'staff1')
            payload = {
                'application_id': app_id,
                'product_code': 'working_capital',
                'amount': 3_000_000,
                'term_months': 24,
            }
            first = (await client.post('/staff/loan-preview', headers=staff, json=payload)).json()
            with Session(api.state.db_engine) as session:
                session.get(Application, app_id).company.avg_monthly_revenue = 99_000_000
                session.commit()
            repeated = (
                await client.post('/staff/loan-preview', headers=staff, json=payload)
            ).json()
            assert repeated['snapshot_id'] == first['snapshot_id']
            assert repeated['pd'] == first['pd']
            assert repeated['dscr'] == first['dscr']
            api.state.workflow_scorer = BrokenScorer()
            failed = await client.post('/staff/loan-preview', headers=staff, json=payload)
            assert failed.status_code == 503
            assert failed.json()['code'] == 'SCORING_UNAVAILABLE'
            return app_id

    app_id = asyncio.run(check())
    with Session(api.state.db_engine) as session:
        assert (
            session.scalar(
                select(func.count()).select_from(Score).where(Score.application_id == app_id)
            )
            == 1
        )
        assert (
            session.scalar(
                select(func.count()).select_from(Offer).where(Offer.application_id == app_id)
            )
            == 0
        )
        assert session.get(Application, app_id).status == 'manual_review'
        assert (
            session.scalar(
                select(func.count())
                .select_from(AuditEvent)
                .where(
                    AuditEvent.application_id == app_id,
                    AuditEvent.action == 'staff.loan_preview',
                )
            )
            == 2
        )


def test_manual_decision_scores_again_after_preview(api: FastAPI) -> None:
    class CountingScorer:
        source = 'mock'
        is_test_result = True

        def __init__(self):
            self.delegate = MockScorer(api.state.settings.key_rate)
            self.calls = 0

        def score(self, request):
            self.calls += 1
            return self.delegate.score(request)

    async def check() -> int:
        async with AsyncClient(
            transport=ASGITransport(app=api), base_url='http://testserver'
        ) as client:
            app_id, _ = await manual_application(client)
            staff = await auth(client, 'staff1')
            scorer = CountingScorer()
            api.state.workflow_scorer = scorer
            preview = await client.post(
                '/staff/loan-preview',
                headers=staff,
                json={
                    'application_id': app_id,
                    'product_code': 'working_capital',
                    'amount': 3_000_000,
                    'term_months': 36,
                },
            )
            assert preview.status_code == 200, preview.text
            assert scorer.calls == 1
            with Session(api.state.db_engine) as session:
                assert (
                    session.scalar(
                        select(func.count())
                        .select_from(Score)
                        .where(Score.application_id == app_id)
                    )
                    == 1
                )
            decided = await client.post(
                f'/staff/applications/{app_id}/decision',
                headers=staff,
                json={
                    'decision': 'counter',
                    'amount': 3_000_000,
                    'term_months': 36,
                    'comment': 'Внутреннее основание решения',
                },
            )
            assert decided.status_code == 200, decided.text
            assert scorer.calls == 2
            return app_id

    app_id = asyncio.run(check())
    with Session(api.state.db_engine) as session:
        assert (
            session.scalar(
                select(func.count()).select_from(Score).where(Score.application_id == app_id)
            )
            == 2
        )
        assert session.get(Application, app_id).status == 'counter_offer'


def test_staff_internal_comments_access_persistence_and_audit(api: FastAPI) -> None:
    with Session(api.state.db_engine) as session:
        session.add(
            User(
                login='staff2',
                role='STAFF',
                full_name='Другой аналитик',
                password_hash=hash_password('synthetic-demo-password-only'),
                is_active=True,
            )
        )
        session.commit()

    async def check() -> tuple[int, int, int]:
        async with AsyncClient(
            transport=ASGITransport(app=api), base_url='http://testserver'
        ) as client:
            app_id, owner = await manual_application(client)
            staff = await auth(client, 'staff1')
            other_staff = await auth(client, 'staff2')
            before = (await client.get(f'/applications/{app_id}', headers=owner)).json()
            history = (await client.get(f'/applications/{app_id}/history', headers=owner)).json()
            path = f'/staff/applications/{app_id}/comments'
            assert (await client.get(path)).status_code == 401
            assert (await client.post(path, json={'body': 'Тест'})).status_code == 401
            assert (await client.get(path, headers=owner)).status_code == 403
            assert (
                await client.post(path, headers=owner, json={'body': 'Тест'})
            ).status_code == 403
            for body in ('', '   ', 'x' * 2001):
                assert (
                    await client.post(path, headers=staff, json={'body': body})
                ).status_code == 422
            assert (
                await client.post(path, headers=staff, json={'body': 'Тест', 'actor_user_id': 999})
            ).status_code == 422
            first = await client.post(
                path, headers=other_staff, json={'body': '  Внутренняя заметка  '}
            )
            assert first.status_code == 201, first.text
            assert first.json()['body'] == 'Внутренняя заметка'
            assert first.json()['application_id'] == app_id
            assert first.json()['created_at']
            assert first.json()['audit_event_id']
            assert first.json()['status_event_id'] is None
            second = await client.post(path, headers=staff, json={'body': 'Вторая заметка'})
            assert second.status_code == 201
            detail = (await client.get(f'/staff/applications/{app_id}', headers=staff)).json()
            assert [item['body'] for item in detail['comments']] == [
                'Внутренняя заметка',
                'Вторая заметка',
            ]
            assert detail['comments'][0]['audit_event_id'] == first.json()['audit_event_id']
            journal = (
                await client.get(f'/staff/audit?application_id={app_id}', headers=other_staff)
            ).json()
            assert {
                item['internal_comment']
                for item in journal
                if item['action'] == 'staff.comment.create'
            } == {'Внутренняя заметка', 'Вторая заметка'}
            assert [item['id'] for item in (await client.get(path, headers=staff)).json()] == [
                first.json()['id'],
                second.json()['id'],
            ]
            assert [
                item['id']
                for item in (
                    await client.get(f'{path}?limit=1&offset=1', headers=other_staff)
                ).json()
            ] == [second.json()['id']]
            assert (await client.get(f'{path}?limit=101', headers=staff)).status_code == 422
            assert (
                await client.get('/staff/applications/999999/comments', headers=staff)
            ).status_code == 404
            assert (
                await client.post(
                    '/staff/applications/999999/comments', headers=staff, json={'body': 'Тест'}
                )
            ).status_code == 404
            after = (await client.get(f'/applications/{app_id}', headers=owner)).json()
            assert after == before
            assert (
                await client.get(f'/applications/{app_id}/history', headers=owner)
            ).json() == history
            assert after['info_request'] is None
            assert 'Внутренняя заметка' not in str(after)
            return app_id, first.json()['id'], second.json()['id']

    app_id, first_id, second_id = asyncio.run(check())
    with Session(api.state.db_engine) as session:
        rows = list(session.scalars(select(StaffComment).order_by(StaffComment.id)))
        assert [row.id for row in rows] == [first_id, second_id]
        assert rows[0].author.login == 'staff2'
        assert rows[1].author.login == 'staff1'
        assert rows[0].created_at is not None
        assert session.get(Application, app_id).status == 'manual_review'
        assert (
            session.scalar(
                select(func.count())
                .select_from(StatusEvent)
                .where(StatusEvent.application_id == app_id)
            )
            == 6
        )
        audit_rows = list(
            session.scalars(select(AuditEvent).where(AuditEvent.application_id == app_id))
        )
        assert [row.action for row in audit_rows].count('staff.comment.create') == 2
        assert [row.action for row in audit_rows].count('staff.comments_view') == 2
        assert 'Внутренняя заметка' not in str([row.details for row in audit_rows])


def test_staff_comments_hidden_from_client_and_documents(api: FastAPI) -> None:
    marker = 'PRIVATE-STAFF-NOTE-ONLY-74192'
    private_keys = {
        'pd',
        'risk_band',
        'dscr',
        'scoring_reasons',
        'model_version',
        'internal_comment',
        'comments',
        'checks',
        'rationale',
        'payload',
        'reason_codes',
        'tax_debt',
        'max_overdue_days_12m',
    }

    def all_keys(value):
        if isinstance(value, dict):
            return set(value) | set().union(*(all_keys(item) for item in value.values()))
        if isinstance(value, list):
            return set().union(*(all_keys(item) for item in value))
        return set()

    async def check() -> None:
        async with AsyncClient(
            transport=ASGITransport(app=api), base_url='http://testserver'
        ) as client:
            owner = await auth(client, 'client1')
            staff = await auth(client, 'staff1')
            company = await company_id(client, owner)
            draft = await create(client, owner, company)
            app_id = draft.json()['id']
            path = f'/staff/applications/{app_id}/comments'
            assert (await client.get(path, headers=staff)).status_code == 404
            assert (
                await client.post(path, headers=staff, json={'body': marker})
            ).status_code == 404
            submitted = await client.post(f'/applications/{app_id}/submit', headers=owner)
            assert submitted.json()['status'] == 'approved'
            offer = (await client.get(f'/applications/{app_id}/offers', headers=owner)).json()[0]
            created = await client.post(path, headers=staff, json={'body': marker})
            assert created.status_code == 201
            for route in (
                f'/applications/{app_id}',
                '/applications',
                f'/applications/{app_id}/history',
                f'/applications/{app_id}/offers',
                f'/offers/{offer["id"]}/contract',
                f'/offers/{offer["id"]}/schedule',
            ):
                response = await client.get(route, headers=owner)
                assert response.status_code == 200, response.text
                assert marker not in response.text
                assert 'staff_comments' not in response.text
                assert private_keys.isdisjoint(all_keys(response.json()))
            assert (
                await client.post(f'/offers/{offer["id"]}/accept', headers=owner)
            ).status_code == 200
            contract = await client.get(f'/offers/{offer["id"]}/contract', headers=owner)
            pdf = await client.get(f'/offers/{offer["id"]}/contract.pdf', headers=owner)
            assert marker not in contract.text
            assert private_keys.isdisjoint(all_keys(contract.json()))
            assert pdf.status_code == 200
            assert marker.encode() not in pdf.content
            denied_owner = await auth(client, 'client4')
            assert (await client.get('/staff/audit', headers=denied_owner)).status_code == 403

    asyncio.run(check())


def test_client_additional_info_rechecks_and_preserves_history(api: FastAPI) -> None:
    async def check():
        async with AsyncClient(
            transport=ASGITransport(app=api), base_url='http://testserver'
        ) as client:
            owner = await auth(client)
            company = await company_id(client, owner)
            created = await create(
                client,
                owner,
                company,
                fields={
                    'product_code': 'investment',
                    'amount': 8_000_000,
                    'investment_object': 'Станок',
                },
            )
            app_id = created.json()['id']
            submitted = await client.post(f'/applications/{app_id}/submit', headers=owner)
            assert submitted.json()['status'] == 'needs_info'
            staff = await auth(client, 'staff1')
            assert [
                row['id']
                for row in (
                    await client.get('/staff/applications?status=needs_info', headers=staff)
                ).json()
            ] == [app_id]
            assert (await client.get(f'/staff/applications/{app_id}', headers=staff)).json()[
                'dscr'
            ][0]['value'] is None
            version = submitted.json()['version']
            other = await auth(client, 'client2')
            assert (
                await client.post(
                    f'/applications/{app_id}/additional-info',
                    headers=other,
                    json={
                        'version': version,
                        'collateral_type': 'Техника',
                        'collateral_value': 9_000_000,
                    },
                )
            ).status_code == 404
            assert (
                await client.post(
                    f'/applications/{app_id}/additional-info',
                    headers=staff,
                    json={'version': version, 'collateral_type': 'Техника'},
                )
            ).status_code == 403
            assert (
                await client.post(
                    f'/applications/{app_id}/additional-info',
                    headers=owner,
                    json={'version': version, 'amount': 9_000_000},
                )
            ).status_code == 422
            response = await client.post(
                f'/applications/{app_id}/additional-info',
                headers=owner,
                json={
                    'version': version,
                    'collateral_type': 'Техника',
                    'collateral_value': 9_000_000,
                    'message': 'Синтетические сведения о залоге',
                },
            )
            assert response.status_code == 200, response.text
            assert response.json()['status'] == 'approved'
            assert response.json()['info_request'] is None
            assert response.json()['info_response'] == 'Синтетические сведения о залоге'
            assert (
                await client.post(
                    f'/applications/{app_id}/additional-info',
                    headers=owner,
                    json={'version': version, 'collateral_type': 'Техника'},
                )
            ).status_code == 409
            history = (await client.get(f'/applications/{app_id}/history', headers=owner)).json()
            assert [item['to_status'] for item in history] == [
                'draft',
                'submitted',
                'checking',
                'needs_info',
                'checking',
                'scoring',
                'decision',
                'approved',
            ]
            assert history[4]['actor_role'] == 'CLIENT'
            return app_id

    app_id = asyncio.run(check())
    with Session(api.state.db_engine) as session:
        snapshots = list(
            session.scalars(
                select(ApplicationSnapshot)
                .where(ApplicationSnapshot.application_id == app_id)
                .order_by(ApplicationSnapshot.version)
            )
        )
        assert len(snapshots) == 3
        assert snapshots[-2].data['collateral_value'] is None
        assert snapshots[-1].data['collateral_value'] == 9_000_000
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
                select(func.count()).select_from(Score).where(Score.application_id == app_id)
            )
            >= 1
        )


def test_staff_queue_card_and_scoped_audit(api: FastAPI) -> None:
    with Session(api.state.db_engine) as session:
        session.add(
            User(
                login='staff2',
                role='STAFF',
                full_name='Другой демо-аналитик',
                password_hash=hash_password('synthetic-demo-password-only'),
                is_active=True,
            )
        )
        session.commit()

    async def check():
        async with AsyncClient(
            transport=ASGITransport(app=api), base_url='http://testserver'
        ) as client:
            app_id, client_headers = await manual_application(client)
            staff = await auth(client, 'staff1')
            other_staff = await auth(client, 'staff2')
            assert (
                await client.get('/staff/applications', headers=client_headers)
            ).status_code == 403
            assert (await client.get('/staff/applications')).status_code == 401
            assert (
                await client.get('/staff/applications?status=unknown', headers=staff)
            ).status_code == 422
            queue = (
                await client.get('/staff/applications?status=manual_review', headers=staff)
            ).json()
            assert [item['id'] for item in queue] == [app_id]
            assert (
                await client.get('/staff/applications?status=approved', headers=staff)
            ).json() == []
            assert [
                item['id']
                for item in (await client.get('/staff/applications', headers=other_staff)).json()
            ] == [app_id]
            assert (
                await client.get(f'/staff/applications/{app_id}', headers=other_staff)
            ).status_code == 200
            assert (
                await client.get(f'/staff/audit?application_id={app_id}', headers=other_staff)
            ).status_code == 200
            assert (
                await client.post(
                    '/staff/loan-preview',
                    headers=other_staff,
                    json={
                        'application_id': app_id,
                        'product_code': 'working_capital',
                        'amount': 3_000_000,
                        'term_months': 24,
                    },
                )
            ).status_code == 200
            assert (
                await client.post(
                    f'/staff/applications/{app_id}/decision',
                    headers=other_staff,
                    json={
                        'decision': 'reject',
                        'comment': 'Тест',
                    },
                )
            ).status_code == 404
            assert (
                await client.post(
                    f'/staff/applications/{app_id}/request-info',
                    headers=other_staff,
                    json={'fields': ['purpose_details'], 'comment': 'Тест'},
                )
            ).status_code == 404
            detail = (await client.get(f'/staff/applications/{app_id}', headers=staff)).json()
            assert detail['application']['status'] == 'manual_review'
            assert detail['company']['name'] == 'ООО «Гранит-Строй»'
            assert detail['company_snapshot']['avg_monthly_revenue'] == 2_000_000
            assert {item['source'] for item in detail['checks']} == {'DATA', 'FNS', 'BKI'}
            assert detail['score']['pd'] is not None
            assert detail['score']['reasons']
            assert detail['dscr'][0]['value'] is None
            assert detail['dscr'][0]['explanation']
            assert detail['history'][-1]['to_status'] == 'manual_review'
            assert (
                await client.get(f'/staff/applications/{app_id}', headers=client_headers)
            ).status_code == 403
            audit_rows = (
                await client.get(f'/staff/audit?application_id={app_id}', headers=staff)
            ).json()
            assert any(item['action'] == 'staff.view' for item in audit_rows)
            assert (await client.get('/staff/audit?limit=1', headers=staff)).status_code == 200
            assert any(
                item['action'] == 'staff.list_view'
                for item in (await client.get('/staff/audit?limit=100', headers=staff)).json()
            )
            return app_id

    app_id = asyncio.run(check())
    with Session(api.state.db_engine) as session:
        application = session.get(Application, app_id)
        staff1 = session.scalar(select(User).where(User.login == 'staff1'))
        assert application.assigned_staff_user_id == staff1.id
        actions = [
            row.action
            for row in session.scalars(
                select(AuditEvent).where(AuditEvent.application_id == app_id)
            )
        ]
        assert 'staff.view' in actions and 'staff.audit_view' in actions
        assert (
            session.scalar(
                select(func.count())
                .select_from(AuditEvent)
                .where(
                    AuditEvent.action == 'staff.list_view',
                    AuditEvent.actor_role == 'STAFF',
                )
            )
            >= 2
        )
        assert (
            session.scalar(
                select(func.count())
                .select_from(AuditEvent)
                .where(
                    AuditEvent.application_id == app_id,
                    AuditEvent.action == 'staff.view',
                )
            )
            == 2
        )


def test_staff_journal_keeps_automatic_offer_terms_after_acceptance(api: FastAPI) -> None:
    async def check() -> None:
        async with AsyncClient(
            transport=ASGITransport(app=api), base_url='http://testserver'
        ) as client:
            owner = await auth(client, 'client1')
            staff = await auth(client, 'staff1')
            company = await company_id(client, owner)
            created = await create(client, owner, company)
            app_id = created.json()['id']
            assert (await client.post(f'/applications/{app_id}/submit', headers=owner)).json()[
                'status'
            ] == 'approved'
            offer = (await client.get(f'/applications/{app_id}/offers', headers=owner)).json()[0]
            accepted_offer = await client.post(f'/offers/{offer["id"]}/accept', headers=owner)
            assert accepted_offer.status_code == 200
            journal = (
                await client.get(f'/staff/audit?application_id={app_id}', headers=staff)
            ).json()
            policy = next(item for item in journal if item['action'] == 'workflow.policy')
            assert policy['decision_source'] == 'SYSTEM'
            assert policy['actor_display_name'] == 'Система'
            assert policy['status_at_event'] == 'approved'
            assert policy['offer_id'] == offer['id']
            assert policy['offer_terms']['amount'] == offer['amount']
            assert policy['offer_terms']['rate'] == offer['rate']
            accepted = next(item for item in journal if item['action'] == 'application.accepted')
            assert accepted['status_at_event'] == 'accepted'
            assert accepted['status_event_id']
            assert policy['decision_id']
            with Session(api.state.db_engine) as session:
                saved = session.get(AuditEvent, policy['id'])
                saved.details = {
                    key: value
                    for key, value in saved.details.items()
                    if key not in ('decision_id', 'offer_id', 'status')
                }
                session.commit()
            legacy = (
                await client.get(f'/staff/audit?application_id={app_id}', headers=staff)
            ).json()
            legacy_policy = next(item for item in legacy if item['action'] == 'workflow.policy')
            assert legacy_policy['offer_id'] == offer['id']
            assert legacy_policy['status_at_event'] == 'approved'

    asyncio.run(check())


def test_staff_reads_submitted_results_and_historical_dscr(api: FastAPI) -> None:
    async def check():
        async with AsyncClient(
            transport=ASGITransport(app=api), base_url='http://testserver'
        ) as client:
            staff = await auth(client, 'staff1')
            created_ids = {}
            for login, fields in (
                ('client1', {}),
                ('client2', {'amount': 5_000_000, 'term_months': 12}),
                ('client4', {}),
            ):
                owner = await auth(client, login)
                company = await company_id(client, owner)
                created = await create(client, owner, company, fields=fields)
                app_id = created.json()['id']
                created_ids[login] = app_id
                assert (
                    await client.get(f'/staff/applications/{app_id}', headers=staff)
                ).status_code == 404
                submitted = await client.post(f'/applications/{app_id}/submit', headers=owner)
                assert submitted.status_code == 200, submitted.text
            draft_owner = await auth(client, 'client3')
            draft_company = await company_id(client, draft_owner)
            draft = await create(client, draft_owner, draft_company)
            draft_id = draft.json()['id']
            await client.post(f'/applications/{draft_id}/withdraw', headers=draft_owner)
            queue = (await client.get('/staff/applications', headers=staff)).json()
            assert {row['id'] for row in queue} == set(created_ids.values())
            assert (
                await client.get(f'/staff/applications/{draft_id}', headers=staff)
            ).status_code == 404
            assert [
                row['id']
                for row in (
                    await client.get('/staff/applications?limit=1&offset=1', headers=staff)
                ).json()
            ] == [created_ids['client2']]
            for login, expected in (
                ('client1', 'approved'),
                ('client2', 'counter_offer'),
                ('client4', 'rejected'),
            ):
                app_id = created_ids[login]
                filtered = (
                    await client.get(f'/staff/applications?status={expected}', headers=staff)
                ).json()
                assert [row['id'] for row in filtered] == [app_id]
                detail = (await client.get(f'/staff/applications/{app_id}', headers=staff)).json()
                assert detail['application']['status'] == expected
                assert detail['decisions'][0]['created_by'] == 'SYSTEM'
                assert detail['dscr'][0]['scope'] == 'original_application'
                assert detail['dscr'][0]['offer_id'] is None
                assert detail['dscr'][0]['product_code'] == 'working_capital'
                assert detail['dscr'][0]['amount'] == (
                    5_000_000 if login == 'client2' else 3_000_000
                )
                if login == 'client4':
                    assert detail['dscr'][0]['value'] is None
                    assert detail['dscr'][0]['explanation']
                    owner = await auth(client, login)
                    public_history = (
                        await client.get(f'/applications/{app_id}/history', headers=owner)
                    ).json()
                    assert 'BKI_STOP' not in str(public_history)
                    assert 'max_overdue_days_12m' not in str(public_history)
                    assert 'Синтетическая кредитная история содержит стоп-фактор' not in str(
                        public_history
                    )
                else:
                    assert detail['dscr'][0]['value'] is not None
                    offer = detail['offers'][0]
                    offer_dscr = detail['dscr'][1]
                    assert offer_dscr['offer_id'] == offer['id']
                    assert offer_dscr['product_code'] == offer['product_code']
                    assert offer_dscr['amount'] == offer['amount']
                    assert offer_dscr['term_months'] == offer['term_months']
                    assert offer_dscr['rate'] == offer['rate']
                    assert offer_dscr['value'] is not None
                    owner = await auth(client, login)
                    assert (
                        'dscr'
                        not in (await client.get(f'/applications/{app_id}', headers=owner)).json()
                    )
                    assert 'dscr' not in str(
                        (await client.get(f'/applications/{app_id}/offers', headers=owner)).json()
                    )
            return created_ids

    created_ids = asyncio.run(check())
    with Session(api.state.db_engine) as session:
        app_id = created_ids['client2']
        application = session.get(Application, app_id)
        application.company.avg_monthly_revenue = 99_000_000
        session.commit()

    async def verify():
        async with AsyncClient(
            transport=ASGITransport(app=api), base_url='http://testserver'
        ) as client:
            staff = await auth(client, 'staff1')
            detail = (
                await client.get(f'/staff/applications/{created_ids["client2"]}', headers=staff)
            ).json()
            original, offered = detail['dscr']
            assert original['amount'] == 5_000_000 and original['term_months'] == 12
            assert (offered['amount'], offered['term_months']) != (
                original['amount'],
                original['term_months'],
            )
            company = detail['company_snapshot']
            for item in (original, offered):
                expected = dscr(
                    company['avg_monthly_revenue'],
                    company['avg_monthly_expenses'],
                    company['existing_monthly_payments'],
                    annuity_payment(item['amount'], Decimal(item['rate']), item['term_months']),
                )
                assert Decimal(item['value']) == expected
            assert detail['company']['avg_monthly_revenue'] == 99_000_000

    asyncio.run(verify())
    with Session(api.state.db_engine) as session:
        for app_id in created_ids.values():
            assert (
                session.scalar(
                    select(func.count())
                    .select_from(AuditEvent)
                    .where(
                        AuditEvent.application_id == app_id,
                        AuditEvent.action == 'staff.view',
                    )
                )
                >= 1
            )


def test_staff_dscr_returns_null_for_incomplete_saved_financials(api: FastAPI) -> None:
    async def setup() -> int:
        async with AsyncClient(
            transport=ASGITransport(app=api), base_url='http://testserver'
        ) as client:
            owner = await auth(client, 'client1')
            company = await company_id(client, owner)
            app_id = (await create(client, owner, company)).json()['id']
            assert (await client.post(f'/applications/{app_id}/submit', headers=owner)).json()[
                'status'
            ] == 'approved'
            return app_id

    app_id = asyncio.run(setup())
    with Session(api.state.db_engine) as session:
        snapshot = session.scalar(
            select(ApplicationSnapshot)
            .where(ApplicationSnapshot.application_id == app_id)
            .order_by(ApplicationSnapshot.version.desc())
            .limit(1)
        )
        snapshot.data = {**snapshot.data, 'company': {}}
        session.commit()

    async def verify() -> None:
        async with AsyncClient(
            transport=ASGITransport(app=api), base_url='http://testserver'
        ) as client:
            staff = await auth(client, 'staff1')
            detail = (await client.get(f'/staff/applications/{app_id}', headers=staff)).json()
            assert detail['company_snapshot'] == {}
            assert all(item['value'] is None and item['explanation'] for item in detail['dscr'])

    asyncio.run(verify())


@pytest.mark.parametrize(
    'decision,extra,status,offer_kind',
    [
        ('approve', {}, 'approved', 'original'),
        ('counter', {'amount': 3_000_000, 'term_months': 36}, 'counter_offer', 'counter'),
        ('reject', {}, 'rejected', None),
    ],
)
def test_staff_manual_decisions(api: FastAPI, decision, extra, status, offer_kind) -> None:
    async def check():
        async with AsyncClient(
            transport=ASGITransport(app=api), base_url='http://testserver'
        ) as client:
            app_id, owner = await manual_application(client)
            staff = await auth(client, 'staff1')
            assert (
                await client.post(
                    f'/staff/applications/{app_id}/decision',
                    headers=owner,
                    json={'decision': 'reject', 'comment': 'Нет'},
                )
            ).status_code == 403
            if decision == 'counter':
                assert (
                    await client.post(
                        f'/staff/applications/{app_id}/decision',
                        headers=staff,
                        json={'decision': 'reject', 'comment': '   '},
                    )
                ).status_code == 422
                assert (
                    await client.post(
                        f'/staff/applications/{app_id}/decision',
                        headers=staff,
                        json={
                            'decision': 'counter',
                            'amount': 299_999,
                            'comment': 'Тест',
                        },
                    )
                ).status_code == 422
                assert (
                    await client.post(
                        f'/staff/applications/{app_id}/decision',
                        headers=staff,
                        json={
                            'decision': 'counter',
                            'product_code': 'investment',
                            'comment': 'Тест',
                        },
                    )
                ).status_code == 422
            assert (
                await client.post(
                    f'/staff/applications/{app_id}/decision',
                    headers=staff,
                    json={
                        'decision': decision,
                        **extra,
                        'comment': 'Обоснование синтетического решения',
                    },
                )
            ).status_code == 200
            client_application = (await client.get(f'/applications/{app_id}', headers=owner)).json()
            assert client_application['status'] == status
            assert {'pd', 'risk_band', 'reasons', 'payload', 'model_version'}.isdisjoint(
                client_application
            )
            assert (
                await client.post(
                    f'/staff/applications/{app_id}/decision',
                    headers=staff,
                    json={
                        'decision': 'reject',
                        'comment': 'Повтор',
                    },
                )
            ).status_code == 409
            offers = (await client.get(f'/applications/{app_id}/offers', headers=owner)).json()
            detail = (await client.get(f'/staff/applications/{app_id}', headers=staff)).json()
            assert detail['decisions'][-1]['created_by'] == 'STAFF'
            assert detail['decisions'][-1]['rationale'] == ['Обоснование синтетического решения']
            journal = (
                await client.get(f'/staff/audit?application_id={app_id}', headers=staff)
            ).json()
            decision_row = next(row for row in journal if row['action'] == 'staff.decision')
            assert decision_row['decision_source'] == 'STAFF'
            assert decision_row['internal_comment'] == 'Обоснование синтетического решения'
            assert decision_row['decision_id'] == detail['decisions'][-1]['id']
            assert (
                decision_row['status_at_event']
                == {'approve': 'approved', 'counter': 'counter_offer', 'reject': 'rejected'}[
                    decision
                ]
            )
            assert decision_row['actor_display_name']
            assert decision_row['company_name'] == detail['company']['name']
            assert decision_row['comment_author_user_id']
            assert decision_row['comment_created_at']
            assert decision_row['status_event_id']
            assert detail['decisions'][-1]['internal_comment'] == decision_row['internal_comment']
            if offer_kind:
                assert len(offers) == 1 and offers[0]['kind'] == offer_kind
                assert offers[0]['monthly_payment'] > 0
                assert decision_row['offer_id'] == offers[0]['id']
                assert decision_row['offer_terms']['amount'] == offers[0]['amount']
                assert decision_row['offer_terms']['term_months'] == offers[0]['term_months']
                assert decision_row['offer_terms']['rate'] == offers[0]['rate']
            else:
                assert offers == []
                assert decision_row['offer_id'] is None
            return app_id

    app_id = asyncio.run(check())
    with Session(api.state.db_engine) as session:
        result = session.scalar(select(Decision).where(Decision.application_id == app_id))
        assert result.decision == decision
        assert result.created_by == 'STAFF' and result.created_by_user_id is not None
        assert result.rationale == ['Обоснование синтетического решения']
        assert result.rule_version == 'manual-demo-2026-1'
        assert session.scalar(
            select(func.count()).select_from(Offer).where(Offer.application_id == app_id)
        ) == (1 if offer_kind else 0)
        assert session.scalar(
            select(StatusEvent)
            .where(StatusEvent.application_id == app_id)
            .order_by(StatusEvent.sequence.desc())
        )
        assert (
            session.scalar(
                select(func.count())
                .select_from(AuditEvent)
                .where(AuditEvent.application_id == app_id, AuditEvent.action == 'staff.decision')
            )
            == 1
        )


def test_staff_requests_info_then_client_rechecks(api: FastAPI) -> None:
    async def check():
        async with AsyncClient(
            transport=ASGITransport(app=api), base_url='http://testserver'
        ) as client:
            app_id, owner = await manual_application(client)
            staff = await auth(client, 'staff1')
            assert (
                await client.post(
                    f'/staff/applications/{app_id}/request-info',
                    headers=staff,
                    json={'fields': ['purpose_details'], 'comment': '   '},
                )
            ).status_code == 422
            assert (
                await client.post(
                    f'/staff/applications/{app_id}/request-info',
                    headers=staff,
                    json={'fields': ['collateral_value'], 'comment': 'Тест'},
                )
            ).json()['code'] == 'INVALID_INFO_FIELDS'
            for invalid in (
                {'fields': [], 'comment': 'Тест'},
                {'fields': ['purpose_details', 'purpose_details'], 'comment': 'Тест'},
                {'fields': ['tax_debt'], 'comment': 'Тест'},
                {'message': 'Уточните данные'},
            ):
                assert (
                    await client.post(
                        f'/staff/applications/{app_id}/request-info',
                        headers=staff,
                        json=invalid,
                    )
                ).status_code == 422
            requested = await client.post(
                f'/staff/applications/{app_id}/request-info',
                headers=staff,
                json={
                    'fields': ['purpose_details'],
                    'comment': 'Внутренний анализ исполнения контракта',
                },
            )
            assert requested.status_code == 200
            assert requested.json()['status'] == 'needs_info'
            assert requested.json()['info_request'] == (
                'Пожалуйста, дополните: подробности цели кредита.'
            )
            detail = (await client.get(f'/staff/applications/{app_id}', headers=staff)).json()
            assert detail['comments'][-1]['body'] == 'Внутренний анализ исполнения контракта'
            assert detail['comments'][-1]['author_user_id']
            assert detail['comments'][-1]['created_at']
            journal = (
                await client.get(f'/staff/audit?application_id={app_id}', headers=staff)
            ).json()
            request_row = next(row for row in journal if row['action'] == 'staff.request_info')
            assert request_row['details']['requested_fields'] == ['purpose_details']
            assert request_row['internal_comment'] == 'Внутренний анализ исполнения контракта'
            assert request_row['status_at_event'] == 'needs_info'
            assert request_row['comment_id'] == detail['comments'][-1]['id']
            assert request_row['status_event_id'] == detail['comments'][-1]['status_event_id']
            assert request_row['comment_author_user_id'] == detail['comments'][-1]['author_user_id']
            assert request_row['comment_created_at'] == detail['comments'][-1]['created_at']
            assert request_row['actor_display_name']
            assert request_row['company_name'] == detail['company']['name']
            with Session(api.state.db_engine) as session:
                saved = session.get(AuditEvent, request_row['id'])
                saved.details = {
                    key: value for key, value in saved.details.items() if key != 'status_event_id'
                }
                session.commit()
            legacy_detail = (
                await client.get(f'/staff/applications/{app_id}', headers=staff)
            ).json()
            assert (
                legacy_detail['comments'][-1]['status_event_id'] == request_row['status_event_id']
            )
            legacy_journal = (
                await client.get(f'/staff/audit?application_id={app_id}', headers=staff)
            ).json()
            assert (
                next(row for row in legacy_journal if row['action'] == 'staff.request_info')[
                    'status_event_id'
                ]
                == request_row['status_event_id']
            )
            assert 'Внутренний анализ' not in str(requested.json())
            version = requested.json()['version']
            assert (
                await client.post(
                    f'/staff/applications/{app_id}/request-info',
                    headers=staff,
                    json={'fields': ['purpose_details'], 'comment': 'Повтор'},
                )
            ).status_code == 409
            updated = await client.post(
                f'/applications/{app_id}/additional-info',
                headers=owner,
                json={
                    'version': version,
                    'purpose_details': 'Контракт исполняется по графику',
                },
            )
            assert updated.status_code == 200, updated.text
            assert updated.json()['status'] == 'manual_review'
            history = (await client.get(f'/applications/{app_id}/history', headers=owner)).json()
            assert 'Внутренний анализ' not in str(history)
            assert [event['to_status'] for event in history][-3:] == [
                'scoring',
                'decision',
                'manual_review',
            ]
            assert [event['to_status'] for event in history].count('manual_review') == 2
            return app_id

    app_id = asyncio.run(check())
    with Session(api.state.db_engine) as session:
        assert (
            session.scalar(
                select(func.count())
                .select_from(ApplicationSnapshot)
                .where(ApplicationSnapshot.application_id == app_id)
            )
            == 3
        )
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
                .select_from(AuditEvent)
                .where(
                    AuditEvent.application_id == app_id, AuditEvent.action == 'staff.request_info'
                )
            )
            == 1
        )


def test_staff_request_info_investment_fields_are_client_fillable(api: FastAPI) -> None:
    async def check() -> None:
        async with AsyncClient(
            transport=ASGITransport(app=api), base_url='http://testserver'
        ) as client:
            app_id, owner = await manual_application(
                client,
                fields={
                    'product_code': 'investment',
                    'amount': 4_000_000,
                    'term_months': 24,
                    'purpose': 'equipment_purchase',
                    'investment_object': 'Станок',
                },
            )
            staff = await auth(client, 'staff1')
            preview = await client.post(
                '/staff/loan-preview',
                headers=staff,
                json={
                    'application_id': app_id,
                    'product_code': 'working_capital',
                    'amount': 3_000_000,
                    'term_months': 24,
                },
            )
            assert preview.status_code == 200, preview.text
            assert preview.json()['product']['code'] == 'working_capital'
            request = await client.post(
                f'/staff/applications/{app_id}/request-info',
                headers=staff,
                json={
                    'fields': ['investment_object', 'collateral_type', 'collateral_value'],
                    'comment': 'Внутреннее обоснование запроса',
                },
            )
            assert request.status_code == 200, request.text
            assert request.json()['info_request'] == (
                'Пожалуйста, дополните: объект инвестиций, вид залога, стоимость залога.'
            )
            updated = await client.post(
                f'/applications/{app_id}/additional-info',
                headers=owner,
                json={
                    'version': request.json()['version'],
                    'investment_object': 'Новый станок',
                    'collateral_type': 'Техника',
                    'collateral_value': 5_000_000,
                },
            )
            assert updated.status_code == 200, updated.text
            assert updated.json()['status'] == 'manual_review'
            assert 'Внутреннее обоснование' not in str(updated.json())

    asyncio.run(check())


def test_client_withdraws_manual_review_and_blocks_staff(api: FastAPI) -> None:
    async def check() -> int:
        async with AsyncClient(
            transport=ASGITransport(app=api), base_url='http://testserver'
        ) as client:
            app_id, owner = await manual_application(client)
            staff = await auth(client, 'staff1')
            other = await auth(client, 'client2')
            queue = await client.get('/staff/applications?status=manual_review', headers=staff)
            assert any(item['id'] == app_id for item in queue.json())
            assert (
                await client.post(f'/applications/{app_id}/withdraw', headers=other)
            ).status_code == 404
            withdrawn = await client.post(f'/applications/{app_id}/withdraw', headers=owner)
            assert withdrawn.status_code == 200
            assert withdrawn.json()['status'] == 'closed'
            assert (
                await client.post(f'/applications/{app_id}/withdraw', headers=owner)
            ).status_code == 409
            history = (await client.get(f'/applications/{app_id}/history', headers=owner)).json()
            assert history[-1]['from_status'] == 'manual_review'
            assert history[-1]['to_status'] == 'closed'
            assert history[-1]['actor_role'] == 'CLIENT'
            assert history[-1]['reason'] == 'Клиент отозвал заявку'
            assert [event['sequence'] for event in history] == list(range(1, len(history) + 1))
            queue = await client.get('/staff/applications?status=manual_review', headers=staff)
            assert all(item['id'] != app_id for item in queue.json())
            assert (
                await client.post(
                    f'/staff/applications/{app_id}/decision',
                    headers=staff,
                    json={'decision': 'reject', 'comment': 'Демо-решение'},
                )
            ).status_code == 409
            assert (
                await client.post(
                    f'/staff/applications/{app_id}/request-info',
                    headers=staff,
                    json={'fields': ['purpose_details'], 'comment': 'Дополнительные сведения'},
                )
            ).status_code == 409
            assert (await client.get(f'/staff/applications/{app_id}', headers=staff)).json()[
                'application'
            ]['status'] == 'closed'
            return app_id

    app_id = asyncio.run(check())
    with Session(api.state.db_engine) as session:
        assert session.get(Application, app_id).status == 'closed'
        assert (
            session.scalar(
                select(func.count())
                .select_from(AuditEvent)
                .where(
                    AuditEvent.application_id == app_id,
                    AuditEvent.action == 'application.closed',
                    AuditEvent.actor_role == 'CLIENT',
                )
            )
            == 1
        )
        assert (
            session.scalar(
                select(func.count()).select_from(Decision).where(Decision.application_id == app_id)
            )
            == 0
        )
        assert (
            session.scalar(
                select(func.count())
                .select_from(StatusEvent)
                .where(StatusEvent.application_id == app_id)
            )
            == 7
        )


def test_stale_staff_decision_after_withdraw_rolls_back(api: FastAPI) -> None:
    async def setup() -> int:
        async with AsyncClient(
            transport=ASGITransport(app=api), base_url='http://testserver'
        ) as client:
            application_id, _ = await manual_application(client)
            return application_id

    application_id = asyncio.run(setup())
    payload = ManualDecisionInput(decision='reject', comment='Синтетическое решение')
    with (
        Session(api.state.db_engine) as client_session,
        Session(api.state.db_engine) as staff_session,
    ):
        owner = client_session.scalar(select(User).where(User.login == 'client3'))
        employee = staff_session.scalar(select(User).where(User.login == 'staff1'))
        staff_session.get(Application, application_id)
        application = client_session.get(Application, application_id)
        transition(client_session, application, 'closed', owner, 'Клиент отозвал заявку')
        client_session.commit()
        with pytest.raises(ApiError) as error:
            make_manual_decision(
                staff_session, application_id, employee, payload, api.state.settings
            )
        assert error.value.status_code == 409
        staff_session.rollback()
    with Session(api.state.db_engine) as session:
        assert session.get(Application, application_id).status == 'closed'
        assert (
            session.scalar(
                select(func.count())
                .select_from(Decision)
                .where(Decision.application_id == application_id, Decision.created_by == 'STAFF')
            )
            == 0
        )
        assert (
            session.scalar(
                select(func.count())
                .select_from(AuditEvent)
                .where(
                    AuditEvent.application_id == application_id,
                    AuditEvent.action == 'staff.decision',
                )
            )
            == 0
        )


@pytest.mark.parametrize(
    'status',
    ['checking', 'scoring', 'decision', 'accepted', 'signed_demo', 'disbursed_demo'],
)
def test_withdraw_remains_forbidden_outside_client_states(status: str) -> None:
    assert TRANSITIONS.get((status, 'closed')) is None


def test_stale_staff_decision_rolls_back(api: FastAPI) -> None:
    async def setup() -> int:
        async with AsyncClient(
            transport=ASGITransport(app=api), base_url='http://testserver'
        ) as client:
            application_id, _ = await manual_application(client)
            return application_id

    application_id = asyncio.run(setup())
    payload = ManualDecisionInput(decision='approve', comment='Синтетическое решение')
    with Session(api.state.db_engine) as first, Session(api.state.db_engine) as second:
        actor_one = first.scalar(select(User).where(User.login == 'staff1'))
        actor_two = second.scalar(select(User).where(User.login == 'staff1'))
        second.get(Application, application_id)
        make_manual_decision(first, application_id, actor_one, payload, api.state.settings)
        first.commit()
        with pytest.raises(ApiError) as error:
            make_manual_decision(second, application_id, actor_two, payload, api.state.settings)
        assert error.value.status_code == 409
        second.rollback()
    with Session(api.state.db_engine) as session:
        assert (
            session.scalar(
                select(func.count())
                .select_from(Decision)
                .where(Decision.application_id == application_id, Decision.created_by == 'STAFF')
            )
            == 1
        )
        assert (
            session.scalar(
                select(func.count())
                .select_from(Offer)
                .where(Offer.application_id == application_id, Offer.created_by == 'STAFF')
            )
            == 1
        )
