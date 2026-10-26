# ruff: noqa: F811 -- imported fixture is used as a test parameter
import asyncio
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from test_applications import api, auth  # noqa: F401
from test_staff import manual_application

from app.services.staff_application_list import received_additional_info, work_categories


@pytest.mark.parametrize(
    ('status', 'author', 'decision', 'kind', 'offer_status', 'expected'),
    [
        ('manual_review', 'SYSTEM', 'approve', 'original', 'proposed', ['manual_review']),
        ('approved', 'SYSTEM', 'approve', 'original', 'proposed', []),
        ('approved', 'STAFF', 'approve', 'original', 'proposed', ['staff_approved']),
        ('accepted', 'STAFF', 'approve', 'original', 'accepted', ['staff_approved']),
        ('disbursed_demo', 'STAFF', 'approve', 'original', 'accepted', ['staff_approved']),
        ('closed', 'STAFF', 'approve', 'original', 'declined', ['staff_approved']),
        ('rejected', 'SYSTEM', 'reject', 'original', 'proposed', []),
        ('rejected', 'STAFF', 'reject', 'original', 'proposed', ['staff_rejected']),
        ('counter_offer', 'SYSTEM', 'counter', 'counter', 'proposed', []),
        ('counter_offer', 'STAFF', 'counter', 'counter', 'proposed', ['waiting_offer']),
        ('counter_offer', 'STAFF', 'counter', 'counter', 'declined', []),
        ('accepted', 'STAFF', 'counter', 'counter', 'accepted', ['staff_offer_accepted']),
        ('signed_demo', 'STAFF', 'counter', 'counter', 'accepted', ['staff_offer_accepted']),
        ('disbursed_demo', 'STAFF', 'counter', 'alternative_product', 'accepted',
         ['staff_offer_accepted']),
        ('accepted', 'SYSTEM', 'counter', 'counter', 'accepted', []),
        ('accepted', 'STAFF', 'counter', 'original', 'accepted', []),
        ('closed', 'STAFF', 'counter', 'counter', 'declined', []),
    ],
)
def test_real_authors_and_linked_offers(status, author, decision, kind, offer_status, expected):
    app = SimpleNamespace(status=status)
    record = SimpleNamespace(id=2, created_by=author, decision=decision)
    offer = SimpleNamespace(decision_id=2, created_by=author, kind=kind, status=offer_status)
    assert work_categories(app, record, [offer], []) == expected
    if status != 'manual_review':
        offer.decision_id = 1
        assert work_categories(app, record, [offer], []) == (
            ['staff_rejected'] if expected == ['staff_rejected'] else []
        )


def test_request_author_and_sequential_responses():
    def event(sequence, source, target, role):
        return SimpleNamespace(sequence=sequence, from_status=source, to_status=target,
                               actor_role=role)
    history = [event(1, 'decision', 'needs_info', 'STAFF')]
    app = SimpleNamespace(status='needs_info')
    assert work_categories(app, None, [], history) == ['needs_info']
    history += [event(2, 'needs_info', 'checking', 'CLIENT'),
                event(3, 'decision', 'manual_review', 'SYSTEM')]
    assert received_additional_info('manual_review', history)
    assert not received_additional_info('approved', history)
    history += [event(4, 'manual_review', 'needs_info', 'SYSTEM')]
    assert work_categories(app, None, [], history) == []
    assert not received_additional_info('manual_review', history)
    history += [event(5, 'needs_info', 'checking', 'CLIENT'),
                event(6, 'decision', 'manual_review', 'SYSTEM')]
    assert received_additional_info('manual_review', history)


@pytest.mark.parametrize('decision', ['approve', 'reject', 'counter'])
def test_list_contract_with_real_decision_and_acceptance(api: FastAPI, decision):
    async def check():
        async with AsyncClient(transport=ASGITransport(app=api), base_url='http://testserver') as client:
            app_id, owner = await manual_application(client)
            staff = await auth(client, 'staff1')
            initial = (await client.get('/staff/applications?limit=1', headers=staff)).json()[0]
            assert initial['work_categories'] == ['manual_review']
            assert initial['company']['name']
            assert initial['risk_band'] in ('A', 'B', 'C', 'D')
            assert initial['received_additional_info'] is False
            payload = {'decision': decision, 'comment': 'Синтетическое тестовое решение'}
            if decision == 'counter':
                payload.update(amount=3_000_000, term_months=24)
            response = await client.post(f'/staff/applications/{app_id}/decision',
                                         headers=staff, json=payload)
            assert response.status_code == 200, response.text
            rows = (await client.get('/staff/applications', headers=staff)).json()
            assert rows[0]['work_categories'] == [{
                'approve': 'staff_approved', 'reject': 'staff_rejected', 'counter': 'waiting_offer'
            }[decision]]
            if decision != 'reject':
                detail = (await client.get(f'/staff/applications/{app_id}', headers=staff)).json()
                offer = next(offer for offer in detail['offers'] if offer['status'] == 'proposed')
                accepted = await client.post(f'/offers/{offer["id"]}/accept', headers=owner)
                assert accepted.status_code == 200, accepted.text
                row = (await client.get('/staff/applications', headers=staff)).json()[0]
                assert row['work_categories'] == [
                    'staff_approved' if decision == 'approve' else 'staff_offer_accepted'
                ]
            assert (await client.get('/staff/applications', headers=owner)).status_code == 403
            client_row = (await client.get(f'/applications/{app_id}', headers=owner)).json()
            assert 'work_categories' not in client_row
    asyncio.run(check())


def test_list_received_additions_without_detail_requests(api: FastAPI):
    async def check():
        async with AsyncClient(transport=ASGITransport(app=api), base_url='http://testserver') as client:
            app_id, owner = await manual_application(client)
            staff = await auth(client, 'staff1')
            requested = await client.post(f'/staff/applications/{app_id}/request-info',
                                         headers=staff, json={'fields': ['purpose_details'],
                                                              'comment': 'Тестовый запрос'})
            assert requested.status_code == 200
            row = (await client.get('/staff/applications', headers=staff)).json()[0]
            assert row['work_categories'] == ['needs_info']
            response = await client.post(f'/applications/{app_id}/additional-info', headers=owner,
                                        json={'version': requested.json()['version'],
                                              'purpose_details': 'Уточнённая цель'})
            assert response.status_code == 200, response.text
            row = (await client.get('/staff/applications', headers=staff)).json()[0]
            assert row['work_categories'] == ['manual_review']
            assert row['received_additional_info'] is True
    asyncio.run(check())
