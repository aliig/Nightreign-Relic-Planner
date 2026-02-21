"""Add planner tables: save_upload, character_slot, relic, build; drop item

Revision ID: a1b2c3d4e5f6
Revises: 1a31ce608336
Create Date: 2026-02-21 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa
import sqlmodel.sql.sqltypes


# revision identifiers, used by Alembic.
revision = 'a1b2c3d4e5f6'
down_revision = '1a31ce608336'
branch_labels = None
depends_on = None


def upgrade():
    # Drop the template placeholder item table
    op.drop_table('item')

    # Remove item relationship from user (no FK on user side, nothing to do)

    # --- save_upload ---
    op.create_table(
        'save_upload',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('owner_id', sa.UUID(), nullable=False),
        sa.Column('platform', sqlmodel.sql.sqltypes.AutoString(length=10), nullable=False),
        sa.Column('uploaded_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('character_count', sa.Integer(), nullable=False, server_default='0'),
        sa.ForeignKeyConstraint(['owner_id'], ['user.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )

    # --- character_slot ---
    op.create_table(
        'character_slot',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('owner_id', sa.UUID(), nullable=False),
        sa.Column('save_upload_id', sa.UUID(), nullable=False),
        sa.Column('slot_index', sa.Integer(), nullable=False),
        sa.Column('name', sqlmodel.sql.sqltypes.AutoString(length=100), nullable=False),
        sa.ForeignKeyConstraint(['owner_id'], ['user.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['save_upload_id'], ['save_upload.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )

    # --- relic ---
    op.create_table(
        'relic',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('owner_id', sa.UUID(), nullable=False),
        sa.Column('character_id', sa.UUID(), nullable=False),
        sa.Column('ga_handle', sa.BigInteger(), nullable=False),
        sa.Column('item_id', sa.BigInteger(), nullable=False),
        sa.Column('real_id', sa.BigInteger(), nullable=False),
        sa.Column('color', sqlmodel.sql.sqltypes.AutoString(length=10), nullable=False),
        sa.Column('effect_1', sa.BigInteger(), nullable=False),
        sa.Column('effect_2', sa.BigInteger(), nullable=False),
        sa.Column('effect_3', sa.BigInteger(), nullable=False),
        sa.Column('curse_1', sa.BigInteger(), nullable=False),
        sa.Column('curse_2', sa.BigInteger(), nullable=False),
        sa.Column('curse_3', sa.BigInteger(), nullable=False),
        sa.Column('is_deep', sa.Boolean(), nullable=False),
        sa.Column('name', sqlmodel.sql.sqltypes.AutoString(length=255), nullable=False),
        sa.Column('tier', sqlmodel.sql.sqltypes.AutoString(length=20), nullable=False),
        sa.ForeignKeyConstraint(['character_id'], ['character_slot.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['owner_id'], ['user.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )

    # --- build ---
    op.create_table(
        'build',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('owner_id', sa.UUID(), nullable=False),
        sa.Column('name', sqlmodel.sql.sqltypes.AutoString(length=255), nullable=False),
        sa.Column('character', sqlmodel.sql.sqltypes.AutoString(length=50), nullable=False),
        sa.Column('tiers', sa.JSON(), nullable=False),
        sa.Column('family_tiers', sa.JSON(), nullable=False),
        sa.Column('include_deep', sa.Boolean(), nullable=False, server_default='true'),
        sa.Column('curse_max', sa.Integer(), nullable=False, server_default='1'),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(['owner_id'], ['user.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )


def downgrade():
    op.drop_table('build')
    op.drop_table('relic')
    op.drop_table('character_slot')
    op.drop_table('save_upload')

    # Recreate item table
    op.create_table(
        'item',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('title', sqlmodel.sql.sqltypes.AutoString(length=255), nullable=False),
        sa.Column('description', sqlmodel.sql.sqltypes.AutoString(length=255), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('owner_id', sa.UUID(), nullable=False),
        sa.ForeignKeyConstraint(['owner_id'], ['user.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
