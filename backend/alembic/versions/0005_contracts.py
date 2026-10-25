"""Company contract details and immutable accepted contract snapshots."""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = '0005_contracts'
down_revision: str | Sequence[str] | None = '0004_sign_code_attempts'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    for name, length in (
        ('registration_number', 15),
        ('legal_address', 255),
        ('settlement_account', 20),
        ('bank_bik', 9),
        ('bank_name', 200),
        ('correspondent_account', 20),
    ):
        op.add_column('companies', sa.Column(name, sa.String(length), nullable=True))
    if op.get_bind().dialect.name == 'postgresql':
        op.create_check_constraint(
            'ck_companies_registration_number',
            'companies',
            'registration_number IS NULL OR '
            "(legal_form = 'OOO' AND length(registration_number) = 13) "
            "OR (legal_form = 'IP' AND length(registration_number) = 15)",
        )
        for name, length in (
            ('settlement_account', 20),
            ('bank_bik', 9),
            ('correspondent_account', 20),
        ):
            op.create_check_constraint(
                f'ck_companies_{name}',
                'companies',
                f'{name} IS NULL OR length({name}) = {length}',
            )
    op.create_table(
        'credit_contracts',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('offer_id', sa.Integer(), nullable=False, unique=True),
        sa.Column('application_id', sa.Integer(), nullable=False),
        sa.Column('schema_version', sa.Integer(), server_default='1', nullable=False),
        sa.Column(
            'terms', sa.JSON().with_variant(postgresql.JSONB(), 'postgresql'), nullable=False
        ),
        sa.Column(
            'created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(['offer_id'], ['offers.id'], ondelete='RESTRICT'),
        sa.ForeignKeyConstraint(['application_id'], ['applications.id'], ondelete='RESTRICT'),
        sa.UniqueConstraint('application_id', name='uq_credit_contracts_application'),
        sa.CheckConstraint('schema_version = 1', name='ck_credit_contracts_schema_version'),
    )


def downgrade() -> None:
    op.drop_table('credit_contracts')
    if op.get_bind().dialect.name == 'postgresql':
        for name in (
            'registration_number',
            'settlement_account',
            'bank_bik',
            'correspondent_account',
        ):
            op.drop_constraint(f'ck_companies_{name}', 'companies', type_='check')
    for name in (
        'registration_number',
        'legal_address',
        'settlement_account',
        'bank_bik',
        'bank_name',
        'correspondent_account',
    ):
        op.drop_column('companies', name)
