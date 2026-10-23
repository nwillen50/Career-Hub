# 0043. Interface stack: FastAPI, Jinja2, a bundled HTMX, and how pages are tested

- Status: Accepted (new dependencies flagged for Nicholas)
- Date: 2026-10-23
- Ticket: CH-137 (spike), for epic CH-31 (step 9)
- Deciders: Leo (owner), Sam (reviewer), Nicholas (approver)

## Context

The PRD says the web app is FastAPI with server-rendered Jinja2 pages and HTMX, listening on the
local machine only, with nothing loaded from the internet. This spike re-checks that stack against
current releases, proves the pieces work together in a small throwaway app, and picks how pages are
tested. The throwaway app was not added to the repository; this record holds what it showed.

## Decision

**Stack (all current as of Oct 23, 2026, all permissive licenses):**

| Piece | Version checked | License | Use |
| --- | --- | --- | --- |
| FastAPI | 0.161 | MIT | routes, form handling |
| Starlette | 1.9 | BSD-3 | comes with FastAPI; middleware, `TestClient` |
| Jinja2 | 3.1 | BSD-3 | server-rendered pages; autoescape on |
| Uvicorn | 0.54 | BSD-3 | serves the app on `127.0.0.1` |
| HTMX | 2.0.11 | 0BSD | partial page updates, bundled as one file |
| httpx2 | 2.16 | BSD-3 | what Starlette's `TestClient` now wants (dev only) |

1. **New runtime dependencies:** `fastapi`, `jinja2`, `uvicorn` (Starlette and `python-multipart`, if a
   form needs it, come with them). **New dev dependency:** `httpx2`. `httpx` is already a dependency.
   These are the stack the PRD already names; they are flagged because they are new to the project.
2. **HTMX is a file in the repository** (CH-138 adds `src/careerhub/web/static/htmx.min.js`; v2.0.11, 52,182 bytes,
   SHA-256 `d6fdc75f204e6bdefa99b69bf1e6d4ac69b8a364f77929f45c13476b4000f717`, from the project's
   GitHub tag), served by the app itself. No CDN; the app works offline. The file has no hard-coded
   outside addresses. A test (also CH-138) checks the file's hash so it cannot change unnoticed.
3. **HTMX settings** (a `<meta name="htmx-config">` tag): `allowEval` false (no `unsafe-eval`),
   `includeIndicatorStyles` false (HTMX otherwise injects an inline `<style>`, which a strict policy
   blocks; our own stylesheet styles the indicator), and `selfRequestsOnly` true.
4. **Content-Security-Policy** on every response: `default-src 'self'; script-src 'self';
   style-src 'self'; img-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none';
   form-action 'self'`. The spike confirmed the page and the HTMX file load under it. Pages use
   external stylesheet files, never inline `style` attributes or `<script>` blocks.
5. **Local only:** a middleware that answers 400 to any `Host` other than `127.0.0.1` or `localhost`
   (the spike showed `TestClient(base_url="http://evil.example")` getting 400), the server binds
   `127.0.0.1`, and `docs_url`, `redoc_url`, `openapi_url` are turned off. CH-138 adds the session
   token, Origin and CSRF checks on top.
6. **Escaping:** Jinja2 autoescape on; the spike confirmed `<script>alert(1)</script>` in a posting
   comes out as `&lt;script&gt;`. Templates never use `|safe` on posting or profile text.
7. **Testing pages:** Starlette's `TestClient` (needs `httpx2`; with plain `httpx` installed it
   prints a deprecation warning) for requests, plus an accessibility check written in pytest with the
   standard library's `html.parser`: `lang` on `<html>`, one `<h1>`, `<main>` and `<nav>` landmarks,
   a skip link, every non-hidden input has a matching `<label for>`, images have `alt`, headings do
   not skip levels, and text/background colour pairs from the design tokens (CH-105) meet WCAG AA
   contrast (computed from the token values, so it needs no browser). A browser-based checker such
   as axe-core stays optional and out of CI for now (it would need Playwright and a browser download).

## Evidence

The throwaway spike ran: a Jinja2 page through `TestClient` returned 200 with the CSP header; hostile
text was escaped; a foreign `Host` got 400; the landmark and label checks passed on the base layout.

## Consequences

Three runtime packages and one dev package are added when CH-138 starts (this record changes no
code). HTMX updates are deliberate: replace the file, update the hash in the test and this record.
The contrast check needs the token values in a form Python can read (they are already in
`web/assets`).
