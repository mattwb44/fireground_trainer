"""add session_board_states table

Revision ID: c3d4e5f6a7b8
Revises: b2c3d4e5f6a7
Create Date: 2026-05-23 00:02:00.000000

"""
import sqlalchemy as sa
from alembic import op

revision = 'c3d4e5f6a7b8'
down_revision = 'b2c3d4e5f6a7'
branch_labels = None
depends_on = None


def _table_exists(bind, name: str) -> bool:
    return sa.inspect(bind).has_table(name)


def upgrade():
    bind = op.get_bind()

    if not _table_exists(bind, 'session_board_states'):
        op.create_table(
            'session_board_states',
            sa.Column('id', sa.Integer(), nullable=False),
            sa.Column('training_session_id', sa.Integer(), nullable=False),
            sa.Column('state_json', sa.Text(), nullable=False),
            sa.Column('updated_at', sa.DateTime(), nullable=False),
            sa.ForeignKeyConstraint(['training_session_id'], ['training_sessions.id'], ondelete='CASCADE'),
            sa.PrimaryKeyConstraint('id'),
            sa.UniqueConstraint('training_session_id'),
        )
        op.create_index(
            'ix_session_board_states_session_id',
            'session_board_states',
            ['training_session_id'],
        )


def downgrade():
    op.drop_index('ix_session_board_states_session_id', table_name='session_board_states')
    op.drop_table('session_board_states')
