"""Career Hub's tables.

Every table subclasses `Base`. Importing this package must import every model module, so that
`Base.metadata` lists every table; migrations and the drift check rely on it.
"""

from careerhub.core.models.base import (
    NAMING_CONVENTION,
    Base,
    TimestampMixin,
    UTCDateTime,
    utcnow,
)

__all__ = ["NAMING_CONVENTION", "Base", "TimestampMixin", "UTCDateTime", "utcnow"]
