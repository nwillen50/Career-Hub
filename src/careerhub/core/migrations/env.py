"""Alembic's entry point, run for every migration command.

Career Hub never lets Alembic open the database itself: the encrypted connection comes from
`careerhub.core.migrate`, passed in through ``config.attributes["connection"]``.
"""

from __future__ import annotations

from alembic import context

from careerhub.core.models import Base

connection = context.config.attributes.get("connection")
if connection is None:
    raise RuntimeError(
        "Run migrations through careerhub.core.migrate (migrate() or new_revision()), "
        "which supplies the encrypted connection."
    )

context.configure(
    connection=connection,
    target_metadata=Base.metadata,
    # SQLite can't alter most things in place; batch mode rebuilds the table instead.
    render_as_batch=True,
    compare_type=True,
)

with context.begin_transaction():
    context.run_migrations()
