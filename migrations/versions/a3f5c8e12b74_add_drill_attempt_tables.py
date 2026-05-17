"""add drill_attempts and drill_attempt_answers tables

Revision ID: a3f5c8e12b74
Revises: d4e2f8a91c35
Create Date: 2026-05-17 00:00:00.000000

"""
import sqlalchemy as sa
from alembic import op

revision = 'a3f5c8e12b74'
down_revision = 'd4e2f8a91c35'
branch_labels = None
depends_on = None


def _table_exists(bind, name: str) -> bool:
    result = bind.execute(
        sa.text("SELECT name FROM sqlite_master WHERE type='table' AND name=:n"),
        {"n": name},
    ).fetchone()
    return result is not None


def upgrade():
    bind = op.get_bind()
    if _table_exists(bind, 'drill_attempts') and _table_exists(bind, 'drill_attempt_answers'):
        return

    op.create_table(
        'drill_attempts',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('scenario_id', sa.Integer(), nullable=False),
        sa.Column('attempt_number', sa.Integer(), nullable=False),
        sa.Column('status', sa.String(length=20), nullable=False),
        sa.Column('submitted_at', sa.DateTime(), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['scenario_id'], ['scenarios.id'], ondelete='RESTRICT'),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('user_id', 'scenario_id', 'attempt_number'),
    )
    op.create_index('ix_drill_attempts_user_scenario', 'drill_attempts', ['user_id', 'scenario_id'])

    op.create_table(
        'drill_attempt_answers',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('drill_attempt_id', sa.Integer(), nullable=False),
        sa.Column('question_id', sa.Integer(), nullable=False),
        sa.Column('answer_text', sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(['drill_attempt_id'], ['drill_attempts.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['question_id'], ['questions.id'], ondelete='RESTRICT'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('drill_attempt_id', 'question_id'),
    )
    op.create_index('ix_drill_attempt_answers_question', 'drill_attempt_answers', ['question_id'])


def downgrade():
    op.drop_index('ix_drill_attempt_answers_question', table_name='drill_attempt_answers')
    op.drop_table('drill_attempt_answers')
    op.drop_index('ix_drill_attempts_user_scenario', table_name='drill_attempts')
    op.drop_table('drill_attempts')
