"""The nightly shortlist page: the best jobs, why, and when it last ran (Milestone A).

`gather_shortlist` reads the database; `render_shortlist` turns that into one self-contained HTML
file (no scripts, no external requests) that the user bookmarks. Posting text comes from the
internet, so everything is HTML-escaped and links are used only when they are http or https (a
`javascript:` link in a hostile posting must never become clickable). The page follows the
system's light or dark mode, uses real headings and landmarks for VoiceOver, and shows designed
empty and error states.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from html import escape
from pathlib import Path
from urllib.parse import urlparse

from sqlalchemy import select
from sqlalchemy.orm import Session

from careerhub.core.files import write_private
from careerhub.core.models import Assessment, Job, RunLog, Source
from careerhub.core.paths import data_dir
from careerhub.modules.jobs.muse import KIND as MUSE_KIND
from careerhub.modules.jobs.muse import VIA
from careerhub.modules.jobs.scoring import ranked_jobs
from careerhub.modules.network import WarmPath, warm_paths

#: Today shows at most this many ranked jobs (PRD).
MAX_JOBS = 5
#: Warm paths shown per job.
MAX_CONTACTS = 2
RUN_STEP = "nightly"


@dataclass(frozen=True)
class ShortlistEntry:
    """One job on the page."""

    title: str
    company: str
    location: str | None
    url: str
    score: float
    explanation: str
    criteria: list[dict[str, object]]
    #: Up to two people the user knows at the company, strongest first.
    contacts: list[WarmPath] = field(default_factory=list)
    #: Tailoring notes: {"lead_with": [...], "gaps": [...], "resume_suggestions": [...]}.
    tailoring: dict[str, list[str]] | None = None
    #: Where the posting came from when the source requires credit (for example "via The Muse").
    via: str | None = None


@dataclass(frozen=True)
class ShortlistData:
    """Everything the page shows."""

    entries: list[ShortlistEntry] = field(default_factory=list)
    last_success: datetime | None = None
    #: What went wrong in the latest run, if it failed (error type names only).
    last_error: str | None = None


def gather_shortlist(session: Session, limit: int = MAX_JOBS) -> ShortlistData:
    """Collect the top jobs and the state of the latest nightly run."""
    entries = [
        ShortlistEntry(
            title=job.title,
            company=job.company.name,
            location=job.location,
            url=job.url,
            score=assessment.fit_score or 0.0,
            explanation=assessment.explanation or "",
            criteria=list(assessment.criteria_scores),
            contacts=warm_paths(session, job.company_id, limit=MAX_CONTACTS),
            tailoring=_notes(assessment),
            via=VIA if _from_muse(session, job) else None,
        )
        for job, assessment in ranked_jobs(session, min(limit, MAX_JOBS))
    ]
    runs = list(
        session.scalars(
            select(RunLog)
            .where(RunLog.step == RUN_STEP, RunLog.status != "running")
            .order_by(RunLog.started_at.desc(), RunLog.id.desc())
            .limit(20)
        )
    )
    success = next((run for run in runs if run.status == "succeeded"), None)
    error = runs[0].error_summary if runs and runs[0].status == "failed" else None
    return ShortlistData(
        entries,
        success.finished_at if success is not None else None,
        error or ("The last run failed." if runs and runs[0].status == "failed" else None),
    )


def _from_muse(session: Session, job: Job) -> bool:
    """Say whether `job` came from The Muse, which requires crediting it."""
    source = session.get(Source, job.source_id)
    return source is not None and source.kind == MUSE_KIND


def _notes(assessment: Assessment) -> dict[str, list[str]] | None:
    """Return the saved tailoring notes, if any, with every value as a list of strings."""
    saved = (assessment.tailoring or {}).get("notes")
    if not isinstance(saved, dict):
        return None
    return {
        key: [str(item) for item in saved.get(key, [])]
        for key in ("lead_with", "gaps", "resume_suggestions")
    }


def _safe_link(url: str) -> str | None:
    """Return `url` only if it is an http or https address."""
    parsed = urlparse(url.strip())
    return url.strip() if parsed.scheme in ("http", "https") and parsed.netloc else None


def _first_sentence(text: str) -> str:
    match = re.match(r"(.+?[.!?])(\s|$)", text.strip(), re.DOTALL)
    return match.group(1) if match else text.strip()


_STYLE = """
:root { --bg:#fafaf7; --fg:#1d1d1b; --muted:#5b5b57; --card:#ffffff; --line:#dcdcd5;
  --accent:#1b5e8c; --bad:#9b1c1c; }
@media (prefers-color-scheme: dark) { :root { --bg:#16171a; --fg:#ecece8; --muted:#a6a6a0;
  --card:#202226; --line:#33363b; --accent:#7ab8e6; --bad:#f19999; } }
body { margin:0; background:var(--bg); color:var(--fg);
  font:16px/1.5 -apple-system, system-ui, "Segoe UI", sans-serif; }
main { max-width:46rem; margin:0 auto; padding:1.5rem 1rem 3rem; }
h1 { font-size:1.6rem; margin:0 0 .25rem; } h2 { font-size:1.15rem; margin:0; }
h3 { font-size:1rem; margin:.6rem 0 .2rem; } ul { margin:.2rem 0 .6rem; padding-left:1.2rem; }
.meta { color:var(--muted); margin:0 0 1.5rem; }
.card { background:var(--card); border:1px solid var(--line); border-radius:10px;
  padding:1rem 1.1rem; margin:0 0 1rem; }
.score { float:right; font-weight:600; color:var(--accent); }
.where { color:var(--muted); margin:.1rem 0 .6rem; }
a { color:var(--accent); } a:focus-visible, summary:focus-visible { outline:3px solid var(--accent);
  outline-offset:2px; }
.error { border-left:4px solid var(--bad); color:var(--bad); }
table { border-collapse:collapse; width:100%; margin-top:.5rem; }
th, td { text-align:left; padding:.2rem .4rem; border-bottom:1px solid var(--line); }
"""


def render_shortlist(data: ShortlistData, now: datetime) -> str:
    """Render the page as a complete HTML document."""
    ran = (
        f"Last successful run: {data.last_success:%a %b %d, %H:%M} UTC."
        if data.last_success
        else "No successful run yet."
    )
    parts = [
        '<!doctype html><html lang="en"><head><meta charset="utf-8">',
        '<meta name="viewport" content="width=device-width, initial-scale=1">',
        '<meta name="color-scheme" content="light dark">',
        f"<title>Career Hub shortlist</title><style>{_STYLE}</style></head><body><main>",
        "<h1>Today's shortlist</h1>",
        f'<p class="meta">{escape(ran)} Page built {now:%a %b %d, %H:%M} UTC.</p>',
    ]
    if data.last_error:
        parts.append(
            '<section class="card error" role="alert"><h2>The last nightly run failed</h2>'
            f"<p>{escape(data.last_error)} The jobs below are from an earlier run.</p></section>"
        )
    if not data.entries:
        parts.append(
            '<section class="card"><h2>Nothing to show yet</h2><p>No jobs have been ranked. '
            "Check that a source is approved (<code>hub sources list</code>) and that the nightly "
            "run has completed.</p></section>"
        )
    for index, entry in enumerate(data.entries, start=1):
        parts.append(_render_entry(index, entry))
    parts.append("</main></body></html>")
    return "".join(parts)


def _render_entry(index: int, entry: ShortlistEntry) -> str:
    link = _safe_link(entry.url)
    title = escape(entry.title)
    heading = (
        f'<a href="{escape(link, quote=True)}" rel="noopener noreferrer">{title}</a>'
        if link
        else title
    )
    where = escape(" · ".join(filter(None, (entry.company, entry.location, entry.via))))
    rows = "".join(
        f"<tr><td>{escape(str(c.get('name', '')))}</td>"
        f"<td>{escape(str(c.get('score', '')))}/10</td>"
        f"<td>{escape(str(c.get('reason', '')))}</td></tr>"
        for c in entry.criteria
    )
    table = (
        '<table><caption class="meta">How it scored on each criterion</caption>'
        "<thead><tr><th>Criterion</th><th>Score</th><th>Why</th></tr></thead>"
        f"<tbody>{rows}</tbody></table>"
        if rows
        else ""
    )
    return (
        f'<article class="card"><h2><span class="score" aria-label="fit score">'
        f"{entry.score:.0f}/100</span>{index}. {heading}</h2>"
        f'<p class="where">{where}</p><p>{escape(_first_sentence(entry.explanation))}</p>'
        f"{_render_contacts(entry.contacts)}"
        f"<details><summary>Full explanation</summary><p>{escape(entry.explanation)}</p>{table}"
        f"</details>{_render_tailoring(entry.tailoring)}</article>"
    )


_TAILORING_TITLES = (
    ("lead_with", "Lead with"),
    ("gaps", "Gaps to address"),
    ("resume_suggestions", "Resume edits to consider"),
)


def _render_tailoring(notes: dict[str, list[str]] | None) -> str:
    """Render tailoring notes in a collapsed section, or nothing when there are none."""
    if not notes or not any(notes.values()):
        return ""
    sections = "".join(
        f"<h3>{title}</h3><ul>{''.join(f'<li>{escape(item)}</li>' for item in notes[key])}</ul>"
        for key, title in _TAILORING_TITLES
        if notes.get(key)
    )
    return f"<details><summary>Tailoring notes</summary>{sections}</details>"


def _render_contacts(contacts: list[WarmPath]) -> str:
    """Render "who to contact" for a job, or a short note when the user knows no one there."""
    if not contacts:
        return '<p class="meta">Who to contact: no one you know here yet.</p>'
    items = "".join(
        f"<li><strong>{escape(path.name)}</strong>"
        f"{' (' + escape(path.title) + ')' if path.title else ''}"
        f"{': ' + escape('; '.join(path.reasons)) if path.reasons else ''}</li>"
        for path in contacts
    )
    return f"<h3>Who to contact</h3><ul>{items}</ul>"


def shortlist_path() -> Path:
    """Return where the page is written: `shortlist/index.html` in the data folder."""
    return data_dir() / "shortlist" / "index.html"


def write_shortlist(session: Session, now: datetime, path: Path | None = None) -> Path:
    """Build the page from the database and write it, readable only by the user."""
    path = path or shortlist_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    html = render_shortlist(gather_shortlist(session), now)
    temporary = path.with_suffix(".tmp")
    write_private(temporary, html)
    temporary.replace(path)  # swap in whole, so a reader never sees a half-written page
    return path
