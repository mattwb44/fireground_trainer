"""add scenario_flags table

Revision ID: a1b2c3d4e5f6
Revises: f1a2b3c4d5e6
Create Date: 2026-05-23 00:00:00.000000

"""
import sqlalchemy as sa
from alembic import op

revision = 'a1b2c3d4e5f6'
down_revision = 'f1a2b3c4d5e6'
branch_labels = None
depends_on = None


def _table_exists(bind, name: str) -> bool:
    return sa.inspect(bind).has_table(name)


def upgrade():
    bind = op.get_bind()

    if not _table_exists(bind, 'scenario_flags'):
        op.create_table(
            'scenario_flags',
            sa.Column('id', sa.Integer(), nullable=False),
            sa.Column('scenario_id', sa.Integer(), nullable=False),
            sa.Column('user_id', sa.Integer(), nullable=False),
            sa.Column('reason', sa.String(length=50), nullable=True),
            sa.Column('created_at', sa.DateTime(), nullable=False),
            sa.Column('updated_at', sa.DateTime(), nullable=False),
            sa.ForeignKeyConstraint(['scenario_id'], ['scenarios.id'], ondelete='CASCADE'),
            sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
            sa.PrimaryKeyConstraint('id'),
            sa.UniqueConstraint('scenario_id', 'user_id', name='uq_scenario_flags_scenario_user'),
        )
        op.create_index('ix_scenario_flags_scenario', 'scenario_flags', ['scenario_id'])


def downgrade():
    op.drop_index('ix_scenario_flags_scenario', table_name='scenario_flags')
    op.drop_table('scenario_flags')
