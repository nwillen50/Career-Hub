"""The Jobs module: discover postings, store them once, and (later) understand and rank them."""

from careerhub.modules.jobs.store import IngestReport, NormalizedJob, dedupe_key, store_jobs

__all__ = ["IngestReport", "NormalizedJob", "dedupe_key", "store_jobs"]
