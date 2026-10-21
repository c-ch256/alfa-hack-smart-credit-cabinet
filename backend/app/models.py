from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base

json_type = JSON().with_variant(JSONB(), 'postgresql')


class User(Base):
    __tablename__ = 'users'
    __table_args__ = (
        CheckConstraint("role IN ('CLIENT', 'STAFF')", name='ck_users_role'),
        CheckConstraint('length(password_hash) >= 20', name='ck_users_password_hash_length'),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    login: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[str] = mapped_column(String(16), nullable=False)
    full_name: Mapped[str] = mapped_column(String(200), nullable=False)
    position: Mapped[str | None] = mapped_column(String(200))
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default='true')
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    owned_companies: Mapped[list['Company']] = relationship(back_populates='owner')


class Company(Base):
    __tablename__ = 'companies'
    __table_args__ = (
        CheckConstraint("legal_form IN ('OOO', 'IP')", name='ck_companies_legal_form'),
        CheckConstraint('length(inn) IN (10, 12)', name='ck_companies_inn_length'),
        CheckConstraint('avg_monthly_revenue >= 0', name='ck_companies_revenue_nonnegative'),
        CheckConstraint('avg_monthly_expenses >= 0', name='ck_companies_expenses_nonnegative'),
        CheckConstraint('existing_monthly_payments >= 0', name='ck_companies_payments_nonnegative'),
        CheckConstraint('revenue_trend_6m >= -1', name='ck_companies_revenue_trend_min'),
        Index('ix_companies_owner_user_id', 'owner_user_id'),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    owner_user_id: Mapped[int] = mapped_column(ForeignKey('users.id', ondelete='RESTRICT'))
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    inn: Mapped[str] = mapped_column(String(12), unique=True, nullable=False)
    legal_form: Mapped[str] = mapped_column(String(8), nullable=False)
    okved: Mapped[str] = mapped_column(String(16), nullable=False)
    industry: Mapped[str | None] = mapped_column(String(128))
    region: Mapped[str] = mapped_column(String(128), nullable=False)
    registered_at: Mapped[date] = mapped_column(Date, nullable=False)
    bank_client_since: Mapped[date] = mapped_column(Date, nullable=False)
    avg_monthly_revenue: Mapped[int] = mapped_column(BigInteger, nullable=False)
    avg_monthly_expenses: Mapped[int] = mapped_column(BigInteger, nullable=False)
    revenue_trend_6m: Mapped[Decimal] = mapped_column(Numeric(9, 6), nullable=False)
    existing_monthly_payments: Mapped[int] = mapped_column(BigInteger, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    owner: Mapped[User] = relationship(back_populates='owned_companies')
    applications: Mapped[list['Application']] = relationship(back_populates='company')


class Product(Base):
    __tablename__ = 'products'
    __table_args__ = (
        CheckConstraint("code IN ('working_capital', 'investment')", name='ck_products_code'),
        CheckConstraint('min_amount > 0 AND max_amount >= min_amount', name='ck_products_amounts'),
        CheckConstraint(
            'min_term_months > 0 AND max_term_months >= min_term_months',
            name='ck_products_terms',
        ),
        CheckConstraint('margin_pp >= 0', name='ck_products_margin_nonnegative'),
    )

    code: Mapped[str] = mapped_column(String(32), primary_key=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    terms_version: Mapped[str] = mapped_column(String(64), nullable=False)
    min_amount: Mapped[int] = mapped_column(BigInteger, nullable=False)
    max_amount: Mapped[int] = mapped_column(BigInteger, nullable=False)
    min_term_months: Mapped[int] = mapped_column(Integer, nullable=False)
    max_term_months: Mapped[int] = mapped_column(Integer, nullable=False)
    margin_pp: Mapped[Decimal] = mapped_column(Numeric(6, 3), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default='true')


class Application(Base):
    __tablename__ = 'applications'
    __table_args__ = (
        CheckConstraint(
            "status IN ('draft', 'submitted', 'checking', 'scoring', 'decision', "
            "'needs_info', 'manual_review', 'approved', 'counter_offer', 'accepted', "
            "'signed_demo', 'disbursed_demo', 'rejected', 'closed')",
            name='ck_applications_status',
        ),
        CheckConstraint('amount > 0', name='ck_applications_amount_positive'),
        CheckConstraint('term_months > 0', name='ck_applications_term_positive'),
        CheckConstraint(
            'collateral_value IS NULL OR collateral_value >= 0',
            name='ck_applications_collateral_nonnegative',
        ),
        CheckConstraint('version >= 0', name='ck_applications_version_nonnegative'),
        UniqueConstraint(
            'created_by_user_id', 'idempotency_key', name='uq_applications_creator_idempotency'
        ),
        UniqueConstraint('id', 'company_id', name='uq_applications_id_company'),
        Index('ix_applications_company_status', 'company_id', 'status'),
        Index('ix_applications_created_by_user_id', 'created_by_user_id'),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    number: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    company_id: Mapped[int] = mapped_column(ForeignKey('companies.id', ondelete='RESTRICT'))
    created_by_user_id: Mapped[int] = mapped_column(ForeignKey('users.id', ondelete='RESTRICT'))
    product_code: Mapped[str] = mapped_column(
        ForeignKey('products.code', ondelete='RESTRICT'), nullable=False
    )
    status: Mapped[str] = mapped_column(String(32), nullable=False, server_default='draft')
    amount: Mapped[int] = mapped_column(BigInteger, nullable=False)
    term_months: Mapped[int] = mapped_column(Integer, nullable=False)
    purpose: Mapped[str] = mapped_column(String(128), nullable=False)
    purpose_details: Mapped[str | None] = mapped_column(Text)
    investment_object: Mapped[str | None] = mapped_column(String(255))
    collateral_type: Mapped[str | None] = mapped_column(String(128))
    collateral_value: Mapped[int | None] = mapped_column(BigInteger)
    info_request: Mapped[str | None] = mapped_column(Text)
    info_response: Mapped[str | None] = mapped_column(Text)
    idempotency_key: Mapped[str | None] = mapped_column(String(128))
    sign_code_hash: Mapped[str | None] = mapped_column(String(255))
    sign_code_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    version: Mapped[int] = mapped_column(Integer, nullable=False, server_default='0')
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    company: Mapped[Company] = relationship(back_populates='applications')
    product: Mapped[Product] = relationship()
    created_by: Mapped[User] = relationship()
    snapshots: Mapped[list['ApplicationSnapshot']] = relationship(back_populates='application')
    offers: Mapped[list['Offer']] = relationship(back_populates='application')
    status_events: Mapped[list['StatusEvent']] = relationship(back_populates='application')


class ApplicationSnapshot(Base):
    __tablename__ = 'application_snapshots'
    __table_args__ = (
        CheckConstraint('version > 0', name='ck_application_snapshots_version_positive'),
        UniqueConstraint('application_id', 'version', name='uq_application_snapshots_version'),
        UniqueConstraint('id', 'application_id', name='uq_application_snapshots_id_application'),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    application_id: Mapped[int] = mapped_column(ForeignKey('applications.id', ondelete='RESTRICT'))
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    data: Mapped[dict[str, Any]] = mapped_column(json_type, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    application: Mapped[Application] = relationship(back_populates='snapshots')


class CheckResult(Base):
    __tablename__ = 'check_results'
    __table_args__ = (
        ForeignKeyConstraint(
            ['snapshot_id', 'application_id'],
            ['application_snapshots.id', 'application_snapshots.application_id'],
            ondelete='RESTRICT',
            name='fk_check_results_snapshot_application',
        ),
        CheckConstraint("source IN ('DATA', 'FNS', 'BKI')", name='ck_check_results_source'),
        CheckConstraint("status IN ('ok', 'warn', 'fail')", name='ck_check_results_status'),
        Index('ix_check_results_application_created', 'application_id', 'created_at'),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    application_id: Mapped[int] = mapped_column(Integer, nullable=False)
    snapshot_id: Mapped[int] = mapped_column(Integer, nullable=False)
    source: Mapped[str] = mapped_column(String(8), nullable=False)
    status: Mapped[str] = mapped_column(String(8), nullable=False)
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(json_type, nullable=False)
    adapter_version: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    snapshot: Mapped[ApplicationSnapshot] = relationship()


class Score(Base):
    __tablename__ = 'scores'
    __table_args__ = (
        ForeignKeyConstraint(
            ['snapshot_id', 'application_id'],
            ['application_snapshots.id', 'application_snapshots.application_id'],
            ondelete='RESTRICT',
            name='fk_scores_snapshot_application',
        ),
        UniqueConstraint('id', 'snapshot_id', 'application_id', name='uq_scores_id_snapshot_app'),
        CheckConstraint("source IN ('mock', 'model')", name='ck_scores_source'),
        CheckConstraint("result_status IN ('success', 'error')", name='ck_scores_result_status'),
        CheckConstraint(
            "risk_band IS NULL OR risk_band IN ('A', 'B', 'C', 'D')", name='ck_scores_risk_band'
        ),
        CheckConstraint('pd IS NULL OR (pd >= 0 AND pd <= 1)', name='ck_scores_pd_range'),
        CheckConstraint(
            "(result_status = 'success' AND pd IS NOT NULL AND risk_band IS NOT NULL "
            "AND error_code IS NULL) OR (result_status = 'error' AND pd IS NULL "
            'AND risk_band IS NULL AND error_code IS NOT NULL)',
            name='ck_scores_result_consistency',
        ),
        Index('ix_scores_application_created', 'application_id', 'created_at'),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    application_id: Mapped[int] = mapped_column(Integer, nullable=False)
    snapshot_id: Mapped[int] = mapped_column(Integer, nullable=False)
    source: Mapped[str] = mapped_column(String(8), nullable=False)
    is_test_result: Mapped[bool] = mapped_column(Boolean, nullable=False)
    result_status: Mapped[str] = mapped_column(String(8), nullable=False)
    pd: Mapped[Decimal | None] = mapped_column(Numeric(7, 6))
    risk_band: Mapped[str | None] = mapped_column(String(1))
    model_version: Mapped[str] = mapped_column(String(64), nullable=False)
    feature_schema_version: Mapped[str] = mapped_column(String(64), nullable=False)
    reasons: Mapped[list[dict[str, Any]]] = mapped_column(json_type, nullable=False)
    features: Mapped[dict[str, Any]] = mapped_column(json_type, nullable=False)
    input_snapshot: Mapped[dict[str, Any]] = mapped_column(json_type, nullable=False)
    error_code: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    snapshot: Mapped[ApplicationSnapshot] = relationship()


class Decision(Base):
    __tablename__ = 'decisions'
    __table_args__ = (
        ForeignKeyConstraint(
            ['snapshot_id', 'application_id'],
            ['application_snapshots.id', 'application_snapshots.application_id'],
            ondelete='RESTRICT',
            name='fk_decisions_snapshot_application',
        ),
        ForeignKeyConstraint(
            ['score_id', 'snapshot_id', 'application_id'],
            ['scores.id', 'scores.snapshot_id', 'scores.application_id'],
            ondelete='RESTRICT',
            name='fk_decisions_score_snapshot_application',
        ),
        UniqueConstraint('id', 'application_id', name='uq_decisions_id_application'),
        CheckConstraint(
            "decision IN ('approve', 'counter', 'reject')", name='ck_decisions_decision'
        ),
        CheckConstraint("created_by IN ('SYSTEM', 'STAFF')", name='ck_decisions_created_by'),
        CheckConstraint(
            "(created_by = 'SYSTEM' AND created_by_user_id IS NULL) OR "
            "(created_by = 'STAFF' AND created_by_user_id IS NOT NULL)",
            name='ck_decisions_actor',
        ),
        Index('ix_decisions_application_created', 'application_id', 'created_at'),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    application_id: Mapped[int] = mapped_column(Integer, nullable=False)
    snapshot_id: Mapped[int] = mapped_column(Integer, nullable=False)
    score_id: Mapped[int | None] = mapped_column(Integer)
    decision: Mapped[str] = mapped_column(String(16), nullable=False)
    created_by: Mapped[str] = mapped_column(String(8), nullable=False)
    created_by_user_id: Mapped[int | None] = mapped_column(
        ForeignKey('users.id', ondelete='RESTRICT')
    )
    rule_version: Mapped[str] = mapped_column(String(64), nullable=False)
    rationale: Mapped[list[str]] = mapped_column(json_type, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    snapshot: Mapped[ApplicationSnapshot] = relationship(foreign_keys=[snapshot_id, application_id])
    score: Mapped[Score | None] = relationship(
        foreign_keys=[score_id, snapshot_id, application_id], viewonly=True
    )
    actor: Mapped[User | None] = relationship()


class Offer(Base):
    __tablename__ = 'offers'
    __table_args__ = (
        ForeignKeyConstraint(
            ['decision_id', 'application_id'],
            ['decisions.id', 'decisions.application_id'],
            ondelete='RESTRICT',
            name='fk_offers_decision_application',
        ),
        CheckConstraint(
            "kind IN ('original', 'counter', 'alternative_product')", name='ck_offers_kind'
        ),
        CheckConstraint(
            "status IN ('proposed', 'accepted', 'declined', 'superseded')",
            name='ck_offers_status',
        ),
        CheckConstraint("created_by IN ('SYSTEM', 'STAFF')", name='ck_offers_created_by'),
        CheckConstraint('amount > 0', name='ck_offers_amount_positive'),
        CheckConstraint('term_months > 0', name='ck_offers_term_positive'),
        CheckConstraint('rate >= 0', name='ck_offers_rate_nonnegative'),
        CheckConstraint('monthly_payment >= 0', name='ck_offers_payment_nonnegative'),
        Index('ix_offers_application_status', 'application_id', 'status'),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    application_id: Mapped[int] = mapped_column(ForeignKey('applications.id', ondelete='RESTRICT'))
    decision_id: Mapped[int] = mapped_column(Integer, nullable=False)
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    product_code: Mapped[str] = mapped_column(
        ForeignKey('products.code', ondelete='RESTRICT'), nullable=False
    )
    amount: Mapped[int] = mapped_column(BigInteger, nullable=False)
    term_months: Mapped[int] = mapped_column(Integer, nullable=False)
    rate: Mapped[Decimal] = mapped_column(Numeric(7, 4), nullable=False)
    monthly_payment: Mapped[int] = mapped_column(BigInteger, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, server_default='proposed')
    created_by: Mapped[str] = mapped_column(String(8), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    application: Mapped[Application] = relationship(back_populates='offers')
    decision_record: Mapped[Decision] = relationship(viewonly=True)
    product: Mapped[Product] = relationship()


class StatusEvent(Base):
    __tablename__ = 'status_events'
    __table_args__ = (
        CheckConstraint('sequence > 0', name='ck_status_events_sequence_positive'),
        CheckConstraint(
            "from_status IS NULL OR from_status IN ('draft', 'submitted', 'checking', "
            "'scoring', 'decision', 'needs_info', 'manual_review', 'approved', "
            "'counter_offer', 'accepted', 'signed_demo', 'disbursed_demo', 'rejected', 'closed')",
            name='ck_status_events_from_status',
        ),
        CheckConstraint(
            "to_status IN ('draft', 'submitted', 'checking', 'scoring', 'decision', "
            "'needs_info', 'manual_review', 'approved', 'counter_offer', 'accepted', "
            "'signed_demo', 'disbursed_demo', 'rejected', 'closed')",
            name='ck_status_events_to_status',
        ),
        CheckConstraint(
            'from_status IS NULL OR from_status <> to_status',
            name='ck_status_events_changed',
        ),
        CheckConstraint(
            "actor_role IN ('CLIENT', 'STAFF', 'SYSTEM')", name='ck_status_events_actor_role'
        ),
        CheckConstraint(
            "(actor_role = 'SYSTEM' AND actor_user_id IS NULL) OR "
            "(actor_role IN ('CLIENT', 'STAFF') AND actor_user_id IS NOT NULL)",
            name='ck_status_events_actor',
        ),
        UniqueConstraint('application_id', 'sequence', name='uq_status_events_sequence'),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    application_id: Mapped[int] = mapped_column(ForeignKey('applications.id', ondelete='RESTRICT'))
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    from_status: Mapped[str | None] = mapped_column(String(32))
    to_status: Mapped[str] = mapped_column(String(32), nullable=False)
    actor_role: Mapped[str] = mapped_column(String(8), nullable=False)
    actor_user_id: Mapped[int | None] = mapped_column(ForeignKey('users.id', ondelete='RESTRICT'))
    reason: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    application: Mapped[Application] = relationship(back_populates='status_events')
    actor: Mapped[User | None] = relationship()


class AuditEvent(Base):
    __tablename__ = 'audit_events'
    __table_args__ = (
        ForeignKeyConstraint(
            ['application_id', 'company_id'],
            ['applications.id', 'applications.company_id'],
            ondelete='RESTRICT',
            name='fk_audit_events_application_company',
        ),
        CheckConstraint(
            "actor_role IN ('CLIENT', 'STAFF', 'SYSTEM')", name='ck_audit_events_actor_role'
        ),
        CheckConstraint(
            "(actor_role = 'SYSTEM' AND actor_user_id IS NULL) OR "
            "(actor_role IN ('CLIENT', 'STAFF') AND actor_user_id IS NOT NULL)",
            name='ck_audit_events_actor',
        ),
        Index('ix_audit_events_application_created', 'application_id', 'created_at'),
        Index('ix_audit_events_actor_created', 'actor_user_id', 'created_at'),
        Index('ix_audit_events_correlation_id', 'correlation_id'),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    actor_user_id: Mapped[int | None] = mapped_column(ForeignKey('users.id', ondelete='RESTRICT'))
    actor_role: Mapped[str] = mapped_column(String(8), nullable=False)
    company_id: Mapped[int | None] = mapped_column(ForeignKey('companies.id', ondelete='RESTRICT'))
    application_id: Mapped[int | None] = mapped_column(
        ForeignKey('applications.id', ondelete='RESTRICT')
    )
    object_type: Mapped[str] = mapped_column(String(64), nullable=False)
    object_id: Mapped[str | None] = mapped_column(String(64))
    action: Mapped[str] = mapped_column(String(128), nullable=False)
    result: Mapped[str] = mapped_column(String(32), nullable=False)
    correlation_id: Mapped[str] = mapped_column(String(64), nullable=False)
    rule_version: Mapped[str | None] = mapped_column(String(64))
    details: Mapped[dict[str, Any]] = mapped_column(json_type, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    actor: Mapped[User | None] = relationship()
    company: Mapped[Company | None] = relationship()
    application: Mapped[Application | None] = relationship(foreign_keys=[application_id])
