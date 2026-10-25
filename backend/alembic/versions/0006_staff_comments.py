"""Store internal staff comments separately from client messages."""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = '0006_staff_comments'
down_revision: str | Sequence[str] | None = '0005_contracts'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        'staff_comments',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('application_id', sa.Integer(), nullable=False),
        sa.Column('author_user_id', sa.Integer(), nullable=False),
        sa.Column('body', sa.String(length=2000), nullable=False),
        sa.Column(
            'created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(['application_id'], ['applications.id'], ondelete='RESTRICT'),
        sa.ForeignKeyConstraint(['author_user_id'], ['users.id'], ondelete='RESTRICT'),
        sa.CheckConstraint('length(trim(body)) > 0', name='ck_staff_comments_body_nonempty'),
    )
    op.create_index(
        'ix_staff_comments_application_created', 'staff_comments', ['application_id', 'created_at']
    )


def downgrade() -> None:
    op.drop_index('ix_staff_comments_application_created', table_name='staff_comments')
    op.drop_table('staff_comments')
