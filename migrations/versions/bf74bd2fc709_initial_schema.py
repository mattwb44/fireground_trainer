"""initial schema

Revision ID: bf74bd2fc709
Revises:
Create Date: 2026-05-11 16:22:00.424406

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'bf74bd2fc709'
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    # Drop the hand-rolled migration tracking table now that Alembic takes over.
    # Only exists on instances that were running before Flask-Migrate was introduced.
    conn = op.get_bind()
    if sa.inspect(conn).has_table('schema_migrations'):
        op.drop_table('schema_migrations')


def downgrade():
    op.create_table(
        'schema_migrations',
        sa.Column('name', sa.String(length=120), primary_key=True),
        sa.Column(
            'applied_at',
            sa.DateTime(),
            server_default=sa.text('(CURRENT_TIMESTAMP)'),
            nullable=False,
        ),
    )
