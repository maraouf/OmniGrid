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
    SKILLS / run_skill  — status (read; + recent success/failure rate) + jobs
                          (read, rich list, per-row Run-now) + running (read,
                          rich list, per-row Abort) + executions (read, rich
                          list, per-row Retry on failed) + run-a-job / abort /
                          retry (write; DESTRUCTIVE, arg).

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
from logic.tuning import Tunable as _Tunable
from logic.tuning import tuning_int as _tuning_int

# Catalog template slugs handled by this module.
SLUGS: tuple[str, ...] = ("rundeck",)

# Pinned API version — low + JSON-complete (since v14) so it works on every
# Rundeck (the server accepts any version ≤ its max).
_API = "/api/18"
# Retry needs a newer endpoint (POST /execution/{id}/retry, added in API v24);
# called at this version specifically. On an older server it returns an
# unsupported-version error, which _retry_skill surfaces clearly.
_API_RETRY = "/api/24"
# The job-forecast endpoint (next-scheduled-run time) needs API v31+; called at
# this version. Best-effort — on an older server / no scheduled jobs the
# next-run stat is simply absent (the card degrades to the scheduled count).
_API_FORECAST = "/api/31"
# Cap on scheduled jobs forecast per fetch (each is one extra call — bound it so
# a console with many scheduled jobs doesn't fan out unboundedly).
_MAX_FORECAST = 8

DEFAULT_CACHE_TTL_S = 60
_data_cache: dict[str, tuple[float, dict]] = {}

# Bounds: projects fanned out per fetch + rich-item rows a list skill returns.
_MAX_PROJECTS = 20
_MAX_ROWS = 50
# Recent executions fetched per project to compute the success/failure rate.
_RECENT_MAX = 15
# Execution statuses that count as a FAILED run for the failure-rate signal
# (a timed-out run is a failure; an aborted run is an operator stop, not a
# failure of the job, so it's tallied separately and excluded from "failed").
_FAILED_STATUSES = frozenset({"failed", "timedout", "timeout"})
_SUCCESS_STATUSES = frozenset({"succeeded"})


def _tally_statuses(statuses: list) -> "tuple[int, int, int, int]":
    """Tally recent execution statuses → ``(completed, failed, succeeded,
    aborted)``. ``completed`` excludes still-running / scheduled / queued runs
    so the failure-rate is over FINISHED executions only."""
    failed = sum(1 for s in statuses if s in _FAILED_STATUSES)
    succeeded = sum(1 for s in statuses if s in _SUCCESS_STATUSES)
    aborted = sum(1 for s in statuses if s == "aborted")
    return failed + succeeded + aborted, failed, succeeded, aborted


def _exec_ms(field: Any) -> int:
    """Epoch-MILLISECONDS from a Rundeck ``{unixtime, date}`` timestamp field
    (``date-started`` / ``date-ended``); 0 when absent."""
    return safe_int(as_dict(field).get("unixtime")) if isinstance(field, dict) else 0


def _exec_duration_ms(e: dict) -> int:
    """Duration in ms of a FINISHED execution (ended − started); 0 when either
    bound is missing or the run is still in flight."""
    started = _exec_ms(e.get("date-started"))
    ended = _exec_ms(e.get("date-ended"))
    return ended - started if (started and ended and ended > started) else 0


def _parse_iso(s: str) -> float:
    """Parse an ISO-8601 timestamp (Rundeck forecast times, e.g.
    ``2026-06-14T16:00:00Z``) to epoch seconds; 0.0 on any parse failure."""
    s = (s or "").strip()
    if not s:
        return 0.0
    try:
        from datetime import datetime  # noqa: PLC0415
        return datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()
    except (ValueError, TypeError):
        return 0.0


def _soonest_future(forecast_body: Any) -> "Optional[int]":
    """Seconds until the soonest FUTURE scheduled execution in a job-forecast
    response. Walks ``futureScheduledExecutions`` (items are ISO strings on some
    builds, ``{time}`` / ``{date}`` dicts on others). ``None`` when there's no
    parseable future time."""
    fse = as_dict(forecast_body).get("futureScheduledExecutions")
    now = time.time()
    best: "Optional[float]" = None
    for item in as_list(fse):
        iso = item if isinstance(item, str) else str(
            as_dict(item).get("time") or as_dict(item).get("date") or "")
        ep = _parse_iso(iso)
        if ep and ep > now:
            delta = ep - now
            if best is None or delta < best:
                best = delta
    return int(best) if best is not None else None


async def _forecast_next_run(cli: "httpx.AsyncClient", base: str, token: str,
                             scheduled: list) -> "Optional[tuple[str, int]]":
    """Soonest upcoming scheduled run across the scheduled jobs →
    ``(job_name, seconds_until)`` or ``None``. Best-effort: needs API v31+ (the
    forecast endpoint); on an older server every call 404s and this returns
    ``None`` (the card just shows the scheduled count). Bounded by
    ``_MAX_FORECAST`` + run in parallel."""
    if not scheduled:
        return None

    async def _one(jid: str, jname: str) -> "Optional[tuple[str, int]]":
        body = await _get(cli, base + _API_FORECAST + f"/job/{jid}/forecast?time=7d&max=1",
                          token)
        secs = _soonest_future(body)
        return (jname, secs) if secs is not None else None

    results = await asyncio.gather(*[
        _one(jid, jname) for jid, jname in scheduled[:_MAX_FORECAST]
    ], return_exceptions=True)
    best: "Optional[tuple[str, int]]" = None
    best_secs: "Optional[int]" = None
    for r in results:
        if not (isinstance(r, tuple) and len(r) == 2):
            continue
        cand_secs = int(r[1])
        if best_secs is None or cand_secs < best_secs:
            # Reconstruct the tuple with explicit element types — `isinstance`
            # narrows `r` to a bare (unparameterized) `tuple`, which the checker
            # won't accept into the `tuple[str, int]` target directly.
            best, best_secs = (str(r[0]), cand_secs), cand_secs
    return best


def _fmt_secs(secs: int) -> str:
    """Compact human duration for a second count (backend skill text — English):
    ``Xh Ym`` / ``Ym Zs`` / ``Zs``. '' for non-positive."""
    secs = int(secs)
    if secs <= 0:
        return ""
    if secs >= 3600:
        return f"{secs // 3600}h {(secs % 3600) // 60}m"
    if secs >= 60:
        return f"{secs // 60}m {secs % 60}s"
    return f"{secs}s"


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
    {
        "id": "rundeck_abort",
        "name": "Abort a Rundeck execution",
        "ai_phrases": ("abort the rundeck execution, kill the stuck job, stop "
                       "execution <id>, abort execution <id>, cancel the running "
                       "rundeck job, stop the running execution"),
        "arg": True,
        "arg_hint": "the Rundeck execution id to abort",
        "destructive": True,
    },
    {
        "id": "rundeck_retry",
        "name": "Retry a failed Rundeck execution",
        "ai_phrases": ("retry the failed rundeck job, rerun execution <id>, retry "
                       "execution <id>, try the failed job again, re-run the "
                       "failed rundeck execution"),
        "arg": True,
        "arg_hint": "the Rundeck execution id to retry",
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


async def _project_summary(cli: "httpx.AsyncClient", base: str, token: str,
                           project: str) -> "tuple[int, int, list, list, list, int]":
    """``(jobs, running, recent_statuses, durations_ms, scheduled, day_count)``
    for one project. Best-effort — a failed sub-call contributes 0 / []. Where:
    ``recent_statuses`` is the lowercased status of the project's most-recent
    executions (failure-rate); ``durations_ms`` is each FINISHED recent
    execution's run time (avg-duration stat); ``scheduled`` is ``(job_id,
    job_name)`` for each schedule-enabled job (scheduled-count + next-run
    forecast); ``day_count`` is the number of executions in the last 24h
    (``recentFilter=1d`` → ``paging.total`` — the executions-per-day rate)."""
    jobs_body, run_body, exec_body, day_body = await asyncio.gather(
        _get(cli, base + _API + f"/project/{project}/jobs", token),
        _get(cli, base + _API + f"/project/{project}/executions/running", token),
        _get(cli, base + _API + f"/project/{project}/executions?max={_RECENT_MAX}", token),
        # recentFilter=1d + max=1: we only need paging.total (the count of
        # executions started in the last day) — no need to fetch the rows.
        _get(cli, base + _API + f"/project/{project}/executions?recentFilter=1d&max=1", token),
    )
    job_list = [j for j in as_list(jobs_body) if isinstance(j, dict)]
    jobs = len(job_list)
    # Schedule-enabled jobs (scheduled flag set AND not explicitly disabled).
    scheduled: list = []
    for j in job_list:
        if j.get("scheduled") and j.get("scheduleEnabled", True):
            jid = str(j.get("id") or "").strip()
            if jid:
                scheduled.append((jid, str(j.get("name") or "").strip() or jid))
    running = len([e for e in as_list(as_dict(run_body).get("executions"))
                   if isinstance(e, dict)])
    exec_list = [e for e in as_list(as_dict(exec_body).get("executions"))
                 if isinstance(e, dict)]
    statuses = [str(e.get("status") or "").strip().lower() for e in exec_list]
    durations_ms = [d for d in (_exec_duration_ms(e) for e in exec_list) if d > 0]
    # paging.total is the exact last-24h count; fall back to the returned-row
    # count if a build omits the paging block.
    day_paging = as_dict(as_dict(day_body).get("paging"))
    day_count = safe_int(day_paging.get("total")) or len(
        [e for e in as_list(as_dict(day_body).get("executions")) if isinstance(e, dict)])
    return jobs, running, statuses, durations_ms, scheduled, day_count


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
                _project_summary(cli, base, token, p) for p in projects[:_MAX_PROJECTS]
            ]) if projects else []

            jobs = sum(j for j, _, _, _, _, _ in per_proj)
            running = sum(r for _, r, _, _, _, _ in per_proj)
            executions_per_day = sum(dc for _, _, _, _, _, dc in per_proj)
            all_statuses: list = []
            all_durations: list = []
            scheduled_all: list = []
            for _, _, sts, durs, sched, _ in per_proj:
                all_statuses.extend(sts)
                all_durations.extend(durs)
                scheduled_all.extend(sched)
            # Next scheduled run across schedule-enabled jobs (best-effort,
            # needs API v31+) — inside the client block so it shares the session.
            next_run = await _forecast_next_run(cli, base, token, scheduled_all)
    except (httpx.HTTPError, OSError) as e:  # noqa: BLE001
        print(f"[rundeck] error: fetch host={host_id} base={base} "
              f"failed — {type(e).__name__}: {e}")
        raise RuntimeError(f"upstream fetch failed: {type(e).__name__}: {e}")

    completed, failed, succeeded, aborted = _tally_statuses(all_statuses)
    failure_rate = round(failed / completed * 100, 1) if completed else 0.0
    avg_duration_s = round(sum(all_durations) / len(all_durations) / 1000, 1) \
        if all_durations else 0.0
    # Per-project failure-rate breakdown (which project is flakiest) — each
    # project's own completed/failed tally from its recent statuses. Flakiest
    # first; only projects with finished executions are included.
    per_project_failure: list = []
    for pname, psum in zip(projects[:_MAX_PROJECTS], per_proj):
        pc, pf, _ps, _pa = _tally_statuses(psum[2])
        if pc > 0:
            per_project_failure.append({
                "name": pname, "failure_rate": round(pf / pc * 100, 1),
                "completed": pc, "failed": pf})
    per_project_failure.sort(
        key=lambda x: (-x["failure_rate"], -x["failed"], x["name"].lower()))
    per_project_failure = per_project_failure[:_MAX_ROWS]
    flakiest = next((p for p in per_project_failure if p["failed"] > 0), {})

    out: dict[str, Any] = {
        "available": True,
        "version": _version_str(info),
        "projects": len(projects),
        "jobs": jobs,
        "running": running,
        "scheduled_jobs": len(scheduled_all),
        # Executions started in the last 24h across projects (throughput rate).
        "executions_per_day": executions_per_day,
        # Recent execution outcomes across projects (the CI-health signal).
        "recent_completed": completed,
        "recent_failed": failed,
        "recent_succeeded": succeeded,
        "recent_aborted": aborted,
        "failure_rate": failure_rate,
        "avg_duration_s": avg_duration_s,
        # Per-project failure-rate breakdown (which project is flakiest) +
        # the flakiest project as a one-line at-a-glance (empty when nothing
        # has failed). The breakdown list is drawer-only on the SPA.
        "per_project_failure": per_project_failure,
        "flakiest_project": flakiest.get("name") or "",
        "flakiest_rate": flakiest.get("failure_rate") or 0.0,
        "next_run_job": next_run[0] if next_run else "",
        "next_run_in_s": next_run[1] if next_run else 0,
        "fetched_at": int(now),
    }
    # Failure-rate / executions trend from the lifespan sampler (Rundeck keeps
    # its own history, but a glanceable local rollup is the at-a-glance signal).
    try:
        from logic.apps import rundeck_sampler as _rd_sampler  # noqa: PLC0415
        out["trend"] = _rd_sampler.trend_summary(
            str(host_id or ""), int(service_idx or 0),
            days=_tuning_int(_Tunable.RUNDECK_HISTORY_DAYS))
    except Exception as e:  # noqa: BLE001
        print(f"[rundeck] trend_summary skipped: {type(e).__name__}: {e}")
    print(f"[rundeck] INFO fetched host={host_id} projects={out['projects']} "
          f"jobs={out['jobs']} running={out['running']} "
          f"recent={failed}/{completed} failed ver={out['version'] or '-'}")
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
        "recent_completed": safe_int(data.get("recent_completed")),
        "recent_failed": safe_int(data.get("recent_failed")),
        "failure_rate": data.get("failure_rate") or 0.0,
        "flakiest_project": data.get("flakiest_project") or "",
        "flakiest_rate": data.get("flakiest_rate") or 0.0,
        "scheduled_jobs": safe_int(data.get("scheduled_jobs")),
        "executions_per_day": safe_int(data.get("executions_per_day")),
        "avg_duration_s": data.get("avg_duration_s") or 0.0,
        "next_run_job": data.get("next_run_job") or "",
        "next_run_in_s": safe_int(data.get("next_run_in_s")),
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
    if skill_id == "rundeck_abort":
        return await _abort_skill(host_row, chip, arg=arg, host_id=host_id)
    if skill_id == "rundeck_retry":
        return await _retry_skill(host_row, chip, arg=arg, host_id=host_id)
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
    completed = safe_int(data.get("recent_completed"))
    failed = safe_int(data.get("recent_failed"))
    lines = [
        f"🗂️ Projects: {safe_int(data.get('projects'))} · "
        f"⚙️ Jobs: {safe_int(data.get('jobs'))}",
        f"▶️ Running now: {running}",
    ]
    if completed:
        emoji = "❌" if failed else "✅"
        lines.append(f"{emoji} Recent runs: {failed}/{completed} failed "
                     f"({data.get('failure_rate') or 0}% failure rate)")
    flakiest = str(data.get("flakiest_project") or "").strip()
    if flakiest:
        lines.append(f"🎯 Flakiest project: {flakiest} "
                     f"({data.get('flakiest_rate') or 0}% failure rate)")
    avg_dur = _fmt_secs(round(float(data.get("avg_duration_s") or 0)))
    if avg_dur:
        lines.append(f"⏱️ Avg run time: {avg_dur}")
    per_day = safe_int(data.get("executions_per_day"))
    if per_day:
        lines.append(f"📈 Runs today: {per_day} (last 24h)")
    scheduled = safe_int(data.get("scheduled_jobs"))
    if scheduled:
        lines.append(f"🗓️ Scheduled jobs: {scheduled}")
    nr_job = str(data.get("next_run_job") or "").strip()
    nr_in = _fmt_secs(safe_int(data.get("next_run_in_s")))
    if nr_job and nr_in:
        lines.append(f"⏭️ Next run: {nr_job} in {nr_in}")
    ver = str(data.get("version") or "").strip()
    if ver:
        lines.append(f"· Rundeck {ver}")
    return {"ok": True, "detail": "\n".join(lines), "status": 200,
            "jobs": safe_int(data.get("jobs")), "running": running,
            "recent_failed": failed, "recent_completed": completed,
            "scheduled_jobs": scheduled}


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
        eid = str(safe_int(e.get("id")) or "").strip()
        name = str(job.get("name") or "").strip() or f"execution #{eid}"
        proj = str(e.get("project") or job.get("project") or "").strip()
        user = str(e.get("user") or "").strip()
        bits = [b for b in (proj, (f"by {user}" if user else "")) if b]
        sub = "🟢 running" + ((" · " + " · ".join(bits)) if bits else "")
        item: dict = {"title": name, "subtitle": sub}
        # Per-row ⏹ Abort button → dispatches rundeck_abort against this
        # execution's id (the "kill the stuck job" action), confirm-gated.
        if eid:
            item["row_action"] = {
                "skill_id": "rundeck_abort",
                "arg": eid,
                "destructive": True,
                "icon": "square",
                "title_i18n": "apps.rundeck.abort",
                "confirm_i18n": "apps.rundeck.abort_confirm",
                "confirm_text_i18n": "apps.rundeck.abort",
            }
        items.append(item)
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
        eid = str(safe_int(e.get("id")) or "").strip()
        name = str(job.get("name") or "").strip() or f"execution #{eid}"
        proj = str(e.get("project") or job.get("project") or "").strip()
        user = str(e.get("user") or "").strip()
        status = str(e.get("status") or "").strip().lower()
        emoji, label = _exec_status_label(status)
        bits = [f"{emoji} {label}"]
        if proj:
            bits.append(proj)
        if user:
            bits.append(f"by {user}")
        sub = " · ".join(bits)
        item: dict = {"title": name, "subtitle": sub}
        # Per-row 🔁 Retry button on FAILED / timed-out runs → dispatches
        # rundeck_retry against this execution's id, confirm-gated.
        if eid and status in _FAILED_STATUSES:
            item["row_action"] = {
                "skill_id": "rundeck_retry",
                "arg": eid,
                "destructive": True,
                "icon": "refresh-cw",
                "title_i18n": "apps.rundeck.retry",
                "confirm_i18n": "apps.rundeck.retry_confirm",
                "confirm_text_i18n": "apps.rundeck.retry",
            }
        items.append(item)
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


async def _abort_skill(host_row: dict, chip: dict, *,
                       arg: Optional[str],
                       host_id: Optional[str] = None) -> dict:
    """DESTRUCTIVE: abort a running execution by id (``POST /execution/{id}/
    abort``) — the "kill the stuck job" companion to the running list. Never
    raises."""
    eid = (arg or "").strip()
    if not eid:
        return {"ok": False, "status": 0,
                "detail": "no execution id given (run \"running Rundeck "
                          "executions\" first)"}
    token, base, err = _resolve_skill_target(host_row, chip)
    if err:
        return err
    print(f"[rundeck] INFO rundeck_abort host={host_id} execution={eid!r}")
    try:
        async with httpx.AsyncClient(verify=_verify(chip), timeout=20.0,
                                     follow_redirects=True) as cli:
            ar = await cli.post(base + _API + f"/execution/{eid}/abort",
                                headers=_hdr(token))
    except (httpx.HTTPError, OSError) as e:  # noqa: BLE001
        return {"ok": False, "status": 0, "detail": f"abort failed: {type(e).__name__}: {e}"}
    if ar.status_code in (401, 403):
        return {"ok": False, "status": ar.status_code,
                "detail": "auth failed (check the Rundeck API token)"}
    if ar.status_code == 404:
        return {"ok": False, "status": 404,
                "detail": f"no Rundeck execution #{eid} (already finished?)"}
    if not (200 <= ar.status_code < 300):
        return {"ok": False, "status": ar.status_code, "detail": f"HTTP {ar.status_code}"}
    try:
        abort_status = str(as_dict(as_dict(ar.json()).get("abort")).get("status") or "").strip()
    except (ValueError, TypeError):
        abort_status = ""
    tail = f" ({abort_status})" if abort_status else ""
    return {"ok": True, "status": 200,
            "detail": f"⏹️ Abort requested for execution #{eid}{tail}."}


async def _retry_skill(host_row: dict, chip: dict, *,
                       arg: Optional[str],
                       host_id: Optional[str] = None) -> dict:
    """DESTRUCTIVE: re-run a failed execution by id (``POST /api/24/execution/
    {id}/retry``) — re-runs the job with the same options. Needs Rundeck API
    v24+; on an older server the unsupported-version error is surfaced clearly.
    Never raises."""
    eid = (arg or "").strip()
    if not eid:
        return {"ok": False, "status": 0,
                "detail": "no execution id given (run \"recent Rundeck "
                          "executions\" first)"}
    token, base, err = _resolve_skill_target(host_row, chip)
    if err:
        return err
    print(f"[rundeck] INFO rundeck_retry host={host_id} execution={eid!r}")
    try:
        async with httpx.AsyncClient(verify=_verify(chip), timeout=20.0,
                                     follow_redirects=True) as cli:
            ar = await cli.post(base + _API_RETRY + f"/execution/{eid}/retry",
                                headers=_hdr(token))
    except (httpx.HTTPError, OSError) as e:  # noqa: BLE001
        return {"ok": False, "status": 0, "detail": f"retry failed: {type(e).__name__}: {e}"}
    if ar.status_code in (401, 403):
        return {"ok": False, "status": ar.status_code,
                "detail": "auth failed (check the Rundeck API token)"}
    if not (200 <= ar.status_code < 300):
        try:
            msg = str(as_dict(ar.json()).get("message") or "").strip()
        except (ValueError, TypeError):
            msg = ""
        return {"ok": False, "status": ar.status_code,
                "detail": msg or (f"couldn't retry execution #{eid} (retry needs "
                                  f"Rundeck API v24+, or the execution wasn't found)")}
    try:
        new_eid = str(as_dict(ar.json()).get("id") or "")
    except (ValueError, TypeError):
        new_eid = ""
    tail = f" (new execution #{new_eid})" if new_eid else ""
    return {"ok": True, "status": 200,
            "detail": f"🔁 Retried execution #{eid}{tail}."}
