import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations
from test_models import migration_config, seed_application

from alembic import command


@pytest.mark.parametrize('legacy', [True, False])
def test_staff_comments_repair_preserves_rows(tmp_path, legacy: bool) -> None:
    engine = sa.create_engine(f'sqlite+pysqlite:///{tmp_path / "comments.sqlite"}')
    try:
        with engine.connect() as connection:
            command.upgrade(migration_config(connection), '0006_staff_comments')
        user_id, app_id, _ = seed_application(engine)
        with engine.begin() as connection:
            if legacy:
                connection.execute(sa.text('DROP TABLE staff_comments'))
                connection.execute(
                    sa.text(
                        """CREATE TABLE staff_comments (
                    id INTEGER PRIMARY KEY,
                    application_id INTEGER NOT NULL REFERENCES applications(id),
                    author_user_id INTEGER NOT NULL REFERENCES users(id),
                    body VARCHAR(2000) NOT NULL,
                    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    CONSTRAINT ck_staff_comments_body_nonempty CHECK (length(trim(body)) > 0))"""
                    )
                )
                connection.execute(
                    sa.text(
                        'CREATE INDEX ix_staff_comments_application_created '
                        'ON staff_comments(application_id, created_at)'
                    )
                )
            else:
                # Also exercise the revised 0006 deployed on some local databases.
                operations = Operations(MigrationContext.configure(connection))
                with operations.batch_alter_table('staff_comments') as batch:
                    batch.alter_column('body', existing_type=sa.String(2000), type_=sa.Text())
                    batch.drop_constraint('ck_staff_comments_body_nonempty', type_='check')
                    batch.create_check_constraint(
                        'ck_staff_comments_body_length', 'length(trim(body)) BETWEEN 1 AND 2000'
                    )
                    batch.drop_index('ix_staff_comments_application_created')
                    batch.create_index(
                        'ix_staff_comments_application_id_id', ['application_id', 'id']
                    )
            connection.execute(
                sa.text(
                    'INSERT INTO staff_comments '
                    '(id, application_id, author_user_id, body) VALUES (1, :app, :user, :body)'
                ),
                {'app': app_id, 'user': user_id, 'body': 'Сохранённый комментарий'},
            )
            before = connection.execute(sa.text('SELECT * FROM staff_comments')).all()
        with engine.connect() as connection:
            command.upgrade(migration_config(connection), 'head')
            command.upgrade(migration_config(connection), 'head')
            inspector = sa.inspect(connection)
            assert connection.execute(sa.text('SELECT * FROM staff_comments')).all() == before
            columns = {c['name']: c for c in inspector.get_columns('staff_comments')}
            assert isinstance(columns['body']['type'], sa.Text)
            assert not columns['body']['nullable']
            assert {c['name'] for c in inspector.get_check_constraints('staff_comments')} == {
                'ck_staff_comments_body_length'
            }
            assert {i['name'] for i in inspector.get_indexes('staff_comments')} == {
                'ix_staff_comments_application_id_id'
            }
            assert len(inspector.get_foreign_keys('staff_comments')) == 2
            command.downgrade(migration_config(connection), '0006_staff_comments')
            inspector = sa.inspect(connection)
            assert connection.execute(sa.text('SELECT * FROM staff_comments')).all() == before
            assert {i['name'] for i in inspector.get_indexes('staff_comments')} == {
                'ix_staff_comments_application_created'
            }
            assert {c['name'] for c in inspector.get_check_constraints('staff_comments')} == {
                'ck_staff_comments_body_nonempty'
            }
            command.upgrade(migration_config(connection), 'head')
            assert connection.execute(sa.text('SELECT * FROM staff_comments')).all() == before
    finally:
        engine.dispose()
