"""drop stale whole-submission reveal columns from training_sessions

Revision ID: d4e2f8a91c35
Revises: 3a91c7fe2d04
Create Date: 2026-05-17 00:00:00.000000

"""
import sqlalchemy as sa
from alembic import op

revision = 'd4e2f8a91c35'
down_revision = '3a91c7fe2d04'
branch_labels = None
depends_on = None


def _column_names(bind, table_name: str) -> set:
    result = bind.execute(sa.text(f"PRAGMA table_info({table_name})"))
    return {row[1] for row in result}


def upgrade():
    bind = op.get_bind()
    cols = _column_names(bind, "training_sessions")
    stale = [c for c in ("revealed_submission_id", "reveal_mode", "revealed_at") if c in cols]
    if not stale:
        return

    with op.batch_alter_table("training_sessions") as batch_op:
        for col in stale:
            batch_op.drop_column(col)


def downgrade():
    bind = op.get_bind()
    cols = _column_names(bind, "training_sessions")

    with op.batch_alter_table("training_sessions") as batch_op:
        if "revealed_at" not in cols:
            batch_op.add_column(sa.Column("revealed_at", sa.DateTime(), nullable=True))
        if "reveal_mode" not in cols:
            batch_op.add_column(sa.Column("reveal_mode", sa.String(length=20), nullable=True))
        if "revealed_submission_id" not in cols:
            batch_op.add_column(sa.Column("revealed_submission_id", sa.Integer(), nullable=True))
