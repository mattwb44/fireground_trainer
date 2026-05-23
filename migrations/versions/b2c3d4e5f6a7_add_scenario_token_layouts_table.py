"""add scenario_token_layouts table

Revision ID: b2c3d4e5f6a7
Revises: a1b2c3d4e5f6
Create Date: 2026-05-23 00:01:00.000000

"""
import sqlalchemy as sa
from alembic import op

revision = 'b2c3d4e5f6a7'
down_revision = 'a1b2c3d4e5f6'
branch_labels = None
depends_on = None


def _table_exists(bind, name: str) -> bool:
    return sa.inspect(bind).has_table(name)


def upgrade():
    bind = op.get_bind()

    if not _table_exists(bind, 'scenario_token_layouts'):
        op.create_table(
            'scenario_token_layouts',
            sa.Column('id', sa.Integer(), nullable=False),
            sa.Column('scenario_id', sa.Integer(), nullable=False),
            sa.Column('layout_json', sa.Text(), nullable=False),
            sa.Column('created_at', sa.DateTime(), nullable=False),
            sa.Column('updated_at', sa.DateTime(), nullable=False),
            sa.ForeignKeyConstraint(['scenario_id'], ['scenarios.id'], ondelete='CASCADE'),
            sa.PrimaryKeyConstraint('id'),
            sa.UniqueConstraint('scenario_id'),
        )
        op.create_index('ix_scenario_token_layouts_scenario_id', 'scenario_token_layouts', ['scenario_id'])


def downgrade():
    op.drop_index('ix_scenario_token_layouts_scenario_id', table_name='scenario_token_layouts')
    op.drop_table('scenario_token_layouts')
