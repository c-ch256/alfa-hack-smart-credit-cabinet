# ruff: noqa: F811 -- imported pytest fixtures are used as parameters
import asyncio
from decimal import Decimal

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session
from test_applications import api, auth, company_id, create  # noqa: F401
from test_staff import manual_application

from app.models import Application, ApplicationSnapshot, AuditEvent, Decision, Offer, Product, Score
from app.services.pricing import RISK_PREMIUM_PP, annual_rate, credit_terms, dscr


def test_client_indicative_rate_uses_settings_without_exposing_risk(api: FastAPI) -> None:
    async def check() -> None:
        async with AsyncClient(
            transport=ASGITransport(app=api), base_url='http://testserver'
        ) as client:
            owner = await auth(client)
            staff = await auth(client, 'staff1')
            assert (await client.get('/products')).status_code == 401
            assert (await client.get('/products', headers=staff)).status_code == 403
            for key_rate in (Decimal('16.00'), Decimal('14.00')):
                api.state.settings.key_rate = key_rate
                response = await client.get('/products', headers=owner)
                assert response.status_code == 200
                for product in response.json():
                    minimum = min(RISK_PREMIUM_PP, key=RISK_PREMIUM_PP.get)
                    assert Decimal(product['indicative_rate_min']) == annual_rate(
                        key_rate, Decimal(product['margin_pp']), minimum
                    )
                    assert (
                        not {'pd', 'risk_band', 'scoring_reasons', 'internal_comment'}
                        & product.keys()
                    )
            assert (await client.get('/staff/products', headers=owner)).status_code == 403

    asyncio.run(check())


def test_original_dscr_without_offer_or_decision_and_explicit_missing_reason(api: FastAPI) -> None:
    async def check() -> None:
        async with AsyncClient(
            transport=ASGITransport(app=api), base_url='http://testserver'
        ) as client:
            app_id, owner = await manual_application(client)
            staff = await auth(client, 'staff1')
            with Session(api.state.db_engine) as session:
                assert not list(
                    session.scalars(select(Offer).where(Offer.application_id == app_id))
                )
                assert not list(
                    session.scalars(select(Decision).where(Decision.application_id == app_id))
                )
            api.state.settings.key_rate = Decimal('16')
            detail = (await client.get(f'/staff/applications/{app_id}', headers=staff)).json()
            original = detail['dscr'][0]
            with Session(api.state.db_engine) as session:
                snapshot = session.get(ApplicationSnapshot, original['snapshot_id'])
                score = session.get(Score, original['score_id'])
                product = session.get(Product, original['product_code'])
                terms = credit_terms(
                    product,
                    original['amount'],
                    original['term_months'],
                    Decimal('16'),
                    score.risk_band,
                )
                assert (
                    snapshot.version == detail['snapshot_version'] == original['snapshot_version']
                )
                assert score.snapshot_id == snapshot.id
                company = snapshot.data['company']
                assert Decimal(original['rate']) == terms.rate
                assert original['monthly_payment'] == terms.monthly_payment
                assert Decimal(original['value']) == dscr(
                    company['avg_monthly_revenue'],
                    company['avg_monthly_expenses'],
                    company['existing_monthly_payments'],
                    terms.monthly_payment,
                )
                assert original['calculation_basis'] == 'current_formula'
                assert original['offer_id'] is None
                assert not list(
                    session.scalars(select(Offer).where(Offer.application_id == app_id))
                )
                assert not list(
                    session.scalars(select(Decision).where(Decision.application_id == app_id))
                )
                # An older score on another snapshot must not be used.
                score.input_snapshot = {**score.input_snapshot, 'amount': original['amount'] + 1}
                session.commit()
            missing = (await client.get(f'/staff/applications/{app_id}', headers=staff)).json()[
                'dscr'
            ][0]
            assert missing['value'] is None and missing['rate'] is None
            assert (
                missing['explanation']
                == 'Нет успешной оценки риска исходных условий для этого снимка'
            )
            assert (
                await client.get(f'/staff/applications/{app_id}', headers=owner)
            ).status_code == 403

    asyncio.run(check())


def test_multiple_info_cycles_capture_old_values_and_exact_links(api: FastAPI) -> None:
    async def check() -> None:
        async with AsyncClient(
            transport=ASGITransport(app=api), base_url='http://testserver'
        ) as client:
            app_id, owner = await manual_application(client)
            staff = await auth(client, 'staff1')
            previous = 'Синтетический тест'
            stored = []
            for after in ('Первое дополнение', 'Второе дополнение'):
                request = await client.post(
                    f'/staff/applications/{app_id}/request-info',
                    headers=staff,
                    json={'fields': ['purpose_details'], 'comment': 'Внутренняя секретная заметка'},
                )
                assert request.status_code == 200
                answered = await client.post(
                    f'/applications/{app_id}/additional-info',
                    headers=owner,
                    json={'version': request.json()['version'], 'purpose_details': after},
                )
                assert answered.status_code == 200 and answered.json()['status'] == 'manual_review'
                detail = (await client.get(f'/staff/applications/{app_id}', headers=staff)).json()
                saved = detail['additional_info_responses'][0]
                assert saved['requested_fields'] == ['purpose_details']
                assert saved['changes'] == [
                    {'field': 'purpose_details', 'before': previous, 'after': after}
                ]
                assert saved['answered_at']
                journal = (
                    await client.get(f'/staff/audit?application_id={app_id}', headers=staff)
                ).json()
                event = next(e for e in journal if e['id'] == saved['response_event_id'])
                assert event['additional_info'] == saved
                requested = next(e for e in journal if e['id'] == saved['request_event_id'])
                assert requested['action'] == 'application.needs_info'
                assert requested['details']['requested_fields'] == saved['requested_fields']
                assert 'Внутренняя секретная' not in str(answered.json())
                assert 'additional_info_responses' not in answered.json()
                stored.insert(0, saved)
                previous = after
            final = (await client.get(f'/staff/applications/{app_id}', headers=staff)).json()
            assert final['additional_info_responses'] == stored
            # Editing current state must not rewrite either saved response.
            with Session(api.state.db_engine) as session:
                session.get(Application, app_id).purpose_details = 'Текущее другое значение'
                session.commit()
            final = (await client.get(f'/staff/applications/{app_id}', headers=staff)).json()
            assert final['additional_info_responses'] == stored
            client_history = (
                await client.get(f'/applications/{app_id}/history', headers=owner)
            ).json()
            assert 'Внутренняя секретная' not in str(client_history)
            assert 'additional_info' not in str(client_history)
            assert (
                await client.get(f'/staff/audit?application_id={app_id}', headers=owner)
            ).status_code == 403

    asyncio.run(check())


def test_system_request_cycles_and_legacy_events_are_not_reconstructed(api: FastAPI) -> None:
    async def check() -> None:
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
                    'amount': 5000000,
                    'term_months': 24,
                    'purpose': 'equipment_purchase',
                },
            )
            app_id = created.json()['id']
            current = (await client.post(f'/applications/{app_id}/submit', headers=owner)).json()
            assert current['status'] == 'needs_info'
            first = await client.post(
                f'/applications/{app_id}/additional-info',
                headers=owner,
                json={'version': current['version'], 'investment_object': 'Станок'},
            )
            assert first.status_code == 200 and first.json()['status'] == 'needs_info'
            second = await client.post(
                f'/applications/{app_id}/additional-info',
                headers=owner,
                json={
                    'version': first.json()['version'],
                    'collateral_type': 'Оборудование',
                    'collateral_value': 6000000,
                },
            )
            assert second.status_code == 200
            staff = await auth(client, 'staff1')
            detail = (await client.get(f'/staff/applications/{app_id}', headers=staff)).json()
            responses = detail['additional_info_responses']
            assert len(responses) == 2
            assert set(responses[1]['requested_fields']) == {
                'investment_object',
                'collateral_type',
                'collateral_value',
            }
            assert set(responses[0]['requested_fields']) == {'collateral_type', 'collateral_value'}
            assert responses[0]['request_event_id'] != responses[1]['request_event_id']
            assert all(change['before'] is None for change in responses[0]['changes'])
            with Session(api.state.db_engine) as session:
                event = session.get(AuditEvent, responses[1]['response_event_id'])
                event.details = {k: v for k, v in event.details.items() if k != 'additional_info'}
                session.commit()
            legacy = (await client.get(f'/staff/applications/{app_id}', headers=staff)).json()
            assert legacy['additional_info_responses'][0] == responses[0]
            old = legacy['additional_info_responses'][1]
            assert old['response_event_id'] == responses[1]['response_event_id']
            assert old['answered_at'] == responses[1]['answered_at']
            assert old['changes'] is None
            assert old['requested_fields'] is None

    asyncio.run(check())


def test_additional_info_failure_rolls_back_fields_and_history(api: FastAPI, monkeypatch) -> None:
    async def check() -> None:
        async with AsyncClient(
            transport=ASGITransport(app=api), base_url='http://testserver'
        ) as client:
            app_id, owner = await manual_application(client)
            staff = await auth(client, 'staff1')
            request = (
                await client.post(
                    f'/staff/applications/{app_id}/request-info',
                    headers=staff,
                    json={'fields': ['purpose_details'], 'comment': 'Внутренний комментарий'},
                )
            ).json()
            with Session(api.state.db_engine) as session:
                before = session.get(Application, app_id).purpose_details
                count = session.scalar(select(func.count()).select_from(AuditEvent))

            def fail(*args, **kwargs):
                raise RuntimeError('Synthetic processing failure')

            monkeypatch.setattr('app.services.workflow.process_application', fail)
            async with AsyncClient(
                transport=ASGITransport(app=api, raise_app_exceptions=False),
                base_url='http://testserver',
            ) as failed_client:
                result = await failed_client.post(
                    f'/applications/{app_id}/additional-info',
                    headers=owner,
                    json={
                        'version': request['version'],
                        'purpose_details': 'Не должно сохраниться',
                    },
                )
                assert result.status_code == 500
            with Session(api.state.db_engine) as session:
                application = session.get(Application, app_id)
                assert application.status == 'needs_info'
                assert application.version == request['version']
                assert application.purpose_details == before
                assert session.scalar(select(func.count()).select_from(AuditEvent)) == count

    asyncio.run(check())


def test_current_dscr_does_not_reprice_saved_offer(api: FastAPI) -> None:
    async def check() -> None:
        async with AsyncClient(
            transport=ASGITransport(app=api), base_url='http://testserver'
        ) as client:
            owner = await auth(client)
            company = await company_id(client, owner)
            app_id = (await create(client, owner, company)).json()['id']
            assert (await client.post(f'/applications/{app_id}/submit', headers=owner)).json()[
                'status'
            ] == 'approved'
            staff = await auth(client, 'staff1')
            first = (await client.get(f'/staff/applications/{app_id}', headers=staff)).json()
            saved_offer = first['offers'][0]
            api.state.settings.key_rate += Decimal('2')
            second = (await client.get(f'/staff/applications/{app_id}', headers=staff)).json()
            assert second['offers'] == first['offers']
            assert second['decisions'] == first['decisions']
            original = next(
                item for item in second['dscr'] if item['scope'] == 'original_application'
            )
            historical = next(
                item for item in second['dscr'] if item['offer_id'] == saved_offer['id']
            )
            assert Decimal(original['rate']) == Decimal(first['dscr'][0]['rate']) + 2
            assert historical['rate'] == saved_offer['rate']
            assert historical['monthly_payment'] == saved_offer['monthly_payment']
            assert historical['calculation_basis'] == 'saved_offer'

    asyncio.run(check())


def test_legacy_request_without_id_does_not_get_guessed_link(api: FastAPI) -> None:
    async def check() -> None:
        async with AsyncClient(
            transport=ASGITransport(app=api), base_url='http://testserver'
        ) as client:
            app_id, owner = await manual_application(client)
            staff = await auth(client, 'staff1')
            requested = (
                await client.post(
                    f'/staff/applications/{app_id}/request-info',
                    headers=staff,
                    json={'fields': ['purpose_details'], 'comment': 'Внутренний текст'},
                )
            ).json()
            with Session(api.state.db_engine) as session:
                for event in session.scalars(
                    select(AuditEvent).where(
                        AuditEvent.application_id == app_id,
                        AuditEvent.action.in_(['application.needs_info', 'staff.request_info']),
                    )
                ):
                    event.details = {'status': 'needs_info'}
                session.commit()
            response = await client.post(
                f'/applications/{app_id}/additional-info',
                headers=owner,
                json={'version': requested['version'], 'purpose_details': 'Сохранённое изменение'},
            )
            assert response.status_code == 200
            saved = (await client.get(f'/staff/applications/{app_id}', headers=staff)).json()[
                'additional_info_responses'
            ][0]
            assert saved['request_event_id'] is None
            assert saved['requested_fields'] is None
            assert saved['changes'][0]['after'] == 'Сохранённое изменение'

    asyncio.run(check())
