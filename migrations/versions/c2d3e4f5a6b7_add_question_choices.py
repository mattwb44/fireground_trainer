"""add question_choices table and selected_choice_id to answers

Revision ID: c2d3e4f5a6b7
Revises: b1c2d3e4f5a6
Create Date: 2026-05-17 00:00:00.000000

"""
import sqlalchemy as sa
from alembic import op

revision = 'c2d3e4f5a6b7'
down_revision = 'b1c2d3e4f5a6'
branch_labels = None
depends_on = None


def _table_exists(bind, table: str) -> bool:
    return sa.inspect(bind).has_table(table)


def _column_exists(bind, table: str, column: str) -> bool:
    inspector = sa.inspect(bind)
    if not inspector.has_table(table):
        return False
    return any(col['name'] == column for col in inspector.get_columns(table))


def upgrade():
    bind = op.get_bind()

    if not _table_exists(bind, "question_choices"):
        op.create_table(
            "question_choices",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("question_id", sa.Integer(), sa.ForeignKey("questions.id", ondelete="CASCADE"), nullable=False),
            sa.Column("choice_text", sa.Text(), nullable=False),
            sa.Column("is_correct", sa.Boolean(), nullable=False, server_default="0"),
            sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
        )
        op.create_index("ix_question_choices_question", "question_choices", ["question_id"])

    if not _column_exists(bind, "submission_answers", "selected_choice_id"):
        with op.batch_alter_table("submission_answers") as batch_op:
            batch_op.add_column(sa.Column("selected_choice_id", sa.Integer(), nullable=True))
            batch_op.create_foreign_key(
                "fk_submission_answers_choice",
                "question_choices",
                ["selected_choice_id"],
                ["id"],
                ondelete="SET NULL",
            )

    if not _column_exists(bind, "drill_attempt_answers", "selected_choice_id"):
        with op.batch_alter_table("drill_attempt_answers") as batch_op:
            batch_op.add_column(sa.Column("selected_choice_id", sa.Integer(), nullable=True))
            batch_op.create_foreign_key(
                "fk_drill_attempt_answers_choice",
                "question_choices",
                ["selected_choice_id"],
                ["id"],
                ondelete="SET NULL",
            )


def downgrade():
    with op.batch_alter_table("drill_attempt_answers") as batch_op:
        batch_op.drop_constraint("fk_drill_attempt_answers_choice", type_="foreignkey")
        batch_op.drop_column("selected_choice_id")

    with op.batch_alter_table("submission_answers") as batch_op:
        batch_op.drop_constraint("fk_submission_answers_choice", type_="foreignkey")
        batch_op.drop_column("selected_choice_id")

    op.drop_index("ix_question_choices_question", "question_choices")
    op.drop_table("question_choices")
