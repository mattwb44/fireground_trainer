"""add user_lists and user_list_scenarios tables

Revision ID: d9e0f1a2b3c4
Revises: c8ced6e97bb2
Create Date: 2026-05-17 23:30:00.000000

"""
from alembic import op
import sqlalchemy as sa


revision = 'd9e0f1a2b3c4'
down_revision = 'c8ced6e97bb2'
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing = inspector.get_table_names()

    if 'user_lists' not in existing:
        op.create_table(
            'user_lists',
            sa.Column('id', sa.Integer(), primary_key=True),
            sa.Column('user_id', sa.Integer(), nullable=False),
            sa.Column('name', sa.String(length=100), nullable=False),
            sa.Column('created_at', sa.DateTime(), nullable=False),
            sa.Column('updated_at', sa.DateTime(), nullable=False),
            sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        )
        op.create_index('ix_user_lists_user_created', 'user_lists', ['user_id', 'created_at'])

    if 'user_list_scenarios' not in existing:
        op.create_table(
            'user_list_scenarios',
            sa.Column('list_id', sa.Integer(), nullable=False),
            sa.Column('scenario_id', sa.Integer(), nullable=False),
            sa.Column('added_at', sa.DateTime(), nullable=False),
            sa.ForeignKeyConstraint(['list_id'], ['user_lists.id'], ondelete='CASCADE'),
            sa.ForeignKeyConstraint(['scenario_id'], ['scenarios.id'], ondelete='CASCADE'),
            sa.PrimaryKeyConstraint('list_id', 'scenario_id'),
            sa.UniqueConstraint('list_id', 'scenario_id', name='uq_user_list_scenarios'),
        )


def downgrade():
    op.drop_table('user_list_scenarios')
    op.drop_index('ix_user_lists_user_created', table_name='user_lists')
    op.drop_table('user_lists')
