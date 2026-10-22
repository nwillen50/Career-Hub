"""The job-source adapters by kind, so the nightly run and `hub ingest` treat them alike."""

from __future__ import annotations

from collections.abc import Callable

import httpx
from sqlalchemy.orm import Session

from careerhub.core.models import Source
from careerhub.modules.jobs.ashby import ingest_ashby
from careerhub.modules.jobs.greenhouse import ingest_greenhouse
from careerhub.modules.jobs.lever import ingest_lever
from careerhub.modules.jobs.muse import ingest_muse
from careerhub.modules.jobs.store import IngestReport

Ingester = Callable[[Session, Source, httpx.Client | None], IngestReport]

#: Source kind to the function that fetches and stores one approved source of that kind.
ADAPTERS: dict[str, Ingester] = {
    "greenhouse": ingest_greenhouse,
    "lever": ingest_lever,
    "ashby": ingest_ashby,
    "themuse": ingest_muse,
}
#: Kinds whose sources are named by a board in their address (the last part of the URL).
BOARD_KINDS = frozenset({"greenhouse", "lever"})
