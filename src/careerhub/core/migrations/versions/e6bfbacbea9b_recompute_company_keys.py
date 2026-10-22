"""Database migration e6bfbacbea9b.

recompute company keys and merge duplicates (CH-86)

Previous migration: 'e8414035bd90'
Created: 2026-10-22
"""

from __future__ import annotations

import json
from collections import defaultdict
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from careerhub.core.names import company_key

revision: str = "e6bfbacbea9b"
down_revision: str | None = "e8414035bd90"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _load(value: object, default: object) -> object:
    """Read a JSON column value that may come back as text."""
    if isinstance(value, str):
        return json.loads(value)
    return value if value is not None else default


def upgrade() -> None:
    """Give every company its new key; merge companies that now share one (keep the earliest).

    Uses `company_key` as it is today. If its rules change later, add a new migration; do not edit
    this one.
    """
    bind = op.get_bind()
    rows = bind.execute(sa.text("SELECT id, name FROM company ORDER BY id")).fetchall()
    groups: dict[str, list[int]] = defaultdict(list)
    for company_id, name in rows:
        groups[company_key(name) or f"company {company_id}"].append(company_id)
    for ids in groups.values():
        keep, *drop = ids
        for other in drop:
            for table in ("job", "person"):
                bind.execute(
                    sa.text(f"UPDATE {table} SET company_id = :keep WHERE company_id = :other"),  # noqa: S608
                    {"keep": keep, "other": other},
                )
            kept = bind.execute(
                sa.text("SELECT domains, ats_board_ids FROM company WHERE id = :i"), {"i": keep}
            ).one()
            gone = bind.execute(
                sa.text("SELECT domains, ats_board_ids FROM company WHERE id = :i"), {"i": other}
            ).one()
            domains = sorted({*_load(kept[0], []), *_load(gone[0], [])})  # type: ignore[misc]
            boards: dict[str, str] = {**_load(gone[1], {}), **_load(kept[1], {})}  # type: ignore[dict-item]
            bind.execute(
                sa.text("UPDATE company SET domains = :d, ats_board_ids = :b WHERE id = :i"),
                {"d": json.dumps(domains), "b": json.dumps(boards), "i": keep},
            )
            bind.execute(sa.text("DELETE FROM company WHERE id = :i"), {"i": other})
    for key, ids in groups.items():
        bind.execute(
            sa.text("UPDATE company SET name_key = :k WHERE id = :i"), {"k": key, "i": ids[0]}
        )


def downgrade() -> None:
    """Do nothing: merged companies cannot be split again."""
