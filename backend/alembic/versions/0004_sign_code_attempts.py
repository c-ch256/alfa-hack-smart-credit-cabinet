"""Persist demo signing attempt count.

Revision ID: 0004_sign_code_attempts
Revises: 0003_staff_assignment
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = '0004_sign_code_attempts'
down_revision: str | Sequence[str] | None = '0003_staff_assignment'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        'applications',
        sa.Column('sign_code_attempts', sa.Integer(), server_default='0', nullable=False),
    )


def downgrade() -> None:
    op.drop_column('applications', 'sign_code_attempts')
