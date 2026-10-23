import asyncio

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.config import Settings
from app.db import Base
from app.main import create_app
from app.models import Application, Company, User
from app.reset_demo_passwords import main, reset_demo_passwords
from app.security import hash_password, verify_password
from app.seed import DEMO_USERS, seed_demo_catalog, seed_demo_users

OLD_PASSWORD = 'synthetic-old-password-only'
NEW_PASSWORD = 'synthetic-new-password-only'
JWT_SECRET = 'synthetic-test-jwt-secret-with-at-least-32-bytes'


@pytest.fixture
def engine(tmp_path):
    db_engine = create_engine(f'sqlite+pysqlite:///{tmp_path / "reset-demo.sqlite"}')
    Base.metadata.create_all(db_engine)
    seed_demo_users(db_engine, Settings(env='test', demo_password=OLD_PASSWORD))
    seed_demo_catalog(db_engine)
    try:
        yield db_engine
    finally:
        db_engine.dispose()


def test_reset_changes_only_existing_demo_hashes_and_login(engine) -> None:
    with Session(engine) as session:
        client = session.scalar(select(User).where(User.login == 'client1'))
        company = session.scalar(select(Company).where(Company.owner_user_id == client.id))
        outsider = User(
            login='other-client',
            role='CLIENT',
            full_name='Другой синтетический клиент',
            password_hash=hash_password(OLD_PASSWORD),
        )
        session.add(outsider)
        session.flush()
        application = Application(
            number='SYN-RESET-001',
            company_id=company.id,
            created_by_user_id=client.id,
            product_code='working_capital',
            amount=3_000_000,
            term_months=24,
            purpose='Закупка',
            version=1,
        )
        session.add(application)
        session.commit()
        outsider_id, application_id = outsider.id, application.id

    with Session(engine) as session:
        before = {
            user.login: (user.id, user.password_hash, user.role, user.full_name)
            for user in session.scalars(select(User))
        }
        company_before = [
            (row.id, row.owner_user_id, row.name, row.inn)
            for row in session.scalars(select(Company).order_by(Company.id))
        ]
        application_before = session.get(Application, application_id)
        application_state = (
            application_before.company_id,
            application_before.status,
            application_before.amount,
            application_before.version,
        )

    settings = Settings(env='test', demo_password=NEW_PASSWORD, jwt_secret=JWT_SECRET)
    assert reset_demo_passwords(engine, settings) == 5
    assert seed_demo_users(engine, Settings(env='test', demo_password=OLD_PASSWORD)) == 0
    with Session(engine) as session:
        after = {user.login: user for user in session.scalars(select(User))}
        assert set(after) == set(before)
        for login, _role, _name, _position in DEMO_USERS:
            user = after[login]
            assert (user.id, user.role, user.full_name) == (
                before[login][0],
                before[login][2],
                before[login][3],
            )
            assert user.password_hash != before[login][1]
            assert user.password_hash.startswith('$argon2id$')
            assert verify_password(NEW_PASSWORD, user.password_hash)
            assert not verify_password(OLD_PASSWORD, user.password_hash)
        assert after['other-client'].id == outsider_id
        assert after['other-client'].password_hash == before['other-client'][1]
        assert [
            (row.id, row.owner_user_id, row.name, row.inn)
            for row in session.scalars(select(Company).order_by(Company.id))
        ] == company_before
        application = session.get(Application, application_id)
        assert (
            application.company_id,
            application.status,
            application.amount,
            application.version,
        ) == application_state

    app = create_app(settings, db_engine=engine)

    async def check_login():
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url='http://testserver'
        ) as client:
            for login in ('client1', 'staff1'):
                old = await client.post(
                    '/auth/login',
                    json={
                        'login': login,
                        'password': OLD_PASSWORD,
                    },
                )
                new = await client.post(
                    '/auth/login',
                    json={
                        'login': login,
                        'password': NEW_PASSWORD,
                    },
                )
                assert old.status_code == 401
                assert new.status_code == 200

    asyncio.run(check_login())


def test_missing_accounts_are_not_created(engine) -> None:
    with Session(engine) as session:
        staff = session.scalar(select(User).where(User.login == 'staff1'))
        session.delete(staff)
        session.commit()
    assert reset_demo_passwords(engine, Settings(env='test', demo_password=NEW_PASSWORD)) == 4
    with Session(engine) as session:
        assert session.scalar(select(User).where(User.login == 'staff1')) is None


def test_identity_conflict_rolls_back_all_hashes(engine) -> None:
    with Session(engine) as session:
        hashes = {user.login: user.password_hash for user in session.scalars(select(User))}
        wrong = session.scalar(select(User).where(User.login == 'client3'))
        wrong.full_name = 'Не демо'
        session.commit()
    with pytest.raises(ValueError, match='identity conflict'):
        reset_demo_passwords(engine, Settings(env='test', demo_password=NEW_PASSWORD))
    with Session(engine) as session:
        assert {user.login: user.password_hash for user in session.scalars(select(User))} == hashes


def test_hashing_failure_rolls_back_all_hashes(engine, monkeypatch) -> None:
    from app import reset_demo_passwords as module

    with Session(engine) as session:
        hashes = {user.login: user.password_hash for user in session.scalars(select(User))}
    calls = 0
    real_hash = hash_password

    def failing_hash(password):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise RuntimeError('synthetic hashing failure')
        return real_hash(password)

    monkeypatch.setattr(module, 'hash_password', failing_hash)
    with pytest.raises(RuntimeError, match='synthetic hashing failure'):
        reset_demo_passwords(engine, Settings(env='test', demo_password=NEW_PASSWORD))
    with Session(engine) as session:
        assert {user.login: user.password_hash for user in session.scalars(select(User))} == hashes


def test_cli_uses_environment_and_prints_no_secrets(engine, monkeypatch, capsys) -> None:
    from app import reset_demo_passwords as module

    monkeypatch.setenv('DEMO_PASSWORD', NEW_PASSWORD)
    monkeypatch.setattr(module, 'create_db_engine', lambda _settings: engine)
    main()
    output = capsys.readouterr()
    assert output.err == ''
    assert output.out.strip() == 'Updated 5 existing demo accounts'
    assert OLD_PASSWORD not in output.out and NEW_PASSWORD not in output.out
    assert '$argon2id$' not in output.out


def test_cli_rejects_missing_password_before_database_access(monkeypatch, capsys) -> None:
    from app import reset_demo_passwords as module

    monkeypatch.delenv('DEMO_PASSWORD', raising=False)

    def forbidden_engine(_settings):
        raise AssertionError('database must not be opened')

    monkeypatch.setattr(module, 'create_db_engine', forbidden_engine)
    with pytest.raises(SystemExit, match='DEMO_PASSWORD is required'):
        main()
    assert capsys.readouterr().out == ''
