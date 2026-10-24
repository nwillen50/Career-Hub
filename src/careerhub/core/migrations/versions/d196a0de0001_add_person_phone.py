"""Database migration d196a0de0001.

add person phone

Previous migration: 'c174a0de0001'
Created: 2026-10-24
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "d196a0de0001"
down_revision: str | None = "c174a0de0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Apply this migration."""
    with op.batch_alter_table("person", schema=None) as batch_op:
        batch_op.add_column(sa.Column("phone", sa.String(length=40), nullable=True))


def downgrade() -> None:
    """Undo this migration."""
    with op.batch_alter_table("person", schema=None) as batch_op:
        batch_op.drop_column("phone")
