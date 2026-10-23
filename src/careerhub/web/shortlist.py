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
from careerhub.core.models import Assessment, Job, RunLog, Source, utcnow
from careerhub.core.paths import data_dir
from careerhub.core.settings import load_settings
from careerhub.modules.jobs.age import age_days
from careerhub.modules.jobs.muse import KIND as MUSE_KIND
from careerhub.modules.jobs.muse import VIA
from careerhub.modules.jobs.scoring import current_rank_adjustments, is_internship, ranked_jobs
from careerhub.modules.network import WarmPath, warm_paths
from careerhub.modules.network.schools import alumni_search_link, my_schools
from careerhub.profile.readiness import current_readiness
from careerhub.web.assets import copy_fonts, tokens_css

#: Today shows at most this many ranked jobs (PRD).
MAX_JOBS = 5
#: Warm paths shown per job.
MAX_CONTACTS = 2
RUN_STEP = "nightly"
INTERNSHIP_NOTE = "Internship: ranked a little lower (your setting)"


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
    #: Short muted lines at the bottom of the card (for example the internship ordering note).
    footnotes: tuple[str, ...] = ()
    #: A link the user can open to search their school's alumni at this company on LinkedIn.
    alumni_link: str | None = None
    alumni_school: str | None = None
    #: Where the posting came from when the source requires credit (for example "via The Muse").
    via: str | None = None


@dataclass(frozen=True)
class ShortlistData:
    """Everything the page shows."""

    entries: list[ShortlistEntry] = field(default_factory=list)
    last_success: datetime | None = None
    #: What went wrong in the latest run, if it failed (error type names only).
    last_error: str | None = None
    #: While scoring is switched off, what the profile still needs (None once it is on).
    waiting_for: tuple[str, ...] | None = None
    #: Scores at or above `tier_high` get the green bubble, at or above `tier_mid` the brass one.
    tier_high: int = 75
    tier_mid: int = 55


def gather_shortlist(
    session: Session, limit: int = MAX_JOBS, now: datetime | None = None
) -> ShortlistData:
    """Collect the top jobs and the state of the latest nightly run."""
    now = now or utcnow()
    adjustments = current_rank_adjustments()
    penalty = adjustments["internship_penalty"]
    slug = load_settings().linkedin_school_slug
    schools = my_schools(session) if slug else []
    stale_days = adjustments["stale_after_days"]
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
            footnotes=_footnotes(job, now, penalty, stale_days),
            alumni_link=alumni_search_link(slug, job.company.name),
            alumni_school=schools[0] if schools else None,
            via=VIA if _from_muse(session, job) else None,
        )
        for job, assessment in ranked_jobs(session, min(limit, MAX_JOBS), now=now, **adjustments)
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
    settings = load_settings()
    readiness = current_readiness(session, settings.profile_min_skills)
    return ShortlistData(
        entries,
        success.finished_at if success is not None else None,
        error or ("The last run failed." if runs and runs[0].status == "failed" else None),
        None if readiness.on else tuple(readiness.missing),
        settings.score_tier_high,
        settings.score_tier_mid,
    )


def _footnotes(job: Job, now: datetime, penalty: float, stale_days: int) -> tuple[str, ...]:
    """Return the muted lines for a card: how old the posting is, and the internship note."""
    days = age_days(job, now)
    age = "Posted today" if days == 0 else f"Posted {days} day{'s' if days != 1 else ''} ago"
    if days > stale_days:
        weeks = stale_days // 7
        limit = (
            f"{weeks} week{'s' if weeks != 1 else ''}"
            if stale_days % 7 == 0
            else f"{stale_days} days"
        )
        age += f", older than {limit}"
    notes = [age]
    if penalty > 0 and is_internship(job):
        notes.append(INTERNSHIP_NOTE)
    return tuple(notes)


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
* { box-sizing: border-box; }
body { margin:0; background:var(--page); color:var(--text); font:var(--size-body)/var(--line-body)
  var(--font-body); }
h1, h2, h3 { font-family:var(--font-heading); font-weight:var(--heading-weight); }
h1 { font-size:var(--size-h1); line-height:var(--line-h1); margin:0 0 var(--space-1); }
h2 { font-size:var(--size-h3); line-height:var(--line-h3); margin:0; }
h3 { font-size:1rem; margin:var(--space-3) 0 var(--space-1); }
ul { margin:var(--space-1) 0 var(--space-3); padding-left:1.2rem; }
.bar { background:var(--header); color:var(--header-text);
  border-bottom:3px solid var(--brass-rule); }
.bar > div { max-width:46rem; margin:0 auto; padding:0 var(--space-4); min-height:56px;
  display:flex; flex-wrap:wrap; align-items:center; gap:var(--space-4); }
.brand { font:var(--heading-weight) 22px/1.2 var(--font-heading); letter-spacing:.02em; }
.status { margin-left:auto; font-size:14px; color:var(--tab-inactive); }
main { max-width:46rem; margin:0 auto; padding:var(--space-5) var(--space-4) var(--space-6); }
.meta { color:var(--muted); margin:0 0 var(--space-5); }
.card { background:var(--surface); border:1px solid var(--border); border-radius:var(--radius-md);
  box-shadow:var(--shadow-card); padding:var(--space-4) 20px; margin:0 0 var(--space-4); }
.head { display:flex; align-items:center; gap:var(--space-4); }
.head > div:first-child { flex:1 1 auto; min-width:0; }
.bubble { flex:none; width:44px; height:44px; border-radius:50%; display:flex; align-items:center;
  justify-content:center; font-weight:600; font-size:1.1rem; }
.tier-high { background:var(--score-high-bg); color:var(--score-high-text); }
.tier-mid { background:var(--score-mid-bg); color:var(--score-mid-text); }
.tier-low { background:var(--score-low-bg); color:var(--score-low-text); }
.where { color:var(--muted); margin:var(--space-1) 0 var(--space-3); font-size:15px; }
.same-school { font-size:13px; font-weight:600; background:var(--panel); color:var(--panel-text);
  border-radius:var(--radius-pill); padding:1px var(--space-2); }
.footnote { color:var(--muted); font-size:.85rem; margin:var(--space-2) 0 0; }
a { color:var(--accent); } a:hover { color:var(--accent-hover); }
a:focus-visible, summary:focus-visible { outline:3px solid var(--focus); outline-offset:2px; }
summary { cursor:pointer; color:var(--accent); }
.error { border-left:4px solid var(--overdue); color:var(--overdue); }
table { border-collapse:collapse; width:100%; margin-top:var(--space-2); }
th, td { text-align:left; padding:var(--space-1) var(--space-2);
  border-bottom:1px solid var(--divider); }
details { transition:var(--fade); }
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
        f"<title>Career Hub shortlist</title><style>{tokens_css()}{_STYLE}</style></head><body>",
        '<header class="bar"><div><div class="brand">Career Hub</div>'
        f'<div class="status">{escape(ran)}</div></div></header><main>',
        "<h1>Today's shortlist</h1>",
        f'<p class="meta">Page built {now:%a %b %d, %H:%M} UTC.</p>',
    ]
    if data.last_error:
        parts.append(
            '<section class="card error" role="alert"><h2>The last nightly run failed</h2>'
            f"<p>{escape(data.last_error)} The jobs below are from an earlier run.</p></section>"
        )
    if data.waiting_for is not None:
        needs = "".join(f"<li>{escape(item)}</li>" for item in data.waiting_for)
        parts.append(
            '<section class="card"><h2>Scoring starts when your profile is ready</h2>'
            f"<p>Still needed:</p><ul>{needs or '<li>Run <code>hub profile ready</code></li>'}</ul>"
            "<p>Then run <code>hub profile ready</code> once to switch scoring on.</p></section>"
        )
    if not data.entries:
        parts.append(
            '<section class="card"><h2>Nothing to show yet</h2><p>No jobs have been ranked. '
            "Check that a source is approved (<code>hub sources list</code>) and that the nightly "
            "run has completed.</p></section>"
        )
    for index, entry in enumerate(data.entries, start=1):
        parts.append(_render_entry(index, entry, data.tier_high, data.tier_mid))
    parts.append("</main></body></html>")
    return "".join(parts)


def _bubble(score: float, high: int, mid: int) -> str:
    """Render the 44 px score circle: its tier decides the colors, and it has a text label."""
    tier = "high" if score >= high else "mid" if score >= mid else "low"
    return (
        f'<div class="bubble tier-{tier}" role="img" '
        f'aria-label="Fit score {score:.0f} out of 100">{score:.0f}</div>'
    )


def _render_entry(index: int, entry: ShortlistEntry, high: int = 75, mid: int = 55) -> str:
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
        f'<article class="card"><div class="head"><div><h2>{index}. {heading}</h2>'
        f'<p class="where">{where}</p></div>{_bubble(entry.score, high, mid)}</div>'
        f"<p>{escape(_first_sentence(entry.explanation))}</p>"
        f"{_render_contacts(entry.contacts)}{_render_alumni_link(entry)}"
        f"<details><summary>Full explanation</summary><p>{escape(entry.explanation)}</p>{table}"
        f"</details>{_render_tailoring(entry.tailoring)}{_render_footnotes(entry.footnotes)}"
        "</article>"
    )


_TAILORING_TITLES = (
    ("lead_with", "Lead with"),
    ("gaps", "Gaps to address"),
    ("resume_suggestions", "Resume edits to consider"),
)


def _render_footnotes(footnotes: tuple[str, ...]) -> str:
    """Render the small muted lines at the bottom of a card (none: nothing)."""
    return "".join(f'<p class="footnote">{escape(note)}</p>' for note in footnotes)


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
        f"{' <span class="same-school">Same school</span>' if path.same_school else ''}"
        f"{' (' + escape(path.title) + ')' if path.title else ''}"
        f"{': ' + escape('; '.join(path.reasons)) if path.reasons else ''}</li>"
        for path in contacts
    )
    return f"<h3>Who to contact</h3><ul>{items}</ul>"


def _render_alumni_link(entry: ShortlistEntry) -> str:
    """Render the link to search the user's school's alumni on LinkedIn (built, never fetched)."""
    link = _safe_link(entry.alumni_link or "")
    if not link:
        return ""
    school = escape(entry.alumni_school or "your school")
    return (
        f'<p class="meta"><a href="{escape(link, quote=True)}" rel="noopener noreferrer">'
        f"Find {school} alumni at {escape(entry.company)} on LinkedIn</a> "
        "(opens LinkedIn in your browser; Career Hub doesn't visit it).</p>"
    )


def shortlist_path() -> Path:
    """Return where the page is written: `shortlist/index.html` in the data folder."""
    return data_dir() / "shortlist" / "index.html"


def write_shortlist(session: Session, now: datetime, path: Path | None = None) -> Path:
    """Build the page from the database and write it, readable only by the user."""
    path = path or shortlist_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    html = render_shortlist(gather_shortlist(session, now=now), now)
    copy_fonts(path.parent / "fonts")
    temporary = path.with_suffix(".tmp")
    write_private(temporary, html)
    temporary.replace(path)  # swap in whole, so a reader never sees a half-written page
    return path
