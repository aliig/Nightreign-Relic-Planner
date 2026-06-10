"""index hot foreign keys

Every relic/profile/build/snapshot query filters on these columns; without
indexes they are full table scans.  optimization_snapshot.build_id is
already covered by the (build_id, slot_index) unique constraint.

Revision ID: c4d5e6f7a8b9
Revises: b3c4d5e6f7a8
Create Date: 2026-06-09 00:00:00.000000

"""
from alembic import op


# revision identifiers, used by Alembic.
revision = 'c4d5e6f7a8b9'
down_revision = 'b3c4d5e6f7a8'
branch_labels = None
depends_on = None

_INDEXES = [
    ('relic', 'owner_id'),
    ('relic', 'profile_id'),
    ('profile', 'owner_id'),
    ('profile', 'save_upload_id'),
    ('save_upload', 'owner_id'),
    ('build', 'owner_id'),
    ('optimization_snapshot', 'owner_id'),
]


def upgrade():
    for table, column in _INDEXES:
        op.create_index(op.f(f'ix_{table}_{column}'), table, [column])


def downgrade():
    for table, column in reversed(_INDEXES):
        op.drop_index(op.f(f'ix_{table}_{column}'), table_name=table)
