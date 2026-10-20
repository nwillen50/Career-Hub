"""Database migration 6469abc261b6.

core tables match prd

Previous migration: '65854b590acb'
Created: 2026-10-20 17:36:14.561414
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa  # noqa: F401  (used by most migrations, not all)
from alembic import op  # noqa: F401  (used by most migrations, not all)

import careerhub.core.models.base  # noqa: F401  (autogenerate writes UTCDateTime columns as this)

revision: str = "6469abc261b6"
down_revision: str | None = "65854b590acb"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Apply this migration.

    Edited by hand after autogenerate: new NOT NULL columns get defaults so existing rows stay
    valid, `company.domain` is carried into `domains`, proposals marked `dismissed` become
    `rejected`, and the new CHECK constraints are added.
    """
    with op.batch_alter_table("company", schema=None) as batch_op:
        batch_op.add_column(sa.Column("domains", sa.JSON(), nullable=False, server_default="[]"))
        batch_op.add_column(
            sa.Column("ats_board_ids", sa.JSON(), nullable=False, server_default="{}")
        )
        batch_op.add_column(sa.Column("size", sa.String(length=50), nullable=True))
        batch_op.add_column(sa.Column("industry", sa.String(length=100), nullable=True))
    op.execute("UPDATE company SET domains = json_array(domain) WHERE domain IS NOT NULL")
    with op.batch_alter_table("company", schema=None) as batch_op:
        batch_op.drop_column("domain")

    with op.batch_alter_table("interaction", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column("follow_up_due", careerhub.core.models.base.UTCDateTime(), nullable=True)
        )

    with op.batch_alter_table("person", schema=None) as batch_op:
        batch_op.add_column(sa.Column("how_known", sa.String(length=500), nullable=True))
        batch_op.add_column(
            sa.Column("last_contact_at", careerhub.core.models.base.UTCDateTime(), nullable=True)
        )

    with op.batch_alter_table("proposal", schema=None) as batch_op:
        batch_op.drop_constraint("status_valid", type_="check")
    op.execute("UPDATE proposal SET status = 'rejected' WHERE status = 'dismissed'")
    with op.batch_alter_table("proposal", schema=None) as batch_op:
        batch_op.add_column(sa.Column("origin", sa.String(length=20), nullable=True))
        batch_op.add_column(sa.Column("target", sa.String(length=20), nullable=True))
        batch_op.add_column(sa.Column("diff", sa.Text(), nullable=True))
        batch_op.create_check_constraint(
            "status_valid", "status IN ('pending', 'accepted', 'rejected')"
        )
        batch_op.create_check_constraint("origin_valid", "origin IN ('conversation', 'agent')")
        batch_op.create_check_constraint(
            "target_valid", "target IN ('preferences', 'rubric', 'source_list')"
        )

    with op.batch_alter_table("run_log", schema=None) as batch_op:
        batch_op.add_column(sa.Column("cost_usd", sa.Float(), nullable=False, server_default="0"))

    with op.batch_alter_table("source", schema=None) as batch_op:
        batch_op.add_column(sa.Column("config", sa.JSON(), nullable=False, server_default="{}"))
        batch_op.add_column(
            sa.Column("last_fetched_at", careerhub.core.models.base.UTCDateTime(), nullable=True)
        )
        batch_op.add_column(
            sa.Column("health", sa.String(length=20), nullable=False, server_default="unknown")
        )
        batch_op.create_check_constraint("health_valid", "health IN ('unknown', 'ok', 'failing')")


def downgrade() -> None:
    """Undo this migration. Only the first domain survives, since the old column held one."""
    with op.batch_alter_table("source", schema=None) as batch_op:
        batch_op.drop_constraint("health_valid", type_="check")
        batch_op.drop_column("health")
        batch_op.drop_column("last_fetched_at")
        batch_op.drop_column("config")

    with op.batch_alter_table("run_log", schema=None) as batch_op:
        batch_op.drop_column("cost_usd")

    with op.batch_alter_table("proposal", schema=None) as batch_op:
        batch_op.drop_constraint("target_valid", type_="check")
        batch_op.drop_constraint("origin_valid", type_="check")
        batch_op.drop_constraint("status_valid", type_="check")
    op.execute("UPDATE proposal SET status = 'dismissed' WHERE status = 'rejected'")
    with op.batch_alter_table("proposal", schema=None) as batch_op:
        batch_op.drop_column("diff")
        batch_op.drop_column("target")
        batch_op.drop_column("origin")
        batch_op.create_check_constraint(
            "status_valid", "status IN ('pending', 'accepted', 'dismissed')"
        )

    with op.batch_alter_table("person", schema=None) as batch_op:
        batch_op.drop_column("last_contact_at")
        batch_op.drop_column("how_known")

    with op.batch_alter_table("interaction", schema=None) as batch_op:
        batch_op.drop_column("follow_up_due")

    with op.batch_alter_table("company", schema=None) as batch_op:
        batch_op.add_column(sa.Column("domain", sa.VARCHAR(length=253), nullable=True))
    op.execute("UPDATE company SET domain = json_extract(domains, '$[0]')")
    with op.batch_alter_table("company", schema=None) as batch_op:
        batch_op.drop_column("industry")
        batch_op.drop_column("size")
        batch_op.drop_column("ats_board_ids")
        batch_op.drop_column("domains")
