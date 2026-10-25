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
    Score,
    StaffComment,
    StatusEvent,
    User,
)
from app.security import hash_password
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
            staff = await auth(client, 'staff1')
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
            for path in (f'/staff/applications/{app_id}', f'/staff/audit?application_id={app_id}'):
                assert (await client.get(path, headers=other_staff)).status_code == 200
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
            detail = (await client.get(f'/staff/applications/{app_id}', headers=staff)).json()
            assert detail['application']['status'] == 'manual_review'
            assert detail['company']['name'] == 'ООО «Гранит-Строй»'
            assert detail['company_snapshot']['avg_monthly_revenue'] == 2_000_000
            assert {item['source'] for item in detail['checks']} == {'DATA', 'FNS', 'BKI'}
            assert detail['score']['pd'] is not None
            assert detail['score']['reasons']
            assert detail['history'][-1]['to_status'] == 'manual_review'
            assert (
                await client.get(f'/staff/applications/{app_id}', headers=client_headers)
            ).status_code == 403
            audit_rows = (
                await client.get(f'/staff/audit?application_id={app_id}', headers=staff)
            ).json()
            assert any(item['action'] == 'staff.view' for item in audit_rows)
            assert (await client.get('/staff/audit?limit=1', headers=staff)).status_code == 200
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


def test_staff_overview_preview_and_internal_comments(api: FastAPI) -> None:
    with Session(api.state.db_engine) as session:
        session.add(
            User(
                login='staff2',
                role='STAFF',
                full_name='Второй аналитик',
                password_hash=hash_password('synthetic-demo-password-only'),
                is_active=True,
            )
        )
        session.commit()

    async def check() -> int:
        async with AsyncClient(
            transport=ASGITransport(app=api), base_url='http://testserver'
        ) as client:
            owner = await auth(client, 'client1')
            owner_company = await company_id(client, owner)
            draft = await create(client, owner, owner_company, key='draft-overview')
            assert draft.status_code == 201
            draft_id = draft.json()['id']
            app_id, applicant = await manual_application(client)
            staff = await auth(client, 'staff1')
            other_staff = await auth(client, 'staff2')
            queue = (await client.get('/staff/applications', headers=other_staff)).json()
            assert [item['id'] for item in queue] == [app_id]
            assert (
                await client.get('/staff/applications?status=draft', headers=staff)
            ).json() == []
            assert (
                await client.get(f'/staff/applications/{draft_id}', headers=staff)
            ).status_code == 404
            assert (
                await client.post(
                    '/staff/loan-preview', headers=staff, json={'application_id': draft_id}
                )
            ).status_code == 404
            assert (
                await client.get(f'/staff/applications/{app_id}', headers=other_staff)
            ).status_code == 200
            assert (
                await client.post(
                    f'/staff/applications/{app_id}/request-info',
                    headers=other_staff,
                    json={'message': 'Запрос клиента'},
                )
            ).status_code == 404
            preview = await client.post(
                '/staff/loan-preview',
                headers=other_staff,
                json={'application_id': app_id, 'amount': 3_000_000, 'term_months': 36},
            )
            assert preview.status_code == 200, preview.text
            assert (
                await client.post(
                    '/staff/loan-preview', headers=applicant, json={'application_id': app_id}
                )
            ).status_code == 403
            values = preview.json()
            assert values['application_id'] == app_id
            assert values['snapshot_id'] > 0 and values['snapshot_version'] > 0
            assert values['amount'] == 3_000_000 and values['term_months'] == 36
            assert abs(
                Decimal(str(values['dscr']))
                - Decimal(2_000_000 - 1_600_000) / (180_000 + values['monthly_payment'])
            ) < Decimal('0.000000000000000000000000001')
            detail = (await client.get(f'/staff/applications/{app_id}', headers=staff)).json()
            assert detail['score']['snapshot_id'] == values['snapshot_id']
            assert detail['score']['monthly_payment'] > 0
            assert Decimal(str(detail['score']['dscr'])) > 0
            assert detail['score']['rate'] is not None
            assert (
                await client.post(
                    f'/staff/applications/{app_id}/comments',
                    headers=staff,
                    json={'body': '   '},
                )
            ).status_code == 422
            response = await client.post(
                f'/staff/applications/{app_id}/comments',
                headers=other_staff,
                json={'body': '  Внутренний комментарий  '},
            )
            assert response.status_code == 201, response.text
            assert response.json()['body'] == 'Внутренний комментарий'
            comments = (
                await client.get(f'/staff/applications/{app_id}/comments', headers=staff)
            ).json()
            assert len(comments) == 1
            assert (
                await client.get(f'/staff/applications/{app_id}/comments', headers=applicant)
            ).status_code == 403
            client_view = (await client.get(f'/applications/{app_id}', headers=applicant)).json()
            assert 'Внутренний комментарий' not in str(client_view)
            assert 'Внутренний комментарий' not in str(
                (await client.get(f'/applications/{app_id}/history', headers=applicant)).json()
            )

            class BrokenScorer:
                def score(self, _request):
                    raise RuntimeError('synthetic scorer failure')

            api.state.workflow_scorer = BrokenScorer()
            try:
                assert (
                    await client.post(
                        '/staff/loan-preview', headers=staff, json={'application_id': app_id}
                    )
                ).status_code == 503
            finally:
                api.state.workflow_scorer = None
            return app_id

    app_id = asyncio.run(check())
    with Session(api.state.db_engine) as session:
        assert (
            session.scalar(
                select(func.count())
                .select_from(StaffComment)
                .where(StaffComment.application_id == app_id)
            )
            == 1
        )
        assert session.get(Application, app_id).status == 'manual_review'


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
            if offer_kind:
                assert len(offers) == 1 and offers[0]['kind'] == offer_kind
                assert offers[0]['monthly_payment'] > 0
            else:
                assert offers == []
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
                    json={'message': '   '},
                )
            ).status_code == 422
            requested = await client.post(
                f'/staff/applications/{app_id}/request-info',
                headers=staff,
                json={
                    'message': 'Уточните исполнение контракта',
                },
            )
            assert requested.status_code == 200
            assert requested.json()['status'] == 'needs_info'
            assert requested.json()['info_request'] == 'Уточните исполнение контракта'
            version = requested.json()['version']
            assert (
                await client.post(
                    f'/staff/applications/{app_id}/request-info',
                    headers=staff,
                    json={'message': 'Повтор'},
                )
            ).status_code == 409
            updated = await client.post(
                f'/applications/{app_id}/additional-info',
                headers=owner,
                json={
                    'version': version,
                    'message': 'Контракт исполняется по графику',
                },
            )
            assert updated.status_code == 200, updated.text
            assert updated.json()['status'] == 'manual_review'
            history = (await client.get(f'/applications/{app_id}/history', headers=owner)).json()
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
                    json={'message': 'Дополнительные сведения'},
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
