"""Career Hub's tables.

Every table subclasses `Base`. Importing this package must import every model module, so that
`Base.metadata` lists every table; migrations and the drift check rely on it.
"""

from careerhub.core.models.application import APPLICATION_STATUSES, Application
from careerhub.core.models.assessment import Assessment
from careerhub.core.models.base import (
    NAMING_CONVENTION,
    Base,
    TimestampMixin,
    UTCDateTime,
    utcnow,
)
from careerhub.core.models.conversation import ROLES, ConversationMessage
from careerhub.core.models.core_tables import (
    Company,
    Interaction,
    Person,
    Proposal,
    RunLog,
    Source,
)
from careerhub.core.models.feedback import REACTIONS, REASONS, Feedback
from careerhub.core.models.job import JOB_STATUSES, Job
from careerhub.core.models.llm_cache import LLMCacheEntry
from careerhub.core.models.llm_usage import LLMUsage
from careerhub.core.models.profile_extraction import ProfileExtraction

__all__ = [
    "APPLICATION_STATUSES",
    "Application",
    "Assessment",
    "NAMING_CONVENTION",
    "Base",
    "Company",
    "ConversationMessage",
    "Feedback",
    "JOB_STATUSES",
    "Interaction",
    "Job",
    "LLMCacheEntry",
    "LLMUsage",
    "Person",
    "REACTIONS",
    "ROLES",
    "REASONS",
    "ProfileExtraction",
    "Proposal",
    "RunLog",
    "Source",
    "TimestampMixin",
    "UTCDateTime",
    "utcnow",
]
