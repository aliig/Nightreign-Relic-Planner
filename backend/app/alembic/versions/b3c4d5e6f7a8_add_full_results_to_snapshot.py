"""add full_results to optimization_snapshot

GET /optimize/snapshot rebuilds complete VesselResults; top_layouts only
stores the compact diff baseline, so full results need their own column.

Revision ID: b3c4d5e6f7a8
Revises: a2b3c4d5e6f7
Create Date: 2026-06-09 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'b3c4d5e6f7a8'
down_revision = 'a2b3c4d5e6f7'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        'optimization_snapshot',
        sa.Column('full_results', sa.JSON(), nullable=False, server_default='[]'),
    )


def downgrade():
    op.drop_column('optimization_snapshot', 'full_results')
