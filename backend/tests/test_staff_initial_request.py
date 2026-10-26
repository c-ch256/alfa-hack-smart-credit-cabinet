# ruff: noqa: F811 -- imported fixture is used as a test parameter
import asyncio
from datetime import UTC, datetime
from types import SimpleNamespace

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.orm import Session
from test_applications import api, auth, company_id, create  # noqa: F401

from app.models import ApplicationSnapshot
from app.services.additional_info_history import initial_request


def test_first_submission_persists_through_info_cycles(api: FastAPI):
    async def check():
        async with AsyncClient(
            transport=ASGITransport(app=api), base_url='http://testserver'
        ) as client:
            owner = await auth(client, 'client3')
            company = await company_id(client, owner)
            draft = await create(client, owner, company, fields={'amount': 4_000_000})
            assert draft.status_code == 201
            app_id = draft.json()['id']
            edited = await client.patch(
                f'/applications/{app_id}',
                headers=owner,
                json={'purpose_details': 'Первоначальные сведения'},
            )
            assert edited.status_code == 200
            submitted = await client.post(f'/applications/{app_id}/submit', headers=owner)
            assert submitted.status_code == 200
            staff = await auth(client, 'staff1')
            first = (await client.get(f'/staff/applications/{app_id}', headers=staff)).json()
            original = first['initial_request']
            assert original['purpose_details'] == 'Первоначальные сведения'
            assert original['snapshot_version'] == 3
            for after in ('Первое дополнение', 'Второе дополнение'):
                request = await client.post(
                    f'/staff/applications/{app_id}/request-info',
                    headers=staff,
                    json={'fields': ['purpose_details'], 'comment': 'Тестовый комментарий'},
                )
                assert request.status_code == 200
                response = await client.post(
                    f'/applications/{app_id}/additional-info',
                    headers=owner,
                    json={'version': request.json()['version'], 'purpose_details': after},
                )
                assert response.status_code == 200
                detail = (await client.get(f'/staff/applications/{app_id}', headers=staff)).json()
                assert detail['initial_request'] == original
                assert detail['application']['purpose_details'] == after
                assert detail['snapshot_version'] > original['snapshot_version']
                assert detail['additional_info_responses'][0]['changes'][0]['after'] == after
                assert 'initial_request' not in response.json()
            with Session(api.state.db_engine) as session:
                snapshots = list(
                    session.scalars(
                        select(ApplicationSnapshot).where(
                            ApplicationSnapshot.application_id == app_id
                        )
                    )
                )
                linked = [s for s in snapshots if 'initial_submission_event_id' in s.data]
                assert len(linked) == 1 and linked[0].id == original['snapshot_id']
            assert (
                await client.get(f'/staff/applications/{app_id}', headers=owner)
            ).status_code == 403

    asyncio.run(check())


def test_legacy_submission_and_missing_snapshot_are_not_guessed():
    now = datetime.now(UTC)
    fields = dict(
        product_code='investment',
        amount=1000000,
        term_months=12,
        purpose='Цель',
        purpose_details=None,
        investment_object='Первоначальный объект',
        collateral_type=None,
        collateral_value=0,
    )
    draft = SimpleNamespace(id=1, version=3, data=fields, created_at=now)
    submitted = SimpleNamespace(id=2, version=4, data={**fields, 'company': {}}, created_at=now)
    reply = SimpleNamespace(
        id=3,
        version=9,
        data={**fields, 'company': {}, 'investment_object': 'Текущий'},
        created_at=now,
    )
    event = SimpleNamespace(
        id=10,
        sequence=2,
        from_status='draft',
        to_status='submitted',
        actor_role='CLIENT',
        created_at=now,
    )
    result = initial_request([draft, submitted, reply], [event])
    assert result.snapshot_id == 2
    assert result.investment_object == 'Первоначальный объект'
    assert result.collateral_value == 0
    assert initial_request([draft, reply], [event]) is None
    assert initial_request([draft, submitted, reply], []) is None
