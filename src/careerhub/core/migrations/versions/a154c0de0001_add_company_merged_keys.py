"""Database migration a154c0de0001.

add company merged keys

Previous migration: '2366a1a4501b'
Created: 2026-10-23
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a154c0de0001"
down_revision: str | None = "2366a1a4501b"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Apply this migration."""
    with op.batch_alter_table("company", schema=None) as batch_op:
        batch_op.add_column(sa.Column("merged_keys", sa.JSON(), nullable=True))


def downgrade() -> None:
    """Undo this migration."""
    with op.batch_alter_table("company", schema=None) as batch_op:
        batch_op.drop_column("merged_keys")
