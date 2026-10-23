"""Явный сброс паролей существующих синтетических демо-учёток."""

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from app.config import Settings
from app.db import create_db_engine
from app.models import User
from app.security import hash_password
from app.seed import DEMO_USERS


def reset_demo_passwords(engine: Engine, settings: Settings) -> int:
    if settings.demo_password is None:
        raise ValueError('DEMO_PASSWORD is required')

    expected = {login: (role, full_name) for login, role, full_name, _ in DEMO_USERS}
    with Session(engine) as session, session.begin():
        users = list(
            session.scalars(select(User).where(User.login.in_(expected)).with_for_update())
        )
        if any((user.role, user.full_name) != expected[user.login] for user in users):
            raise ValueError('demo account identity conflict')
        password = settings.demo_password.get_secret_value()
        for user in users:
            user.password_hash = hash_password(password)
    return len(users)


def main() -> None:
    try:
        settings = Settings()
    except ValidationError:
        raise SystemExit('Invalid environment configuration') from None
    if settings.demo_password is None:
        raise SystemExit('DEMO_PASSWORD is required')

    try:
        engine = create_db_engine(settings)
        try:
            count = reset_demo_passwords(engine, settings)
        finally:
            engine.dispose()
    except Exception:
        raise SystemExit('Demo password reset failed') from None
    print(f'Updated {count} existing demo accounts')


if __name__ == '__main__':
    main()
