"""add scenario_positions table

Revision ID: c8ced6e97bb2
Revises: c2d3e4f5a6b7
Create Date: 2026-05-17 22:47:06.760566

"""
from alembic import op
import sqlalchemy as sa


revision = 'c8ced6e97bb2'
down_revision = 'c2d3e4f5a6b7'
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if 'scenario_positions' not in inspector.get_table_names():
        op.create_table(
            'scenario_positions',
            sa.Column('scenario_id', sa.Integer(), nullable=False),
            sa.Column('position', sa.String(length=40), nullable=False),
            sa.ForeignKeyConstraint(['scenario_id'], ['scenarios.id'], ondelete='CASCADE'),
            sa.PrimaryKeyConstraint('scenario_id', 'position'),
        )


def downgrade():
    op.drop_table('scenario_positions')
