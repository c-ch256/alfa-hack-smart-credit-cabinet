"""Normalize staff_comments after either deployed variant of revision 0006."""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = '0007_normalize_staff_comments'
down_revision: str | Sequence[str] | None = '0006_staff_comments'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    columns = {column['name']: column for column in inspector.get_columns('staff_comments')}
    checks = {check['name'] for check in inspector.get_check_constraints('staff_comments')}
    indexes = {index['name'] for index in inspector.get_indexes('staff_comments')}
    with op.batch_alter_table('staff_comments') as batch:
        if not isinstance(columns['body']['type'], sa.Text):
            batch.alter_column(
                'body', existing_type=columns['body']['type'], type_=sa.Text(), nullable=False
            )
        if 'ck_staff_comments_body_nonempty' in checks:
            batch.drop_constraint('ck_staff_comments_body_nonempty', type_='check')
        if 'ck_staff_comments_body_length' not in checks:
            batch.create_check_constraint(
                'ck_staff_comments_body_length', 'length(trim(body)) BETWEEN 1 AND 2000'
            )
        if 'ix_staff_comments_application_created' in indexes:
            batch.drop_index('ix_staff_comments_application_created')
        if 'ix_staff_comments_application_id_id' not in indexes:
            batch.create_index('ix_staff_comments_application_id_id', ['application_id', 'id'])


def downgrade() -> None:
    # Restore the original 0006 kept in main, including its index for further downgrades.
    with op.batch_alter_table('staff_comments') as batch:
        batch.drop_constraint('ck_staff_comments_body_length', type_='check')
        batch.alter_column(
            'body', existing_type=sa.Text(), type_=sa.String(length=2000), existing_nullable=False
        )
        batch.create_check_constraint('ck_staff_comments_body_nonempty', 'length(trim(body)) > 0')
        batch.drop_index('ix_staff_comments_application_id_id')
        batch.create_index(
            'ix_staff_comments_application_created', ['application_id', 'created_at']
        )
