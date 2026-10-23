import asyncio
from datetime import UTC, datetime, timedelta
from typing import Annotated

import jwt
import pytest
from fastapi import Depends, FastAPI
from httpx import ASGITransport, AsyncClient
from pydantic import ValidationError
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.config import Settings
from app.db import Base
from app.deps import require_role
from app.main import create_app
from app.models import User
from app.security import LoginRateLimiter, verify_password
from app.seed import seed_demo_users

TEST_JWT_SECRET = 'synthetic-test-jwt-secret-with-at-least-32-bytes'
TEST_DEMO_PASSWORD = 'synthetic-demo-password-only'


@pytest.fixture
def auth_app() -> tuple[FastAPI, Settings]:
    engine = create_engine(
        'sqlite+pysqlite:///:memory:',
        connect_args={'check_same_thread': False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    settings = Settings(env='test', jwt_secret=TEST_JWT_SECRET, demo_password=TEST_DEMO_PASSWORD)
    assert seed_demo_users(engine, settings) == 5
    try:
        yield create_app(settings, db_engine=engine), settings
    finally:
        engine.dispose()


@pytest.mark.parametrize(
    ('login', 'role'),
    [('client1', 'CLIENT'), ('staff1', 'STAFF')],
)
def test_login_and_me(auth_app: tuple[FastAPI, Settings], login: str, role: str) -> None:
    app, settings = auth_app

    async def check() -> None:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url='http://testserver'
        ) as client:
            response = await client.post(
                '/auth/login', json={'login': login, 'password': TEST_DEMO_PASSWORD}
            )
            assert response.status_code == 200
            assert response.headers['cache-control'] == 'no-store'
            body = response.json()
            assert body['token_type'] == 'bearer'
            assert body['expires_in'] == settings.jwt_ttl_minutes * 60
            token = body['access_token']
            claims = jwt.decode(
                token,
                TEST_JWT_SECRET,
                algorithms=['HS256'],
                issuer=settings.jwt_issuer,
                audience=settings.jwt_audience,
            )
            assert claims['sub'].isdecimal()
            assert 'role' not in claims
            me = await client.get('/auth/me', headers={'Authorization': f'Bearer {token}'})
            assert me.status_code == 200
            assert me.headers['cache-control'] == 'no-store'
            assert me.json()['login'] == login
            assert me.json()['role'] == role
            assert 'password_hash' not in me.json()
            assert TEST_DEMO_PASSWORD not in me.text
            assert (await client.get('/health')).json() == {'status': 'ok'}
            assert (await client.get('/ready')).status_code == 200
            assert (await client.get('/docs')).status_code == 200

    asyncio.run(check())


def test_invalid_credentials_do_not_reveal_login(auth_app: tuple[FastAPI, Settings]) -> None:
    app, _settings = auth_app

    async def check() -> None:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url='http://testserver'
        ) as client:
            known = await client.post(
                '/auth/login', json={'login': 'client1', 'password': 'wrong-synthetic-password'}
            )
            unknown = await client.post(
                '/auth/login', json={'login': 'missing', 'password': 'wrong-synthetic-password'}
            )
            assert known.status_code == unknown.status_code == 401
            assert (
                known.json()
                == unknown.json()
                == {
                    'detail': 'Неверный логин или пароль',
                    'code': 'INVALID_CREDENTIALS',
                }
            )
            assert 'access_token' not in known.text
            assert (await client.get('/auth/me')).status_code == 401
            assert (
                await client.get('/auth/me', headers={'Authorization': 'Basic synthetic'})
            ).status_code == 401

    asyncio.run(check())


def test_invalid_login_body_does_not_echo_password(auth_app: tuple[FastAPI, Settings]) -> None:
    app, _settings = auth_app
    sentinel = 'SYNTHETIC_SECRET_MARKER'

    async def check() -> None:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url='http://testserver'
        ) as client:
            response = await client.post(
                '/auth/login',
                json={'login': 'client1', 'password': sentinel + 'x' * 1100, 'role': 'STAFF'},
            )
            assert response.status_code == 422
            assert response.json()['code'] == 'VALIDATION_ERROR'
            assert sentinel not in response.text

    asyncio.run(check())


def test_database_failure_returns_generic_auth_error(
    auth_app: tuple[FastAPI, Settings], tmp_path
) -> None:
    app, _settings = auth_app
    unavailable = create_engine(f'sqlite+pysqlite:///{tmp_path / "missing" / "auth.sqlite"}')
    app.state.db_engine = unavailable

    async def check() -> None:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url='http://testserver'
        ) as client:
            response = await client.post(
                '/auth/login', json={'login': 'client1', 'password': TEST_DEMO_PASSWORD}
            )
            assert response.status_code == 503
            assert response.json() == {
                'detail': 'Авторизация недоступна',
                'code': 'AUTH_UNAVAILABLE',
            }
            assert TEST_DEMO_PASSWORD not in response.text
            assert str(tmp_path) not in response.text

    try:
        asyncio.run(check())
    finally:
        unavailable.dispose()


def test_jwt_signature_claims_and_active_user(auth_app: tuple[FastAPI, Settings]) -> None:
    app, settings = auth_app
    with Session(app.state.db_engine) as session:
        user = session.scalar(select(User).where(User.login == 'client1'))
        assert user is not None
        user_id = user.id

    now = datetime.now(UTC)
    valid_claims = {
        'sub': str(user_id),
        'iss': settings.jwt_issuer,
        'aud': settings.jwt_audience,
        'iat': now,
        'nbf': now,
        'exp': now + timedelta(minutes=5),
        'jti': 'synthetic-token-id',
    }
    variants = [
        jwt.encode(valid_claims, 'wrong-synthetic-jwt-key-at-least-32-bytes', algorithm='HS256'),
        jwt.encode(valid_claims | {'iss': 'other'}, TEST_JWT_SECRET, algorithm='HS256'),
        jwt.encode(valid_claims | {'aud': 'other'}, TEST_JWT_SECRET, algorithm='HS256'),
        jwt.encode(
            valid_claims | {'exp': now - timedelta(seconds=1)}, TEST_JWT_SECRET, algorithm='HS256'
        ),
        jwt.encode(
            valid_claims | {'nbf': now + timedelta(minutes=5)}, TEST_JWT_SECRET, algorithm='HS256'
        ),
        jwt.encode(valid_claims | {'sub': '999999'}, TEST_JWT_SECRET, algorithm='HS256'),
        jwt.encode(valid_claims, key='', algorithm='none'),
        jwt.encode(
            {key: value for key, value in valid_claims.items() if key != 'exp'},
            TEST_JWT_SECRET,
            algorithm='HS256',
        ),
    ]

    async def check() -> None:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url='http://testserver'
        ) as client:
            for token in variants:
                response = await client.get(
                    '/auth/me', headers={'Authorization': f'Bearer {token}'}
                )
                assert response.status_code == 401
                assert response.json()['code'] == 'UNAUTHORIZED'
            valid_token = jwt.encode(valid_claims, TEST_JWT_SECRET, algorithm='HS256')
            assert (
                await client.get('/auth/me', headers={'Authorization': f'Bearer {valid_token}'})
            ).status_code == 200
            with Session(app.state.db_engine) as session:
                user = session.get(User, user_id)
                assert user is not None
                user.is_active = False
                session.commit()
            assert (
                await client.get('/auth/me', headers={'Authorization': f'Bearer {valid_token}'})
            ).status_code == 401

    asyncio.run(check())


def test_roles_come_from_database_not_jwt(auth_app: tuple[FastAPI, Settings]) -> None:
    app, settings = auth_app

    async def staff_only(user: Annotated[User, Depends(require_role('STAFF'))]) -> dict[str, str]:
        return {'login': user.login}

    async def client_only(user: Annotated[User, Depends(require_role('CLIENT'))]) -> dict[str, str]:
        return {'login': user.login}

    app.add_api_route('/test/staff-only', staff_only, methods=['GET'])
    app.add_api_route('/test/client-only', client_only, methods=['GET'])

    async def check() -> None:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url='http://testserver'
        ) as client:
            assert (await client.get('/test/staff-only')).status_code == 401
            client_token = (
                await client.post(
                    '/auth/login', json={'login': 'client1', 'password': TEST_DEMO_PASSWORD}
                )
            ).json()['access_token']
            staff_token = (
                await client.post(
                    '/auth/login', json={'login': 'staff1', 'password': TEST_DEMO_PASSWORD}
                )
            ).json()['access_token']
            client_claims = jwt.decode(
                client_token,
                TEST_JWT_SECRET,
                algorithms=['HS256'],
                issuer=settings.jwt_issuer,
                audience=settings.jwt_audience,
            )
            claimed_staff = jwt.encode(
                client_claims | {'role': 'STAFF'}, TEST_JWT_SECRET, algorithm='HS256'
            )
            for token in (client_token, claimed_staff):
                response = await client.get(
                    '/test/staff-only', headers={'Authorization': f'Bearer {token}'}
                )
                assert response.status_code == 403
                assert response.json()['code'] == 'FORBIDDEN'
            response = await client.get(
                '/test/staff-only', headers={'Authorization': f'Bearer {staff_token}'}
            )
            assert response.status_code == 200
            assert response.json() == {'login': 'staff1'}
            assert (
                await client.get(
                    '/test/client-only', headers={'Authorization': f'Bearer {client_token}'}
                )
            ).status_code == 200
            assert (
                await client.get(
                    '/test/client-only', headers={'Authorization': f'Bearer {staff_token}'}
                )
            ).status_code == 403

    asyncio.run(check())


def test_login_rate_limit_and_window(auth_app: tuple[FastAPI, Settings]) -> None:
    app, _settings = auth_app

    async def check() -> None:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url='http://testserver'
        ) as client:
            for _ in range(5):
                response = await client.post(
                    '/auth/login', json={'login': 'missing', 'password': 'synthetic-wrong-password'}
                )
                assert response.status_code == 401
            blocked = await client.post(
                '/auth/login', json={'login': 'missing', 'password': 'synthetic-wrong-password'}
            )
            assert blocked.status_code == 429
            assert blocked.json()['code'] == 'TOO_MANY_ATTEMPTS'
            assert blocked.headers['retry-after'] == '60'

    asyncio.run(check())
    now = [100.0]
    limiter = LoginRateLimiter(clock=lambda: now[0])
    for _ in range(5):
        assert limiter.allow('client1', '127.0.0.1')
    assert not limiter.allow('client1', '127.0.0.2')
    now[0] += 61
    assert limiter.allow('client1', '127.0.0.2')
    for index in range(30):
        assert limiter.allow(f'other-{index}', '127.0.0.3')
    assert not limiter.allow('other-last', '127.0.0.3')


def test_demo_seed_is_idempotent_and_hashes_password(auth_app: tuple[FastAPI, Settings]) -> None:
    app, settings = auth_app
    with Session(app.state.db_engine) as session:
        first_hashes = {user.login: user.password_hash for user in session.scalars(select(User))}
    assert set(first_hashes) == {'client1', 'client2', 'client3', 'client4', 'staff1'}
    assert all(value.startswith('$argon2id$') for value in first_hashes.values())
    assert len(set(first_hashes.values())) == 5
    assert all(verify_password(TEST_DEMO_PASSWORD, value) for value in first_hashes.values())
    assert seed_demo_users(app.state.db_engine, settings) == 0
    with Session(app.state.db_engine) as session:
        assert {
            user.login: user.password_hash for user in session.scalars(select(User))
        } == first_hashes


def test_demo_seed_rejects_existing_role_conflict(auth_app: tuple[FastAPI, Settings]) -> None:
    app, settings = auth_app
    with Session(app.state.db_engine) as session:
        staff = session.scalar(select(User).where(User.login == 'staff1'))
        assert staff is not None
        staff.role = 'CLIENT'
        session.commit()
    with pytest.raises(ValueError, match='demo account conflicts'):
        seed_demo_users(app.state.db_engine, settings)


def test_auth_configuration_requires_strong_secrets() -> None:
    marker = 'synthetic-sensitive-marker'
    with pytest.raises(ValidationError, match='JWT_SECRET'):
        Settings(jwt_secret='short')
    with pytest.raises(ValidationError, match='DEMO_PASSWORD'):
        Settings(demo_password='short')
    with pytest.raises(ValidationError):
        Settings(jwt_ttl_minutes=481)
    with pytest.raises(ValidationError, match='JWT_SECRET'):
        Settings(
            env='production',
            docs_enabled=False,
            allowed_hosts=['api.example.test'],
            db_password=marker,
        )
    with pytest.raises(ValidationError) as captured:
        Settings(jwt_secret=marker)
    assert marker not in str(captured.value)
