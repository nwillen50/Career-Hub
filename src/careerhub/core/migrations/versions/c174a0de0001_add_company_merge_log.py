"""Database migration c174a0de0001.

add company merge log

Previous migration: 'b3c7a1d4e902'
Created: 2026-10-24
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c174a0de0001"
down_revision: str | None = "b3c7a1d4e902"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Apply this migration."""
    with op.batch_alter_table("company", schema=None) as batch_op:
        batch_op.add_column(sa.Column("merge_log", sa.JSON(), nullable=True))


def downgrade() -> None:
    """Undo this migration."""
    with op.batch_alter_table("company", schema=None) as batch_op:
        batch_op.drop_column("merge_log")
