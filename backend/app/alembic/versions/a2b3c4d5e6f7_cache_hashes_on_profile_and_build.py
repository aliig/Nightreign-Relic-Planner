"""cache relics_hash on profile and build_hash on build

Revision ID: a2b3c4d5e6f7
Revises: f1a2b3c4d5e6
Create Date: 2026-06-07 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'a2b3c4d5e6f7'
down_revision = 'f1a2b3c4d5e6'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('profile', sa.Column('relics_hash', sa.String(length=64), nullable=True))
    op.add_column('build', sa.Column('build_hash', sa.String(length=64), nullable=True))


def downgrade():
    op.drop_column('build', 'build_hash')
    op.drop_column('profile', 'relics_hash')
