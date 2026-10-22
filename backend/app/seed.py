from datetime import date
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.engine import Engine
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.config import Settings
from app.db import create_db_engine
from app.models import Company, Product, User
from app.security import hash_password

DEMO_USERS = (
    ('client1', 'CLIENT', 'Демо-клиент 1', None),
    ('client2', 'CLIENT', 'Демо-клиент 2', None),
    ('client3', 'CLIENT', 'Демо-клиент 3', None),
    ('client4', 'CLIENT', 'Демо-клиент 4', None),
    ('staff1', 'STAFF', 'Демо-аналитик', 'Кредитный аналитик МСБ'),
)

DEMO_PRODUCTS = (
    ('working_capital', 'Оборотный кредит', 300_000, 30_000_000, 3, 36, Decimal('4.000')),
    ('investment', 'Инвестиционный кредит', 1_000_000, 100_000_000, 12, 84, Decimal('3.000')),
)

DEMO_COMPANIES = (
    (
        'client1',
        'ООО «Северный ветер»',
        '0000000000',
        'OOO',
        '47.11',
        'Торговля',
        4_200_000,
        3_300_000,
        120_000,
        date(2016, 1, 1),
    ),
    (
        'client2',
        'ИП Морозова',
        '000000000001',
        'IP',
        '56.10',
        'Общепит',
        1_300_000,
        1_050_000,
        60_000,
        date(2021, 1, 1),
    ),
    (
        'client3',
        'ООО «Гранит-Строй»',
        '0000000002',
        'OOO',
        '41.20',
        'Строительство',
        2_000_000,
        1_600_000,
        180_000,
        date(2018, 1, 1),
    ),
    (
        'client4',
        'ООО «Ромашка-Трейд»',
        '0000000003',
        'OOO',
        '46.90',
        'Торговля',
        1_500_000,
        1_300_000,
        200_000,
        date(2019, 1, 1),
    ),
)


def seed_demo_users(engine: Engine, settings: Settings) -> int:
    if settings.demo_password is None:
        raise ValueError('DEMO_PASSWORD is required to seed demo users')
    if engine.dialect.name == 'postgresql':
        insert_user = postgresql_insert
    elif engine.dialect.name == 'sqlite':
        insert_user = sqlite_insert
    else:
        raise ValueError('demo seed supports PostgreSQL and SQLite only')

    with Session(engine) as session:
        logins = tuple(user[0] for user in DEMO_USERS)
        existing = {
            user.login: user for user in session.scalars(select(User).where(User.login.in_(logins)))
        }
        created = 0
        for login, role, full_name, position in DEMO_USERS:
            if login in existing:
                current = existing[login]
                if current.role != role or not current.password_hash.startswith('$argon2id$'):
                    raise ValueError('demo account conflicts with expected role or hash type')
                continue
            statement = (
                insert_user(User)
                .values(
                    login=login,
                    password_hash=hash_password(settings.demo_password.get_secret_value()),
                    role=role,
                    full_name=full_name,
                    position=position,
                )
                .on_conflict_do_nothing(index_elements=['login'])
            )
            created += session.execute(statement).rowcount or 0
        session.commit()
    return created


def seed_demo_catalog(engine: Engine) -> None:
    with Session(engine) as session:
        for code, name, min_amount, max_amount, min_term, max_term, margin in DEMO_PRODUCTS:
            if session.get(Product, code) is None:
                session.add(
                    Product(
                        code=code,
                        name=name,
                        terms_version='demo-2026-1',
                        min_amount=min_amount,
                        max_amount=max_amount,
                        min_term_months=min_term,
                        max_term_months=max_term,
                        margin_pp=margin,
                        is_active=True,
                    )
                )
        users = {
            user.login: user.id
            for user in session.scalars(
                select(User).where(User.login.in_(tuple(row[0] for row in DEMO_COMPANIES)))
            )
        }
        if len(users) != len(DEMO_COMPANIES):
            raise ValueError('demo users must be initialized before companies')
        for (
            login,
            name,
            inn,
            legal_form,
            okved,
            industry,
            revenue,
            expenses,
            payments,
            registered,
        ) in DEMO_COMPANIES:
            company = session.scalar(select(Company).where(Company.inn == inn))
            if company is not None:
                if company.owner_user_id != users[login]:
                    raise ValueError('demo company conflicts with expected owner')
                continue
            session.add(
                Company(
                    owner_user_id=users[login],
                    name=name,
                    inn=inn,
                    legal_form=legal_form,
                    okved=okved,
                    industry=industry,
                    region='Демо-регион',
                    registered_at=registered,
                    bank_client_since=date(2022, 1, 1),
                    avg_monthly_revenue=revenue,
                    avg_monthly_expenses=expenses,
                    revenue_trend_6m=Decimal('0.000000'),
                    existing_monthly_payments=payments,
                )
            )
        session.commit()


def main() -> None:
    settings = Settings()
    engine = create_db_engine(settings)
    try:
        try:
            seed_demo_users(engine, settings)
            seed_demo_catalog(engine)
        except SQLAlchemyError:
            raise SystemExit('Demo account initialization failed') from None
    finally:
        engine.dispose()


if __name__ == '__main__':
    main()
