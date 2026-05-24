"""Add participant permissions (kicked_at, is_cohost, can_move_tokens)

Revision ID: f2a3b4c5d6e7
Revises: c3d4e5f6a7b8
Create Date: 2026-05-23
"""
from alembic import op
import sqlalchemy as sa

revision = 'f2a3b4c5d6e7'
down_revision = 'c3d4e5f6a7b8'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('participants', sa.Column('kicked_at', sa.DateTime(), nullable=True))
    op.add_column('participants', sa.Column('is_cohost', sa.Boolean(), nullable=False, server_default=sa.false()))
    op.add_column('participants', sa.Column('can_move_tokens', sa.Boolean(), nullable=False, server_default=sa.false()))


def downgrade():
    op.drop_column('participants', 'can_move_tokens')
    op.drop_column('participants', 'is_cohost')
    op.drop_column('participants', 'kicked_at')
