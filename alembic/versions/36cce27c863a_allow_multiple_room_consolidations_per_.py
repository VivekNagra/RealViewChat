"""allow multiple room consolidations per room_type

Revision ID: 36cce27c863a
Revises: 453f1135957b
Create Date: 2026-05-29 21:49:26.681118

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '36cce27c863a'
down_revision: Union[str, Sequence[str], None] = '453f1135957b'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # rooms can legitimately have multiple consolidations per room_type
    # (pass2.5 chunks images in groups of 4), so the uniqueness is wrong.
    op.execute("ALTER TABLE rooms DROP CONSTRAINT IF EXISTS uq_rooms_property_room_type;")
    op.execute("DROP INDEX IF EXISTS ix_rooms_property_id;")
    # Defensive: an earlier create_all() may have produced an orphan
    # ix_rooms_property_room_type on the wrong table. Drop by name first
    # so the next CREATE lands on the intended table (rooms).
    op.execute("DROP INDEX IF EXISTS ix_rooms_property_room_type;")
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_rooms_property_room_type "
        "ON rooms (property_id, room_type);"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_rooms_property_room_type;")
    op.execute("CREATE INDEX IF NOT EXISTS ix_rooms_property_id ON rooms (property_id);")
    op.execute(
        "ALTER TABLE rooms ADD CONSTRAINT uq_rooms_property_room_type "
        "UNIQUE (property_id, room_type);"
    )
