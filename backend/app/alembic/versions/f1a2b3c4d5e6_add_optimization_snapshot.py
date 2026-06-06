"""add optimization_snapshot table

Revision ID: f1a2b3c4d5e6
Revises: 2b1545b90ad5
Create Date: 2026-06-05 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa
import sqlmodel.sql.sqltypes


# revision identifiers, used by Alembic.
revision = 'f1a2b3c4d5e6'
down_revision = '2b1545b90ad5'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'optimization_snapshot',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('owner_id', sa.UUID(), nullable=False),
        sa.Column('build_id', sa.UUID(), nullable=False),
        sa.Column('slot_index', sa.Integer(), nullable=False),
        sa.Column('relics_hash', sqlmodel.sql.sqltypes.AutoString(length=64), nullable=False),
        sa.Column('build_hash', sqlmodel.sql.sqltypes.AutoString(length=64), nullable=False),
        sa.Column('game_data_version', sqlmodel.sql.sqltypes.AutoString(length=32), nullable=False),
        sa.Column('optimizer_version', sa.Integer(), nullable=False),
        sa.Column('top_layouts', sa.JSON(), nullable=False, server_default='[]'),
        sa.Column('best_score', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('any_truncated', sa.Boolean(), nullable=False, server_default='false'),
        sa.Column('last_change', sa.JSON(), nullable=True),
        sa.Column('reviewed', sa.Boolean(), nullable=False, server_default='false'),
        sa.Column('computed_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(['owner_id'], ['user.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['build_id'], ['build.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('build_id', 'slot_index', name='uq_optimization_snapshot_build_slot'),
    )


def downgrade():
    op.drop_table('optimization_snapshot')
