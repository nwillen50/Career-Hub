"""The model router: the only way Career Hub talks to a language model.

Nothing outside this package may call a model directly (CLAUDE.md). See README.md.
"""

from careerhub.llm.cost import BudgetExceeded, CostLog, Price
from careerhub.llm.provider import Destination, Provider
from careerhub.llm.router import PrivacyViolation, Router, UnknownProvider
from careerhub.llm.types import LLMRequest, LLMResponse, PrivacyTier

__all__ = [
    "BudgetExceeded",
    "CostLog",
    "Destination",
    "LLMRequest",
    "LLMResponse",
    "Price",
    "PrivacyTier",
    "PrivacyViolation",
    "Provider",
    "Router",
    "UnknownProvider",
]
