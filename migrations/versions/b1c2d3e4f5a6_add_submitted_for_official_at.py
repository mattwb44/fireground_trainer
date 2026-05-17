"""add submitted_for_official_at to scenarios

Revision ID: b1c2d3e4f5a6
Revises: a4b5c6d7e8f9
Create Date: 2026-05-17 00:00:00.000000

"""
import sqlalchemy as sa
from alembic import op

revision = 'b1c2d3e4f5a6'
down_revision = 'a4b5c6d7e8f9'
branch_labels = None
depends_on = None


def _column_exists(bind, table: str, column: str) -> bool:
    result = bind.execute(sa.text(f"PRAGMA table_info({table})")).fetchall()
    return any(row[1] == column for row in result)


def upgrade():
    bind = op.get_bind()
    if not _column_exists(bind, "scenarios", "submitted_for_official_at"):
        with op.batch_alter_table("scenarios") as batch_op:
            batch_op.add_column(sa.Column("submitted_for_official_at", sa.DateTime(), nullable=True))


def downgrade():
    with op.batch_alter_table("scenarios") as batch_op:
        batch_op.drop_column("submitted_for_official_at")
