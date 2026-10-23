"""Database migration b3c7a1d4e902.

add interaction follow up done

Previous migration: '9d3944bd0e22'
Created: 2026-10-23 08:20:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa  # noqa: F401  (used by most migrations, not all)
from alembic import op  # noqa: F401  (used by most migrations, not all)

import careerhub.core.models.base  # noqa: F401  (autogenerate writes UTCDateTime columns as this)

revision: str = "b3c7a1d4e902"
down_revision: str | None = "9d3944bd0e22"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Apply this migration."""
    with op.batch_alter_table("interaction", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column("follow_up_done", sa.Boolean(), server_default="0", nullable=False)
        )


def downgrade() -> None:
    """Undo this migration."""
    with op.batch_alter_table("interaction", schema=None) as batch_op:
        batch_op.drop_column("follow_up_done")
