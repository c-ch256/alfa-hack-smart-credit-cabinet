# ruff: noqa: F811 -- pytest fixture импортируется для параметров тестов

import asyncio
from datetime import UTC, datetime, timedelta

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session
from test_applications import api, auth, company_id, create  # noqa: F401 -- pytest fixture

from app.errors import ApiError
from app.models import Application, AuditEvent, Offer, StatusEvent, User
from app.services import completion
from app.services.completion import accept_offer, issue_demo_code, sign_and_disburse_demo


async def submitted_offer(client: AsyncClient, login='client1', fields=None, key='completion'):
    owner = await auth(client, login)
    company = await company_id(client, owner)
    created = await create(client, owner, company, key=key, fields=fields)
    assert created.status_code == 201, created.text
    application_id = created.json()['id']
    submitted = await client.post(f'/applications/{application_id}/submit', headers=owner)
    assert submitted.status_code == 200, submitted.text
    offers = (await client.get(f'/applications/{application_id}/offers', headers=owner)).json()
    assert len(offers) == 1
    return application_id, offers[0]['id'], owner, submitted.json()['status']


def test_approved_offer_to_demo_disbursement(api: FastAPI) -> None:
    async def check():
        async with AsyncClient(
            transport=ASGITransport(app=api), base_url='http://testserver'
        ) as client:
            application_id, offer_id, owner, status = await submitted_offer(client)
            assert status == 'approved'
            staff = await auth(client, 'staff1')
            other = await auth(client, 'client2')
            assert (await client.post(f'/offers/{offer_id}/accept')).status_code == 401
            assert (
                await client.post(f'/offers/{offer_id}/accept', headers=staff)
            ).status_code == 403
            assert (
                await client.post(f'/offers/{offer_id}/accept', headers=other)
            ).status_code == 404
            assert (
                await client.post(f'/applications/{application_id}/sign-demo/code', headers=owner)
            ).status_code == 409
            accepted = await client.post(f'/offers/{offer_id}/accept', headers=owner)
            assert accepted.status_code == 200 and accepted.json()['status'] == 'accepted'
            assert (
                await client.post(f'/offers/{offer_id}/accept', headers=owner)
            ).status_code == 409
            assert (
                await client.post(f'/offers/{offer_id}/decline', headers=owner)
            ).status_code == 409
            assert (
                await client.post(f'/applications/{application_id}/sign-demo/code', headers=other)
            ).status_code == 404
            code_response = await client.post(
                f'/applications/{application_id}/sign-demo/code', headers=owner
            )
            assert code_response.status_code == 200
            assert code_response.headers['cache-control'] == 'no-store'
            code = code_response.json()['code']
            assert len(code) == 6 and code.isdecimal()
            assert (
                await client.post(f'/applications/{application_id}/sign-demo/code', headers=owner)
            ).status_code == 429
            assert (
                await client.post(
                    f'/applications/{application_id}/sign-demo', headers=other, json={'code': code}
                )
            ).status_code == 404
            signed = await client.post(
                f'/applications/{application_id}/sign-demo', headers=owner, json={'code': code}
            )
            assert signed.status_code == 200, signed.text
            assert signed.json()['status'] == 'disbursed_demo'
            assert signed.json()['status_label'] == 'Кредит выдан (демо)'
            current = await client.get(f'/applications/{application_id}', headers=owner)
            assert current.json()['status_label'] == 'Кредит выдан (демо)'
            assert set(signed.json()).isdisjoint({'pd', 'risk_band', 'reasons', 'sign_code_hash'})
            assert (
                await client.post(
                    f'/applications/{application_id}/sign-demo', headers=owner, json={'code': code}
                )
            ).status_code == 409
            history = (
                await client.get(f'/applications/{application_id}/history', headers=owner)
            ).json()
            assert [row['to_status'] for row in history] == [
                'draft',
                'submitted',
                'checking',
                'scoring',
                'decision',
                'approved',
                'accepted',
                'signed_demo',
                'disbursed_demo',
            ]
            assert history[-2]['actor_role'] == 'CLIENT'
            assert history[-1]['actor_role'] == 'SYSTEM'
            assert history[-1]['reason'] == 'Кредит выдан (демо)'
            assert code not in str(history)
            return application_id, offer_id, code

    application_id, offer_id, code = asyncio.run(check())
    with Session(api.state.db_engine) as session:
        application = session.get(Application, application_id)
        assert application.sign_code_hash is None
        assert application.sign_code_expires_at is None
        assert application.sign_code_attempts == 0
        assert session.get(Offer, offer_id).status == 'accepted'
        assert (
            session.scalar(
                select(func.count())
                .select_from(StatusEvent)
                .where(
                    StatusEvent.application_id == application_id,
                    StatusEvent.to_status == 'disbursed_demo',
                )
            )
            == 1
        )
        audits = list(
            session.scalars(select(AuditEvent).where(AuditEvent.application_id == application_id))
        )
        assert {
            'offer.accept',
            'sign_demo.code_issued',
            'sign_demo.signed',
            'disbursement.demo',
        } <= {row.action for row in audits}
        assert code not in str([row.details for row in audits])


@pytest.mark.parametrize(
    'login,fields,expected',
    [
        ('client2', {'amount': 5_000_000, 'term_months': 12}, 'counter_offer'),
        ('client1', {}, 'approved'),
    ],
)
def test_counter_offer_acceptance_and_decline(api: FastAPI, login, fields, expected) -> None:
    async def check():
        async with AsyncClient(
            transport=ASGITransport(app=api), base_url='http://testserver'
        ) as client:
            app_id, offer_id, owner, status = await submitted_offer(client, login, fields)
            assert status == expected
            assert (
                await client.post(f'/offers/{offer_id}/decline', headers=owner)
            ).status_code == 200
            assert (
                await client.post(f'/offers/{offer_id}/decline', headers=owner)
            ).status_code == 409
            assert (
                await client.post(f'/offers/{offer_id}/accept', headers=owner)
            ).status_code == 409
            assert (
                await client.post(f'/applications/{app_id}/sign-demo/code', headers=owner)
            ).status_code == 409
            history = (await client.get(f'/applications/{app_id}/history', headers=owner)).json()
            assert [row['to_status'] for row in history][-2:] == [expected, 'closed']
            assert history[-1]['actor_role'] == 'CLIENT'
            return offer_id

    offer_id = asyncio.run(check())
    with Session(api.state.db_engine) as session:
        assert session.get(Offer, offer_id).status == 'declined'


def test_counter_offer_can_be_accepted(api: FastAPI) -> None:
    async def check():
        async with AsyncClient(
            transport=ASGITransport(app=api), base_url='http://testserver'
        ) as client:
            app_id, offer_id, owner, status = await submitted_offer(
                client, 'client2', {'amount': 5_000_000, 'term_months': 12}
            )
            assert status == 'counter_offer'
            assert (await client.post(f'/offers/{offer_id}/accept', headers=owner)).json()[
                'status'
            ] == 'accepted'
            code = (
                await client.post(f'/applications/{app_id}/sign-demo/code', headers=owner)
            ).json()['code']
            response = await client.post(
                f'/applications/{app_id}/sign-demo', headers=owner, json={'code': code}
            )
            assert response.status_code == 200 and response.json()['status'] == 'disbursed_demo'

    asyncio.run(check())


def test_invalid_and_expired_code(api: FastAPI) -> None:
    async def check():
        async with AsyncClient(
            transport=ASGITransport(app=api), base_url='http://testserver'
        ) as client:
            app_id, offer_id, owner, _ = await submitted_offer(client)
            await client.post(f'/offers/{offer_id}/accept', headers=owner)
            code = (
                await client.post(f'/applications/{app_id}/sign-demo/code', headers=owner)
            ).json()['code']
            wrong = '000000' if code != '000000' else '111111'
            for attempt in range(5):
                response = await client.post(
                    f'/applications/{app_id}/sign-demo', headers=owner, json={'code': wrong}
                )
                assert response.status_code == (429 if attempt == 4 else 422)
                assert response.json()['code'] == (
                    'CODE_ATTEMPTS_EXCEEDED' if attempt == 4 else 'INVALID_CODE'
                )
                assert code not in response.text
                assert 'hash' not in response.text
            blocked = await client.post(
                f'/applications/{app_id}/sign-demo', headers=owner, json={'code': code}
            )
            assert blocked.status_code == 429
            assert blocked.json()['code'] == 'CODE_ATTEMPTS_EXCEEDED'
            assert (
                await client.post(f'/applications/{app_id}/sign-demo/code', headers=owner)
            ).status_code == 429
            return app_id, owner

    app_id, owner = asyncio.run(check())
    with Session(api.state.db_engine) as session:
        application = session.get(Application, app_id)
        assert application.status == 'accepted'
        assert application.sign_code_attempts == 5
        assert application.sign_code_hash is None
        application.sign_code_expires_at = datetime.now(UTC) - timedelta(seconds=1)
        session.commit()

    async def retry():
        async with AsyncClient(
            transport=ASGITransport(app=api), base_url='http://testserver'
        ) as client:
            new_code = (
                await client.post(f'/applications/{app_id}/sign-demo/code', headers=owner)
            ).json()['code']
            assert len(new_code) == 6
            return new_code

    code = asyncio.run(retry())
    with Session(api.state.db_engine) as session:
        application = session.get(Application, app_id)
        application.sign_code_expires_at = datetime.now(UTC) - timedelta(seconds=1)
        session.commit()

    async def expired():
        async with AsyncClient(
            transport=ASGITransport(app=api), base_url='http://testserver'
        ) as client:
            response = await client.post(
                f'/applications/{app_id}/sign-demo', headers=owner, json={'code': code}
            )
            assert response.status_code == 422 and response.json()['code'] == 'CODE_EXPIRED'
            assert code not in response.text
            assert (
                await client.post(
                    f'/applications/{app_id}/sign-demo', headers=owner, json={'code': '123'}
                )
            ).status_code == 422

    asyncio.run(expired())


def test_stale_offer_and_signing_operations(api: FastAPI) -> None:
    async def setup():
        async with AsyncClient(
            transport=ASGITransport(app=api), base_url='http://testserver'
        ) as client:
            return await submitted_offer(client)

    app_id, offer_id, _, _ = asyncio.run(setup())
    with Session(api.state.db_engine) as first, Session(api.state.db_engine) as second:
        actor_one = first.scalar(select(User).where(User.login == 'client1'))
        actor_two = second.scalar(select(User).where(User.login == 'client1'))
        second.get(Application, app_id)
        second.get(Offer, offer_id)
        accept_offer(first, offer_id, actor_one)
        first.commit()
        with pytest.raises(ApiError) as error:
            accept_offer(second, offer_id, actor_two)
        assert error.value.status_code == 409
        second.rollback()

    with Session(api.state.db_engine) as first, Session(api.state.db_engine) as second:
        actor_one = first.scalar(select(User).where(User.login == 'client1'))
        actor_two = second.scalar(select(User).where(User.login == 'client1'))
        second.get(Application, app_id)
        code, _ = issue_demo_code(first, app_id, actor_one, api.state.settings)
        first.commit()
        with pytest.raises(ApiError) as error:
            issue_demo_code(second, app_id, actor_two, api.state.settings)
        assert error.value.status_code in (409, 429)
        second.rollback()

    with Session(api.state.db_engine) as first, Session(api.state.db_engine) as second:
        actor_one = first.scalar(select(User).where(User.login == 'client1'))
        actor_two = second.scalar(select(User).where(User.login == 'client1'))
        second.get(Application, app_id)
        outcome = sign_and_disburse_demo(first, app_id, actor_one, api.state.settings, code)
        assert outcome.error is None
        first.commit()
        with pytest.raises(ApiError) as error:
            sign_and_disburse_demo(second, app_id, actor_two, api.state.settings, code)
        assert error.value.status_code == 409
        second.rollback()
    with Session(api.state.db_engine) as session:
        assert (
            session.scalar(
                select(func.count())
                .select_from(StatusEvent)
                .where(
                    StatusEvent.application_id == app_id, StatusEvent.to_status == 'disbursed_demo'
                )
            )
            == 1
        )


def test_failed_demo_disbursement_rolls_back_signing(api: FastAPI, monkeypatch) -> None:
    async def setup():
        async with AsyncClient(
            transport=ASGITransport(app=api), base_url='http://testserver'
        ) as client:
            app_id, offer_id, owner, _ = await submitted_offer(client)
            await client.post(f'/offers/{offer_id}/accept', headers=owner)
            code = (
                await client.post(f'/applications/{app_id}/sign-demo/code', headers=owner)
            ).json()['code']
            return app_id, owner, code

    app_id, owner, code = asyncio.run(setup())
    original_transition = completion.transition

    def fail_disbursement(session, application, to_status, actor, reason):
        if to_status == 'disbursed_demo':
            raise ApiError(503, 'DEMO_UNAVAILABLE', 'Демо-выдача недоступна')
        return original_transition(session, application, to_status, actor, reason)

    monkeypatch.setattr(completion, 'transition', fail_disbursement)

    async def attempt():
        async with AsyncClient(
            transport=ASGITransport(app=api), base_url='http://testserver'
        ) as client:
            response = await client.post(
                f'/applications/{app_id}/sign-demo', headers=owner, json={'code': code}
            )
            assert response.status_code == 503

    asyncio.run(attempt())
    with Session(api.state.db_engine) as session:
        application = session.get(Application, app_id)
        assert application.status == 'accepted'
        assert application.sign_code_hash is not None
        assert (
            session.scalar(
                select(func.count())
                .select_from(StatusEvent)
                .where(
                    StatusEvent.application_id == app_id,
                    StatusEvent.to_status.in_(['signed_demo', 'disbursed_demo']),
                )
            )
            == 0
        )
