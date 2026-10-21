"""The Jobs module: discover postings, store them once, and (later) understand and rank them."""

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
    "IngestReport",
    "NormalizedJob",
    "SourceNotApproved",
    "UnknownSource",
    "approve_source",
    "approved_sources",
    "dedupe_key",
    "disable_source",
    "list_sources",
    "propose_source",
    "reject_source",
    "require_approved",
    "store_jobs",
]
