"""The Network module: who the user knows, and where (build step 7)."""

from careerhub.modules.network.alumni import AlumniReport, Preview, import_alumni, preview_alumni
from careerhub.modules.network.interactions import (
    InteractionError,
    log_interaction,
    recent_interactions,
    set_follow_up_done,
    set_own_words,
)
from careerhub.modules.network.paths import WarmPath, find_company, warm_paths
from careerhub.modules.network.people import (
    UnknownPerson,
    find_people,
    tag_person,
)

__all__ = [
    "InteractionError",
    "log_interaction",
    "recent_interactions",
    "set_follow_up_done",
    "set_own_words",
    "AlumniReport",
    "UnknownPerson",
    "WarmPath",
    "find_company",
    "find_people",
    "Preview",
    "import_alumni",
    "preview_alumni",
    "tag_person",
    "warm_paths",
]
