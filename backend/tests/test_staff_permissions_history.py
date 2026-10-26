# ruff: noqa: F811 -- imported pytest fixture is used as a parameter
import asyncio
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy.orm import Session
from test_applications import api, auth, company_id, create  # noqa: F401
from test_staff import manual_application

from app.models import Application, AuditEvent, User
from app.security import hash_password
from app.services.staff import allowed_staff_actions


@pytest.mark.parametrize(
    'status',
    [
        'draft',
        'submitted',
        'checking',
        'scoring',
        'decision',
        'needs_info',
        'approved',
        'rejected',
        'counter_offer',
        'accepted',
        'signed_demo',
        'disbursed_demo',
        'closed',
    ],
)
def test_no_manual_actions_outside_review(status: str) -> None:
    app = SimpleNamespace(status=status, assigned_staff_user_id=1)
    actor = SimpleNamespace(id=1, role='STAFF', is_active=True)
    assert allowed_staff_actions(app, actor) == []


@pytest.mark.parametrize(
    ('assigned_id', 'actor_id', 'role', 'active'),
    [
        (None, 1, 'STAFF', True),
        (2, 1, 'STAFF', True),
        (1, 1, 'CLIENT', True),
        (1, 1, 'STAFF', False),
    ],
)
def test_no_manual_actions_without_authority(assigned_id, actor_id, role, active) -> None:
    app = SimpleNamespace(status='manual_review', assigned_staff_user_id=assigned_id)
    actor = SimpleNamespace(id=actor_id, role=role, is_active=active)
    assert allowed_staff_actions(app, actor) == []


def test_card_actions_and_assignment_snapshot(api: FastAPI) -> None:
    with Session(api.state.db_engine) as session:
        other = User(
            login='staff2',
            role='STAFF',
            full_name='Второй сотрудник',
            password_hash=hash_password('synthetic-demo-password-only'),
            is_active=True,
        )
        session.add(other)
        session.commit()
        other_id = other.id

    async def check() -> None:
        async with AsyncClient(
            transport=ASGITransport(app=api), base_url='http://testserver'
        ) as client:
            app_id, owner = await manual_application(client)
            staff, other = await auth(client, 'staff1'), await auth(client, 'staff2')
            detail = (await client.get(f'/staff/applications/{app_id}', headers=staff)).json()
            assigned_id = detail['application']['assigned_staff_user_id']
            assert assigned_id is not None
            assert detail['allowed_actions'] == ['approve', 'counter', 'reject', 'request_info']
            readonly = (await client.get(f'/staff/applications/{app_id}', headers=other)).json()
            assert readonly['allowed_actions'] == []
            for payload in (
                {'decision': 'approve'},
                {'decision': 'counter', 'amount': 3000000},
                {'decision': 'reject'},
            ):
                response = await client.post(
                    f'/staff/applications/{app_id}/decision',
                    headers=other,
                    json=payload | {'comment': 'Тест'},
                )
                assert response.status_code == 404
            assert (
                await client.post(
                    f'/staff/applications/{app_id}/request-info',
                    headers=other,
                    json={'fields': ['purpose_details'], 'comment': 'Тест'},
                )
            ).status_code == 404
            journal = (
                await client.get(f'/staff/audit?application_id={app_id}', headers=staff)
            ).json()
            saved = next(e for e in journal if e['action'] == 'application.manual_review')
            assert saved['assigned_staff_user_id'] == assigned_id
            assert saved['assigned_staff_display_name'] == 'Демо-аналитик'
            with Session(api.state.db_engine) as session:
                session.get(User, assigned_id).full_name = 'Новое имя сотрудника'
                session.get(Application, app_id).assigned_staff_user_id = other_id
                session.commit()
            journal = (
                await client.get(f'/staff/audit?application_id={app_id}', headers=staff)
            ).json()
            historical = next(e for e in journal if e['id'] == saved['id'])
            assert historical['assigned_staff_user_id'] == assigned_id
            assert historical['assigned_staff_display_name'] == 'Демо-аналитик'
            with Session(api.state.db_engine) as session:
                session.get(Application, app_id).assigned_staff_user_id = None
                session.commit()
            assert (await client.get(f'/staff/applications/{app_id}', headers=staff)).json()[
                'allowed_actions'
            ] == []
            assert (
                await client.post(f'/applications/{app_id}/withdraw', headers=owner)
            ).status_code == 200
            assert (await client.get(f'/staff/applications/{app_id}', headers=other)).json()[
                'allowed_actions'
            ] == []
            client_card = (await client.get(f'/applications/{app_id}', headers=owner)).json()
            assert 'assigned_staff_user_id' not in client_card
            assert 'allowed_actions' not in client_card

    asyncio.run(check())


def test_request_text_is_historical_and_private_note_stays_private(api: FastAPI) -> None:
    async def check() -> None:
        async with AsyncClient(
            transport=ASGITransport(app=api), base_url='http://testserver'
        ) as client:
            app_id, owner = await manual_application(client)
            staff = await auth(client, 'staff1')
            response = await client.post(
                f'/staff/applications/{app_id}/request-info',
                headers=staff,
                json={'fields': ['purpose_details'], 'comment': 'Секретная заметка'},
            )
            assert response.status_code == 200
            public_text = response.json()['info_request']
            assert 'Секретная' not in str(response.json())
            with Session(api.state.db_engine) as session:
                session.get(Application, app_id).info_request = 'Следующий запрос'
                session.commit()
            journal = (
                await client.get(f'/staff/audit?application_id={app_id}', headers=staff)
            ).json()
            for action in ('staff.request_info', 'application.needs_info'):
                row = next(e for e in journal if e['action'] == action)
                assert row['info_request_text'] == public_text
            row = next(e for e in journal if e['action'] == 'staff.request_info')
            assert row['details']['requested_fields'] == ['purpose_details']
            assert row['internal_comment'] == 'Секретная заметка'
            with Session(api.state.db_engine) as session:
                old = session.get(AuditEvent, row['id'])
                old.details = {k: v for k, v in old.details.items() if k != 'info_request_text'}
                session.commit()
            legacy = (
                await client.get(f'/staff/audit?application_id={app_id}', headers=staff)
            ).json()
            assert next(e for e in legacy if e['id'] == row['id'])['info_request_text'] is None
            client_card = (await client.get(f'/applications/{app_id}', headers=owner)).json()
            assert 'Секретная' not in str(client_card)

    asyncio.run(check())


def test_system_request_text_survives_withdrawal(api: FastAPI) -> None:
    async def check() -> None:
        async with AsyncClient(
            transport=ASGITransport(app=api), base_url='http://testserver'
        ) as client:
            owner = await auth(client)
            company = await company_id(client, owner)
            response = await create(
                client,
                owner,
                company,
                fields={
                    'product_code': 'investment',
                    'purpose': 'equipment_purchase',
                    'amount': 5000000,
                    'term_months': 24,
                },
            )
            assert response.status_code == 201
            app_id = response.json()['id']
            submitted = (await client.post(f'/applications/{app_id}/submit', headers=owner)).json()
            assert submitted['status'] == 'needs_info'
            text = submitted['info_request']
            assert text
            assert (
                await client.post(f'/applications/{app_id}/withdraw', headers=owner)
            ).status_code == 200
            staff = await auth(client, 'staff1')
            journal = (
                await client.get(f'/staff/audit?application_id={app_id}', headers=staff)
            ).json()
            event = next(e for e in journal if e['action'] == 'application.needs_info')
            assert event['actor_role'] == 'SYSTEM'
            assert event['info_request_text'] == text

    asyncio.run(check())
