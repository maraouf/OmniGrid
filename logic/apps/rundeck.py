"""Rundeck per-app module (runbook automation / job scheduler).

Wires a Rundeck server into the OmniGrid Apps surface following the per-app
contract (``unifi.py`` single-token-header shape):

    SLUGS               — catalog slugs this module handles.
    requires_api_key()  — True. Rundeck authenticates every call with a user API
                          token (Profile → User API Tokens) sent in the
                          ``X-Rundeck-Auth-Token`` header. The token lives in the
                          chip's ``api_key`` field.
    test_credential(host_row, chip, candidate_key) -> dict
    fetch_data(host_row, chip, *, host_id, service_idx, force) -> dict
    peek_latest(host_id, service_idx) -> dict | None    (AI context)
    SKILLS / run_skill  — status (read) + jobs (read, rich list) + running
                          executions (read, rich list) + run-a-job (write;
                          DESTRUCTIVE, arg).

Auth model: a Rundeck **user API token** in the ``X-Rundeck-Auth-Token`` header
(NOT a login). Every request sends ``Accept: application/json`` — Rundeck
defaults to XML otherwise. We pin API **v18** in the path (JSON-complete since
v14, and Rundeck accepts any version ≤ the server's max, so a low pin is
universally backward-compatible). Rundeck may serve HTTPS with an internal cert,
so TLS verification defaults OFF (per-chip ``verify_tls`` toggle). Single-
instance app (NOT fleet). No image proxy (no thumbnails).

The expanded card answers "is my automation healthy":

    projects              — Rundeck projects
    jobs                  — total job definitions across projects
    running               — executions running right now
    version               — Rundeck server version

Upstream API reference (base ``<rundeck-url>/api/18``):
``GET /system/info`` (``{system:{rundeck:{version}, stats:{…}}}``) ·
``GET /projects`` (``[{name,…}]``) · ``GET /project/{p}/jobs`` (``[{id,name,
group,project}]``) · ``GET /project/{p}/executions/running`` (``{executions:[…]}``)
· ``POST /job/{id}/run``.
"""
from __future__ import annotations

import asyncio
import time
from typing import Any, Optional

import httpx

from logic.apps._common import (
    cache_key, fetch_gate, peek_cache, resolve_base_url, resolve_cache_ttl)
from logic.coerce import as_dict, as_list, safe_int

# Catalog template slugs handled by this module.
SLUGS: tuple[str, ...] = ("rundeck",)

# Pinned API version — low + JSON-complete (since v14) so it works on every
# Rundeck (the server accepts any version ≤ its max).
_API = "/api/18"

DEFAULT_CACHE_TTL_S = 60
_data_cache: dict[str, tuple[float, dict]] = {}

# Bounds: projects fanned out per fetch + rich-item rows a list skill returns.
_MAX_PROJECTS = 20
_MAX_ROWS = 50

SKILLS: tuple[dict, ...] = (
    {
        "id": "rundeck_status",
        "name": "Rundeck status",
        "ai_phrases": ("rundeck status, how many rundeck jobs, automation status, "
                       "rundeck overview, are any jobs running, rundeck projects, "
                       "job scheduler status, runbook automation health"),
        "destructive": False,
    },
    {
        "id": "rundeck_jobs",
        "name": "List Rundeck jobs",
        "ai_phrases": ("list rundeck jobs, what jobs do i have, show automation "
                       "jobs, rundeck job list, what can i run, runbook list"),
        "destructive": False,
    },
    {
        "id": "rundeck_running",
        "name": "Running Rundeck executions",
        "ai_phrases": ("what's running in rundeck, running executions, active "
                       "jobs, what jobs are running now, rundeck running, "
                       "current executions"),
        "destructive": False,
    },
    {
        "id": "rundeck_executions",
        "name": "Recent Rundeck executions",
        "ai_phrases": ("recent rundeck executions, last executions, execution "
                       "history, did the job succeed or fail, last job runs, "
                       "rundeck run history, recent job results, what failed "
                       "in rundeck"),
        "destructive": False,
    },
    {
        "id": "rundeck_run_job",
        "name": "Run a Rundeck job",
        "ai_phrases": ("run the <name> job, execute <name>, trigger the <name> "
                       "rundeck job, start the <name> job, kick off <name>"),
        "arg": True,
        "arg_hint": "the Rundeck job name to run",
        "destructive": True,
    },
)


def requires_api_key() -> bool:
    """Rundeck authenticates every call with a user API token; the editor MUST
    render the token input (stored in the chip's api_key) + Test."""
    return True


def _verify(chip: dict) -> bool:
    """Whether to verify the upstream TLS certificate. Default False — a
    self-hosted Rundeck often runs an internal / self-signed cert; the operator
    flips the per-chip ``verify_tls`` toggle ON for a real cert."""
    return bool(chip.get("verify_tls"))


def _hdr(token: str) -> dict:
    """Rundeck API-token header + JSON Accept (Rundeck defaults to XML)."""
    return {"X-Rundeck-Auth-Token": token, "Accept": "application/json"}


async def _get(cli: "httpx.AsyncClient", url: str, token: str) -> Any:
    """GET a Rundeck endpoint; parsed JSON or None on non-2xx / parse failure."""
    r = await cli.get(url, headers=_hdr(token))
    if not (200 <= r.status_code < 300):
        return None
    try:
        return r.json()
    except (ValueError, TypeError):
        return None


def _version_str(info: Any) -> str:
    """Rundeck server version from ``GET /system/info``'s
    ``system.rundeck.version``; '' when absent."""
    rd = as_dict(as_dict(as_dict(info).get("system")).get("rundeck"))
    return str(rd.get("version") or "").strip()


async def _project_counts(cli: "httpx.AsyncClient", base: str, token: str,
                          project: str) -> "tuple[int, int]":
    """``(jobs, running)`` counts for one project. Best-effort — a failed sub-
    call contributes 0."""
    jobs_body, run_body = await asyncio.gather(
        _get(cli, base + _API + f"/project/{project}/jobs", token),
        _get(cli, base + _API + f"/project/{project}/executions/running", token),
    )
    jobs = len([j for j in as_list(jobs_body) if isinstance(j, dict)])
    running = len([e for e in as_list(as_dict(run_body).get("executions"))
                   if isinstance(e, dict)])
    return jobs, running


# noinspection DuplicatedCode
async def test_credential(host_row: dict, chip: dict, candidate_key: str, *,
                          payload: Optional[dict] = None, **_kw) -> dict:
    """Probe ``GET /api/18/system/info`` with the candidate token. Returns
    ``{ok, detail, status}``. Falls back to the chip's stored ``api_key`` when
    ``candidate_key`` is blank so the operator can re-test after first save."""
    pay = payload or {}
    token = (candidate_key or "").strip() or (chip.get("api_key") or "").strip()
    if not token:
        return {"ok": False, "detail": "API token required", "status": 0}
    base = resolve_base_url(host_row, chip)
    if not base:
        return {"ok": False, "detail": "no upstream URL configured", "status": 0}
    verify = bool(pay.get("verify_tls")) if "verify_tls" in pay else _verify(chip)
    url = base + _API + "/system/info"
    try:
        async with httpx.AsyncClient(verify=verify, timeout=10.0,
                                     follow_redirects=True) as cli:
            r = await cli.get(url, headers=_hdr(token))
    except (httpx.HTTPError, OSError) as e:  # noqa: BLE001
        return {"ok": False, "detail": f"{type(e).__name__}: {e}", "status": 0}
    if r.status_code in (401, 403):
        return {"ok": False,
                "detail": "auth failed (check the Rundeck API token — Profile → "
                          "User API Tokens)",
                "status": r.status_code}
    if r.status_code == 404:
        return {"ok": False,
                "detail": "404 — no Rundeck API here (check the chip URL points at "
                          "the Rundeck server, default port 4440)",
                "status": 404}
    if r.status_code != 200:
        return {"ok": False, "detail": f"HTTP {r.status_code}", "status": r.status_code}
    try:
        ver = _version_str(r.json())
    except (ValueError, TypeError):
        ver = ""
    return {"ok": True, "detail": f"OK{(' — Rundeck ' + ver) if ver else ''}",
            "status": 200}


async def fetch_data(host_row: dict, chip: dict, *,
                     host_id: str, service_idx: int,
                     force: bool = False) -> dict:
    """Fetch the Rundeck summary for the card: version + projects, then fan out
    job + running-execution counts per project (capped). Returns the card
    payload. Raises ``ValueError`` / ``RuntimeError`` (caller maps to
    HTTPException) when the token is unset / the base URL won't resolve / the
    upstream errors."""
    token = (chip.get("api_key") or "").strip()
    now = time.time()
    base, hit = fetch_gate(host_row, chip, host_id, service_idx, _data_cache,
                           resolve_cache_ttl(chip, DEFAULT_CACHE_TTL_S), now, force,
                           credential=token, log_tag="rundeck")
    if hit is not None:
        return hit
    try:
        async with httpx.AsyncClient(verify=_verify(chip), timeout=20.0,
                                     follow_redirects=True) as cli:
            info, projs_body = await asyncio.gather(
                _get(cli, base + _API + "/system/info", token),
                _get(cli, base + _API + "/projects", token),
            )
            if info is None and projs_body is None:
                raise RuntimeError(
                    "Rundeck API not reachable — check the token + that the chip "
                    "URL points at the Rundeck server (default port 4440)")
            projects = [str(p.get("name") or "").strip()
                        for p in as_list(projs_body) if isinstance(p, dict) and p.get("name")]
            per_proj = await asyncio.gather(*[
                _project_counts(cli, base, token, p) for p in projects[:_MAX_PROJECTS]
            ]) if projects else []
    except (httpx.HTTPError, OSError) as e:  # noqa: BLE001
        print(f"[rundeck] error: fetch host={host_id} base={base} "
              f"failed — {type(e).__name__}: {e}")
        raise RuntimeError(f"upstream fetch failed: {type(e).__name__}: {e}")

    jobs = sum(j for j, _ in per_proj)
    running = sum(r for _, r in per_proj)

    out: dict[str, Any] = {
        "available": True,
        "version": _version_str(info),
        "projects": len(projects),
        "jobs": jobs,
        "running": running,
        "fetched_at": int(now),
    }
    print(f"[rundeck] INFO fetched host={host_id} projects={out['projects']} "
          f"jobs={out['jobs']} running={out['running']} ver={out['version'] or '-'}")
    _data_cache[cache_key(host_id, service_idx)] = (now, out)
    return out


def peek_latest(host_id: str, service_idx: int) -> Optional[dict]:
    """Cache-only peek (no upstream call) for the AI context's
    ``app_skills[].last``."""
    data = peek_cache(_data_cache, host_id, service_idx)
    if not isinstance(data, dict) or not data.get("available"):
        return None
    return {
        "version": data.get("version") or "",
        "projects": safe_int(data.get("projects")),
        "jobs": safe_int(data.get("jobs")),
        "running": safe_int(data.get("running")),
        "fetched_at": safe_int(data.get("fetched_at")),
    }


# ---------------------------------------------------------------------------
# Skills
# ---------------------------------------------------------------------------
def _resolve_skill_target(host_row: dict, chip: dict) -> "tuple[str, str, Optional[dict]]":
    """Resolve ``(token, base)`` or a ready ``{ok: False, detail}`` error dict
    for a Rundeck skill."""
    token = (chip.get("api_key") or "").strip()
    if not token:
        return "", "", {"ok": False, "status": 0, "detail": "Rundeck API token not set"}
    base = resolve_base_url(host_row, chip)
    if not base:
        return "", "", {"ok": False, "status": 0, "detail": "no upstream URL configured"}
    return token, base, None


def _attach_items(out: dict, items: list, count_i18n: str) -> dict:
    """Attach the rich-item list + count + count-i18n key to a skill result
    (no-op when empty). Returns ``out`` for one-line use."""
    if items:
        out["items"] = items
        out["count"] = len(items)
        out["count_i18n"] = count_i18n
    return out


# noinspection DuplicatedCode
async def _all_jobs(cli: "httpx.AsyncClient", base: str, token: str) -> list:
    """Every job across the console's projects (capped). Each row carries its
    project + group so the skills can label + resolve by name."""
    projs = await _get(cli, base + _API + "/projects", token)
    names = [str(p.get("name") or "").strip()
             for p in as_list(projs) if isinstance(p, dict) and p.get("name")]
    nested = await asyncio.gather(*[
        _get(cli, base + _API + f"/project/{p}/jobs", token) for p in names[:_MAX_PROJECTS]
    ]) if names else []
    jobs: list = []
    for body in nested:
        jobs.extend(j for j in as_list(body) if isinstance(j, dict))
    return jobs


async def run_skill(skill_id: str, host_row: dict, chip: dict, *,
                    host_id: Optional[str] = None,
                    service_idx: Optional[int] = None,
                    arg: Optional[str] = None, **_kw) -> dict:
    """Dispatch one of this app's SKILLS. Returns ``{ok, detail, status?}``.
    Raises ValueError on an unknown skill id (route maps to HTTP 404)."""
    if skill_id == "rundeck_status":
        return await _status_skill(host_row, chip, host_id=host_id,
                                   service_idx=service_idx)
    if skill_id == "rundeck_jobs":
        return await _jobs_skill(host_row, chip, host_id=host_id)
    if skill_id == "rundeck_running":
        return await _running_skill(host_row, chip, host_id=host_id)
    if skill_id == "rundeck_executions":
        return await _executions_skill(host_row, chip, host_id=host_id)
    if skill_id == "rundeck_run_job":
        return await _run_job_skill(host_row, chip, arg=arg, host_id=host_id)
    raise ValueError(f"unknown skill: {skill_id!r}")


async def _status_skill(host_row: dict, chip: dict, *,
                        host_id: Optional[str] = None,
                        service_idx: Optional[int] = None) -> dict:
    """Read-only: live-fetch the automation summary. Never raises."""
    print(f"[rundeck] INFO rundeck_status host={host_id} svc_idx={service_idx} "
          f"(live fetch)")
    try:
        data = await fetch_data(host_row, chip, host_id=str(host_id or ""),
                                service_idx=int(service_idx or 0), force=True)
    except (ValueError, RuntimeError) as e:
        return {"ok": False, "detail": str(e), "status": 0}
    running = safe_int(data.get("running"))
    lines = [
        f"🗂️ Projects: {safe_int(data.get('projects'))} · "
        f"⚙️ Jobs: {safe_int(data.get('jobs'))}",
        f"▶️ Running now: {running}",
    ]
    ver = str(data.get("version") or "").strip()
    if ver:
        lines.append(f"· Rundeck {ver}")
    return {"ok": True, "detail": "\n".join(lines), "status": 200,
            "jobs": safe_int(data.get("jobs")), "running": running}


async def _jobs_skill(host_row: dict, chip: dict, *,
                      host_id: Optional[str] = None) -> dict:
    """Read-only: list job definitions as rich rows (name + project / group).
    Never raises."""
    token, base, err = _resolve_skill_target(host_row, chip)
    if err:
        return err
    print(f"[rundeck] INFO rundeck_jobs host={host_id} (live fetch)")
    try:
        async with httpx.AsyncClient(verify=_verify(chip), timeout=20.0,
                                     follow_redirects=True) as cli:
            jobs = await _all_jobs(cli, base, token)
    except (httpx.HTTPError, OSError) as e:  # noqa: BLE001
        return {"ok": False, "status": 0, "detail": f"fetch failed: {type(e).__name__}: {e}"}
    if not jobs:
        return {"ok": True, "status": 200, "detail": "⚙️ No Rundeck jobs found."}
    jobs.sort(key=lambda job: (str(job.get("project") or "").lower(),
                               str(job.get("group") or "").lower(),
                               str(job.get("name") or "").lower()))
    items: list = []
    lines: list = []
    for j in jobs[:_MAX_ROWS]:
        jid = str(j.get("id") or "").strip()
        name = str(j.get("name") or "").strip() or jid
        proj = str(j.get("project") or "").strip()
        group = str(j.get("group") or "").strip()
        bits = [b for b in (proj, group) if b]
        sub = " · ".join(bits) if bits else "job"
        # Per-row ▶ "Run now" button → dispatches rundeck_run_job against this
        # job's ID (unambiguous vs. resolving by name), confirm-gated since
        # running a job is a real state change. (Falls back to the name when
        # the API omitted the id, which run_job still resolves.)
        items.append({
            "title": name,
            "subtitle": sub,
            "row_action": {
                "skill_id": "rundeck_run_job",
                "arg": jid or name,
                "destructive": True,
                "icon": "play",
                "title_i18n": "apps.rundeck.run_now",
                "confirm_i18n": "apps.rundeck.run_job_confirm",
                "confirm_text_i18n": "apps.rundeck.run_now",
            },
        })
        lines.append(f"• {name}  ({sub})")
    out: dict = {"ok": True, "status": 200,
                 "detail": "⚙️ Rundeck jobs:\n" + "\n".join(lines)}
    return _attach_items(out, items, "apps.rundeck.jobs_count")


# noinspection DuplicatedCode
async def _running_skill(host_row: dict, chip: dict, *,
                         host_id: Optional[str] = None) -> dict:
    """Read-only: list currently-running executions as rich rows (job name +
    project + status). Never raises."""
    token, base, err = _resolve_skill_target(host_row, chip)
    if err:
        return err
    print(f"[rundeck] INFO rundeck_running host={host_id} (live fetch)")
    try:
        async with httpx.AsyncClient(verify=_verify(chip), timeout=20.0,
                                     follow_redirects=True) as cli:
            projs = await _get(cli, base + _API + "/projects", token)
            names = [str(p.get("name") or "").strip()
                     for p in as_list(projs) if isinstance(p, dict) and p.get("name")]
            nested = await asyncio.gather(*[
                _get(cli, base + _API + f"/project/{p}/executions/running", token)
                for p in names[:_MAX_PROJECTS]
            ]) if names else []
    except (httpx.HTTPError, OSError) as e:  # noqa: BLE001
        return {"ok": False, "status": 0, "detail": f"fetch failed: {type(e).__name__}: {e}"}
    execs: list = []
    for body in nested:
        execs.extend(e for e in as_list(as_dict(body).get("executions")) if isinstance(e, dict))
    if not execs:
        return {"ok": True, "status": 200, "detail": "▶️ No Rundeck executions running."}
    items: list = []
    lines: list = []
    for e in execs[:_MAX_ROWS]:
        job = as_dict(e.get("job"))
        name = str(job.get("name") or "").strip() or f"execution #{safe_int(e.get('id'))}"
        proj = str(e.get("project") or job.get("project") or "").strip()
        user = str(e.get("user") or "").strip()
        bits = [b for b in (proj, (f"by {user}" if user else "")) if b]
        sub = "🟢 running" + ((" · " + " · ".join(bits)) if bits else "")
        items.append({"title": name, "subtitle": sub})
        lines.append(f"• {name}  ({sub})")
    out: dict = {"ok": True, "status": 200,
                 "detail": f"▶️ {len(execs)} execution(s) running:\n" + "\n".join(lines)}
    return _attach_items(out, items, "apps.rundeck.running_count")


# Rundeck execution status → emoji for the recent-executions list.
_EXEC_STATUS_EMOJI = {
    "succeeded": "✅", "failed": "❌", "aborted": "⏹️", "running": "🟢",
    "timedout": "⏱️", "timeout": "⏱️", "scheduled": "🕒", "queued": "🕒",
    "missed": "⚠️", "partial": "🟡", "other": "🟡", "incomplete": "🟡",
}


def _exec_status_label(status: str) -> "tuple[str, str]":
    """``(emoji, Title-cased label)`` for a Rundeck execution status. Unknown
    statuses get a neutral ⚪ + the raw value."""
    s = (status or "").strip().lower()
    return _EXEC_STATUS_EMOJI.get(s, "⚪"), (s.capitalize() if s else "unknown")


def _exec_started_ms(e: dict) -> int:
    """Execution start time in epoch-ms for newest-first sorting (Rundeck's
    ``date-started`` is ``{unixtime, date}``); 0 when absent."""
    ds = e.get("date-started")
    return safe_int(as_dict(ds).get("unixtime")) if isinstance(ds, dict) else 0


# noinspection DuplicatedCode
async def _executions_skill(host_row: dict, chip: dict, *,
                            host_id: Optional[str] = None) -> dict:
    """Read-only: the most recent executions across projects with their final
    status (✅ succeeded / ❌ failed / ⏹️ aborted / 🟢 running / ⏱️ timed out /
    🟡 partial / …), newest first. Never raises."""
    token, base, err = _resolve_skill_target(host_row, chip)
    if err:
        return err
    print(f"[rundeck] INFO rundeck_executions host={host_id} (live fetch)")
    try:
        async with httpx.AsyncClient(verify=_verify(chip), timeout=20.0,
                                     follow_redirects=True) as cli:
            projs = await _get(cli, base + _API + "/projects", token)
            names = [str(p.get("name") or "").strip()
                     for p in as_list(projs) if isinstance(p, dict) and p.get("name")]
            nested = await asyncio.gather(*[
                _get(cli, base + _API + f"/project/{p}/executions?max=10", token)
                for p in names[:_MAX_PROJECTS]
            ]) if names else []
    except (httpx.HTTPError, OSError) as e:  # noqa: BLE001
        return {"ok": False, "status": 0, "detail": f"fetch failed: {type(e).__name__}: {e}"}
    execs: list = []
    for body in nested:
        execs.extend(e for e in as_list(as_dict(body).get("executions"))
                     if isinstance(e, dict))
    if not execs:
        return {"ok": True, "status": 200, "detail": "📜 No recent Rundeck executions."}
    execs.sort(key=_exec_started_ms, reverse=True)
    items: list = []
    lines: list = []
    for e in execs[:_MAX_ROWS]:
        job = as_dict(e.get("job"))
        name = str(job.get("name") or "").strip() or f"execution #{safe_int(e.get('id'))}"
        proj = str(e.get("project") or job.get("project") or "").strip()
        user = str(e.get("user") or "").strip()
        emoji, label = _exec_status_label(str(e.get("status") or ""))
        bits = [f"{emoji} {label}"]
        if proj:
            bits.append(proj)
        if user:
            bits.append(f"by {user}")
        sub = " · ".join(bits)
        items.append({"title": name, "subtitle": sub})
        lines.append(f"• {name}  ({sub})")
    out: dict = {"ok": True, "status": 200,
                 "detail": "📜 Recent Rundeck executions:\n" + "\n".join(lines)}
    return _attach_items(out, items, "apps.rundeck.executions_count")


async def _run_job_skill(host_row: dict, chip: dict, *,
                         arg: Optional[str],
                         host_id: Optional[str] = None) -> dict:
    """DESTRUCTIVE: run ONE job by name. Resolves the job across the console's
    projects, then POSTs ``/job/{id}/run``. Never raises."""
    needle = (arg or "").strip()
    if not needle:
        return {"ok": False, "status": 0,
                "detail": "no job name given (say e.g. \"run the Nightly Backup job\")"}
    token, base, err = _resolve_skill_target(host_row, chip)
    if err:
        return err
    needle_l = needle.lower()
    print(f"[rundeck] INFO rundeck_run_job host={host_id} target={needle!r}")
    try:
        async with httpx.AsyncClient(verify=_verify(chip), timeout=20.0,
                                     follow_redirects=True) as cli:
            jobs = await _all_jobs(cli, base, token)
            match_id = ""
            match_name = ""
            for j in jobs:
                jid = str(j.get("id") or "").strip()
                jname = str(j.get("name") or "").strip()
                # Exact job-ID match first (the per-row ▶ Run-now button passes
                # the id — unambiguous); then exact name; then substring (the
                # AI / Telegram free-text path passes a name).
                if jid and jid == needle:
                    match_id, match_name = jid, jname
                    break
                if jname.lower() == needle_l:
                    match_id, match_name = jid, jname
                    break
                if not match_id and needle_l in jname.lower():
                    match_id, match_name = jid, jname
            if not match_id:
                return {"ok": False, "status": 404,
                        "detail": f"no Rundeck job matched \"{needle}\""}
            ar = await cli.post(base + _API + f"/job/{match_id}/run",
                                headers=_hdr(token))
    except (httpx.HTTPError, OSError) as e:  # noqa: BLE001
        return {"ok": False, "status": 0, "detail": f"run failed: {type(e).__name__}: {e}"}
    if ar.status_code in (401, 403):
        return {"ok": False, "status": ar.status_code,
                "detail": "auth failed (check the Rundeck API token)"}
    if not (200 <= ar.status_code < 300):
        return {"ok": False, "status": ar.status_code, "detail": f"HTTP {ar.status_code}"}
    try:
        eid = str(as_dict(ar.json()).get("id") or "")
    except (ValueError, TypeError):
        eid = ""
    tail = f" (execution #{eid})" if eid else ""
    return {"ok": True, "status": 200,
            "detail": f"▶️ Started the \"{match_name}\" job{tail}."}
