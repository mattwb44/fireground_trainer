"""add tags and scenario_tags tables

Revision ID: f1a2b3c4d5e6
Revises: e5b3a9d12f76
Create Date: 2026-05-17 00:00:00.000000

"""
from datetime import datetime

import sqlalchemy as sa
from alembic import op

revision = 'f1a2b3c4d5e6'
down_revision = 'e5b3a9d12f76'
branch_labels = None
depends_on = None

SEED_TAGS = [
    ("Commercial Structure", "commercial-structure"),
    ("Apartment Complex", "apartment-complex"),
    ("Duplex", "duplex"),
    ("Electric Vehicle", "electric-vehicle"),
    ("Extrication", "extrication"),
    ("Wildland Interface", "wildland-interface"),
    ("High-Rise", "high-rise"),
    ("Pediatric", "pediatric"),
    ("Trauma", "trauma"),
]


def _table_exists(bind, name: str) -> bool:
    return sa.inspect(bind).has_table(name)


def upgrade():
    bind = op.get_bind()

    if not _table_exists(bind, 'tags'):
        op.create_table(
            'tags',
            sa.Column('id', sa.Integer(), nullable=False),
            sa.Column('name', sa.String(length=80), nullable=False),
            sa.Column('slug', sa.String(length=80), nullable=False),
            sa.Column('is_active', sa.Boolean(), nullable=False),
            sa.Column('created_at', sa.DateTime(), nullable=False),
            sa.Column('updated_at', sa.DateTime(), nullable=False),
            sa.PrimaryKeyConstraint('id'),
            sa.UniqueConstraint('name'),
            sa.UniqueConstraint('slug'),
        )
        op.create_index('ix_tags_slug', 'tags', ['slug'])
        op.create_index('ix_tags_is_active', 'tags', ['is_active'])

        now = datetime.utcnow()
        tags_table = sa.table(
            'tags',
            sa.column('name', sa.String),
            sa.column('slug', sa.String),
            sa.column('is_active', sa.Boolean),
            sa.column('created_at', sa.DateTime),
            sa.column('updated_at', sa.DateTime),
        )
        bind.execute(
            tags_table.insert(),
            [
                {"name": name, "slug": slug, "is_active": True, "created_at": now, "updated_at": now}
                for name, slug in SEED_TAGS
            ],
        )

    if not _table_exists(bind, 'scenario_tags'):
        op.create_table(
            'scenario_tags',
            sa.Column('scenario_id', sa.Integer(), nullable=False),
            sa.Column('tag_id', sa.Integer(), nullable=False),
            sa.ForeignKeyConstraint(['scenario_id'], ['scenarios.id'], ondelete='CASCADE'),
            sa.ForeignKeyConstraint(['tag_id'], ['tags.id'], ondelete='CASCADE'),
            sa.PrimaryKeyConstraint('scenario_id', 'tag_id'),
        )


def downgrade():
    op.drop_table('scenario_tags')
    op.drop_index('ix_tags_is_active', table_name='tags')
    op.drop_index('ix_tags_slug', table_name='tags')
    op.drop_table('tags')
