# ruff: noqa: F811 -- импорт pytest fixture используется как параметр тестов

"""Интеграция доверенной модели с существующим workflow и API."""

import asyncio
from pathlib import Path
from time import perf_counter

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.orm import Session
from test_applications import api, auth, company_id, create  # noqa: F401

from app.config import Settings
from app.main import create_app
from app.models import Decision, Score, StatusEvent
from app.scoring.model import ModelScorer, ModelScorerError
from app.scoring.selector import ScorerUnavailableError, select_scorer

PASSWORD = 'synthetic-demo-password-only'
JWT_SECRET = 'synthetic-test-jwt-secret-with-at-least-32-bytes'
REPO_ROOT = Path(__file__).resolve().parents[2]


def _keys(value):
    if isinstance(value, dict):
        for key, nested in value.items():
            yield key
            yield from _keys(nested)
    elif isinstance(value, list):
        for nested in value:
            yield from _keys(nested)


@pytest.fixture
def model_api(api: FastAPI, monkeypatch) -> FastAPI:
    monkeypatch.syspath_prepend(str(REPO_ROOT))
    pytest.importorskip('sklearn', reason='ML runtime is not installed')
    settings = Settings(env='test', jwt_secret=JWT_SECRET, demo_password=PASSWORD, scorer='model')
    return create_app(settings, db_engine=api.state.db_engine)


def test_scorer_selection_and_load_once(api: FastAPI, monkeypatch) -> None:
    assert api.state.settings.scorer == 'mock'
    assert api.state.workflow_scorer is None
    pytest.importorskip('sklearn')
    monkeypatch.syspath_prepend(str(REPO_ROOT))
    calls = []
    original = ModelScorer.__init__

    def counted(self, model_dir=None):
        calls.append(model_dir)
        original(self, model_dir)

    monkeypatch.setattr(ModelScorer, '__init__', counted)
    configured = create_app(Settings(scorer='model'), db_engine=api.state.db_engine)
    assert len(calls) == 1
    assert configured.state.workflow_scorer.source == 'model'
    assert configured.state.workflow_scorer.is_test_result is True


def test_unloadable_model_fails_startup(api: FastAPI, tmp_path) -> None:
    with pytest.raises(ModelScorerError) as error:
        create_app(Settings(scorer='model', model_dir=tmp_path), db_engine=api.state.db_engine)
    assert error.value.code == 'MODEL_ARTIFACT_UNAVAILABLE'


def test_missing_configured_model_never_falls_back_to_mock() -> None:
    with pytest.raises(ScorerUnavailableError):
        select_scorer(Settings(scorer='model'), None, {})


@pytest.mark.parametrize(
    'login,fields,expected',
    [
        ('client1', {}, 'approved'),
        ('client2', {'amount': 5_000_000, 'term_months': 12}, 'counter_offer'),
        ('client3', {'amount': 4_000_000}, 'manual_review'),
        ('client4', {}, 'rejected'),
    ],
)
def test_demo_profiles_model_workflow(model_api: FastAPI, login, fields, expected) -> None:
    async def run():
        async with AsyncClient(
            transport=ASGITransport(app=model_api), base_url='http://testserver'
        ) as client:
            owner = await auth(client, login)
            company = await company_id(client, owner)
            created = await create(client, owner, company, fields=fields)
            app_id = created.json()['id']
            start = perf_counter()
            response = await client.post(f'/applications/{app_id}/submit', headers=owner)
            elapsed = perf_counter() - start
            assert response.status_code == 200, response.text
            assert set(_keys(response.json())).isdisjoint(
                {'pd', 'log_odds', 'model_version', 'reasons', 'total_debt', 'tax_debt'}
            )
            assert (
                await client.post(f'/applications/{app_id}/submit', headers=owner)
            ).status_code == 409
            history = (await client.get(f'/applications/{app_id}/history', headers=owner)).json()
            assert history[0]['to_status'] == 'draft'
            assert history[-1]['to_status'] == response.json()['status']
            return app_id, response.json()['status'], elapsed

    app_id, status, elapsed = asyncio.run(run())
    assert status == expected
    with Session(model_api.state.db_engine) as session:
        scores = list(session.scalars(select(Score).where(Score.application_id == app_id)))
        decisions = list(session.scalars(select(Decision).where(Decision.application_id == app_id)))
        events = list(
            session.scalars(select(StatusEvent).where(StatusEvent.application_id == app_id))
        )
        assert len(events) >= 4
        if login == 'client4':
            assert status == 'rejected' and not scores and len(decisions) == 1
        else:
            assert scores and all(row.source == 'model' and row.is_test_result for row in scores)
            assert all(
                row.model_version == model_api.state.workflow_scorer.model_version for row in scores
            )
            if login == 'client2':
                assert len(scores) > 1
                assert len(
                    {
                        (row.input_snapshot['amount'], row.input_snapshot['term_months'])
                        for row in scores
                    }
                ) == len(scores)
    assert elapsed < 60  # широкий предел: регрессия зависания, не целевой SLA


def test_staff_uses_selected_model(model_api: FastAPI) -> None:
    async def run():
        async with AsyncClient(
            transport=ASGITransport(app=model_api), base_url='http://testserver'
        ) as client:
            owner = await auth(client, 'client3')
            company = await company_id(client, owner)
            created = await create(client, owner, company, fields={'amount': 4_000_000})
            app_id = created.json()['id']
            submitted = await client.post(f'/applications/{app_id}/submit', headers=owner)
            assert submitted.json()['status'] == 'manual_review'
            staff = await auth(client, 'staff1')
            result = await client.post(
                f'/staff/applications/{app_id}/decision',
                headers=staff,
                json={'decision': 'approve', 'comment': 'Синтетическое ручное решение'},
            )
            assert result.status_code == 200, result.text
            detail = (await client.get(f'/staff/applications/{app_id}', headers=staff)).json()
            assert detail['score']['source'] == 'model'
            assert detail['score']['is_test_result'] is True
            assert detail['score']['pd'] is not None
            assert 'pd' not in set(_keys(result.json()))
            return app_id

    app_id = asyncio.run(run())
    with Session(model_api.state.db_engine) as session:
        assert all(
            score.source == 'model'
            for score in session.scalars(select(Score).where(Score.application_id == app_id))
        )


def test_model_inference_error_keeps_scoring_without_decision(
    model_api: FastAPI, monkeypatch
) -> None:
    def fail(_request):
        raise ModelScorerError('MODEL_INFERENCE_FAILED')

    monkeypatch.setattr(model_api.state.workflow_scorer, 'score', fail)

    async def run():
        async with AsyncClient(
            transport=ASGITransport(app=model_api), base_url='http://testserver'
        ) as client:
            owner = await auth(client)
            company = await company_id(client, owner)
            created = await create(client, owner, company)
            app_id = created.json()['id']
            response = await client.post(f'/applications/{app_id}/submit', headers=owner)
            assert response.status_code == 200
            assert response.json()['status'] == 'scoring'
            assert 'MODEL_INFERENCE_FAILED' not in response.text
            return app_id

    app_id = asyncio.run(run())
    with Session(model_api.state.db_engine) as session:
        assert session.scalar(select(Decision).where(Decision.application_id == app_id)) is None
        score = session.scalar(select(Score).where(Score.application_id == app_id))
        assert score.source == 'model' and score.result_status == 'error'


def test_staff_model_error_has_no_false_decision(model_api: FastAPI, monkeypatch) -> None:
    async def run():
        async with AsyncClient(
            transport=ASGITransport(app=model_api), base_url='http://testserver'
        ) as client:
            owner = await auth(client, 'client3')
            company = await company_id(client, owner)
            created = await create(client, owner, company, fields={'amount': 4_000_000})
            app_id = created.json()['id']
            submitted = await client.post(f'/applications/{app_id}/submit', headers=owner)
            assert submitted.json()['status'] == 'manual_review'

            def fail(_request):
                raise ModelScorerError('MODEL_INFERENCE_FAILED')

            monkeypatch.setattr(model_api.state.workflow_scorer, 'score', fail)
            staff = await auth(client, 'staff1')
            response = await client.post(
                f'/staff/applications/{app_id}/decision',
                headers=staff,
                json={'decision': 'approve', 'comment': 'Синтетический тест'},
            )
            assert response.status_code == 503
            assert 'MODEL_INFERENCE_FAILED' not in response.text
            return app_id

    app_id = asyncio.run(run())
    with Session(model_api.state.db_engine) as session:
        assert session.scalar(select(Decision).where(Decision.application_id == app_id)) is None
