import asyncio

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.application_service import TRANSITIONS, transition
from app.config import Settings
from app.db import Base
from app.errors import ApiError
from app.main import create_app
from app.models import Application, AuditEvent, Company, StatusEvent, User
from app.seed import seed_demo_catalog, seed_demo_users

PASSWORD = 'synthetic-demo-password-only'
JWT_SECRET = 'synthetic-test-jwt-secret-with-at-least-32-bytes'
BASE_PAYLOAD = {
    'product_code': 'working_capital',
    'amount': 3_000_000,
    'term_months': 24,
    'purpose': 'Закупка',
    'purpose_details': 'Синтетический тест',
}


@pytest.fixture
def api() -> FastAPI:
    engine = create_engine(
        'sqlite+pysqlite:///:memory:',
        connect_args={'check_same_thread': False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    settings = Settings(env='test', jwt_secret=JWT_SECRET, demo_password=PASSWORD)
    seed_demo_users(engine, settings)
    seed_demo_catalog(engine)
    seed_demo_catalog(engine)
    try:
        yield create_app(settings, db_engine=engine)
    finally:
        engine.dispose()


async def auth(client: AsyncClient, login: str = 'client1') -> dict[str, str]:
    response = await client.post('/auth/login', json={'login': login, 'password': PASSWORD})
    assert response.status_code == 200
    token = response.json()['access_token']
    return {'Authorization': f'Bearer {token}'}


async def company_id(client: AsyncClient, headers: dict[str, str]) -> int:
    response = await client.get('/me/companies', headers=headers)
    assert response.status_code == 200
    assert len(response.json()) == 1
    return response.json()[0]['id']


async def create(
    client: AsyncClient,
    headers: dict[str, str],
    company: int,
    *,
    key: str = 'unique-key',
    fields: dict | None = None,
):
    return await client.post(
        '/applications',
        headers=headers | {'Idempotency-Key': key},
        json={'company_id': company, **BASE_PAYLOAD, **(fields or {})},
    )


def test_catalog_and_client_role(api: FastAPI) -> None:
    async def check() -> None:
        async with AsyncClient(
            transport=ASGITransport(app=api), base_url='http://testserver'
        ) as client:
            assert (await client.get('/products')).status_code == 401
            staff = await auth(client, 'staff1')
            for path in ('/products', '/me/companies', '/applications'):
                assert (await client.get(path, headers=staff)).status_code == 403
            user = await auth(client)
            products = (await client.get('/products', headers=user)).json()
            assert {p['code'] for p in products} == {'working_capital', 'investment'}
            assert {p['terms_version'] for p in products} == {'demo-2026-1'}
            schema = (await client.get('/openapi.json')).json()
            assert '/applications/{application_id}/history' in schema['paths']
            assert '/me/companies' in schema['paths']
            companies_response = await client.get('/me/companies', headers=user)
            assert companies_response.headers['cache-control'] == 'no-store'
            company = companies_response.json()[0]
            assert company['name'] == 'ООО «Северный ветер»'
            assert company['avg_monthly_revenue'] == 4_200_000
            assert 'password_hash' not in str(company)

    asyncio.run(check())


def test_create_edit_submit_history_and_idempotency(api: FastAPI) -> None:
    async def check() -> None:
        async with AsyncClient(
            transport=ASGITransport(app=api), base_url='http://testserver'
        ) as client:
            headers = await auth(client)
            owner = await company_id(client, headers)
            assert (
                await client.post(
                    '/applications', headers=headers, json={'company_id': owner, **BASE_PAYLOAD}
                )
            ).status_code == 422
            staff = await auth(client, 'staff1')
            assert (await create(client, staff, owner, key='staff-key')).status_code == 403
            created = await create(client, headers, owner)
            assert created.status_code == 201, created.text
            body = created.json()
            assert body['status'] == 'draft'
            assert body['version'] == 1
            assert body['number'].startswith('CR-')
            assert 'idempotency_key' not in body
            app_id = body['id']
            replay = await create(client, headers, owner)
            assert replay.status_code == 200
            assert replay.json()['id'] == app_id
            conflict = await create(client, headers, owner, fields={'amount': 4_000_000})
            assert conflict.status_code == 409
            edited = await client.patch(
                f'/applications/{app_id}', headers=headers, json={'amount': 4_000_000}
            )
            assert edited.status_code == 200, edited.text
            assert edited.json()['amount'] == 4_000_000
            assert edited.json()['version'] == 2
            replay_after_edit = await create(client, headers, owner)
            assert replay_after_edit.status_code == 200
            assert replay_after_edit.json()['amount'] == 4_000_000
            assert (await client.get('/applications', headers=headers)).json()[0]['id'] == app_id
            assert (await client.get(f'/applications/{app_id}', headers=headers)).json()[
                'id'
            ] == app_id
            submitted = await client.post(f'/applications/{app_id}/submit', headers=headers)
            assert submitted.status_code == 200, submitted.text
            assert submitted.json()['status'] == 'approved'
            assert submitted.json()['version'] == 7
            assert (
                await client.post(f'/applications/{app_id}/submit', headers=headers)
            ).status_code == 409
            assert (
                await client.patch(
                    f'/applications/{app_id}', headers=headers, json={'purpose': 'Аренда'}
                )
            ).status_code == 409
            history = (await client.get(f'/applications/{app_id}/history', headers=headers)).json()
            assert [(item['from_status'], item['to_status']) for item in history] == [
                (None, 'draft'),
                ('draft', 'submitted'),
                ('submitted', 'checking'),
                ('checking', 'scoring'),
                ('scoring', 'decision'),
                ('decision', 'approved'),
            ]
            assert [item['sequence'] for item in history] == list(range(1, 7))
            assert all(
                item['actor_role'] == 'CLIENT' and item['actor_user_id'] for item in history[:2]
            )
            assert all(
                item['actor_role'] == 'SYSTEM' and item['actor_user_id'] is None
                for item in history[2:]
            )
            assert all(item['created_at'] and item['reason'] for item in history)
            second_page = await client.get(
                f'/applications/{app_id}/history?limit=1&offset=1', headers=headers
            )
            assert [item['to_status'] for item in second_page.json()] == ['submitted']
            assert (await client.get('/applications?limit=101', headers=headers)).status_code == 422

    asyncio.run(check())
    with Session(api.state.db_engine) as session:
        assert session.scalar(select(Application)).status == 'approved'
        assert len(list(session.scalars(select(StatusEvent)))) == 6
        assert len(list(session.scalars(select(AuditEvent)))) == 8


def test_withdraw_and_foreign_objects_are_hidden(api: FastAPI) -> None:
    async def check() -> None:
        async with AsyncClient(
            transport=ASGITransport(app=api), base_url='http://testserver'
        ) as client:
            one, two = await auth(client), await auth(client, 'client2')
            first_company, second_company = (
                await company_id(client, one),
                await company_id(client, two),
            )
            assert (await create(client, one, second_company, key='foreign')).status_code == 404
            first = await create(client, one, first_company, key='first')
            assert first.status_code == 201
            app_id = first.json()['id']
            for method, path, body in (
                ('GET', f'/applications/{app_id}', None),
                ('GET', f'/applications/{app_id}/history', None),
                ('PATCH', f'/applications/{app_id}', {'purpose': 'Тест'}),
                ('POST', f'/applications/{app_id}/submit', None),
                ('POST', f'/applications/{app_id}/withdraw', None),
            ):
                response = await client.request(method, path, headers=two, json=body)
                assert response.status_code == 404
            assert (await client.get('/applications', headers=two)).json() == []
            closed = await client.post(f'/applications/{app_id}/withdraw', headers=one)
            assert closed.status_code == 200
            assert closed.json()['status'] == 'closed'
            assert (
                await client.post(f'/applications/{app_id}/withdraw', headers=one)
            ).status_code == 409
            assert (
                await client.post(f'/applications/{app_id}/submit', headers=one)
            ).status_code == 409
            assert [
                entry['to_status']
                for entry in (
                    await client.get(f'/applications/{app_id}/history', headers=one)
                ).json()
            ] == ['draft', 'closed']

    asyncio.run(check())


@pytest.mark.parametrize(
    ('changes', 'status'),
    [
        ({'amount': 299_999}, 422),
        ({'amount': 30_000_001}, 422),
        ({'term_months': 2}, 422),
        ({'term_months': 37}, 422),
        ({'amount': 3.5}, 422),
        ({'status': 'approved'}, 422),
        ({'purpose': '  '}, 422),
    ],
)
def test_invalid_product_inputs(api: FastAPI, changes: dict, status: int) -> None:
    async def check() -> None:
        async with AsyncClient(
            transport=ASGITransport(app=api), base_url='http://testserver'
        ) as client:
            headers = await auth(client)
            owner = await company_id(client, headers)
            response = await create(client, headers, owner, fields=changes)
            assert response.status_code == status, response.text
            assert (await client.get('/applications', headers=headers)).json() == []

    asyncio.run(check())


def test_investment_valid_and_patch_validation(api: FastAPI) -> None:
    async def check() -> None:
        async with AsyncClient(
            transport=ASGITransport(app=api), base_url='http://testserver'
        ) as client:
            headers = await auth(client)
            owner = await company_id(client, headers)
            created = await create(
                client,
                headers,
                owner,
                fields={
                    'product_code': 'investment',
                    'amount': 5_000_000,
                    'investment_object': 'Оборудование',
                    'collateral_type': 'Техника',
                    'collateral_value': 6_000_000,
                },
            )
            assert created.status_code == 201, created.text
            app_id = created.json()['id']
            assert (
                await client.patch(
                    f'/applications/{app_id}', headers=headers, json={'amount': 101_000_000}
                )
            ).status_code == 422
            assert (
                await client.patch(
                    f'/applications/{app_id}', headers=headers, json={'collateral_type': None}
                )
            ).status_code == 200
            assert (
                await client.patch(f'/applications/{app_id}', headers=headers, json={})
            ).status_code == 422
            assert (await client.get(f'/applications/{app_id}', headers=headers)).json()[
                'version'
            ] == 2

    asyncio.run(check())


def test_fsm_table_and_stale_transition(api: FastAPI) -> None:
    assert TRANSITIONS[('draft', 'submitted')] == 'CLIENT'
    assert TRANSITIONS[('checking', 'rejected')] == 'SYSTEM'
    assert ('submitted', 'closed') not in TRANSITIONS
    with Session(api.state.db_engine) as session:
        actor = session.scalar(select(User).where(User.login == 'client1'))
        company = session.scalar(select(Company).where(Company.owner_user_id == actor.id))
        from app.application_schemas import ApplicationCreate
        from app.application_service import create_application

        application, _ = create_application(
            session, ApplicationCreate(company_id=company.id, **BASE_PAYLOAD), 'test-stale', actor
        )
        session.commit()
        application_id = application.id
    with Session(api.state.db_engine) as first, Session(api.state.db_engine) as second:
        actor_one = first.scalar(select(User).where(User.login == 'client1'))
        actor_two = second.scalar(select(User).where(User.login == 'client1'))
        old = first.get(Application, application_id)
        stale = second.get(Application, application_id)
        transition(first, old, 'submitted', actor_one, 'Тест')
        first.commit()
        with pytest.raises(ApiError) as error:
            transition(second, stale, 'submitted', actor_two, 'Тест')
        assert error.value.status_code == 409
        second.rollback()
    with Session(api.state.db_engine) as session:
        assert [
            event.to_status
            for event in session.scalars(select(StatusEvent).order_by(StatusEvent.sequence))
        ] == ['draft', 'submitted']
