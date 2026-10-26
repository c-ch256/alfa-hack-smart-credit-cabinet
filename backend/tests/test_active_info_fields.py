# ruff: noqa: F811 -- imported fixture is used as a test parameter
import asyncio

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from test_applications import api, auth  # noqa: F401
from test_staff import manual_application


def test_one_multiple_and_repeated_requests_are_partial_and_linked(api: FastAPI):
    async def check():
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
                    'purpose_details': 'Исходная цель',
                    'investment_object': 'Станок',
                },
            )
            staff = await auth(client, 'staff1')
            previous_request_id = None
            previous = (await client.get(f'/applications/{app_id}', headers=owner)).json()
            assert previous['active_info_request'] is None
            for fields, values in (
                (['purpose_details'], {'purpose_details': 'Уточнённая цель'}),
                (
                    ['investment_object', 'collateral_type'],
                    {'investment_object': 'Новый станок', 'collateral_type': 'Оборудование'},
                ),
                (['purpose_details'], {'purpose_details': 'Повторное уточнение цели'}),
            ):
                request = await client.post(
                    f'/staff/applications/{app_id}/request-info',
                    headers=staff,
                    json={'fields': fields, 'comment': 'Только для STAFF'},
                )
                assert request.status_code == 200, request.text
                current = (await client.get(f'/applications/{app_id}', headers=owner)).json()
                active = current['active_info_request']
                assert active['fields'] == fields
                assert 'Только для STAFF' not in str(current)
                # A correct version must not permit replying to an earlier request.
                if previous_request_id is not None:
                    stale = await client.post(
                        f'/applications/{app_id}/additional-info',
                        headers=owner,
                        json={
                            'version': current['version'],
                            'request_event_id': previous_request_id,
                            **values,
                        },
                    )
                    assert stale.status_code == 409
                if fields == ['purpose_details']:
                    extra = await client.post(
                        f'/applications/{app_id}/additional-info',
                        headers=owner,
                        json={
                            'version': current['version'],
                            'request_event_id': active['request_event_id'],
                            **values,
                            'investment_object': 'Лишнее поле',
                        },
                    )
                    assert extra.status_code == 422
                answered = await client.post(
                    f'/applications/{app_id}/additional-info',
                    headers=owner,
                    json={
                        'version': current['version'],
                        'request_event_id': active['request_event_id'],
                        **values,
                    },
                )
                assert answered.status_code == 200, answered.text
                next_app = answered.json()
                for field in (
                    'purpose_details',
                    'investment_object',
                    'collateral_type',
                    'collateral_value',
                ):
                    assert next_app[field] == values.get(field, previous[field])
                detail = (await client.get(f'/staff/applications/{app_id}', headers=staff)).json()
                reply = detail['additional_info_responses'][0]
                assert reply['request_event_id'] == active['request_event_id']
                assert reply['requested_fields'] == fields
                assert {c['field']: c['before'] for c in reply['changes']} == {
                    field: previous[field] for field in values
                }
                assert {c['field']: c['after'] for c in reply['changes']} == values
                assert next_app['status'] == 'manual_review'
                row = next(
                    row
                    for row in (await client.get('/staff/applications', headers=staff)).json()
                    if row['id'] == app_id
                )
                assert row['received_additional_info'] is True
                authors = [
                    e['actor_display_name'] for e in detail['history'] if e['actor_role'] == 'STAFF'
                ]
                assert authors and all(name == 'Демо-аналитик' for name in authors)
                client_history = (
                    await client.get(f'/applications/{app_id}/history', headers=owner)
                ).json()
                assert 'actor_display_name' not in str(client_history)
                previous_request_id, previous = active['request_event_id'], next_app
            assert len(detail['additional_info_responses']) == 3
            assert (await client.get(f'/applications/{app_id}', headers=staff)).status_code == 403
            stranger = await auth(client, 'client1')
            assert (
                await client.get(f'/applications/{app_id}', headers=stranger)
            ).status_code == 404

    asyncio.run(check())
