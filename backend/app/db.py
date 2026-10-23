from sqlalchemy import create_engine, text
from sqlalchemy.engine import URL, Engine, make_url
from sqlalchemy.exc import ArgumentError, SQLAlchemyError
from sqlalchemy.orm import DeclarativeBase

from app.config import Settings


class Base(DeclarativeBase):
    pass


def database_url(settings: Settings) -> URL:
    if settings.database_url is not None:
        try:
            url = make_url(settings.database_url.get_secret_value())
        except ArgumentError:
            raise ValueError('invalid DATABASE_URL') from None
    else:
        url = URL.create(
            'postgresql+psycopg',
            username=settings.db_user,
            password=settings.db_password.get_secret_value() if settings.db_password else None,
            host=settings.db_host,
            port=settings.db_port,
            database=settings.db_name,
        )

    if (
        url.drivername != 'postgresql+psycopg'
        or not url.host
        or not url.database
        or not url.username
        or (settings.env == 'production' and not url.password)
    ):
        raise ValueError('database settings require a PostgreSQL psycopg URL with credentials')
    return url


def create_db_engine(settings: Settings) -> Engine:
    return create_engine(
        database_url(settings),
        pool_pre_ping=True,
        pool_timeout=3,
        connect_args={'connect_timeout': 2},
    )


def database_is_ready(engine: Engine) -> bool:
    try:
        with engine.connect() as connection:
            connection.execute(text('SELECT 1'))
    except SQLAlchemyError:
        return False
    return True
