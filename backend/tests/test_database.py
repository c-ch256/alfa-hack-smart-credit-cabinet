import asyncio

import pytest
from httpx import ASGITransport, AsyncClient
from pydantic import ValidationError
from sqlalchemy import create_engine

from app.config import Settings
from app.db import database_url
from app.main import create_app


def test_database_settings_and_secret_masking(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv('DB_HOST', 'db.example.test')
    monkeypatch.setenv('DB_PORT', '5433')
    monkeypatch.setenv('DB_PASSWORD', 'synthetic-secret-value')
    settings = Settings()

    url = database_url(settings)
    assert url.drivername == 'postgresql+psycopg'
    assert url.host == 'db.example.test'
    assert url.port == 5433
    assert url.password == 'synthetic-secret-value'
    assert 'synthetic-secret-value' not in repr(settings)
    assert 'synthetic-secret-value' not in str(url)


def test_explicit_database_url_takes_precedence(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv('DATABASE_URL', 'postgresql+psycopg://user:demo%40pass@db.example/testdb')
    monkeypatch.setenv('DB_PASSWORD', 'ignored-synthetic-password')

    url = database_url(Settings())
    assert url.username == 'user'
    assert url.password == 'demo@pass'
    assert url.database == 'testdb'


def test_invalid_database_configuration_fails_without_secret() -> None:
    with pytest.raises(ValueError, match='PostgreSQL psycopg URL'):
        database_url(Settings(database_url='sqlite:///local.db'))
    with pytest.raises(ValidationError, match='database credentials'):
        Settings(env='production', docs_enabled=False, allowed_hosts=['api.example.test'])


def test_invalid_database_port_from_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv('DB_PORT', '70000')
    with pytest.raises(ValidationError):
        Settings()


@pytest.mark.parametrize('available', [True, False])
def test_readiness_reports_database_connectivity(tmp_path, available: bool) -> None:
    if available:
        engine = create_engine('sqlite+pysqlite:///:memory:')
    else:
        engine = create_engine(f'sqlite+pysqlite:///{tmp_path / "missing" / "db.sqlite"}')

    async def check() -> None:
        async with AsyncClient(
            transport=ASGITransport(app=create_app(db_engine=engine)),
            base_url='http://testserver',
        ) as client:
            response = await client.get('/ready')
            assert response.status_code == (200 if available else 503)
            assert response.json() == {'status': 'ok' if available else 'unavailable'}
            assert (await client.get('/health')).json() == {'status': 'ok'}

    try:
        asyncio.run(check())
    finally:
        engine.dispose()
