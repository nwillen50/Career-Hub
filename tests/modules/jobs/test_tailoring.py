from __future__ import annotations

import json
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from careerhub.core.migrate import migrate
from careerhub.core.models import Assessment, Job, Source
from careerhub.core.secrets import new_database_key
from careerhub.core.storage import open_engine, session_factory
from careerhub.llm import Destination, PrivacyTier, PrivacyViolation, Router
from careerhub.llm.fake import FakeProvider
from careerhub.modules.jobs import NormalizedJob, store_jobs
from careerhub.modules.jobs.filters import assess_filters
from careerhub.modules.jobs.tailoring import (
    PROMPT_VERSION,
    build_request,
    resume_for_model,
    tailor_job,
)
from careerhub.profile.documents import latex_to_text
from careerhub.profile.models import HardConstraints, ProfileDoc
from careerhub.profile.redact import redact_contacts
from careerhub.web.shortlist import ShortlistData, ShortlistEntry, render_shortlist

DOC = ProfileDoc(name="Jane Sample", skills=["Python"], interests=["ml"])
LATEX = r"""
% my private comment
\documentclass{article}
\begin{document}
\section*{Jane Sample}
jane.sample@example.com \quad +1 (617) 555-0142 \quad https://example.com/jane
\textbf{\emph{Built}} a Python tool that saved 10 hours a week.\\
Cost 50\% less. % trailing comment
\end{document}
"""
NOTES = json.dumps(
    {
        "lead_with": ["Your Python tool"],
        "gaps": ["No SQL shown"],
        "resume_suggestions": ["Move projects up"],
    }
)


@pytest.fixture
def session(tmp_path: Path) -> Session:
    engine = open_engine(tmp_path / "t.db", new_database_key())
    migrate(engine)
    return session_factory(engine)()


def scored_job(session: Session, score: float | None = 80.0) -> Job:
    source = Source(kind="greenhouse", name="s", url="u", status="approved")
    session.add(source)
    session.commit()
    store_jobs(session, source, [NormalizedJob("Acme", "1", "Engineer", "u", "Boston", "Text")])
    job = session.scalars(select(Job)).one()
    assessment = assess_filters(session, job, HardConstraints())
    assessment.fit_score = score
    session.commit()
    return job


RESUME = latex_to_text(LATEX)


def test_latex_is_reduced_to_text() -> None:
    text = RESUME
    assert "Built a Python tool that saved 10 hours a week." in text
    assert "private comment" not in text and "trailing comment" not in text
    assert "\\" not in text.replace("\\%", "") or "50\\% less" in text
    assert "documentclass" not in text and "{" not in text


def test_contact_details_are_removed_before_the_resume_reaches_a_model() -> None:
    text = resume_for_model(RESUME)
    assert "jane.sample@example.com" not in text and "555-0142" not in text
    assert "example.com/jane" not in text
    assert "[email]" in text and "[phone]" in text and "[link]" in text
    assert redact_contacts("call 617.555.0142 or a@b.co") == "call [phone] or [email]"


def test_ordinary_numbers_are_not_mistaken_for_phones() -> None:
    assert redact_contacts("GPA 3.9, 2022-2026, 10 hours") == "GPA 3.9, 2022-2026, 10 hours"


def test_the_request_is_personal_tier_without_the_name_and_not_essential(session: Session) -> None:
    request = build_request(scored_job(session), DOC, resume_for_model(RESUME))
    assert request.tier is PrivacyTier.PERSONAL and not request.essential
    assert "Jane" in request.prompt  # the resume's own heading is the user's resume text...
    assert "jane.sample@example.com" not in request.prompt
    assert "Name:" not in request.prompt  # ...but the profile block adds no name line


def test_notes_are_saved_with_provenance_and_not_redone_until_the_resume_changes(
    session: Session,
) -> None:
    job = scored_job(session)
    provider = FakeProvider("m", replies=[NOTES])
    router = Router([provider])
    notes = tailor_job(session, router, job, DOC, RESUME, ["m"])
    assert notes is not None and notes.lead_with == ["Your Python tool"]
    saved = session.scalars(select(Assessment)).one().tailoring
    assert (
        saved is not None
        and saved["model"] == "m:m-model"
        and saved["prompt_version"] == PROMPT_VERSION
    )
    assert tailor_job(session, router, job, DOC, RESUME, ["m"]) is None
    assert tailor_job(session, router, job, DOC, RESUME + "\nNew line of experience.", ["m"])
    assert len(provider.requests) == 2


def test_unscored_jobs_get_no_notes(session: Session) -> None:
    job = scored_job(session, score=None)
    provider = FakeProvider("m", replies=[NOTES])
    assert tailor_job(session, Router([provider]), job, DOC, RESUME, ["m"]) is None
    assert provider.requests == []


def test_a_training_cloud_provider_cannot_see_the_resume(session: Session) -> None:
    job = scored_job(session)
    cloud = FakeProvider("c", Destination.CLOUD, trains_on_data=True, replies=[NOTES])
    with pytest.raises(PrivacyViolation):
        tailor_job(session, Router([cloud]), job, DOC, RESUME, ["c"])
    assert cloud.requests == []


def test_notes_with_too_many_items_are_rejected_and_retried(session: Session) -> None:
    job = scored_job(session)
    too_many = json.dumps({"lead_with": [str(i) for i in range(9)]})
    provider = FakeProvider("m", replies=[too_many, NOTES])
    assert tailor_job(session, Router([provider]), job, DOC, RESUME, ["m"]) is not None
    assert len(provider.requests) == 2


def test_the_page_shows_notes_escaped_and_hides_the_section_when_empty() -> None:
    entry = ShortlistEntry("T", "C", None, "https://x.com", 70.0, "Why.", [])
    assert "Tailoring notes" not in render_shortlist(ShortlistData([entry]), _now())
    with_notes = ShortlistEntry(
        "T",
        "C",
        None,
        "https://x.com",
        70.0,
        "Why.",
        [],
        tailoring={
            "lead_with": ["<script>x</script>"],
            "gaps": [],
            "resume_suggestions": ["Edit & trim"],
        },
    )
    html = render_shortlist(ShortlistData([with_notes]), _now())
    assert "Tailoring notes" in html and "<script>x" not in html and "Edit &amp; trim" in html
    assert "Gaps to address" not in html


def _now():  # type: ignore[no-untyped-def]
    from datetime import UTC, datetime

    return datetime(2026, 10, 21, tzinfo=UTC)
