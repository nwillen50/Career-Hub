"""Find each target company's public job board (CH-112).

For every *enabled* target without a board, try the public APIs of Greenhouse, Lever, and Ashby with
tokens guessed from the company's name (`northwindlabs`, `northwind-labs`, `northwind`...). A hit is
saved on the target and **proposed** as a source for the user to approve (the normal approval
flow; nothing is fetched from it until then). A hit that can't be tied to the company by name is
only listed as *uncertain* for the user to confirm. Requests are one a second at most, stop when a
site says "slow down", and only ever go to the three public API hosts. This runs when the user asks,
never in the nightly run.
"""

from __future__ import annotations

import re
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field

import httpx
from sqlalchemy.orm import Session

from careerhub.modules.jobs.sources import propose_source
from careerhub.modules.jobs.targets import Target

TIMEOUT_SECONDS = 15.0
REQUEST_DELAY_SECONDS = 1.0
MAX_TOKENS = 8
_TOKEN = re.compile(r"^[a-z0-9-]{2,60}$")
#: Words companies often add that their board name drops ("Acme Labs" -> "acme").
_GENERIC = frozenset(
    "inc llc ltd corp co company labs lab bio biosciences therapeutics technologies technology "
    "tech systems robotics energy materials health sciences group holdings".split()
)
#: Ordinary words that are not a company's own name. A one-word guess made by dropping words from
#: a longer name ("Quantum Machines" -> "quantum") must never be one of these: it hits whichever
#: unrelated company owns that board (CH-213).
_COMMON = _GENERIC | frozenset(
    "quantum ai ml data cloud software digital global network networks machines machine "
    "robots robot sports health medical energy security finance labs".split()
)
#: How each system's board is addressed on the web (used for the proposed source's address).
BOARD_URLS = {
    "greenhouse": "https://boards.greenhouse.io/{token}",
    "lever": "https://jobs.lever.co/{token}",
    "ashby": "https://jobs.ashbyhq.com/{token}",
}
_API = {
    "greenhouse": "https://boards-api.greenhouse.io/v1/boards/{token}/jobs",
    "lever": "https://api.lever.co/v0/postings/{token}?mode=json&limit=3",
    "ashby": "https://api.ashbyhq.com/posting-api/job-board/{token}",
}


class RateLimited(Exception):
    """A site asked us to slow down; the search stops (nothing is retried)."""


@dataclass(frozen=True)
class BoardHit:
    """A board that answered, and whether it could be tied to the company by name."""

    kind: str
    token: str
    verified: bool


@dataclass
class FindReport:
    """What a search did, by company name."""

    found: list[tuple[str, BoardHit]] = field(default_factory=list)
    uncertain: list[tuple[str, BoardHit]] = field(default_factory=list)
    missed: list[str] = field(default_factory=list)
    stopped: str | None = None  # why the search ended early, if it did


def candidate_tokens(name: str) -> list[str]:
    """Return up to `MAX_TOKENS` board names to try for `name`, most likely first."""
    words = [w for w in re.findall(r"[a-z0-9]+", name.lower())]
    if not words:
        return []
    core = [w for w in words if w not in _GENERIC] or words
    guesses = [
        "".join(words), "-".join(words), "".join(core), "-".join(core),
        core[0] if len(core) == 1 else "", "".join(core) + "hq", "".join(words[:2]),
        "-".join(words[:2]),
    ]  # fmt: skip
    seen: list[str] = []
    for guess in guesses:
        if len(words) > 1 and guess in _COMMON:
            continue  # a generic word left after dropping words: it would match a stranger
        if guess and _TOKEN.match(guess) and guess not in seen:
            seen.append(guess)
    return seen[:MAX_TOKENS]


def _mentions(body: object, name: str) -> bool:
    """Say whether the board's answer names the company (every distinctive word, whole words).

    All the words must appear, so "Quantum Formatics" is not tied to "Quantum Machines" by the
    shared first word.
    """
    words = [w for w in re.findall(r"[a-z0-9]+", name.lower()) if w not in _GENERIC]
    if not words:
        return False
    text = str(body).lower()
    return all(re.search(rf"(?<![a-z0-9]){re.escape(w)}(?![a-z0-9])", text) for w in words)


def probe(kind: str, token: str, name: str, client: httpx.Client) -> BoardHit | None:
    """Ask one system whether it has a board called `token`. None means no board (or no jobs)."""
    try:
        reply = client.get(_API[kind].format(token=token))
    except httpx.HTTPError:
        return None
    if reply.status_code == 429:
        raise RateLimited(kind)
    if reply.status_code != 200:
        return None
    try:
        body = reply.json()
    except ValueError:
        return None
    jobs = body.get("jobs") if isinstance(body, dict) else body
    if not jobs:  # an empty answer does not prove the board is this company's
        return None
    if kind == "greenhouse" and isinstance(jobs, list):
        names = {str(j.get("company_name", "")).lower() for j in jobs if isinstance(j, dict)}
        if any(names) and not any(_mentions(n, name) for n in names):
            return BoardHit(kind, token, False)  # a board with another company's name on it
        return BoardHit(kind, token, any(names) or _mentions(jobs, name))
    return BoardHit(kind, token, _mentions(jobs, name))


def find_boards(
    session: Session,
    targets: list[Target],
    client: httpx.Client | None = None,
    sleep: Callable[[float], None] = time.sleep,
    progress: Callable[[str], None] | None = None,
    taken: Iterable[tuple[str | None, str | None]] = (),
) -> FindReport:
    """Search for the boards of enabled targets that have none; save hits and propose sources.

    `progress` gets one line before each company ("Checking Acme (3 of 44)...") and one with its
    result, so a long search does not look frozen. A first line gives the longest it could take.
    """
    say = progress or (lambda _line: None)
    http = client or httpx.Client(timeout=TIMEOUT_SECONDS)
    report = FindReport()
    todo = [t for t in targets if t.enabled and not t.board and not t.unsupported_ats]
    if todo:
        worst = len(todo) * MAX_TOKENS * len(BOARD_URLS) * REQUEST_DELAY_SECONDS
        minutes = max(1, round(worst / 60))
        say(
            f"Looking for the boards of {len(todo)} companies, one request a second. "
            f"This takes up to about {minutes} {'minute' if minutes == 1 else 'minutes'} "
            "(often much less); "
            "Ctrl-C stops it and keeps what was found."
        )
    position = 0
    first = True
    # One board, one company.
    claimed: set[tuple[str | None, str | None]] = {
        (t.board_kind, t.board) for t in targets if t.board
    } | set(taken)  # `taken`: boards held by targets that are not being searched this time
    for target in targets:
        if not target.enabled or target.board or target.unsupported_ats:
            continue
        position += 1
        say(f"Checking {target.name} ({position} of {len(todo)})...")
        hit: BoardHit | None = None
        weak: BoardHit | None = None
        try:
            for token in candidate_tokens(target.name):
                for kind in BOARD_URLS:
                    if not first:
                        sleep(REQUEST_DELAY_SECONDS)
                    first = False
                    found = probe(kind, token, target.name, http)
                    if found and found.verified:
                        hit = found
                        break
                    weak = weak or found
                if hit:
                    break
        except RateLimited as error:
            report.stopped = f"{error} asked us to slow down; run it again later."
            return report
        if hit and (hit.kind, hit.token) in claimed:
            report.uncertain.append((target.name, hit))  # another company already has this board
            say("  unsure: another company already has that board")
        elif hit:
            claimed.add((hit.kind, hit.token))
            target.board_kind, target.board = hit.kind, hit.token  # type: ignore[assignment]
            propose_source(
                session, hit.kind, target.name, BOARD_URLS[hit.kind].format(token=hit.token),
                {"board": hit.token},
            )  # fmt: skip
            report.found.append((target.name, hit))
            say(f"  found: {hit.kind} board {hit.token!r}")
        elif weak:
            report.uncertain.append((target.name, weak))
            say(f"  unsure: a {weak.kind} board answered but doesn't name the company")
        else:
            report.missed.append(target.name)
            say("  no board found")
    return report
