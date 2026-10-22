"""The Jobs module: discover postings, store them once, and (later) understand and rank them."""

from careerhub.modules.jobs.ashby import fetch_ashby, ingest_ashby
from careerhub.modules.jobs.greenhouse import fetch_greenhouse, ingest_greenhouse
from careerhub.modules.jobs.ingest import FetchError
from careerhub.modules.jobs.lever import fetch_lever, ingest_lever
from careerhub.modules.jobs.muse import fetch_muse, ingest_muse
from careerhub.modules.jobs.sources import (
    SourceNotApproved,
    UnknownSource,
    approve_source,
    approved_sources,
    disable_source,
    list_sources,
    propose_source,
    reject_source,
    require_approved,
)
from careerhub.modules.jobs.store import IngestReport, NormalizedJob, dedupe_key, store_jobs

__all__ = [
    "FetchError",
    "IngestReport",
    "NormalizedJob",
    "SourceNotApproved",
    "UnknownSource",
    "approve_source",
    "approved_sources",
    "dedupe_key",
    "fetch_ashby",
    "fetch_greenhouse",
    "fetch_lever",
    "ingest_ashby",
    "fetch_muse",
    "ingest_greenhouse",
    "ingest_lever",
    "ingest_muse",
    "disable_source",
    "list_sources",
    "propose_source",
    "reject_source",
    "require_approved",
    "store_jobs",
]
