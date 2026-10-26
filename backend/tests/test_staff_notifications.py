# ruff: noqa: F811 -- pytest fixture is imported for parameters
import asyncio

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session
from test_applications import api, auth  # noqa: F401
from test_staff import manual_application

from app.models import Application, AuditEvent, User
from app.security import hash_password


def add_other_staff(api):
    with Session(api.state.db_engine) as db:
        user = User(
            login='staff2',
            role='STAFF',
            full_name='Другой сотрудник',
            password_hash=hash_password('synthetic-demo-password-only'),
            is_active=True,
        )
        db.add(user)
        db.commit()
        return user.id


def test_assignment_replies_withdrawal_and_recipient_isolation(api):
    other_id = add_other_staff(api)

    async def check():
        async with AsyncClient(
            transport=ASGITransport(app=api), base_url='http://testserver'
        ) as client:
            app_id, owner = await manual_application(client)
            staff, other = await auth(client, 'staff1'), await auth(client, 'staff2')
            assert (await client.get('/staff/notifications')).status_code == 401
            assert (await client.get('/staff/notifications', headers=owner)).status_code == 403
            notices = (await client.get('/staff/notifications', headers=staff)).json()
            assert [n['kind'] for n in notices] == ['assigned']
            assert notices[0]['company_name'] and notices[0]['application_number']
            assert (await client.get('/staff/notifications', headers=other)).json() == []
            reply_ids = []
            for text in ('Первое дополнение', 'Второе дополнение'):
                requested = await client.post(
                    f'/staff/applications/{app_id}/request-info',
                    headers=staff,
                    json={'fields': ['purpose_details'], 'comment': 'Скрытый текст'},
                )
                assert requested.status_code == 200, requested.text
                detail = (await client.get(f'/applications/{app_id}', headers=owner)).json()
                reply = await client.post(
                    f'/applications/{app_id}/additional-info',
                    headers=owner,
                    json={
                        'version': detail['version'],
                        'request_event_id': detail['active_info_request']['request_event_id'],
                        'purpose_details': text,
                    },
                )
                assert reply.status_code == 200, reply.text
                reply_ids.append(detail['active_info_request']['request_event_id'])
            assert len(set(reply_ids)) == 2
            notices = (await client.get('/staff/notifications', headers=staff)).json()
            assert [n['kind'] for n in notices] == [
                'additional_info',
                'additional_info',
                'assigned',
            ]
            # Simulate a later reassignment: replies still belong to their request's author.
            with Session(api.state.db_engine) as db:
                db.get(Application, app_id).assigned_staff_user_id = other_id
                db.commit()
            withdrawn = await client.post(f'/applications/{app_id}/withdraw', headers=owner)
            assert withdrawn.status_code == 200
            notices = (await client.get('/staff/notifications', headers=staff)).json()
            assert len(notices) == 3 and all(n['kind'] != 'withdrawn' for n in notices)
            other_notices = (await client.get('/staff/notifications', headers=other)).json()
            assert [n['kind'] for n in other_notices] == ['withdrawn']
            assert 'Скрытый текст' not in str(notices)
            with Session(api.state.db_engine) as db:
                before = db.scalar(select(func.count()).select_from(AuditEvent))
            repeated = (await client.get('/staff/notifications', headers=staff)).json()
            assert repeated == notices
            with Session(api.state.db_engine) as db:
                assert db.scalar(select(func.count()).select_from(AuditEvent)) == before
            pages = []
            for offset in range(3):
                pages += (
                    await client.get(f'/staff/notifications?limit=1&offset={offset}', headers=staff)
                ).json()
            assert pages == notices

    asyncio.run(check())


@pytest.mark.parametrize('reply_action', ['accept', 'decline'])
def test_counter_offer_replies_use_offer_author_not_current_assignment(api, reply_action):
    other_id = add_other_staff(api)

    async def check():
        async with AsyncClient(
            transport=ASGITransport(app=api), base_url='http://testserver'
        ) as client:
            app_id, owner = await manual_application(client)
            staff, other = await auth(client, 'staff1'), await auth(client, 'staff2')
            decision = await client.post(
                f'/staff/applications/{app_id}/decision',
                headers=staff,
                json={
                    'decision': 'counter',
                    'amount': 3_000_000,
                    'term_months': 24,
                    'comment': 'Скрытый комментарий',
                },
            )
            assert decision.status_code == 200, decision.text
            offer = (await client.get(f'/applications/{app_id}/offers', headers=owner)).json()[0]
            with Session(api.state.db_engine) as db:
                db.get(Application, app_id).assigned_staff_user_id = other_id
                db.commit()
            response = await client.post(f'/offers/{offer["id"]}/{reply_action}', headers=owner)
            assert response.status_code == 200, response.text
            notices = (await client.get('/staff/notifications', headers=staff)).json()
            expected = 'offer_accepted' if reply_action == 'accept' else 'offer_declined'
            assert [n['kind'] for n in notices] == [expected, 'assigned']
            assert (await client.get('/staff/notifications', headers=other)).json() == []
            # Missing historical link must not be inferred from the latest offer/current status.
            with Session(api.state.db_engine) as db:
                event = db.scalar(
                    select(AuditEvent).where(
                        AuditEvent.application_id == app_id,
                        AuditEvent.action == f'offer.{reply_action}',
                    )
                )
                event.details = {k: v for k, v in event.details.items() if k != 'offer_id'}
                db.commit()
            legacy = (await client.get('/staff/notifications', headers=staff)).json()
            assert [n['kind'] for n in legacy] == ['assigned']

    asyncio.run(check())


@pytest.mark.parametrize('decision_kind', ['approve', 'system'])
def test_original_or_system_offer_never_notifies_staff(api, decision_kind):
    from test_completion import submitted_offer

    async def check():
        async with AsyncClient(
            transport=ASGITransport(app=api), base_url='http://testserver'
        ) as client:
            staff = await auth(client, 'staff1')
            if decision_kind == 'system':
                app_id, offer_id, owner, _ = await submitted_offer(client)
            else:
                app_id, owner = await manual_application(client)
                result = await client.post(
                    f'/staff/applications/{app_id}/decision',
                    headers=staff,
                    json={'decision': 'approve', 'comment': 'Одобрение исходных условий'},
                )
                assert result.status_code == 200, result.text
                offer_id = (
                    await client.get(f'/applications/{app_id}/offers', headers=owner)
                ).json()[0]['id']
            result = await client.post(f'/offers/{offer_id}/accept', headers=owner)
            assert result.status_code == 200, result.text
            notices = (await client.get('/staff/notifications', headers=staff)).json()
            assert all(n['kind'] not in ('offer_accepted', 'offer_declined') for n in notices)

    asyncio.run(check())
