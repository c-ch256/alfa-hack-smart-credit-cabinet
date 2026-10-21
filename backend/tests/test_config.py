import asyncio

import pytest
from httpx import ASGITransport, AsyncClient
from pydantic import ValidationError

from app.config import Settings
from app.main import create_app


def test_local_defaults_are_restricted(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in (
        'APP_ENV',
        'APP_DEBUG',
        'APP_DOCS_ENABLED',
        'APP_ALLOWED_HOSTS',
        'APP_CORS_ORIGINS',
    ):
        monkeypatch.delenv(name, raising=False)

    settings = Settings()
    assert settings.env == 'local'
    assert settings.debug is False
    assert settings.cors_origins == []
    assert '*' not in settings.allowed_hosts


@pytest.mark.parametrize(
    'overrides',
    [
        {},
        {'debug': True, 'docs_enabled': False, 'allowed_hosts': ['api.example.test']},
        {'docs_enabled': False, 'allowed_hosts': ['*']},
        {'docs_enabled': False, 'allowed_hosts': ['*.example.test']},
        {
            'docs_enabled': False,
            'allowed_hosts': ['api.example.test'],
            'cors_origins': ['http://web.example.test'],
        },
        {
            'docs_enabled': False,
            'allowed_hosts': ['api.example.test'],
            'cors_origins': ['https://*.example.test'],
        },
    ],
)
def test_unsafe_production_settings_fail(overrides: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        Settings(env='production', **overrides)


def test_production_disables_docs_and_rejects_other_origins() -> None:
    settings = Settings(
        env='production',
        docs_enabled=False,
        allowed_hosts=['api.example.test'],
        cors_origins=['https://web.example.test'],
        db_password='synthetic-test-password',
    )

    async def check() -> None:
        async with AsyncClient(
            transport=ASGITransport(app=create_app(settings)), base_url='https://api.example.test'
        ) as client:
            assert (await client.get('/health')).json() == {'status': 'ok'}
            assert (await client.get('/docs')).status_code == 404
            assert (await client.get('/openapi.json')).status_code == 404
            response = await client.get('/health', headers={'Origin': 'https://other.example.test'})
            assert 'access-control-allow-origin' not in response.headers

    asyncio.run(check())
