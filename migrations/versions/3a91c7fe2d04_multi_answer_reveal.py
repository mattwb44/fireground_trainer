"""multi-answer reveal per question

Revision ID: 3a91c7fe2d04
Revises: bf74bd2fc709
Create Date: 2026-05-12 00:00:00.000000

"""
import sqlalchemy as sa
from alembic import op

revision = '3a91c7fe2d04'
down_revision = 'bf74bd2fc709'
branch_labels = None
depends_on = None


def _constraint_names(bind, table_name: str) -> set:
    inspector = sa.inspect(bind)
    if not inspector.has_table(table_name):
        return set()
    return {uc['name'] for uc in inspector.get_unique_constraints(table_name)}


def upgrade():
    bind = op.get_bind()
    names = _constraint_names(bind, "session_question_reveals")
    has_old = "uq_session_question_reveals_session_question" in names
    has_new = "uq_session_question_reveals_session_answer" in names
    if has_new and not has_old:
        return

    with op.batch_alter_table("session_question_reveals") as batch_op:
        if has_old:
            batch_op.drop_constraint(
                'uq_session_question_reveals_session_question',
                type_='unique',
            )
        if not has_new:
            batch_op.create_unique_constraint(
                'uq_session_question_reveals_session_answer',
                ['training_session_id', 'submission_answer_id'],
            )


def downgrade():
    bind = op.get_bind()
    names = _constraint_names(bind, "session_question_reveals")
    has_old = "uq_session_question_reveals_session_question" in names
    has_new = "uq_session_question_reveals_session_answer" in names
    if has_old and not has_new:
        return

    with op.batch_alter_table("session_question_reveals") as batch_op:
        if has_new:
            batch_op.drop_constraint(
                'uq_session_question_reveals_session_answer',
                type_='unique',
            )
        if not has_old:
            batch_op.create_unique_constraint(
                'uq_session_question_reveals_session_question',
                ['training_session_id', 'question_id'],
            )
