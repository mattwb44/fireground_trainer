"""add scenario visibility columns (is_public, training_category, department_id, forked_from)

Revision ID: a4b5c6d7e8f9
Revises: f1a2b3c4d5e6
Create Date: 2026-05-17 00:00:00.000000

"""
import sqlalchemy as sa
from alembic import op

revision = 'a4b5c6d7e8f9'
down_revision = 'f1a2b3c4d5e6'
branch_labels = None
depends_on = None


def _column_exists(bind, table: str, column: str) -> bool:
    result = bind.execute(sa.text(f"PRAGMA table_info({table})")).fetchall()
    return any(row[1] == column for row in result)


def upgrade():
    bind = op.get_bind()

    new_columns = [
        ("is_public", "INTEGER NOT NULL DEFAULT 0"),
        ("training_category", "VARCHAR(20)"),
        ("department_id", "INTEGER"),
        ("forked_from_scenario_id", "INTEGER"),
    ]

    if any(not _column_exists(bind, "scenarios", col) for col, _ in new_columns):
        with op.batch_alter_table("scenarios") as batch_op:
            if not _column_exists(bind, "scenarios", "is_public"):
                batch_op.add_column(sa.Column("is_public", sa.Boolean(), nullable=False, server_default="0"))
                batch_op.create_index("ix_scenarios_is_public", ["is_public"])
            if not _column_exists(bind, "scenarios", "training_category"):
                batch_op.add_column(sa.Column("training_category", sa.String(length=20), nullable=True))
                batch_op.create_index("ix_scenarios_training_category", ["training_category"])
            if not _column_exists(bind, "scenarios", "department_id"):
                batch_op.add_column(sa.Column("department_id", sa.Integer(), nullable=True))
                batch_op.create_foreign_key(
                    "fk_scenarios_department_id",
                    "departments",
                    ["department_id"],
                    ["id"],
                    ondelete="SET NULL",
                )
                batch_op.create_index("ix_scenarios_department_id", ["department_id"])
            if not _column_exists(bind, "scenarios", "forked_from_scenario_id"):
                batch_op.add_column(sa.Column("forked_from_scenario_id", sa.Integer(), nullable=True))

    # Always run: existing approved scenarios become public so they stay visible
    bind.execute(
        sa.text("UPDATE scenarios SET is_public = 1, training_category = 'fireground' WHERE status = 'approved'")
    )


def downgrade():
    with op.batch_alter_table("scenarios") as batch_op:
        batch_op.drop_column("forked_from_scenario_id")
        batch_op.drop_index("ix_scenarios_department_id")
        batch_op.drop_constraint("fk_scenarios_department_id", type_="foreignkey")
        batch_op.drop_column("department_id")
        batch_op.drop_index("ix_scenarios_training_category")
        batch_op.drop_column("training_category")
        batch_op.drop_index("ix_scenarios_is_public")
        batch_op.drop_column("is_public")
