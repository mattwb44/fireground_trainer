"""add departments table and user.department_id

Revision ID: e5b3a9d12f76
Revises: a3f5c8e12b74
Create Date: 2026-05-17 00:00:00.000000

"""
import sqlalchemy as sa
from alembic import op

revision = 'e5b3a9d12f76'
down_revision = 'a3f5c8e12b74'
branch_labels = None
depends_on = None


def _table_exists(bind, name: str) -> bool:
    return sa.inspect(bind).has_table(name)


def _column_exists(bind, table: str, column: str) -> bool:
    inspector = sa.inspect(bind)
    if not inspector.has_table(table):
        return False
    return any(col['name'] == column for col in inspector.get_columns(table))


def upgrade():
    bind = op.get_bind()

    if not _table_exists(bind, 'departments'):
        op.create_table(
            'departments',
            sa.Column('id', sa.Integer(), nullable=False),
            sa.Column('name', sa.String(length=200), nullable=False),
            sa.Column('invite_code', sa.String(length=32), nullable=False),
            sa.Column('created_by_user_id', sa.Integer(), nullable=True),
            sa.Column('created_at', sa.DateTime(), nullable=False),
            sa.Column('updated_at', sa.DateTime(), nullable=False),
            sa.ForeignKeyConstraint(['created_by_user_id'], ['users.id'], ondelete='SET NULL'),
            sa.PrimaryKeyConstraint('id'),
            sa.UniqueConstraint('invite_code'),
        )
        op.create_index('ix_departments_invite_code', 'departments', ['invite_code'])

    if not _column_exists(bind, 'users', 'department_id'):
        with op.batch_alter_table('users') as batch_op:
            batch_op.add_column(sa.Column('department_id', sa.Integer(), nullable=True))
            batch_op.create_foreign_key(
                'fk_users_department_id',
                'departments',
                ['department_id'],
                ['id'],
                ondelete='SET NULL',
            )
            batch_op.create_index('ix_users_department_id', ['department_id'])


def downgrade():
    with op.batch_alter_table('users') as batch_op:
        batch_op.drop_index('ix_users_department_id')
        batch_op.drop_constraint('fk_users_department_id', type_='foreignkey')
        batch_op.drop_column('department_id')

    op.drop_index('ix_departments_invite_code', table_name='departments')
    op.drop_table('departments')
