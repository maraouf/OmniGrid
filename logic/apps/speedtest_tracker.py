"""Speedtest Tracker per-app module.

Encapsulates everything Speedtest-Tracker-specific so the route
layer (``main_pkg/apps_routes.py``) stays generic. Public surface:

    SLUGS               — catalog slugs this module handles.
    requires_api_key()  — True (Speedtest Tracker needs Bearer auth).
    resolve_base_url(host_row, chip) -> str
    test_credential(host_row, chip, candidate_key) -> dict
    fetch_data(host_row, chip) -> dict

The fetch path memoises results per (host_id, service_idx) for the
chip's ``cache_ttl`` (default ``DEFAULT_CACHE_TTL_S``) seconds so a busy Apps view doesn't hammer the
upstream. The cache is process-local; the single-replica deploy
constraint makes the dict-cache correct.

Upstream API reference: https://docs.speedtest-tracker.dev/api/authorization
Endpoints used (Speedtest Tracker v1.x — the resource is ``results``,
NOT ``speedtests``; the older ``/api/v1/speedtests`` path 404s on
current builds, flagged from the deploy at
``docker.example.com:5050``):
    GET /api/v1/results/latest  — test-credential probe
    GET /api/v1/results?perPage=60 — data fetch (latest + series + avg)
"""
from __future__ import annotations

import time
from typing import Any, Optional

import httpx

from logic.apps._common import (
    cache_key, fetch_preamble, resolve_base_url, resolve_cache_ttl,
    resolve_credential_target)
from logic.coerce import safe_int

# Catalog template slugs handled by this module. The registry maps
# each slug to this module's exports; adding an alias slug here is
# enough to wire a second template (e.g. a community fork).
SLUGS: tuple[str, ...] = ("speedtest-tracker", "speedtest")

# AI / drawer SKILLS this app exposes (see logic/apps/registry.py for the
# framework). Each is rendered as an app-drawer button AND offered to the AI
# (sidebar + Telegram) as an invokable action — but ONLY when the app's extras
# are enabled AND its api_key is set (the skill route + the prompt-injection
# layer enforce that gate). `ai_phrases` seeds the model's intent matching;
# `destructive` is False because triggering a test is a safe, idempotent queue.
SKILLS: tuple[dict, ...] = (
    {
        "id": "run_speedtest",
        "name": "Run speed test",
        "ai_phrases": ("run a speed test, run speedtest, test my internet speed, "
                       "check connection speed, start a speedtest"),
        "destructive": False,
    },
    {
        # Read-only: pulls the CURRENT latest result straight from the
        # Speedtest Tracker app (live fetch, not the in-process cache) so the
        # AI can answer "show me the latest speed test" even when nothing has
        # been cached yet (fresh process / Telegram-first usage). Distinct from
        # run_speedtest, which QUEUES a new test (result lands ~10-60s later).
        "id": "latest_speedtest",
        "name": "Show latest speed test",
        "ai_phrases": ("show latest speed test, latest speedtest, latest speed test "
                       "result, current internet speed, what is my internet speed, "
                       "latest download upload ping, speed test result"),
        "destructive": False,
    },
    {
        # Arg skill: run a test against a NAMED Ookla server. Resolves the term
        # to a server id from GET /api/v1/servers, then queues a run against it
        # (POST /api/v1/speedtests/run?server_id=<id>). AI / Telegram only — the
        # arg is the server name / location / sponsor.
        "id": "run_speedtest_server",
        "name": "Run speed test against a server",
        "ai_phrases": ("run a speed test against, speedtest against, test speed "
                       "using server, run speedtest on server, speed test from, "
                       "test against the london server, speedtest via"),
        "destructive": False,
        "arg": True,
    },
)

# Bounded per-(host_id, service_idx) cache so repeat reads within
# the TTL window skip the upstream round-trip. Tunable would be
# overkill for a single-app cache — 60s matches the typical scan
# cadence + is short enough that an operator force-refresh covers
# any "I just rebuilt the upstream and want fresh data now" case.
# Per-instance data-cache TTL DEFAULT — overridable per chip via the
# editor's `cache_ttl` field (resolve_cache_ttl); NOT a global TUNABLE.
DEFAULT_CACHE_TTL_S = 60
_data_cache: dict[str, tuple[float, dict]] = {}


def requires_api_key() -> bool:
    """Speedtest Tracker requires Bearer-auth on every endpoint
    documented for OmniGrid's use; the editor MUST render the
    api_key input + Test-connection button."""
    return True


async def test_credential(host_row: dict, chip: dict, candidate_key: str, **_kw) -> dict:
    """Probe Speedtest Tracker's auth-required endpoint with the
    supplied Bearer key. Returns ``{ok, detail, status}`` shaped
    for direct SPA consumption.

    Falls back to the chip's stored ``api_key`` when
    ``candidate_key`` is blank so the operator can re-test after
    first save without re-typing the secret.
    """
    key, base, err = resolve_credential_target(host_row, chip, candidate_key)
    if err:
        return err
    url = base + "/api/v1/results/latest"
    headers = {
        "Authorization": f"Bearer {key}",
        "Accept": "application/json",
    }
    try:
        async with httpx.AsyncClient(verify=False, timeout=10.0) as cli:
            r = await cli.get(url, headers=headers)
    except (httpx.HTTPError, OSError) as e:  # noqa: BLE001
        return {"ok": False, "detail": f"{type(e).__name__}: {e}", "status": 0}
    if r.status_code in (200, 204):
        return {"ok": True, "detail": "OK", "status": r.status_code}
    if r.status_code in (401, 403):
        return {"ok": False, "detail": "auth failed (check api_key)", "status": r.status_code}
    return {"ok": False, "detail": f"HTTP {r.status_code}", "status": r.status_code}


# Operator-configurable averages window — how many of the MOST-RECENT
# results the expanded card / skill rolls the "Avg of last N tests" over.
# Stored per-instance on the chip (`avg_window`, validated + bounded in
# _clean_host_services); absent => DEFAULT_AVG_WINDOW. Bounds match the
# data fetch (perPage=60), so the average can never window more results
# than were pulled.
DEFAULT_AVG_WINDOW = 10
_AVG_WINDOW_MIN = 2
_AVG_WINDOW_MAX = 60


def _avg_window(chip: dict) -> int:
    """Resolve the per-instance averages window from the chip, clamped to
    [_AVG_WINDOW_MIN, _AVG_WINDOW_MAX]; DEFAULT_AVG_WINDOW when unset/bad."""
    # safe_int coerces Any/None/str → int (or the default on blank /
    # unparseable), so the clamp below operates on a concrete int.
    n = safe_int((chip or {}).get("avg_window"), DEFAULT_AVG_WINDOW)
    return max(_AVG_WINDOW_MIN, min(_AVG_WINDOW_MAX, n))


# Recommend a floor at this fraction of the median download (flag tests that run
# meaningfully slower than typical without false-positiving on minor jitter).
_FLOOR_RECOMMEND_FACTOR = 0.9


def _speed_floor(chip: dict) -> float:
    """The operator's per-instance below-floor reliability floor (Mbps); 0.0
    when unset / non-positive (= the below-floor stat is OFF)."""
    try:
        return max(0.0, float((chip or {}).get("speed_floor_mbps") or 0))
    except (TypeError, ValueError):
        return 0.0


# noinspection PyUnusedLocal
async def suggest(kind: str, host_row: dict, chip: dict, *,
                  host_id: str = "", service_idx: int = 0,
                  params: Optional[dict] = None) -> dict:
    """Generic per-app suggestion hook (dispatched by the ``app-suggest``
    route). ``speed-floor``: recommend a below-floor value from the chip's own
    speed-test history over the last ``days`` (default 30) — ~90% of the median
    download, so it flags meaningfully-slow tests. Returns ``{ok,
    recommended_mbps, median_mbps, samples, days}``; ``recommended_mbps`` is 0
    when there isn't enough history yet."""
    if kind != "speed-floor":
        raise ValueError(f"unknown suggestion: {kind!r}")
    p = params or {}
    try:
        days = max(1, min(365, int(p.get("days") or 30)))
    except (TypeError, ValueError):
        days = 30
    median = 0.0
    samples = 0
    try:
        from logic.apps import speedtest_tracker_sampler as _st_sampler  # noqa: PLC0415
        tr = _st_sampler.trend_summary(str(host_id or ""), int(service_idx or 0),
                                       days=days)
        median = float(tr.get("median_download") or 0)
        samples = safe_int(tr.get("samples"))
    except Exception as e:  # noqa: BLE001
        print(f"[speedtest] warning: suggest(speed-floor) trend read failed: {e}")
    recommended = round(median * _FLOOR_RECOMMEND_FACTOR, 1) if (median > 0 and samples >= 2) else 0.0
    return {"ok": True, "recommended_mbps": recommended,
            "median_mbps": round(median, 1), "samples": samples, "days": days}


async def fetch_data(host_row: dict, chip: dict, *,
                     host_id: str, service_idx: int,
                     force: bool = False) -> dict:
    """Fetch the upstream's recent results, derive latest +
    averages + series for the expanded-card render.

    Returns ``{latest, averages, series, fetched_at}``. Raises
    ``ValueError`` (caller should map to HTTPException) when the
    chip's api_key is unset / the base URL won't resolve.
    """
    api_key = (chip.get("api_key") or "").strip()
    if not api_key:
        raise ValueError("api_key not set for this instance")
    now = time.time()
    base, hit = fetch_preamble(host_row, chip, host_id, service_idx,
                               _data_cache, resolve_cache_ttl(chip, DEFAULT_CACHE_TTL_S), now, force)
    if hit is not None:
        return hit
    list_url = base + "/api/v1/results"
    headers = {"Authorization": f"Bearer {api_key}", "Accept": "application/json"}
    # Diagnostic: every fetch logs the host + resolved upstream URL so a
    # failure (404 / auth / timeout) is traceable to a specific host +
    # base URL in stdout / Admin -> Logs WITHOUT exposing the api_key.
    # Operator-flagged: "Speedtest fetch failed: upstream returned HTTP
    # 404 — why no log to tell us which host or the nature of the error?"
    # Severity convention: lines carry an explicit INFO / warning: /
    # error: marker near the start so `logic/logs.py:_severity_for`
    # buckets them deterministically (operator-requested consistent
    # logging) rather than relying on incidental body keywords. The
    # api_key is NEVER logged.
    print(f"[speedtest] INFO fetch host={host_id} svc_idx={service_idx} url={list_url}")
    try:
        async with httpx.AsyncClient(verify=False, timeout=15.0) as cli:
            r = await cli.get(list_url, headers=headers, params={"perPage": 60})
    except (httpx.HTTPError, OSError) as e:  # noqa: BLE001
        print(f"[speedtest] error: fetch host={host_id} url={list_url} "
              f"failed — {type(e).__name__}: {e}")
        raise RuntimeError(f"upstream fetch failed: {type(e).__name__}: {e}")
    if r.status_code != 200:
        # 404 is almost always a base-URL / path issue: the chip's `url`
        # points at the host but not the Speedtest Tracker root (e.g. a
        # reverse-proxy sub-path, or http-vs-https, or a trailing
        # `/dashboard`). Log the full URL + status so the operator can
        # curl it directly. `request.url` carries the final URL incl. the
        # perPage query so it's copy-pasteable.
        print(f"[speedtest] error: fetch host={host_id} url={r.request.url} "
              f"returned HTTP {r.status_code} "
              f"(check the chip URL points at the Speedtest Tracker root, "
              f"e.g. https://speedtest.example.com — not a sub-page)")
        if r.status_code in (401, 403):
            raise RuntimeError(f"upstream auth failed: HTTP {r.status_code} "
                               f"(check api_key) — {list_url}")
        if r.status_code == 404:
            raise RuntimeError(f"upstream returned HTTP 404 for {list_url} — "
                               f"the chip URL may not point at the Speedtest "
                               f"Tracker root (no /api/v1/results there)")
        raise RuntimeError(f"upstream returned HTTP {r.status_code} for {list_url}")
    try:
        body = r.json()
    except (ValueError, TypeError):  # noqa: BLE001
        raise RuntimeError("upstream returned non-JSON")
    # Speedtest Tracker `/api/v1/results` response shape varies by version.
    # Modern builds nest the metrics under a per-row `data` object carrying
    # Ookla's raw result schema:
    #   {"data": [{"id", "service", "server_name", "created_at",
    #              "data": {"download": {"bandwidth": <bytes/s>},
    #                       "upload":   {"bandwidth": <bytes/s>},
    #                       "ping":     {"latency": <ms>}}}, ...]}
    # Older builds expose flat `download` / `upload` / `ping` straight on the
    # row.
    #
    # UNITS — Ookla (and Speedtest Tracker's OWN UI) store bandwidth in BYTES
    # per second and convert to Mbps via ×8÷1e6. BOTH the nested `bandwidth`
    # AND the flat `download`/`upload` are bytes/s, so they normalise the same
    # way. A previous build of this module divided the FLAT field by 1000
    # (assuming Kbps); that read ~125× too large and produced the "23,153 Mbps"
    # bug. The nested Ookla shape is the version-stable source of truth, so it
    # is now tried FIRST; the flat field is the fallback and is treated as
    # bytes/s too. `ping` is milliseconds in both schemas.
    rows = body.get("data") if isinstance(body, dict) else None
    if not isinstance(rows, list):
        rows = []

    def _bytes_per_s_to_mbps(v: float) -> float:
        # Ookla bandwidth is bytes/s; Mbps = bytes/s × 8 bits ÷ 1e6.
        return float(v) * 8.0 / 1_000_000.0

    def _metric(row: dict, key: str) -> float:
        # 1) NESTED Ookla shape first — units are unambiguous here
        #    (bandwidth bytes/s, ping latency ms).
        nested = row.get("data")
        if isinstance(nested, dict):
            sub = nested.get(key)
            if isinstance(sub, dict):
                if key == "ping":
                    lat = sub.get("latency")
                    if isinstance(lat, (int, float)):
                        return float(lat)
                else:
                    bw = sub.get("bandwidth")
                    if isinstance(bw, (int, float)):
                        return _bytes_per_s_to_mbps(bw)
        # 2) FLAT fallback (older builds expose the metric on the row).
        #    Coerce a stringified number too (some builds stringify).
        flat = row.get(key)
        if isinstance(flat, str) and flat.strip():
            try:
                flat = float(flat)
            except (TypeError, ValueError):
                flat = None
        if isinstance(flat, (int, float)):
            if key == "ping":
                return float(flat)  # already ms
            return _bytes_per_s_to_mbps(flat)  # bytes/s → Mbps
        return 0.0

    def _extra(row: dict) -> "tuple[float, float, str, str]":
        # Pull the quality + provenance fields Ookla returns alongside the
        # bandwidth: jitter (ms, under data.ping.jitter), packet loss (%, under
        # data.packetLoss — can be null when not measured), ISP (data.isp), and
        # the test server's location (data.server.location; name is already
        # captured as `server`). Defensive over the version-variant shapes;
        # zero / "" when absent. The flat fallback covers older builds.
        # Locals are bound to a single .get() result before float()/isinstance
        # so the narrowing holds (re-calling .get() would widen back to
        # Any|None) — distinct names from the loop's so nothing is shadowed.
        jit = 0.0
        loss = 0.0
        isp_s = ""
        srv_loc = ""
        nested = row.get("data")
        if isinstance(nested, dict):
            png = nested.get("ping")
            if isinstance(png, dict):
                _j = png.get("jitter")
                if isinstance(_j, (int, float)):
                    jit = float(_j)
            _pl = nested.get("packetLoss")
            if isinstance(_pl, (int, float)):
                loss = float(_pl)
            isp_s = str(nested.get("isp") or "").strip()
            srv = nested.get("server")
            if isinstance(srv, dict):
                srv_loc = str(srv.get("location") or "").strip()
        # Flat fallbacks (older builds expose these straight on the row).
        if not jit:
            _fj = row.get("jitter")
            if isinstance(_fj, (int, float)):
                jit = float(_fj)
        if not loss:
            _fl = row.get("packet_loss")
            if isinstance(_fl, (int, float)):
                loss = float(_fl)
        if not isp_s:
            isp_s = str(row.get("isp") or "").strip()
        return jit, loss, isp_s, srv_loc

    def _result_url(row: dict) -> str:
        # Ookla stores the public share URL at data.result.url
        # (e.g. https://www.speedtest.net/result/c/<uuid>). Older / flat
        # builds expose it straight on the row as `url`. The share page
        # renders a result image at `<url>.png` — that's what lets a chat
        # client (Telegram link-preview) display the speed-test card.
        nested = row.get("data")
        if isinstance(nested, dict):
            res = nested.get("result")
            if isinstance(res, dict):
                u = res.get("url")
                if isinstance(u, str) and u.strip():
                    return u.strip()
        flat = row.get("url")
        if isinstance(flat, str) and flat.strip():
            return flat.strip()
        return ""

    def _image_url(share_url: str) -> str:
        # Append `.png` to the Ookla share URL → the shareable result image.
        # Only for a speedtest.net result page (other URLs aren't image-backed).
        if not share_url:
            return ""
        low = share_url.lower()
        if low.endswith((".png", ".jpg", ".jpeg", ".webp")):
            return share_url
        if "speedtest.net/result" in low:
            return share_url.rstrip("/") + ".png"
        return ""

    series: list[dict[str, Any]] = []
    for entry in rows:
        if not isinstance(entry, dict):
            continue
        download = _metric(entry, "download")
        upload = _metric(entry, "upload")
        ping = _metric(entry, "ping")
        jitter, packet_loss, isp, server_location = _extra(entry)
        ts_str = (entry.get("created_at") or entry.get("scheduled") or "").strip()
        result_url = _result_url(entry)
        # Speedtest Tracker marks a failed/errored run with `healthy: false`
        # (it can still carry a stale/partial download value, so a download-only
        # check misses it). Default True when the field is absent (older builds
        # don't emit it — treat as healthy so we don't over-count).
        _healthy = entry.get("healthy")
        healthy = bool(_healthy) if isinstance(_healthy, bool) else True
        series.append({
            "ts": ts_str,
            "download": download,
            "upload": upload,
            "ping": ping,
            "jitter": jitter,
            "packet_loss": packet_loss,
            "isp": isp,
            "healthy": healthy,
            "status": (entry.get("status") or "").strip(),
            "server": (entry.get("server_name") or entry.get("service") or "").strip(),
            "server_location": server_location,
            "result_url": result_url,
            "image_url": _image_url(result_url),
        })
    # Latest = first row (Speedtest Tracker returns newest-first).
    latest: Optional[dict[str, Any]] = series[0] if series else None
    # Diagnostic: log the RAW upstream metrics of the newest row alongside
    # the normalised Mbps / ms so a unit mismatch is visible in Admin -> Logs
    # WITHOUT exposing the api_key. If the rendered Mbps ever looks off again
    # this line shows exactly what the upstream sent (flat field + whether the
    # nested Ookla `data` block was present) vs what we computed.
    if rows and isinstance(rows[0], dict) and latest is not None:
        _r0 = rows[0]
        _has_nested = isinstance(_r0.get("data"), dict)
        print(
            f"[speedtest] INFO normalise host={host_id} nested={'yes' if _has_nested else 'no'} "
            f"raw_download={_r0.get('download')} raw_upload={_r0.get('upload')} "
            f"raw_ping={_r0.get('ping')} -> download_mbps={round(latest['download'], 2)} "
            f"upload_mbps={round(latest['upload'], 2)} ping_ms={round(latest['ping'], 1)}"
        )
    # Averages over the most-recent N points (per-instance `avg_window`,
    # default 10) — windowed so a single anomalous spike doesn't dominate.
    sample = series[:_avg_window(chip)]
    n = len(sample)
    if n > 0:
        avg = {
            "download": sum(p["download"] for p in sample) / n,
            "upload": sum(p["upload"] for p in sample) / n,
            "ping": sum(p["ping"] for p in sample) / n,
            "jitter": sum(p.get("jitter") or 0 for p in sample) / n,
            "packet_loss": sum(p.get("packet_loss") or 0 for p in sample) / n,
            "sample_size": n,
        }
    else:
        avg = {"download": 0.0, "upload": 0.0, "ping": 0.0,
               "jitter": 0.0, "packet_loss": 0.0, "sample_size": 0}
    # SPA chart expects oldest-first so the line walks left → right.
    series.reverse()
    out: dict = {
        "latest": latest,
        "averages": avg,
        "series": series,
        "fetched_at": int(now),
    }
    # Below-floor reliability: the operator's own ISP download floor (Mbps); the
    # card flags the % of tests below it across EVERY test in the window. A
    # test counts as below-floor when EITHER its download came in under the
    # floor OR the run failed (`healthy == False`) — a failed run never reached
    # the floor even when Speedtest Tracker left a stale/partial download value
    # on the row (so a download-only check under-counts failures, the operator-
    # reported "0 of 25 while some failed" case). 0 / unset = OFF (no below-floor
    # block). OmniGrid-side — it does NOT read speedtest-tracker's pass/fail
    # threshold.
    floor = _speed_floor(chip)
    if floor > 0:
        total = len(series)
        below = sum(1 for p in series
                    if not p.get("healthy", True)
                    or float(p.get("download") or 0) < floor)
        out["below_floor"] = {
            "floor_mbps": round(floor, 1),
            "below_count": below,
            "total": total,
            "pct": round(below / total * 100, 1) if total else 0.0,
        }
    # Embed the long-horizon trend (daily-median download + medians over the
    # retention window) from the lifespan sampler's independent history table —
    # best-effort, a sampler / DB hiccup must not fail the card.
    try:
        from logic.apps import speedtest_tracker_sampler as _st_sampler  # noqa: PLC0415
        out["trend"] = _st_sampler.trend_summary(host_id, int(service_idx))
    except Exception as e:  # noqa: BLE001
        print(f"[speedtest] warning: trend_summary({host_id}#{service_idx}) failed: {e}")
    _data_cache[cache_key(host_id, service_idx)] = (now, out)
    return out


def peek_latest(host_id: str, service_idx: int) -> Optional[dict]:
    """Return the LATEST cached speed-test result for a chip WITHOUT an
    upstream fetch (reads ``_data_cache`` only) — so the AI context can show
    "last result" cheaply without a per-query upstream round-trip. Returns
    ``{download, upload, ping, ts, result_url, image_url, averages}``
    (Mbps / Mbps / ms; averages over the cached window) or ``None`` when
    nothing is cached yet (then the AI offers to run a fresh test).

    The AI surfaces (Telegram + web sidebar) read this via
    ``available_app_skills_context()[].last`` to answer
    "show me the latest speed test" with latest + averages + the result
    image link, WITHOUT triggering a new test."""
    cached = _data_cache.get(cache_key(host_id, service_idx))
    if not cached:
        return None
    payload = cached[1] or {}
    latest = payload.get("latest")
    if not isinstance(latest, dict):
        return None
    out: dict[str, Any] = {
        "download": round(float(latest.get("download") or 0), 2),
        "upload": round(float(latest.get("upload") or 0), 2),
        "ping": round(float(latest.get("ping") or 0), 1),
        "jitter": round(float(latest.get("jitter") or 0), 1),
        "packet_loss": round(float(latest.get("packet_loss") or 0), 2),
        "ts": latest.get("ts") or "",
    }
    _isp = (latest.get("isp") or "").strip()
    _srv = (latest.get("server") or "").strip()
    if _isp:
        out["isp"] = _isp
    if _srv:
        out["server"] = _srv
    result_url = (latest.get("result_url") or "").strip()
    image_url = (latest.get("image_url") or "").strip()
    if result_url:
        out["result_url"] = result_url
    if image_url:
        out["image_url"] = image_url
    avg = payload.get("averages")
    if isinstance(avg, dict) and avg.get("sample_size"):
        out["averages"] = {
            "download": round(float(avg.get("download") or 0), 2),
            "upload": round(float(avg.get("upload") or 0), 2),
            "ping": round(float(avg.get("ping") or 0), 1),
            "sample_size": int(avg.get("sample_size") or 0),
        }
    return out


async def run_skill(skill_id: str, host_row: dict, chip: dict, *,
                    host_id: Optional[str] = None,
                    service_idx: Optional[int] = None, **_kw) -> dict:
    """Dispatch one of this app's SKILLS. Returns ``{ok, detail, status?}``
    shaped for direct SPA / AI consumption. Raises ValueError on an unknown
    skill id (the route maps that to HTTP 404)."""
    if skill_id == "run_speedtest":
        return await _trigger_speedtest(host_row, chip, host_id=host_id, service_idx=service_idx)
    if skill_id == "latest_speedtest":
        return await _fetch_latest_skill(host_row, chip, host_id=host_id, service_idx=service_idx)
    if skill_id == "run_speedtest_server":
        return await _trigger_speedtest_server(host_row, chip, arg=_kw.get("arg"),
                                               host_id=host_id, service_idx=service_idx)
    raise ValueError(f"unknown skill: {skill_id!r}")


def _fmt_mbps(v) -> str:
    """Format a value as a ``'<n> Mbps'`` string (1-decimal, grouped); placeholder on a non-numeric value."""
    try:
        return f"{float(v):,.1f} Mbps"
    except (TypeError, ValueError):
        return "—"


def _fmt_ms(v) -> str:
    """Format a value as a ``'<n> ms'`` string (1-decimal, grouped); placeholder on a non-numeric value."""
    try:
        return f"{float(v):,.1f} ms"
    except (TypeError, ValueError):
        return "—"


# noinspection DuplicatedCode
async def _fetch_latest_skill(host_row: dict, chip: dict, *,
                              host_id: Optional[str] = None,
                              service_idx: Optional[int] = None) -> dict:
    """Read-only skill: fetch the CURRENT latest result straight from the
    Speedtest Tracker app (force-bypasses the per-chip cache) and return a
    formatted ``detail`` summary + structured fields. Used by the AI's
    "show me the latest speed test" path so it queries the app DIRECTLY
    rather than refusing on an empty cache. Never raises — upstream / config
    failures come back as ``{ok: False, detail}``."""
    print(f"[speedtest] INFO latest_speedtest host={host_id} svc_idx={service_idx} (live fetch)")
    try:
        data = await fetch_data(host_row, chip,
                                host_id=str(host_id or ""),
                                service_idx=int(service_idx or 0),
                                force=True)
    except (ValueError, RuntimeError) as e:
        print(f"[speedtest] warning: latest_speedtest host={host_id} "
              f"could not fetch — {e}")
        return {"ok": False, "detail": str(e), "status": 0}
    latest = (data or {}).get("latest") if isinstance(data, dict) else None
    if not isinstance(latest, dict) or not latest:
        return {"ok": False,
                "detail": "no speed-test results found on the app yet",
                "status": 0}
    avg = (data or {}).get("averages") or {}
    # Timestamp in the operator's chosen format (default when no per-user
    # context is available at the skill layer); scheduler-tz aware. Both
    # branches assign ts_disp, so no dead pre-init is needed.
    # noinspection PyBroadException
    try:
        from logic.datetime_fmt import format_user_datetime  # noqa: PLC0415
        ts_disp = format_user_datetime(latest.get("ts") or "")
    except Exception:  # noqa: BLE001
        ts_disp = str(latest.get("ts") or "")
    # Multi-line so the web app-drawer result box (whitespace-pre-wrap) and
    # the Telegram outcome line both read as a small block instead of one
    # long run-on line: latest on one line, time on its own, the rolling
    # average on a third, and the image URL (if any) last.
    lines = [
        (f"⬇️ {_fmt_mbps(latest.get('download'))}   "
         f"⬆️ {_fmt_mbps(latest.get('upload'))}   "
         f"🏓 {_fmt_ms(latest.get('ping'))}"),
    ]
    # Quality line — jitter + packet loss (only when present / non-trivial).
    _jit = latest.get("jitter")
    _loss = latest.get("packet_loss")
    _qbits = []
    if isinstance(_jit, (int, float)) and _jit > 0:
        _qbits.append(f"📶 jitter {_fmt_ms(_jit)}")
    if isinstance(_loss, (int, float)) and _loss > 0:
        _qbits.append(f"📉 loss {float(_loss):.1f}%")
    if _qbits:
        lines.append("   ".join(_qbits))
    # ISP / server provenance.
    _isp = str(latest.get("isp") or "").strip()
    _srv = str(latest.get("server") or "").strip()
    _sloc = str(latest.get("server_location") or "").strip()
    if _isp or _srv:
        srv_full = _srv + (f" ({_sloc})" if _srv and _sloc else "")
        prov = " · ".join(b for b in (_isp, srv_full) if b)
        lines.append(f"🌐 {prov}")
    if ts_disp:
        lines.append(f"🕒 {ts_disp}")
    if avg.get("sample_size"):
        _n = int(avg["sample_size"])
        lines.append(
            f"Avg of last {_n} test{'s' if _n != 1 else ''}:   "
            f"⬇️ {_fmt_mbps(avg.get('download'))}   "
            f"⬆️ {_fmt_mbps(avg.get('upload'))}   "
            f"🏓 {_fmt_ms(avg.get('ping'))}")
    # Reliability over the long-horizon sampler window — only when some failed.
    _trend = (data or {}).get("trend") if isinstance(data, dict) else None
    if isinstance(_trend, dict):
        _failed = int(_trend.get("failed_count") or 0)
        if _failed > 0:
            lines.append(
                f"⚠️ {_failed} failed test{'s' if _failed != 1 else ''} "
                f"({float(_trend.get('failed_pct') or 0):.1f}%) in "
                f"{int(_trend.get('days') or 0)}d")
    detail = "\n".join(lines)
    image_url = (latest.get("image_url") or "").strip()
    if image_url:
        detail += f"\n{image_url}"
    return {
        "ok": True,
        "detail": detail,
        "status": 200,
        "latest": latest,
        "averages": avg,
        "image_url": image_url,
        # Render the Speedtest result graph WIDER than the default skill-image
        # preview (it's a detailed chart, not a small poster). The generic
        # renderer reads this flag — other apps' skill images stay at the
        # default width.
        "image_wide": True,
    }


# noinspection DuplicatedCode
async def _trigger_speedtest(host_row: dict, chip: dict, *,
                             host_id: Optional[str] = None,
                             service_idx: Optional[int] = None) -> dict:
    """Trigger an on-demand speed test on the Speedtest Tracker instance.

    Speedtest Tracker queues a background test and lands the result in
    ``/api/v1/results`` ~10-60s later (it does NOT return the result inline).
    The exact ondemand trigger verb varies by version, so we try GET then POST
    on ``/api/v1/speedtests/run`` and treat any 2xx as "queued". On success we
    drop the per-chip data cache so the next app-data fetch re-pulls once the
    queued test completes (the SPA / AI then shows the new latest result).
    """
    api_key = (chip.get("api_key") or "").strip()
    if not api_key:
        return {"ok": False, "detail": "api_key not set for this instance", "status": 0}
    base = resolve_base_url(host_row, chip)
    if not base:
        return {"ok": False, "detail": "no upstream URL configured for this instance", "status": 0}
    url = base + "/api/v1/speedtests/run"
    headers = {"Authorization": f"Bearer {api_key}", "Accept": "application/json"}
    # Visibility: log the attempt up front (host + resolved URL, NEVER the
    # api_key) so a "the speed test didn't run" report is traceable in stdout /
    # Admin → Logs to this exact host + endpoint. Each GET/POST attempt below
    # logs its own status so a 404/405/auth failure is never silent.
    print(f"[speedtest] INFO run_speedtest host={host_id} svc_idx={service_idx} url={url}")
    last_status = 0
    last_detail = ""
    try:
        async with httpx.AsyncClient(verify=False, timeout=20.0) as cli:
            # POST first — Speedtest Tracker's on-demand trigger is POST-only on
            # current builds (a GET returns 405 Method Not Allowed). GET stays as
            # a fallback for older forks that accepted it. Intermediate
            # verb-probing is logged at INFO (not warning) so a successful POST
            # isn't preceded by a scary "405" warning line; only auth failure or
            # an all-verbs-failed give-up is WARN/ERROR.
            for method in ("POST", "GET"):
                r = await cli.request(method, url, headers=headers)
                last_status = r.status_code
                if r.status_code in (200, 201, 202, 204):
                    print(f"[speedtest] INFO run_speedtest host={host_id} "
                          f"{method} {url} -> {r.status_code} (queued)")
                    if host_id is not None and service_idx is not None:
                        _data_cache.pop(cache_key(host_id, service_idx), None)
                    return {"ok": True, "detail": "Speed test queued", "status": r.status_code}
                if r.status_code in (401, 403):
                    print(f"[speedtest] warning: run_speedtest host={host_id} "
                          f"{method} {url} -> {r.status_code} auth rejected (check api_key)")
                    return {"ok": False, "detail": "auth failed (check api_key)", "status": r.status_code}
                # Non-2xx, non-auth (typically 405 on the non-preferred verb /
                # 404): expected probing — log at INFO and try the other verb.
                print(f"[speedtest] INFO run_speedtest host={host_id} "
                      f"{method} {url} -> {r.status_code} (verb not accepted, trying next)")
                last_detail = f"HTTP {r.status_code}"
    except (httpx.HTTPError, OSError) as e:  # noqa: BLE001
        print(f"[speedtest] error: run_speedtest host={host_id} url={url} "
              f"failed — {type(e).__name__}: {e}")
        return {"ok": False, "detail": f"{type(e).__name__}: {e}", "status": 0}
    print(f"[speedtest] error: run_speedtest host={host_id} url={url} — both POST "
          f"and GET rejected (last={last_status}); the on-demand trigger endpoint "
          f"differs on this Speedtest Tracker version (this build's results "
          f"resource is /api/v1/results). Run the test from the Speedtest "
          f"Tracker UI / its scheduler, or confirm the correct trigger path.")
    return {"ok": False, "detail": last_detail or f"HTTP {last_status}", "status": last_status}


# noinspection DuplicatedCode
async def _trigger_speedtest_server(host_row: dict, chip: dict, *,
                                    arg: Optional[str] = None,
                                    host_id: Optional[str] = None,
                                    service_idx: Optional[int] = None) -> dict:
    """Arg skill: queue a speed test against a NAMED Ookla server.

    Resolves the free-text ``arg`` (server name / location / sponsor / id) to a
    server id from ``GET /api/v1/servers`` (substring match, or a bare numeric
    arg used directly), then ``POST /api/v1/speedtests/run?server_id=<id>`` (the
    documented optional query param). Never raises — config / upstream failures
    come back as ``{ok: False, detail}``."""
    needle = str(arg or "").strip()
    if not needle:
        return {"ok": False, "status": 0,
                "detail": "no server given — say e.g. \"run a speed test against London\""}
    api_key = (chip.get("api_key") or "").strip()
    if not api_key:
        return {"ok": False, "detail": "api_key not set for this instance", "status": 0}
    base = resolve_base_url(host_row, chip)
    if not base:
        return {"ok": False, "detail": "no upstream URL configured for this instance", "status": 0}
    headers = {"Authorization": f"Bearer {api_key}", "Accept": "application/json"}
    print(f"[speedtest] INFO run_speedtest_server host={host_id} target={needle!r}")
    try:
        async with httpx.AsyncClient(verify=False, timeout=20.0,
                                     follow_redirects=True) as cli:
            # Resolve the server id. A bare numeric arg is used directly; else
            # match the term against each server's name / location / sponsor /
            # host from GET /api/v1/servers.
            server_label = needle
            if needle.isdigit():
                server_id = needle
            else:
                sr = await cli.get(base + "/api/v1/servers", headers=headers)
                if sr.status_code in (401, 403):
                    return {"ok": False, "status": sr.status_code,
                            "detail": "auth failed (check api_key)"}
                if sr.status_code != 200:
                    return {"ok": False, "status": sr.status_code,
                            "detail": f"could not list servers (HTTP {sr.status_code})"}
                try:
                    _body = sr.json()
                except (ValueError, TypeError):
                    return {"ok": False, "status": 502, "detail": "non-JSON server list"}
                servers = _body.get("data") if isinstance(_body, dict) else _body
                if not isinstance(servers, list):
                    servers = []
                nl = needle.lower()
                match = None
                for s in servers:
                    if not isinstance(s, dict):
                        continue
                    hay = " ".join(str(s.get(k) or "") for k in
                                   ("name", "location", "sponsor", "host", "country")).lower()
                    if nl in hay:
                        match = s
                        break
                if match is None:
                    return {"ok": False, "status": 404,
                            "detail": f"no Ookla server matched \"{needle}\""}
                server_id = str(match.get("id") or "").strip()
                server_label = (str(match.get("name") or match.get("sponsor") or needle).strip()
                                + (f" ({match.get('location')})" if match.get("location") else ""))
                if not server_id:
                    return {"ok": False, "status": 502,
                            "detail": f"matched \"{server_label}\" but it has no server id"}
            rr = await cli.post(base + "/api/v1/speedtests/run",
                                headers=headers, params={"server_id": server_id})
    except (httpx.HTTPError, OSError) as e:  # noqa: BLE001
        print(f"[speedtest] error: run_speedtest_server host={host_id} failed — {type(e).__name__}: {e}")
        return {"ok": False, "status": 0, "detail": f"{type(e).__name__}: {e}"}
    if rr.status_code in (401, 403):
        return {"ok": False, "status": rr.status_code, "detail": "auth failed (check api_key)"}
    if rr.status_code in (200, 201, 202, 204):
        if host_id is not None and service_idx is not None:
            _data_cache.pop(cache_key(host_id, service_idx), None)
        return {"ok": True, "status": rr.status_code,
                "detail": f"🚀 Queued a speed test against “{server_label}” — the "
                          f"result lands on the card in ~10-60s."}
    return {"ok": False, "status": rr.status_code,
            "detail": f"run against “{server_label}” returned HTTP {rr.status_code}"}
