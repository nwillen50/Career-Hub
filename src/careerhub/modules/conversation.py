"""The conversation panel's brain, part 1: read-only questions about ranked jobs (CH-158).

A question is answered by a model through the router at the Personal tier, from a small, redacted
context: the job's extracted facts, the stored fit explanation and criteria, and the rubric. The
answer is plain text. Nothing here can change a setting, send a message, apply, or fetch anything:
proposals that change preferences or the rubric come in later tickets (CH-159, CH-160), and always
wait for the user's Accept.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from careerhub.core.models import Assessment, ConversationMessage, Job
from careerhub.llm import BudgetExceeded, LLMRequest, PrivacyTier, PrivacyViolation, Router
from careerhub.llm.providers import ProviderError
from careerhub.llm.validated import ValidationFailed, complete_validated
from careerhub.modules.jobs.scoring import current_rank_adjustments, ranked_jobs, rubric_text
from careerhub.modules.proposals import ConversationProposal, ProposalError, propose
from careerhub.profile.files import ProfileFileError
from careerhub.profile.redact import personal_text
from careerhub.profile.rubric import Rubric
from careerhub.prompts import load_prompt, render

PROMPT_NAME = "conversation"
PROMPT_VERSION = 2
MAX_QUESTION_CHARS = 600
MAX_ANSWER_CHARS = 1500
TOP_JOBS_IN_CONTEXT = 5
HISTORY_SHOWN = 30


@dataclass(frozen=True)
class Answer:
    """What came back: the saved messages, or a plain-words reason there is no answer."""

    ok: bool
    text: str
    #: The id of the suggested preference change saved as a pending proposal, if there is one.
    proposal_id: int | None = None


def _job_block(job: Job, assessment: Assessment | None, rank: int | None) -> str:
    fields = (job.structured_fields or {}).get("fields") or {}
    shown = {
        k: v
        for k, v in fields.items()
        if k not in ("preferred_skills",) and v not in (None, [], "")
    }
    lines = [f"Job{f' #{rank}' if rank else ''}: {job.title} at {job.company.name}"]
    if job.location:
        lines.append(f"Location: {job.location}")
    if shown:
        lines.append(
            "Facts read from the posting: " + "; ".join(f"{k}={v}" for k, v in shown.items())
        )
    if assessment is not None and assessment.fit_score is not None:
        lines.append(f"Fit score: {assessment.fit_score:.0f} out of 100")
        if assessment.explanation:
            lines.append(f"Why: {assessment.explanation}")
        for item in assessment.criteria_scores or []:
            lines.append(
                f"- {item.get('name', '')}: {item.get('score', '')}/10, {item.get('reason', '')}"
            )
    return "\n".join(lines)


def build_context(
    session: Session, rubric: Rubric | None, name: str, aliases: Sequence[str], job_id: int | None
) -> str:
    """Describe the job (or the top jobs) and the rubric, with personal details removed."""
    blocks = []
    if job_id is not None:
        job = session.get(Job, job_id)
        if job is not None:
            assessment = session.scalars(
                select(Assessment).where(Assessment.job_id == job.id)
            ).first()
            blocks.append(_job_block(job, assessment, None))
    else:
        for rank, (job, assessment) in enumerate(
            ranked_jobs(session, TOP_JOBS_IN_CONTEXT, **current_rank_adjustments()), start=1
        ):
            blocks.append(_job_block(job, assessment, rank))
    if rubric is not None:
        # the whole context goes through personal_text below, which also covers the rubric
        blocks.append("How jobs are scored (the rubric):\n" + rubric_text(rubric))
    return personal_text("\n\n".join(blocks) or "No ranked jobs yet.", name, aliases)


def recent_messages(session: Session, job_id: int | None = None) -> list[ConversationMessage]:
    """Return the latest messages, oldest first (all of them, or only about one job)."""
    query = select(ConversationMessage)
    if job_id is not None:
        query = query.where(ConversationMessage.job_id == job_id)
    rows = list(session.scalars(query.order_by(ConversationMessage.id.desc()).limit(HISTORY_SHOWN)))
    return rows[::-1]


def ask(
    session: Session,
    router: Router,
    routes: Sequence[str],
    question: str,
    context: str,
    name: str,
    aliases: Sequence[str],
    job_id: int | None = None,
) -> Answer:
    """Answer `question` from `context`. Both messages are saved only if there is an answer."""
    question = " ".join(question.split())[:MAX_QUESTION_CHARS]
    if not question:
        return Answer(False, "Type a question first.")
    request = LLMRequest(
        prompt=render(
            load_prompt(PROMPT_NAME, PROMPT_VERSION),
            question=personal_text(question, name, aliases),
            context=context,
        ),
        tier=PrivacyTier.PERSONAL,
        max_tokens=400,
        essential=False,  # a chat answer may pause when the budget is used up
    )
    try:
        parsed = complete_validated(router, request, ConversationProposal, list(routes))
    except BudgetExceeded:
        return Answer(False, "The monthly model budget is used up, so questions are paused.")
    except PrivacyViolation:
        return Answer(
            False, "That model is not allowed to see personal data. Check `hub models routes`."
        )
    except (ProviderError, ValidationFailed):
        return Answer(False, "No model could answer right now. Try again later.")
    reply = parsed.answer.strip()[:MAX_ANSWER_CHARS]
    proposal_id: int | None = None
    if parsed.change is not None:
        try:
            proposal_id = propose(session, parsed.change).id
        except (ProposalError, ProfileFileError) as problem:
            reply = f"{reply}\n\n(Not suggested: {problem})"
    session.add_all(
        [
            ConversationMessage(role="user", text=question, job_id=job_id),
            ConversationMessage(role="assistant", text=reply, job_id=job_id),
        ]
    )
    session.commit()
    return Answer(True, reply, proposal_id)
