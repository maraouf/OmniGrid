"""Forgejo (self-hosted Git service) per-app module.

Encapsulates everything Forgejo-specific so the route layer
(``main_pkg/apps_routes.py``) stays generic. Forgejo is a community fork of
Gitea and speaks the Gitea-compatible REST API at ``<base>/api/v1``. Public
surface mirrors the per-app contract (``plex.py`` / ``seerr.py`` shape):

    SLUGS               — catalog slugs this module handles ("forgejo").
    requires_api_key()  — True (the chip's ``api_key`` field stores a Forgejo
                          API token — Settings → Applications → Generate Token).
    resolve_base_url(host_row, chip) -> str   (shared helper)
    test_credential(host_row, chip, candidate_key) -> dict
    fetch_data(host_row, chip, *, host_id, service_idx, force) -> dict
    peek_latest(host_id, service_idx) -> dict | None    (AI context)
    image_proxy_url(host_row, chip, path) -> (url, headers)   (avatars)
    SKILLS / run_skill  — status (read) + repos (read) + open PRs (read) +
                          open issues (read) + search (arg, read).

The expanded card answers "how busy is my git server right now":

    repos         — repos the token's user owns / collaborates on
    open_prs      — open pull requests across accessible repos
    open_issues   — open issues across accessible repos
    notifications — unread notifications
    version       — Forgejo / Gitea version

Auth model: a personal API token sent on the ``Authorization: token <token>``
header (Forgejo / Gitea's scheme — NOT a Bearer token). The token is the secret
and lives in the chip's ``api_key`` field. The credential probe hits the
auth-required ``/api/v1/user`` so a bad / missing token fails loudly (401).
Counts come from the ``X-Total-Count`` response header that Gitea / Forgejo
stamps on every paginated list endpoint (so ``limit=1`` is enough to read the
total without fetching the rows). Single-instance app (NOT fleet).
"""
from __future__ import annotations

import time
from typing import Any, Optional
from urllib.parse import urlsplit

import httpx
from logic.external_urls import ExternalURL

from logic.apps._common import (
    cache_key, fetch_gate, peek_cache, resolve_base_url, resolve_cache_ttl,
    resolve_credential_target)
from logic.coerce import as_dict, as_list, safe_int

# Catalog template slugs handled by this module.
SLUGS: tuple[str, ...] = ("forgejo",)

# Forgejo / Gitea REST API base path.
_API = "/api/v1"

# Absolute avatar hosts allowed through the per-app proxy ANONYMOUSLY — a
# Forgejo instance with gravatar federation serves user avatars off gravatar;
# the chip's OWN base host (the Forgejo server) is allowed too (with the token),
# the same pattern Seerr / Plex use for cross-origin-blocked avatars.
_AVATAR_PROXY_HOSTS = (ExternalURL.GRAVATAR_HOST,)

# Forgejo skills. The read skills surface as one-click drawer buttons AND
# AI / Telegram actions; ``forgejo_search`` is arg-carrying (AI / Telegram
# only — a drawer button can't supply the term).
SKILLS: tuple[dict, ...] = (
    {
        "id": "forgejo_status",
        "name": "Forgejo status",
        "ai_phrases": ("forgejo status, gitea status, how many repos, how many "
                       "open pull requests, open prs, open issues, git server "
                       "summary, forgejo overview, unread notifications"),
        "destructive": False,
    },
    {
        "id": "forgejo_repos",
        "name": "Recently updated repos",
        "ai_phrases": ("forgejo repos, my git repos, recently updated repos, "
                       "list repositories, what repos do i have, gitea repos, "
                       "latest repos"),
        "destructive": False,
    },
    {
        "id": "forgejo_prs",
        "name": "Open pull requests",
        "ai_phrases": ("open pull requests, open prs on forgejo, pending merge "
                       "requests, what prs are open, review queue, list pull "
                       "requests"),
        "destructive": False,
    },
    {
        "id": "forgejo_issues",
        "name": "Open issues",
        "ai_phrases": ("open issues on forgejo, list issues, what issues are "
                       "open, my issues, gitea issues, outstanding issues"),
        "destructive": False,
    },
    {
        "id": "forgejo_search",
        "name": "Search repos",
        "ai_phrases": ("search forgejo for <name>, find repo <name>, do i have a "
                       "repo called <name>, look up <name> on forgejo, gitea "
                       "search <name>, search repositories <name>"),
        # arg-carrying → AI / Telegram only (the dispatch supplies the term).
        "arg": True,
        "arg_hint": "the repository name (or part of it) to search for",
        "destructive": False,
    },
)

# Per-(host_id, service_idx) data cache for the expanded card. Default TTL
# overridable per chip via the editor's `cache_ttl` field. 60s — repo / PR /
# issue counts move slowly.
DEFAULT_CACHE_TTL_S = 60
_data_cache: dict[str, tuple[float, dict]] = {}

# Cap on the rich-item rows a list skill returns.
_MAX_ROWS = 12


def requires_api_key() -> bool:
    """Forgejo authenticates via an API token (Settings → Applications); the
    editor MUST render the token input (stored in the chip's api_key) + Test."""
    return True


def _headers(token: str) -> dict:
    """Forgejo / Gitea auth header (``token <token>`` scheme, NOT Bearer) +
    JSON Accept."""
    return {"Authorization": f"token {token}", "Accept": "application/json"}


def _img_headers(token: str) -> dict:
    """Headers for a BINARY avatar fetch — the credential header ONLY plus
    ``Accept: */*`` (a JSON Accept can 406 a binary fetch behind a strict
    upstream / proxy; the project's image-hook rule)."""
    return {"Authorization": f"token {token}", "Accept": "*/*"}


def image_proxy_url(host_row: dict, chip: dict, path: str) -> "tuple[str, dict]":
    """Per-app image-proxy hook — fetch a Forgejo user / org / repo avatar
    server-side so the API token never reaches the browser.

    Three target classes (SSRF-guarded):
      * a relative Forgejo avatar path (``/avatars/<hash>`` / ``/repo-avatars/
        <hash>``) → joined to the configured base + the token header;
      * an absolute http(s) URL on the chip's OWN base host (a Forgejo-served
        avatar URL) → fetched with the token header;
      * an absolute http(s) URL on gravatar (federation) → fetched ANONYMOUSLY
        with ``Accept: */*``.
    Anything else raises ``ValueError`` so the route returns 400."""
    token = (chip.get("api_key") or "").strip()
    p = (path or "").strip()
    if not p:
        raise ValueError("empty avatar path")
    base = resolve_base_url(host_row, chip)
    if not base:
        raise ValueError("no upstream URL configured")
    base_host = (urlsplit(base).hostname or "").lower()
    if "://" in p:
        parts = urlsplit(p)
        if parts.scheme not in ("http", "https") or not parts.hostname:
            raise ValueError("avatar must be an absolute http(s) URL")
        host = (parts.hostname or "").lower()
        if any(host == h or host.endswith("." + h) for h in _AVATAR_PROXY_HOSTS):
            return p, {"Accept": "*/*"}
        if base_host and host == base_host:
            return p, _img_headers(token)
        raise ValueError(f"avatar host not allowed: {host}")
    if not p.startswith("/") or ".." in p:
        raise ValueError("relative avatar must be a clean absolute path")
    return base.rstrip("/") + p, _img_headers(token)


def _count_header(r) -> int:
    """Read the total from the ``X-Total-Count`` header Gitea / Forgejo stamps
    on paginated list endpoints; fall back to the row count of the body."""
    try:
        h = r.headers.get("X-Total-Count")
        if h is not None and str(h).strip().isdigit():
            return int(str(h).strip())
    except (AttributeError, ValueError, TypeError):
        pass
    # Fallback: count the rows (list endpoints) or the `data` array (search).
    try:
        body = r.json()
    except (ValueError, TypeError):
        return 0
    if isinstance(body, list):
        return len(body)
    return len(as_list(as_dict(body).get("data")))


def _version_from(resp) -> str:
    """Forgejo / Gitea version from ``GET /api/v1/version`` ('' on any non-200
    / parse failure — version is never load-bearing)."""
    try:
        if getattr(resp, "status_code", 0) != 200:
            return ""
        return str(as_dict(resp.json()).get("version") or "").strip()
    except (ValueError, TypeError, AttributeError):
        return ""


async def test_credential(host_row: dict, chip: dict, candidate_key: str, **_kw) -> dict:
    """Probe Forgejo's auth-required ``/api/v1/user`` with the supplied token.
    Returns ``{ok, detail, status}``. Falls back to the chip's stored
    ``api_key`` when ``candidate_key`` is blank so the operator can re-test
    after first save without retyping."""
    token, base, err = resolve_credential_target(host_row, chip, candidate_key)
    if err:
        return err
    url = base + _API + "/user"
    try:
        async with httpx.AsyncClient(verify=False, timeout=10.0,
                                     follow_redirects=True) as cli:
            r = await cli.get(url, headers=_headers(token))
            try:
                ver = _version_from(await cli.get(base + _API + "/version",
                                                  headers=_headers(token)))
            except (httpx.HTTPError, OSError):
                ver = ""
    except (httpx.HTTPError, OSError) as e:  # noqa: BLE001
        return {"ok": False, "detail": f"{type(e).__name__}: {e}", "status": 0}
    if r.status_code == 200:
        try:
            login = str(as_dict(r.json()).get("login") or "").strip()
        except (ValueError, TypeError):
            login = ""
        who = f" as {login}" if login else ""
        return {"ok": True,
                "detail": (f"OK{who} (Forgejo {ver})" if ver else f"OK{who}"),
                "status": 200}
    if r.status_code in (401, 403):
        return {"ok": False, "detail": "auth failed (check the Forgejo token)",
                "status": r.status_code}
    return {"ok": False, "detail": f"HTTP {r.status_code}", "status": r.status_code}


# noinspection DuplicatedCode
async def fetch_data(host_row: dict, chip: dict, *,
                     host_id: str, service_idx: int,
                     force: bool = False) -> dict:
    """Fetch Forgejo's repo / PR / issue / notification summary for the card.

    Returns ``{available, repos, open_prs, open_issues, notifications,
    version, fetched_at}``. Raises ``ValueError`` / ``RuntimeError`` (caller
    maps to HTTPException) when the token is unset / the base URL won't resolve
    / the load-bearing ``/user/repos`` call errors."""
    token = (chip.get("api_key") or "").strip()
    now = time.time()
    base, hit = fetch_gate(host_row, chip, host_id, service_idx, _data_cache,
                           resolve_cache_ttl(chip, DEFAULT_CACHE_TTL_S), now, force,
                           credential=token, log_tag="forgejo")
    if hit is not None:
        return hit
    repos_url = base + _API + "/user/repos"
    try:
        async with httpx.AsyncClient(verify=False, timeout=20.0,
                                     follow_redirects=True) as cli:
            rr = await cli.get(repos_url, headers=_headers(token),
                               params={"limit": "1"})
            if rr.status_code != 200:
                print(f"[forgejo] error: fetch host={host_id} url={rr.request.url} "
                      f"returned HTTP {rr.status_code} (check the chip URL points at "
                      f"the Forgejo root, e.g. https://git.example.com:3000)")
                if rr.status_code in (401, 403):
                    raise RuntimeError(f"upstream auth failed: HTTP {rr.status_code} "
                                       f"(check the Forgejo token) — {repos_url}")
                raise RuntimeError(f"upstream returned HTTP {rr.status_code} for {repos_url}")
            repos = _count_header(rr)
            # Open PRs / issues / notifications — nice-to-have; a failure on any
            # ONE must NOT fail the card.
            open_prs = open_issues = notifications = 0
            try:
                pr = await cli.get(base + _API + "/repos/issues/search",
                                   headers=_headers(token),
                                   params={"type": "pulls", "state": "open", "limit": "1"})
                if pr.status_code == 200:
                    open_prs = _count_header(pr)
            except (httpx.HTTPError, OSError):
                open_prs = 0
            try:
                ir = await cli.get(base + _API + "/repos/issues/search",
                                   headers=_headers(token),
                                   params={"type": "issues", "state": "open", "limit": "1"})
                if ir.status_code == 200:
                    open_issues = _count_header(ir)
            except (httpx.HTTPError, OSError):
                open_issues = 0
            try:
                nr = await cli.get(base + _API + "/notifications",
                                   headers=_headers(token),
                                   params={"all": "false", "status-types": "unread",
                                           "limit": "1"})
                if nr.status_code == 200:
                    notifications = _count_header(nr)
            except (httpx.HTTPError, OSError):
                notifications = 0
            try:
                version = _version_from(
                    await cli.get(base + _API + "/version", headers=_headers(token)))
            except (httpx.HTTPError, OSError):
                version = ""
    except (httpx.HTTPError, OSError) as e:  # noqa: BLE001
        print(f"[forgejo] error: fetch host={host_id} url={repos_url} "
              f"failed — {type(e).__name__}: {e}")
        raise RuntimeError(f"upstream fetch failed: {type(e).__name__}: {e}")
    out: dict[str, Any] = {
        "available": True,
        "repos": repos,
        "open_prs": open_prs,
        "open_issues": open_issues,
        "notifications": notifications,
        "version": version,
        "fetched_at": int(now),
    }
    print(f"[forgejo] INFO fetched host={host_id} repos={repos} prs={open_prs} "
          f"issues={open_issues} notifications={notifications}")
    _data_cache[cache_key(host_id, service_idx)] = (now, out)
    return out


def peek_latest(host_id: str, service_idx: int) -> Optional[dict]:
    """Cache-only peek (no upstream call) for the AI context's
    ``app_skills[].last``."""
    data = peek_cache(_data_cache, host_id, service_idx)
    if not isinstance(data, dict) or not data.get("available"):
        return None
    return {
        "repos": safe_int(data.get("repos")),
        "open_prs": safe_int(data.get("open_prs")),
        "open_issues": safe_int(data.get("open_issues")),
        "notifications": safe_int(data.get("notifications")),
        "version": data.get("version") or "",
        "fetched_at": safe_int(data.get("fetched_at")),
    }


# ---------------------------------------------------------------------------
# Skills
# ---------------------------------------------------------------------------
async def run_skill(skill_id: str, host_row: dict, chip: dict, *,
                    host_id: Optional[str] = None,
                    service_idx: Optional[int] = None,
                    arg: Optional[str] = None, **_kw) -> dict:
    """Dispatch one of this app's SKILLS. Returns ``{ok, detail, status?}``.
    Raises ValueError on an unknown skill id (route maps to HTTP 404). ``arg``
    carries the free-form search term for ``forgejo_search``."""
    if skill_id == "forgejo_status":
        return await _status_skill(host_row, chip, host_id=host_id,
                                   service_idx=service_idx)
    if skill_id == "forgejo_repos":
        return await _repos_skill(host_row, chip, host_id=host_id)
    if skill_id == "forgejo_prs":
        return await _issues_skill(host_row, chip, host_id=host_id, kind="pulls")
    if skill_id == "forgejo_issues":
        return await _issues_skill(host_row, chip, host_id=host_id)
    if skill_id == "forgejo_search":
        return await _search_skill(host_row, chip, arg=arg, host_id=host_id)
    raise ValueError(f"unknown skill: {skill_id!r}")


def _resolve_skill_target(host_row: dict, chip: dict) -> "tuple[str, str, Optional[dict]]":
    """Resolve ``(token, base)`` or a ready ``{ok: False, detail}`` error dict
    for a Forgejo skill."""
    token = (chip.get("api_key") or "").strip()
    if not token:
        return "", "", {"ok": False, "status": 0, "detail": "Forgejo token not set"}
    base = resolve_base_url(host_row, chip)
    if not base:
        return "", "", {"ok": False, "status": 0, "detail": "no upstream URL configured"}
    return token, base, None


def _status_guard(r: "httpx.Response") -> Optional[dict]:
    """Shared 401 / 403 + non-200 guard for a Forgejo read skill. Returns a ready
    error dict, or None when the response is 200 OK."""
    if r.status_code in (401, 403):
        return {"ok": False, "status": r.status_code,
                "detail": "auth failed (check the Forgejo token)"}
    if r.status_code != 200:
        return {"ok": False, "status": r.status_code, "detail": f"HTTP {r.status_code}"}
    return None


async def _skill_get(base: str, path: str, *, token: str, params: dict,
                     timeout: float, verb: str) -> "httpx.Response | dict":
    """Shared GET + status guard for a Forgejo read skill. Returns the 200 OK
    response, or a ready ``{ok: False, ...}`` error dict (the call failed OR the
    status wasn't 200). Single owner of the ``httpx.AsyncClient`` boilerplate +
    the auth / non-200 guard the skills used to repeat — the caller discriminates
    with one ``isinstance(r, dict)`` (which also narrows the response type)."""
    try:
        async with httpx.AsyncClient(verify=False, timeout=timeout,
                                     follow_redirects=True) as cli:
            r = await cli.get(base + path, headers=_headers(token), params=params)
    except (httpx.HTTPError, OSError) as e:  # noqa: BLE001
        return {"ok": False, "status": 0,
                "detail": f"{verb} failed: {type(e).__name__}: {e}"}
    return _status_guard(r) or r


def _items_and_lines(rows: list, row_fn) -> "tuple[list[dict], list[str]]":
    """Map the first ``_MAX_ROWS`` raw rows through ``row_fn`` (``_repo_row`` /
    ``_issue_row``) into the rich-item list + the matching ``• title (subtitle)``
    text lines, skipping rows the builder rejects. Shared by every list skill."""
    items: list[dict] = []
    for raw in rows[:_MAX_ROWS]:
        row = row_fn(raw)
        if row:
            items.append(row)
    lines = [f"• {it['title']}" + (f"  ({it['subtitle']})" if it.get("subtitle") else "")
             for it in items]
    return items, lines


def _attach_items(out: dict, items: list[dict], count_i18n: str) -> dict:
    """Attach the rich-item list + count + count-i18n key to a skill result dict
    (no-op when there are no items). Returns ``out`` for one-line use."""
    if items:
        out["items"] = items
        out["count"] = len(items)
        out["count_i18n"] = count_i18n
    return out


# noinspection DuplicatedCode
async def _status_skill(host_row: dict, chip: dict, *,
                        host_id: Optional[str] = None,
                        service_idx: Optional[int] = None) -> dict:
    """Read-only: live-fetch the repo / PR / issue / notification summary
    (force-bypasses the cache) and return a formatted ``detail``. Never raises."""
    print(f"[forgejo] INFO forgejo_status host={host_id} svc_idx={service_idx} (live fetch)")
    try:
        data = await fetch_data(host_row, chip,
                                host_id=str(host_id or ""),
                                service_idx=int(service_idx or 0),
                                force=True)
    except (ValueError, RuntimeError) as e:
        print(f"[forgejo] warning: forgejo_status host={host_id} could not fetch — {e}")
        return {"ok": False, "detail": str(e), "status": 0}
    repos = safe_int(data.get("repos"))
    prs = safe_int(data.get("open_prs"))
    issues = safe_int(data.get("open_issues"))
    notifs = safe_int(data.get("notifications"))
    lines = [
        f"📦 Repos: {repos:,}",
        f"🔀 Open PRs: {prs:,}",
        f"🐛 Open issues: {issues:,}",
    ]
    if notifs:
        lines.append(f"🔔 Unread notifications: {notifs:,}")
    return {
        "ok": True,
        "detail": "\n".join(lines),
        "status": 200,
        "repos": repos, "open_prs": prs, "open_issues": issues,
        "notifications": notifs,
    }


def _repo_row(repo: dict) -> Optional[dict]:
    """One repo as a rich skill-result item: the owner avatar as the thumbnail
    (proxied), the full name as the title, and a ⭐ / 🍴 / language subtitle."""
    if not isinstance(repo, dict):
        return None
    full = str(repo.get("full_name") or repo.get("name") or "").strip()
    if not full:
        return None
    owner = as_dict(repo.get("owner"))
    avatar = str(owner.get("avatar_url") or "").strip()
    stars = safe_int(repo.get("stars_count"))
    forks = safe_int(repo.get("forks_count"))
    lang = str(repo.get("language") or "").strip()
    bits = []
    if stars:
        bits.append(f"⭐ {stars:,}")
    if forks:
        bits.append(f"🍴 {forks:,}")
    if lang:
        bits.append(lang)
    if repo.get("private"):
        bits.append("private")
    out: dict = {"title": full, "subtitle": " · ".join(bits)}
    if avatar:
        out["poster"] = avatar
        out["poster_proxy"] = True
    return out


async def _repos_skill(host_row: dict, chip: dict, *,
                       host_id: Optional[str] = None) -> dict:
    """Read-only: the most recently updated repos the token can access, as rich
    rows. Never raises."""
    token, base, err = _resolve_skill_target(host_row, chip)
    if err:
        return err
    print(f"[forgejo] INFO forgejo_repos host={host_id} (live fetch)")
    r = await _skill_get(base, _API + "/user/repos", token=token,
                         params={"limit": str(_MAX_ROWS), "sort": "updated",
                                 "order": "desc"}, timeout=15.0, verb="fetch")
    if isinstance(r, dict):
        return r
    try:
        repos = as_list(r.json())
    except (ValueError, TypeError):
        return {"ok": False, "status": 502, "detail": "non-JSON from upstream"}
    if not repos:
        return {"ok": True, "status": 200, "detail": "📦 No repositories found."}
    items, lines = _items_and_lines(repos, _repo_row)
    out: dict = {"ok": True, "status": 200,
                 "detail": "📦 Recently updated repos:\n" + "\n".join(lines)}
    return _attach_items(out, items, "apps.forgejo.repos_count")


def _issue_row(it: dict) -> Optional[dict]:
    """One issue / PR as a rich skill-result item: the author avatar as the
    thumbnail (proxied), the title, and a ``repo #number`` subtitle + author
    byline."""
    if not isinstance(it, dict):
        return None
    title = str(it.get("title") or "").strip()
    if not title:
        return None
    number = safe_int(it.get("number"))
    repo = as_dict(it.get("repository"))
    repo_name = str(repo.get("full_name") or repo.get("name") or "").strip()
    user = as_dict(it.get("user"))
    author = str(user.get("login") or "").strip()
    avatar = str(user.get("avatar_url") or "").strip()
    sub = repo_name + (f" #{number}" if number else "")
    out: dict = {"title": title, "subtitle": sub}
    if avatar:
        out["poster"] = avatar
        out["poster_proxy"] = True
    if author:
        out["byline"] = author
    return out


async def _issues_skill(host_row: dict, chip: dict, *,
                        host_id: Optional[str] = None, kind: str = "issues") -> dict:
    """Read-only: open issues (``kind='issues'``) or open PRs (``kind='pulls'``)
    across accessible repos, as rich rows. Never raises."""
    token, base, err = _resolve_skill_target(host_row, chip)
    if err:
        return err
    label = "pull requests" if kind == "pulls" else "issues"
    emoji = "🔀" if kind == "pulls" else "🐛"
    print(f"[forgejo] INFO forgejo_{kind} host={host_id} (live fetch)")
    r = await _skill_get(base, _API + "/repos/issues/search", token=token,
                         params={"type": kind, "state": "open",
                                 "limit": str(_MAX_ROWS), "sort": "updated"},
                         timeout=15.0, verb="fetch")
    if isinstance(r, dict):
        return r
    try:
        rows = as_list(r.json())
    except (ValueError, TypeError):
        return {"ok": False, "status": 502, "detail": "non-JSON from upstream"}
    if not rows:
        return {"ok": True, "status": 200, "detail": f"{emoji} No open {label}."}
    count_key = ("apps.forgejo.prs_count" if kind == "pulls"
                 else "apps.forgejo.issues_count")
    items, lines = _items_and_lines(rows, _issue_row)
    out: dict = {"ok": True, "status": 200,
                 "detail": f"{emoji} Open {label}:\n" + "\n".join(lines)}
    return _attach_items(out, items, count_key)


async def _search_skill(host_row: dict, chip: dict, *,
                        arg: Optional[str] = None,
                        host_id: Optional[str] = None) -> dict:
    """Read-only (arg): search repositories by name via
    ``GET /api/v1/repos/search`` and return the top matches as rich rows.
    Never raises."""
    term = (arg or "").strip()
    if not term:
        return {"ok": False, "status": 0,
                "detail": "no search term given — say e.g. 'search forgejo for omnigrid'"}
    token, base, err = _resolve_skill_target(host_row, chip)
    if err:
        return err
    print(f"[forgejo] INFO forgejo_search host={host_id} term={term!r} (live search)")
    r = await _skill_get(base, _API + "/repos/search", token=token,
                         params={"q": term, "limit": str(_MAX_ROWS)},
                         timeout=20.0, verb="search")
    if isinstance(r, dict):
        return r
    try:
        repos = as_list(as_dict(r.json()).get("data"))
    except (ValueError, TypeError):
        return {"ok": False, "status": 502, "detail": "non-JSON from upstream"}
    if not repos:
        return {"ok": True, "status": 200,
                "detail": f"🔍 No Forgejo repos match “{term}”."}
    items, lines = _items_and_lines(repos, _repo_row)
    out: dict = {"ok": True, "status": 200,
                 "detail": f"🔍 Forgejo repos matching “{term}”:\n" + "\n".join(lines)}
    return _attach_items(out, items, "apps.forgejo.repos_count")
