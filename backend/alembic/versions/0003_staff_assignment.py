"""Assign manual-review applications to a staff user.

Revision ID: 0003_staff_assignment
Revises: 0002_credit_schema
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = '0003_staff_assignment'
down_revision: str | Sequence[str] | None = '0002_credit_schema'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table('applications') as batch:
        batch.add_column(sa.Column('assigned_staff_user_id', sa.Integer(), nullable=True))
        batch.create_foreign_key(
            'fk_applications_assigned_staff_user_id',
            'users',
            ['assigned_staff_user_id'],
            ['id'],
            ondelete='RESTRICT',
        )
    op.create_index(
        'ix_applications_assigned_staff_user_id', 'applications', ['assigned_staff_user_id']
    )
    if op.get_bind().dialect.name == 'sqlite':
        op.execute(
            sa.text(
                'UPDATE applications SET assigned_staff_user_id = ('
                "SELECT id FROM users WHERE login = 'staff1' AND role = 'STAFF' "
                "AND is_active IN (1, 'true')"
                ") WHERE status = 'manual_review'"
            )
        )
    else:
        op.execute(
            sa.text(
                'UPDATE applications SET assigned_staff_user_id = ('
                "SELECT id FROM users WHERE login = 'staff1' AND role = 'STAFF' "
                'AND is_active = true'
                ") WHERE status = 'manual_review'"
            )
        )


def downgrade() -> None:
    op.drop_index('ix_applications_assigned_staff_user_id', table_name='applications')
    with op.batch_alter_table('applications') as batch:
        batch.drop_constraint('fk_applications_assigned_staff_user_id', type_='foreignkey')
        batch.drop_column('assigned_staff_user_id')
