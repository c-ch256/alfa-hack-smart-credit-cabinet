import hashlib
from datetime import date
from decimal import Decimal

import pytest
from alembic.config import Config
from sqlalchemy import create_engine, event, insert, inspect, select
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, configure_mappers

from alembic import command
from app.db import Base
from app.models import (
    Application,
    ApplicationSnapshot,
    AuditEvent,
    CheckResult,
    Company,
    Decision,
    Offer,
    Product,
    Score,
    StatusEvent,
    User,
)


def migration_config(connection) -> Config:
    config = Config('alembic.ini')
    config.attributes['connection'] = connection
    return config


@pytest.fixture
def engine(tmp_path) -> Engine:
    db_engine = create_engine(f'sqlite+pysqlite:///{tmp_path / "model-tests.sqlite"}')

    @event.listens_for(db_engine, 'connect')
    def enable_foreign_keys(dbapi_connection, _connection_record) -> None:
        cursor = dbapi_connection.cursor()
        cursor.execute('PRAGMA foreign_keys=ON')
        cursor.close()

    with db_engine.connect() as connection:
        command.upgrade(migration_config(connection), 'head')
    try:
        yield db_engine
    finally:
        db_engine.dispose()


def synthetic_hash() -> str:
    salt = b'synthetic-unit-test-salt'
    digest = hashlib.pbkdf2_hmac('sha256', b'synthetic-only', salt, 600_000)
    return f'pbkdf2_sha256$600000${salt.hex()}${digest.hex()}'


def seed_application(engine: Engine, *, number: str = 'SYN-001') -> tuple[int, int, int]:
    with Session(engine) as session:
        user = User(
            login=f'client-{number}',
            password_hash=synthetic_hash(),
            role='CLIENT',
            full_name='Синтетический клиент',
        )
        product = Product(
            code='working_capital',
            name='Оборотный кредит',
            terms_version='demo-1',
            min_amount=300_000,
            max_amount=30_000_000,
            min_term_months=3,
            max_term_months=36,
            margin_pp=Decimal('4.000'),
        )
        session.add_all([user, product])
        session.flush()
        company = Company(
            owner_user_id=user.id,
            name='Синтетическая компания',
            inn='1234567890',
            legal_form='OOO',
            okved='47.11',
            region='Москва',
            registered_at=date(2020, 1, 1),
            bank_client_since=date(2021, 1, 1),
            avg_monthly_revenue=4_200_000,
            avg_monthly_expenses=3_300_000,
            revenue_trend_6m=Decimal('0.040000'),
            existing_monthly_payments=120_000,
        )
        session.add(company)
        session.flush()
        application = Application(
            number=number,
            company_id=company.id,
            created_by_user_id=user.id,
            product_code=product.code,
            amount=3_000_000,
            term_months=24,
            purpose='purchase',
            idempotency_key='synthetic-key-1',
        )
        session.add(application)
        session.flush()
        snapshot = ApplicationSnapshot(
            application_id=application.id,
            version=1,
            data={'amount': 3_000_000, 'product_code': 'working_capital'},
        )
        session.add(snapshot)
        session.commit()
        return user.id, application.id, snapshot.id


def test_migration_upgrade_downgrade_and_mapping(tmp_path) -> None:
    configure_mappers()
    db_engine = create_engine(f'sqlite+pysqlite:///{tmp_path / "migration.sqlite"}')
    try:
        with db_engine.connect() as connection:
            config = migration_config(connection)
            command.upgrade(config, 'head')
            assert set(Base.metadata.tables) <= set(inspect(connection).get_table_names())
            assert 'alembic_version' in inspect(connection).get_table_names()
            command.check(config)
            command.downgrade(config, '0001_initial_empty')
            assert inspect(connection).get_table_names() == ['alembic_version']
    finally:
        db_engine.dispose()


def test_complete_history_and_exact_financial_types(engine: Engine) -> None:
    user_id, application_id, snapshot_id = seed_application(engine)
    scoring_input = {'amount': 3_000_000, 'company': {'avg_monthly_revenue': 4_200_000}}
    with Session(engine) as session:
        check = CheckResult(
            application_id=application_id,
            snapshot_id=snapshot_id,
            source='FNS',
            status='ok',
            summary='Синтетический ответ мока',
            payload={'status': 'active'},
            adapter_version='demo-1',
        )
        score = Score(
            application_id=application_id,
            snapshot_id=snapshot_id,
            source='mock',
            is_test_result=True,
            result_status='success',
            pd=Decimal('0.031000'),
            risk_band='A',
            model_version='mock-1',
            feature_schema_version='scoring-request-1',
            reasons=[{'feature': 'max_overdue_days_12m', 'effect': 'decreases_risk'}],
            features={'max_overdue_days_12m': 0},
            input_snapshot=scoring_input,
        )
        session.add_all([check, score])
        session.flush()
        decision = Decision(
            application_id=application_id,
            snapshot_id=snapshot_id,
            score_id=score.id,
            decision='approve',
            created_by='SYSTEM',
            rule_version='demo-1',
            rationale=['Synthetic test decision'],
        )
        session.add(decision)
        session.flush()
        session.add_all(
            [
                Offer(
                    application_id=application_id,
                    decision_id=decision.id,
                    kind='original',
                    product_code='working_capital',
                    amount=3_000_000,
                    term_months=24,
                    rate=Decimal('18.5000'),
                    monthly_payment=150_000,
                    created_by='SYSTEM',
                ),
                StatusEvent(
                    application_id=application_id,
                    sequence=1,
                    from_status=None,
                    to_status='draft',
                    actor_role='CLIENT',
                    actor_user_id=user_id,
                ),
                StatusEvent(
                    application_id=application_id,
                    sequence=2,
                    from_status='draft',
                    to_status='submitted',
                    actor_role='CLIENT',
                    actor_user_id=user_id,
                ),
                AuditEvent(
                    actor_user_id=user_id,
                    actor_role='CLIENT',
                    application_id=application_id,
                    object_type='application',
                    object_id=str(application_id),
                    action='submit',
                    result='success',
                    correlation_id='synthetic-correlation-id',
                    rule_version='demo-1',
                    details={'event': 'submitted'},
                ),
            ]
        )
        session.commit()

    with Session(engine) as session:
        assert session.scalar(select(Score.pd)) == Decimal('0.031000')
        assert session.scalar(select(Score.input_snapshot)) == scoring_input
        assert session.scalar(select(Offer.rate)) == Decimal('18.5000')
        assert session.scalar(select(Offer.amount)) == 3_000_000
        events = session.scalars(select(StatusEvent).order_by(StatusEvent.sequence)).all()
        assert [(event.from_status, event.to_status) for event in events] == [
            (None, 'draft'),
            ('draft', 'submitted'),
        ]
        assert session.scalar(select(AuditEvent.action)) == 'submit'


def test_failed_scoring_run_has_no_risk_or_credit_decision(engine: Engine) -> None:
    _user_id, application_id, snapshot_id = seed_application(engine)
    with Session(engine) as session:
        session.add(
            Score(
                application_id=application_id,
                snapshot_id=snapshot_id,
                source='mock',
                is_test_result=True,
                result_status='error',
                pd=None,
                risk_band=None,
                model_version='mock-1',
                feature_schema_version='scoring-request-1',
                reasons=[],
                features={},
                input_snapshot={'amount': 3_000_000},
                error_code='SYNTHETIC_TIMEOUT',
            )
        )
        session.commit()
        assert session.scalar(select(Score.error_code)) == 'SYNTHETIC_TIMEOUT'
        assert session.scalar(select(Decision.id)) is None


@pytest.mark.parametrize(
    ('table', 'values'),
    [
        (
            User.__table__,
            {'login': 'bad-role', 'password_hash': 'x' * 40, 'role': 'ADMIN', 'full_name': 'Test'},
        ),
        (Company.__table__, {'id': 9, 'owner_user_id': 999}),
        (Application.__table__, {'id': 9, 'status': 'unknown'}),
        (Application.__table__, {'id': 9, 'amount': -1}),
        (CheckResult.__table__, {'id': 9, 'source': 'UNKNOWN'}),
        (Score.__table__, {'id': 9, 'pd': Decimal('1.01')}),
        (Score.__table__, {'id': 9, 'result_status': 'error', 'pd': Decimal('0.1')}),
        (StatusEvent.__table__, {'id': 9, 'to_status': 'unknown'}),
        (StatusEvent.__table__, {'id': 9, 'sequence': 0}),
    ],
)
def test_invalid_values_rejected(engine: Engine, table, values) -> None:
    user_id, application_id, snapshot_id = seed_application(engine)
    row = {
        User.__table__: {
            'login': 'extra-client',
            'password_hash': synthetic_hash(),
            'role': 'CLIENT',
            'full_name': 'Synthetic user',
        },
        Company.__table__: {
            'owner_user_id': user_id,
            'name': 'Synthetic company',
            'inn': '123456789012',
            'legal_form': 'IP',
            'okved': '47.11',
            'region': 'Москва',
            'registered_at': date(2020, 1, 1),
            'bank_client_since': date(2021, 1, 1),
            'avg_monthly_revenue': 1,
            'avg_monthly_expenses': 1,
            'revenue_trend_6m': Decimal('0'),
            'existing_monthly_payments': 0,
        },
        Application.__table__: {
            'number': 'SYN-INVALID',
            'company_id': 1,
            'created_by_user_id': user_id,
            'product_code': 'working_capital',
            'status': 'draft',
            'amount': 1,
            'term_months': 1,
            'purpose': 'purchase',
        },
        CheckResult.__table__: {
            'application_id': application_id,
            'snapshot_id': snapshot_id,
            'source': 'FNS',
            'status': 'ok',
            'summary': 'Synthetic',
            'payload': {},
            'adapter_version': 'demo-1',
        },
        Score.__table__: {
            'application_id': application_id,
            'snapshot_id': snapshot_id,
            'source': 'mock',
            'is_test_result': True,
            'result_status': 'success',
            'pd': Decimal('0.1'),
            'risk_band': 'B',
            'model_version': 'mock-1',
            'feature_schema_version': 'scoring-request-1',
            'reasons': [],
            'features': {},
            'input_snapshot': {},
        },
        StatusEvent.__table__: {
            'application_id': application_id,
            'sequence': 1,
            'to_status': 'draft',
            'actor_role': 'CLIENT',
            'actor_user_id': user_id,
        },
    }[table]
    with pytest.raises(IntegrityError), engine.begin() as connection:
        connection.execute(insert(table).values(**(row | values)))


def test_offer_rate_and_application_binding_rejected(engine: Engine) -> None:
    user_id, application_id, snapshot_id = seed_application(engine)
    with engine.begin() as connection:
        decision_id = connection.execute(
            insert(Decision).values(
                application_id=application_id,
                snapshot_id=snapshot_id,
                decision='approve',
                created_by='SYSTEM',
                rule_version='demo-1',
                rationale=[],
            )
        ).inserted_primary_key[0]
        other_application_id = connection.execute(
            insert(Application).values(
                number='SYN-OTHER',
                company_id=1,
                created_by_user_id=user_id,
                product_code='working_capital',
                amount=1_000_000,
                term_months=12,
                purpose='purchase',
            )
        ).inserted_primary_key[0]
    offer = {
        'application_id': application_id,
        'decision_id': decision_id,
        'kind': 'original',
        'product_code': 'working_capital',
        'amount': 1_000_000,
        'term_months': 12,
        'rate': Decimal('18.5000'),
        'monthly_payment': 100_000,
        'created_by': 'SYSTEM',
    }
    with pytest.raises(IntegrityError), engine.begin() as connection:
        connection.execute(insert(Offer).values(**(offer | {'rate': Decimal('-0.1')})))
    with pytest.raises(IntegrityError), engine.begin() as connection:
        connection.execute(
            insert(Offer).values(**(offer | {'application_id': other_application_id}))
        )


def test_idempotency_key_and_event_sequence_are_unique(engine: Engine) -> None:
    user_id, application_id, _snapshot_id = seed_application(engine)
    with pytest.raises(IntegrityError), engine.begin() as connection:
        connection.execute(
            insert(Application).values(
                number='SYN-SECOND',
                company_id=1,
                created_by_user_id=user_id,
                product_code='working_capital',
                amount=1_000_000,
                term_months=12,
                purpose='purchase',
                idempotency_key='synthetic-key-1',
            )
        )
    event_values = {
        'application_id': application_id,
        'sequence': 1,
        'to_status': 'draft',
        'actor_role': 'CLIENT',
        'actor_user_id': user_id,
    }
    with engine.begin() as connection:
        connection.execute(insert(StatusEvent).values(**event_values))
    with pytest.raises(IntegrityError), engine.begin() as connection:
        connection.execute(insert(StatusEvent).values(**event_values))


def test_cross_application_snapshot_and_history_deletion_rejected(engine: Engine) -> None:
    user_id, application_id, snapshot_id = seed_application(engine)
    with engine.begin() as connection:
        other_company_id = connection.execute(
            insert(Company).values(
                owner_user_id=user_id,
                name='Другая синтетическая компания',
                inn='123456789012',
                legal_form='IP',
                okved='47.11',
                region='Москва',
                registered_at=date(2020, 1, 1),
                bank_client_since=date(2021, 1, 1),
                avg_monthly_revenue=1,
                avg_monthly_expenses=1,
                revenue_trend_6m=Decimal('0'),
                existing_monthly_payments=0,
            )
        ).inserted_primary_key[0]
        other_app_id = connection.execute(
            insert(Application).values(
                number='SYN-002',
                company_id=1,
                created_by_user_id=user_id,
                product_code='working_capital',
                amount=1_000_000,
                term_months=12,
                purpose='purchase',
            )
        ).inserted_primary_key[0]
        connection.execute(
            insert(StatusEvent).values(
                application_id=application_id,
                sequence=1,
                to_status='draft',
                actor_role='CLIENT',
                actor_user_id=user_id,
            )
        )
        connection.execute(
            insert(AuditEvent).values(
                application_id=application_id,
                actor_user_id=user_id,
                actor_role='CLIENT',
                object_type='application',
                action='create',
                result='success',
                correlation_id='synthetic-correlation-id',
                details={},
            )
        )
    with pytest.raises(IntegrityError), engine.begin() as connection:
        connection.execute(
            insert(CheckResult).values(
                application_id=other_app_id,
                snapshot_id=snapshot_id,
                source='FNS',
                status='ok',
                summary='Synthetic',
                payload={},
                adapter_version='demo-1',
            )
        )
    with pytest.raises(IntegrityError), engine.begin() as connection:
        connection.execute(Application.__table__.delete().where(Application.id == application_id))
    with pytest.raises(IntegrityError), engine.begin() as connection:
        connection.execute(
            insert(AuditEvent).values(
                application_id=application_id,
                company_id=other_company_id,
                actor_role='SYSTEM',
                object_type='application',
                action='view',
                result='success',
                correlation_id='synthetic-invalid-association',
                details={},
            )
        )
    with pytest.raises(IntegrityError), engine.begin() as connection:
        connection.execute(
            insert(AuditEvent).values(
                application_id=application_id,
                actor_role='CLIENT',
                object_type='application',
                action='view',
                result='success',
                correlation_id='synthetic-missing-actor',
                details={},
            )
        )
