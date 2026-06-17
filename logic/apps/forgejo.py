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
from datetime import datetime, timezone
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
    {
        "id": "forgejo_starred",
        "name": "Starred repos",
        "ai_phrases": ("starred repos, my starred repositories, what have i "
                       "starred on forgejo, forgejo stars, gitea starred"),
        "destructive": False,
    },
    {
        "id": "forgejo_mark_read",
        "name": "Mark notifications read",
        "ai_phrases": ("mark forgejo notifications read, clear notifications, mark "
                       "all read, dismiss forgejo notifications, read all "
                       "notifications"),
        "destructive": False,
    },
    {
        "id": "forgejo_failing_actions",
        "name": "Failing CI / Actions runs",
        "ai_phrases": ("failing actions, failed ci runs, is my ci red, which "
                       "actions failed, broken builds on forgejo, failing "
                       "workflows, ci status, forgejo actions failures"),
        "destructive": False,
    },
    {
        "id": "forgejo_mirror_sync",
        "name": "Sync a push/pull mirror",
        "ai_phrases": ("sync the <name> mirror, mirror-sync <name>, pull the "
                       "github mirror now, sync my <name> mirror, update the "
                       "<name> mirror, trigger a mirror sync"),
        "arg": True,
        "arg_hint": "the repository name (or part of it) whose mirror to sync",
        "destructive": False,
    },
    {
        "id": "forgejo_sync_all_mirrors",
        "name": "Sync all mirrors",
        "ai_phrases": ("sync all mirrors, mirror-sync everything, pull every "
                       "mirror now, update all my mirrors, refresh all mirrors, "
                       "trigger a sync on every mirror, sync all my mirror repos"),
        # No-arg fleet action; non-destructive — it only kicks already-configured
        # mirrors (no data loss), so no confirm gate.
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
        with ``Accept: */*``;
      * an absolute http(s) URL on ANY OTHER host → treated as a Forgejo avatar
        whose host is the server's configured ROOT_URL (which can differ from
        the base we were handed — reverse proxy / internal vs public hostname):
        its PATH is REWRITTEN onto our OWN configured base (SSRF-safe — we never
        fetch the arbitrary host, only the operator's Forgejo) + the token.
    A path-less / non-http(s) value raises ``ValueError`` so the route 400s."""
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
        # Different host than our base — Forgejo reported the avatar on its
        # configured ROOT_URL (public / proxied hostname) while we hold an
        # internal base. Rewrite the PATH onto OUR base so we fetch the same
        # Forgejo (never the arbitrary host — SSRF-safe).
        if parts.path:
            q = ("?" + parts.query) if parts.query else ""
            return base.rstrip("/") + parts.path + q, _img_headers(token)
        raise ValueError(f"avatar URL has no path: {p}")
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


def _heatmap_week(heatmap: Any, now: float) -> int:
    """Sum the authenticated user's contributions over the last 7 days from a
    ``GET /users/{username}/heatmap`` payload (rows of ``{timestamp (epoch s,
    15-min buckets), contributions}``). 0 on an empty / unexpected shape."""
    cutoff = now - 7 * 86400
    total = 0
    for row in as_list(heatmap):
        rd = as_dict(row)
        if safe_int(rd.get("timestamp")) >= cutoff:
            total += safe_int(rd.get("contributions"))
    return total


def _heatmap_daily_series(heatmap: Any, now: float, *, days: int = 14) -> list:
    """Per-day contribution totals over the last ``days`` days (oldest-first) from
    a heatmap payload — the activity sparkline. The heatmap buckets by 15 min, so
    several rows fall on one day; sum them per calendar day (UTC). Always returns
    a dense ``days``-long list (0 for days with no activity) so the sparkline x-axis
    is even. ``[]`` only on an empty heatmap."""
    if not as_list(heatmap):
        return []
    today = int(now // 86400)
    start = today - (days - 1)
    buckets = [0] * days
    for row in as_list(heatmap):
        rd = as_dict(row)
        day = safe_int(rd.get("timestamp")) // 86400
        idx = day - start
        if 0 <= idx < days:
            buckets[idx] += safe_int(rd.get("contributions"))
    return buckets


async def _fetch_activity(cli: "httpx.AsyncClient", base: str, token: str,
                          now: float) -> "tuple[int, list]":
    """Best-effort ``(activity_week, activity_series)`` from the authenticated
    user's contribution heatmap. Resolves the username via ``GET /user`` then
    pulls ``GET /users/{username}/heatmap``. ``(0, [])`` when the token can't
    resolve a user OR the heatmap is disabled (``ENABLE_USER_HEATMAP=false``) /
    empty — never raises (a heatmap hiccup must not fail the card)."""
    try:
        ur = await cli.get(base + _API + "/user", headers=_headers(token))
        if ur.status_code != 200:
            return 0, []
        username = str(as_dict(ur.json()).get("login") or "").strip()
        if not username:
            return 0, []
        from urllib.parse import quote  # noqa: PLC0415
        hr = await cli.get(base + _API + f"/users/{quote(username, safe='')}/heatmap",
                           headers=_headers(token))
        if hr.status_code != 200:
            return 0, []
        heatmap = hr.json()
    except (httpx.HTTPError, OSError, ValueError, TypeError):
        return 0, []
    return _heatmap_week(heatmap, now), _heatmap_daily_series(heatmap, now)


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
            # "Needs your attention" stats — PRs awaiting YOUR review +
            # PRs/issues assigned to you. Far more actionable than the raw open
            # counts. Best-effort: a failure leaves the count at 0.
            review_requested = assigned_prs = assigned_issues = 0
            try:
                rv = await cli.get(base + _API + "/repos/issues/search",
                                   headers=_headers(token),
                                   params={"type": "pulls", "state": "open",
                                           "review_requested": "true", "limit": "1"})
                if rv.status_code == 200:
                    review_requested = _count_header(rv)
            except (httpx.HTTPError, OSError):
                review_requested = 0
            try:
                ap = await cli.get(base + _API + "/repos/issues/search",
                                   headers=_headers(token),
                                   params={"type": "pulls", "state": "open",
                                           "assigned": "true", "limit": "1"})
                if ap.status_code == 200:
                    assigned_prs = _count_header(ap)
            except (httpx.HTTPError, OSError):
                assigned_prs = 0
            try:
                ai = await cli.get(base + _API + "/repos/issues/search",
                                   headers=_headers(token),
                                   params={"type": "issues", "state": "open",
                                           "assigned": "true", "limit": "1"})
                if ai.status_code == 200:
                    assigned_issues = _count_header(ai)
            except (httpx.HTTPError, OSError):
                assigned_issues = 0
            # Starred repos + organizations — nice-to-have extra stats.
            starred = orgs = 0
            try:
                sr = await cli.get(base + _API + "/user/starred",
                                   headers=_headers(token), params={"limit": "1"})
                if sr.status_code == 200:
                    starred = _count_header(sr)
            except (httpx.HTTPError, OSError):
                starred = 0
            try:
                org = await cli.get(base + _API + "/user/orgs",
                                    headers=_headers(token), params={"limit": "1"})
                if org.status_code == 200:
                    orgs = _count_header(org)
            except (httpx.HTTPError, OSError):
                orgs = 0
            try:
                version = _version_from(
                    await cli.get(base + _API + "/version", headers=_headers(token)))
            except (httpx.HTTPError, OSError):
                version = ""
            # Activity this week + a 14-day activity sparkline from the user's
            # contribution heatmap (commits + PRs + issues + reviews). Best-effort:
            # 0 / [] when the heatmap is disabled server-side.
            activity_week, activity_series = await _fetch_activity(cli, base, token, now)
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
        "review_requested": review_requested,
        "assigned_prs": assigned_prs,
        "assigned_issues": assigned_issues,
        "starred": starred,
        "orgs": orgs,
        # Contribution activity (last 7 days) + a 14-day daily series (drawer
        # sparkline) from the user's heatmap.
        "activity_week": activity_week,
        "activity_series": activity_series,
        "version": version,
        "fetched_at": int(now),
    }
    # Best-effort open-PR/issue backlog trend from the shared lifespan
    # forgejo_sampler (a 30d review-queue burn-down). Missing sampler / no
    # samples yet leaves the card's instantaneous counts untouched.
    out["trend"] = _safe_trend(host_id, service_idx)
    print(f"[forgejo] INFO fetched host={host_id} repos={repos} prs={open_prs} "
          f"issues={open_issues} notifications={notifications} starred={starred} "
          f"orgs={orgs} review_req={review_requested} assigned={assigned_prs}/{assigned_issues} "
          f"activity7d={activity_week} activity_series={len(activity_series)}")
    _data_cache[cache_key(host_id, service_idx)] = (now, out)
    return out


def _safe_trend(host_id: str, service_idx: int) -> Optional[dict]:
    """Best-effort open-PR/issue backlog trend for the card — the shared
    forgejo_sampler's per-chip ``trend_summary``. Returns ``None`` (never raises)
    when the sampler isn't importable / errors, so a trend hiccup can't fail the
    card."""
    try:
        from logic.apps import forgejo_sampler as _sampler  # noqa: PLC0415
        return _sampler.trend_summary(host_id, int(service_idx))
    except Exception as e:  # noqa: BLE001
        print(f"[forgejo] trend_summary({host_id}#{service_idx}) skipped: {e}")
        return None


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
        "review_requested": safe_int(data.get("review_requested")),
        "assigned_prs": safe_int(data.get("assigned_prs")),
        "assigned_issues": safe_int(data.get("assigned_issues")),
        "starred": safe_int(data.get("starred")),
        "orgs": safe_int(data.get("orgs")),
        "activity_week": safe_int(data.get("activity_week")),
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
    if skill_id == "forgejo_starred":
        return await _starred_skill(host_row, chip, host_id=host_id)
    if skill_id == "forgejo_mark_read":
        return await _mark_read_skill(host_row, chip, host_id=host_id)
    if skill_id == "forgejo_failing_actions":
        return await _failing_actions_skill(host_row, chip, host_id=host_id)
    if skill_id == "forgejo_mirror_sync":
        return await _mirror_sync_skill(host_row, chip, arg=arg, host_id=host_id)
    if skill_id == "forgejo_sync_all_mirrors":
        return await _sync_all_mirrors_skill(host_row, chip, host_id=host_id)
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
    review_req = safe_int(data.get("review_requested"))
    assigned = safe_int(data.get("assigned_prs")) + safe_int(data.get("assigned_issues"))
    lines = [
        f"📦 Repos: {repos:,}",
        f"🔀 Open PRs: {prs:,}",
        f"🐛 Open issues: {issues:,}",
    ]
    if review_req:
        lines.append(f"👀 PRs awaiting your review: {review_req:,}")
    if assigned:
        lines.append(f"📌 Assigned to you: {assigned:,}")
    if notifs:
        lines.append(f"🔔 Unread notifications: {notifs:,}")
    activity_week = safe_int(data.get("activity_week"))
    if activity_week:
        lines.append(f"📈 Activity this week: {activity_week:,} contribution"
                     + ("" if activity_week == 1 else "s"))
    return {
        "ok": True,
        "detail": "\n".join(lines),
        "status": 200,
        "repos": repos, "open_prs": prs, "open_issues": issues,
        "notifications": notifs, "review_requested": review_req,
        "activity_week": activity_week,
    }


def _rel_time(iso: Any) -> str:
    """Compact relative age ('just now' / '5m ago' / '3h ago' / '2d ago' /
    '4mo ago' / '1y ago') from an ISO-8601 / RFC3339 timestamp (Forgejo's
    ``updated_at``), or '' when blank / unparseable."""
    s = str(iso or "").strip()
    if not s:
        return ""
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return ""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    secs = int((datetime.now(timezone.utc) - dt).total_seconds())
    if secs < 60:
        return "just now"
    if secs < 3600:
        return f"{secs // 60}m ago"
    if secs < 86400:
        return f"{secs // 3600}h ago"
    days = secs // 86400
    if days < 30:
        return f"{days}d ago"
    if days < 365:
        return f"{days // 30}mo ago"
    return f"{days // 365}y ago"


def _repo_row(repo: dict) -> Optional[dict]:
    """One repo as a rich skill-result item: the repo's own avatar (falling back
    to the owner's) as the thumbnail (proxied), the full name as the title, and a
    ⭐ / 🍴 / language / last-updated subtitle."""
    if not isinstance(repo, dict):
        return None
    full = str(repo.get("full_name") or repo.get("name") or "").strip()
    if not full:
        return None
    owner = as_dict(repo.get("owner"))
    # Prefer the repo's OWN avatar; fall back to the owner's. (Most repos have no
    # custom avatar, so this usually lands on the owner mark — but a repo that
    # set one wins.)
    avatar = (str(repo.get("avatar_url") or "").strip()
              or str(owner.get("avatar_url") or "").strip())
    stars = safe_int(repo.get("stars_count"))
    forks = safe_int(repo.get("forks_count"))
    lang = str(repo.get("language") or "").strip()
    updated = _rel_time(repo.get("updated_at"))
    bits = []
    if stars:
        bits.append(f"⭐ {stars:,}")
    if forks:
        bits.append(f"🍴 {forks:,}")
    if lang:
        bits.append(lang)
    if repo.get("private"):
        bits.append("private")
    if updated:
        bits.append(f"🕒 {updated}")
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
    # Defensive newest-first sort (ISO timestamps sort lexicographically =
    # chronologically) in case the upstream ignores sort=updated&order=desc.
    repos.sort(key=lambda rp: str(as_dict(rp).get("updated_at") or ""), reverse=True)
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


# noinspection DuplicatedCode
async def _starred_skill(host_row: dict, chip: dict, *,
                         host_id: Optional[str] = None) -> dict:
    """Read-only: the repos the token's user has starred, as rich rows
    (``GET /api/v1/user/starred``). Never raises."""
    token, base, err = _resolve_skill_target(host_row, chip)
    if err:
        return err
    print(f"[forgejo] INFO forgejo_starred host={host_id} (live fetch)")
    r = await _skill_get(base, _API + "/user/starred", token=token,
                         params={"limit": str(_MAX_ROWS)}, timeout=15.0, verb="fetch")
    if isinstance(r, dict):
        return r
    try:
        repos = as_list(r.json())
    except (ValueError, TypeError):
        return {"ok": False, "status": 502, "detail": "non-JSON from upstream"}
    if not repos:
        return {"ok": True, "status": 200, "detail": "⭐ No starred repositories."}
    items, lines = _items_and_lines(repos, _repo_row)
    out: dict = {"ok": True, "status": 200,
                 "detail": "⭐ Starred repos:\n" + "\n".join(lines)}
    return _attach_items(out, items, "apps.forgejo.repos_count")


async def _mark_read_skill(host_row: dict, chip: dict, *,
                           host_id: Optional[str] = None) -> dict:
    """Action: mark EVERY notification as read (``PUT /api/v1/notifications``).
    Low-risk (read-state only) → non-destructive. Never raises."""
    token, base, err = _resolve_skill_target(host_row, chip)
    if err:
        return err
    print(f"[forgejo] INFO forgejo_mark_read host={host_id} (mark all notifications read)")
    try:
        async with httpx.AsyncClient(verify=False, timeout=15.0,
                                     follow_redirects=True) as cli:
            r = await cli.put(base + _API + "/notifications",
                              headers=_headers(token),
                              params={"all": "true", "status-types": "unread"})
    except (httpx.HTTPError, OSError) as e:  # noqa: BLE001
        return {"ok": False, "status": 0, "detail": f"mark-read failed: {type(e).__name__}: {e}"}
    if r.status_code in (401, 403):
        return {"ok": False, "status": r.status_code, "detail": "auth failed (check the Forgejo token)"}
    # Gitea/Forgejo returns 205 Reset Content (or 200) on a successful mark-all.
    if r.status_code not in (200, 205):
        return {"ok": False, "status": r.status_code, "detail": f"HTTP {r.status_code}"}
    return {"ok": True, "status": 200, "detail": "🔔 Marked all notifications as read."}


# Forgejo Actions has no server-wide runs endpoint — runs are per-repo, so the
# failing-runs aggregation walks a bounded set of the most-recently-updated
# repos (capped so a big server can't fan out hundreds of calls).
_ACTIONS_REPO_CAP = 12
_FAILED_STATES = ("failure", "failed", "error", "cancelled", "canceled")


def _action_runs(body: Any) -> list:
    """Pull the run rows out of a Forgejo ``/actions/tasks`` response, tolerant of
    the shape drift across versions (``{workflow_runs: [...]}`` /
    ``{tasks: [...]}`` / a bare list). Returns a list of dict rows ([] when
    none)."""
    if isinstance(body, list):
        return [r for r in body if isinstance(r, dict)]
    d = as_dict(body)
    for key in ("workflow_runs", "tasks", "runs", "data"):
        rows = [r for r in as_list(d.get(key)) if isinstance(r, dict)]
        if rows:
            return rows
    return []


def _run_failed(run: dict) -> bool:
    """True when a run's status / conclusion marks it failed."""
    for k in ("status", "conclusion", "state"):
        if str(run.get(k) or "").strip().lower() in _FAILED_STATES:
            return True
    return False


async def _repo_failing_runs(cli: "httpx.AsyncClient", base: str, token: str,
                             repo: dict) -> list:
    """Failing Actions runs for ONE repo as rich rows. Best-effort: [] on any
    per-repo failure (Actions disabled / endpoint absent / non-200)."""
    full = str(repo.get("full_name") or "").strip()
    if not full or "/" not in full:
        return []
    owner, name = full.split("/", 1)
    try:
        r = await cli.get(base + _API + f"/repos/{owner}/{name}/actions/tasks",
                          headers=_headers(token), params={"limit": "20"})
        if r.status_code != 200:
            return []
        runs = _action_runs(r.json())
    except (httpx.HTTPError, OSError, ValueError, TypeError):
        return []
    out: list = []
    for run in runs:
        if not _run_failed(run):
            continue
        wf = str(run.get("name") or run.get("display_title")
                 or run.get("workflow_id") or "workflow").strip()
        when = _rel_time(run.get("updated_at") or run.get("created_at")
                         or run.get("created"))
        sub_bits = [b for b in (full, when) if b]
        out.append({"title": f"❌ {wf}", "subtitle": " · ".join(sub_bits)})
    return out


# noinspection DuplicatedCode
async def _failing_actions_skill(host_row: dict, chip: dict, *,
                                 host_id: Optional[str] = None) -> dict:
    """Read-only: aggregate FAILING CI / Actions runs across the most-recently-
    updated repos (bounded). 'Is my CI red' at a glance. Never raises."""
    token, base, err = _resolve_skill_target(host_row, chip)
    if err:
        return err
    print(f"[forgejo] INFO forgejo_failing_actions host={host_id} (live, bounded repos)")
    r = await _skill_get(base, _API + "/user/repos", token=token,
                         params={"limit": str(_ACTIONS_REPO_CAP), "sort": "updated",
                                 "order": "desc"}, timeout=15.0, verb="fetch")
    if isinstance(r, dict):
        return r
    try:
        repos = as_list(r.json())
    except (ValueError, TypeError):
        return {"ok": False, "status": 502, "detail": "non-JSON from upstream"}
    if not repos:
        return {"ok": True, "status": 200, "detail": "📦 No repositories to check."}
    import asyncio as _asyncio  # noqa: PLC0415
    try:
        async with httpx.AsyncClient(verify=False, timeout=20.0,
                                     follow_redirects=True) as cli:
            results = await _asyncio.gather(
                *(_repo_failing_runs(cli, base, token, as_dict(rp))
                  for rp in repos[:_ACTIONS_REPO_CAP]),
                return_exceptions=True)
    except (httpx.HTTPError, OSError) as e:  # noqa: BLE001
        return {"ok": False, "status": 0,
                "detail": f"actions fetch failed: {type(e).__name__}: {e}"}
    items: list[dict] = []
    for res in results:
        if isinstance(res, list):
            items.extend(res)
    items = items[:_MAX_ROWS]
    if not items:
        return {"ok": True, "status": 200,
                "detail": f"✅ No failing Actions runs in the {min(len(repos), _ACTIONS_REPO_CAP)} "
                          f"most-recently-updated repos."}
    lines = [f"• {it['title']}" + (f"  ({it['subtitle']})" if it.get("subtitle") else "")
             for it in items]
    out: dict = {"ok": True, "status": 200,
                 "detail": f"❌ {len(items)} failing Actions run(s):\n" + "\n".join(lines)}
    return _attach_items(out, items, "apps.forgejo.failing_count")


async def _mirror_sync_skill(host_row: dict, chip: dict, *,
                             arg: Optional[str] = None,
                             host_id: Optional[str] = None) -> dict:
    """Action (arg): trigger a push/pull mirror sync for ONE repo. Resolves the
    repo by name (exact full_name / name first, then substring) from the user's
    repos, then ``POST /repos/{owner}/{repo}/mirror-sync``. Non-destructive — it
    only kicks an already-configured mirror. Never raises."""
    needle = (arg or "").strip()
    if not needle:
        return {"ok": False, "status": 0,
                "detail": "no repo given — say e.g. 'sync the omnigrid mirror'"}
    token, base, err = _resolve_skill_target(host_row, chip)
    if err:
        return err
    nl = needle.lower()
    print(f"[forgejo] INFO forgejo_mirror_sync host={host_id} target={needle!r}")
    r = await _skill_get(base, _API + "/user/repos", token=token,
                         params={"limit": "50"}, timeout=15.0, verb="fetch")
    if isinstance(r, dict):
        return r
    try:
        repos = [as_dict(rp) for rp in as_list(r.json())]
    except (ValueError, TypeError):
        return {"ok": False, "status": 502, "detail": "non-JSON from upstream"}
    target = None
    for rp in repos:  # exact full_name / name first
        if nl in (str(rp.get("full_name") or "").lower(), str(rp.get("name") or "").lower()):
            target = rp
            break
    if target is None:  # substring fallback
        for rp in repos:
            if nl in str(rp.get("full_name") or "").lower():
                target = rp
                break
    if target is None:
        return {"ok": False, "status": 404, "detail": f"no Forgejo repo matched \"{needle}\""}
    full = str(target.get("full_name") or "").strip()
    if "/" not in full:
        return {"ok": False, "status": 404, "detail": f"could not resolve owner/name for \"{needle}\""}
    if not target.get("mirror"):
        return {"ok": False, "status": 400,
                "detail": f"“{full}” is not a mirror repository (nothing to sync)"}
    owner, name = full.split("/", 1)
    try:
        async with httpx.AsyncClient(verify=False, timeout=20.0,
                                     follow_redirects=True) as cli:
            mr = await cli.post(base + _API + f"/repos/{owner}/{name}/mirror-sync",
                                headers=_headers(token))
    except (httpx.HTTPError, OSError) as e:  # noqa: BLE001
        return {"ok": False, "status": 0, "detail": f"mirror-sync failed: {type(e).__name__}: {e}"}
    if mr.status_code in (401, 403):
        return {"ok": False, "status": mr.status_code, "detail": "auth failed (check the Forgejo token)"}
    if mr.status_code not in (200, 202, 204):
        return {"ok": False, "status": mr.status_code, "detail": f"HTTP {mr.status_code}"}
    return {"ok": True, "status": 200, "detail": f"🔄 Triggered a mirror sync for “{full}”."}


# noinspection DuplicatedCode
async def _sync_all_mirrors_skill(host_row: dict, chip: dict, *,
                                  host_id: Optional[str] = None) -> dict:
    """Action (no arg): trigger a mirror sync for EVERY mirror repo the token can
    see — the fleet companion to ``forgejo_mirror_sync``. Lists ``/user/repos``,
    filters ``mirror == true``, then ``POST /repos/{owner}/{repo}/mirror-sync``
    for each (best-effort — a per-repo failure is skipped so one bad sync doesn't
    abort the rest). Non-destructive (only kicks already-configured mirrors).
    Never raises."""
    token, base, err = _resolve_skill_target(host_row, chip)
    if err:
        return err
    print(f"[forgejo] INFO forgejo_sync_all_mirrors host={host_id} (live, bounded repos)")
    r = await _skill_get(base, _API + "/user/repos", token=token,
                         params={"limit": "50"}, timeout=15.0, verb="fetch")
    if isinstance(r, dict):
        return r
    try:
        repos = [as_dict(rp) for rp in as_list(r.json())]
    except (ValueError, TypeError):
        return {"ok": False, "status": 502, "detail": "non-JSON from upstream"}
    mirrors = [rp for rp in repos
               if rp.get("mirror") and "/" in str(rp.get("full_name") or "")]
    if not mirrors:
        return {"ok": True, "status": 200,
                "detail": "🪞 No mirror repositories to sync."}
    synced = 0
    synced_names: list = []
    failed = 0
    try:
        async with httpx.AsyncClient(verify=False, timeout=20.0,
                                     follow_redirects=True) as cli:
            for rp in mirrors:
                full = str(rp.get("full_name") or "").strip()
                owner, name = full.split("/", 1)
                try:
                    mr = await cli.post(base + _API + f"/repos/{owner}/{name}/mirror-sync",
                                        headers=_headers(token))
                except (httpx.HTTPError, OSError) as e:  # noqa: BLE001
                    failed += 1
                    print(f"[forgejo] warning: sync_all_mirrors {full} skipped — "
                          f"{type(e).__name__}: {e}")
                    continue
                if mr.status_code in (200, 202, 204):
                    synced += 1
                    synced_names.append(full)
                else:
                    failed += 1
                    print(f"[forgejo] warning: sync_all_mirrors {full} HTTP {mr.status_code}")
    except (httpx.HTTPError, OSError) as e:  # noqa: BLE001
        return {"ok": False, "status": 0,
                "detail": f"sync-all-mirrors failed: {type(e).__name__}: {e}"}
    if not synced:
        return {"ok": False, "status": 502,
                "detail": f"None of the {len(mirrors):,} mirror(s) synced."}
    tail = (f" ({failed:,} failed)" if failed else "")
    return {"ok": True, "status": 200,
            "detail": f"🔄 Triggered a mirror sync for {synced:,} mirror(s){tail}: "
                      + "; ".join(synced_names)}
