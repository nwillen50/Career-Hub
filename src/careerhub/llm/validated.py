"""Model answers as checked data: parse, validate, retry once, then escalate.

`complete_validated` asks for JSON, checks it against a Pydantic schema, and if the answer is
invalid asks the same model once more (telling it what was wrong). If that fails too, it moves to
the next, stronger provider in `routes` and repeats. Every attempt goes through
`Router.complete`, so the privacy check applies to every provider tried: escalating a Sensitive
call to a cloud provider raises `PrivacyViolation` rather than being skipped.
"""

from __future__ import annotations

import json
import re
from collections.abc import Sequence
from dataclasses import replace

from pydantic import BaseModel, ValidationError

from careerhub.llm.router import Router
from careerhub.llm.types import LLMRequest

#: A model sometimes wraps JSON in a ```json fence; accept that.
_FENCE = re.compile(r"^\s*```(?:json)?\s*(.*?)\s*```\s*$", re.DOTALL)


class ValidationFailed(Exception):
    """No provider in the route produced an answer that matches the schema."""


def _parse[T: BaseModel](text: str, schema: type[T]) -> T:
    """Parse `text` as JSON and validate it against `schema`."""
    fenced = _FENCE.match(text)
    return schema.model_validate_json(fenced.group(1) if fenced else text)


def _describe(error: Exception) -> str:
    """Say what was wrong without echoing any of the model's text (it may quote personal data)."""
    if isinstance(error, ValidationError):
        return "; ".join(
            f"{'.'.join(str(part) for part in item['loc']) or 'answer'}: {item['msg']}"
            for item in error.errors(include_input=False)
        )
    if isinstance(error, json.JSONDecodeError):
        return f"not valid JSON ({error.msg})"
    return type(error).__name__


def complete_validated[T: BaseModel](
    router: Router,
    request: LLMRequest,
    schema: type[T],
    routes: Sequence[str],
    retries: int = 1,
) -> T:
    """Return a `schema` object from the first provider in `routes` that answers validly.

    Each provider gets one attempt plus `retries` retries before moving to the next. Raises
    `ValidationFailed` (listing only what was wrong, never the model's text) if all fail.
    """
    if not routes:
        raise ValueError("routes must name at least one provider")
    problems: list[str] = []
    for name in routes:
        current = request
        for _ in range(retries + 1):
            response = router.complete(current, name)
            try:
                return _parse(response.text, schema)
            except (ValidationError, ValueError) as error:  # JSONDecodeError is a ValueError
                problem = _describe(error)
                problems.append(f"{name}: {problem}")
                current = replace(
                    request,
                    prompt=(
                        f"{request.prompt}\n\nYour previous answer was invalid ({problem}). "
                        "Reply with only valid JSON that matches the requested format."
                    ),
                )
    raise ValidationFailed("No valid answer. " + " | ".join(problems))
