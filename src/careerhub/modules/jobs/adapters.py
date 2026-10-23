"""The job-source adapters by kind, so the nightly run and `hub ingest` treat them alike."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Protocol

import httpx
from sqlalchemy.orm import Session

from careerhub.core.models import Source
from careerhub.modules.jobs.ashby import ingest_ashby
from careerhub.modules.jobs.greenhouse import ingest_greenhouse
from careerhub.modules.jobs.lever import ingest_lever
from careerhub.modules.jobs.muse import ingest_muse
from careerhub.modules.jobs.store import IngestReport


class Ingester(Protocol):
    """An adapter: fetch a source and store its jobs, matching company names with `aliases`."""

    def __call__(
        self,
        session: Session,
        source: Source,
        client: httpx.Client | None = None,
        aliases: Mapping[str, str] | None = None,
    ) -> IngestReport:
        """Fetch `source`, store its jobs, and report what changed."""
        ...


#: Source kind to the function that fetches and stores one approved source of that kind.
ADAPTERS: dict[str, Ingester] = {
    "greenhouse": ingest_greenhouse,
    "lever": ingest_lever,
    "ashby": ingest_ashby,
    "themuse": ingest_muse,
}
#: Kinds whose sources are named by a board in their address (the last part of the URL).
BOARD_KINDS = frozenset({"greenhouse", "lever", "ashby"})
