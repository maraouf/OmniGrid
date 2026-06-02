"""Speedtest Tracker per-app module.

Encapsulates everything Speedtest-Tracker-specific so the route
layer (``main_pkg/apps_routes.py``) stays generic. Public surface:

    SLUGS               — catalog slugs this module handles.
    requires_api_key()  — True (Speedtest Tracker needs Bearer auth).
    resolve_base_url(host_row, chip) -> str
    test_credential(host_row, chip, candidate_key) -> dict
    fetch_data(host_row, chip) -> dict

The fetch path memoises results per (host_id, service_idx) for
``CACHE_TTL_S`` seconds so a busy Apps view doesn't hammer the
upstream. The cache is process-local; the single-replica deploy
constraint makes the dict-cache correct.

Upstream API reference: https://docs.speedtest-tracker.dev/api/authorization
Endpoints used (Speedtest Tracker v1.x — the resource is ``results``,
NOT ``speedtests``; the older ``/api/v1/speedtests`` path 404s on
current builds, operator-flagged from the deploy at
``docker.home.lan:5050``):
    GET /api/v1/results/latest  — test-credential probe
    GET /api/v1/results?perPage=60 — data fetch (latest + series + avg)
"""
from __future__ import annotations

import time
from typing import Any, Optional

import httpx


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
)


# Bounded per-(host_id, service_idx) cache so repeat reads within
# the TTL window skip the upstream round-trip. Tunable would be
# overkill for a single-app cache — 60s matches the typical scan
# cadence + is short enough that an operator force-refresh covers
# any "I just rebuilt the upstream and want fresh data now" case.
CACHE_TTL_S = 60
_data_cache: dict[str, tuple[float, dict]] = {}


def requires_api_key() -> bool:
    """Speedtest Tracker requires Bearer-auth on every endpoint
    documented for OmniGrid's use; the editor MUST render the
    api_key input + Test-connection button."""
    return True


def resolve_base_url(host_row: dict, chip: dict) -> str:
    """Resolve the upstream base URL for one Speedtest Tracker chip.

    Priority order:
      1. Chip's own ``url`` field (operator-set; includes scheme
         + optional port).
      2. ``<proto>://<host.address>:<chip.probe.ports[0].port>``
         when the chip carries a single http/https port.

    Returns the URL with trailing slashes stripped so the caller
    can append ``/api/v1/...`` directly. Empty string when nothing
    resolves.
    """
    url = (chip.get("url") or "").strip()
    if url:
        return url.rstrip("/")
    address = (host_row.get("address") or "").strip()
    if not address:
        return ""
    probe = chip.get("probe") or {}
    ports = probe.get("ports") or []
    if isinstance(ports, list):
        for p in ports:
            if not isinstance(p, dict):
                continue
            port_n = p.get("port")
            proto = (p.get("protocol") or "").strip().lower()
            if isinstance(port_n, int) and 1 <= port_n <= 65535 and proto in ("http", "https"):
                return f"{proto}://{address}:{port_n}".rstrip("/")
    return ""


async def test_credential(host_row: dict, chip: dict, candidate_key: str) -> dict:
    """Probe Speedtest Tracker's auth-required endpoint with the
    supplied Bearer key. Returns ``{ok, detail, status}`` shaped
    for direct SPA consumption.

    Falls back to the chip's stored ``api_key`` when
    ``candidate_key`` is blank so the operator can re-test after
    first save without re-typing the secret.
    """
    key = (candidate_key or "").strip() or (chip.get("api_key") or "").strip()
    if not key:
        return {"ok": False, "detail": "api_key required", "status": 0}
    base = resolve_base_url(host_row, chip)
    if not base:
        return {"ok": False, "detail": "no upstream URL configured", "status": 0}
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


def _cache_key(host_id: str, service_idx: int) -> str:
    return f"{host_id}:{service_idx}"


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
    base = resolve_base_url(host_row, chip)
    if not base:
        raise ValueError("no upstream URL configured for this instance")
    ck = _cache_key(host_id, service_idx)
    now = time.time()
    if not force:
        cached = _data_cache.get(ck)
        if cached and (now - cached[0]) < CACHE_TTL_S:
            return cached[1]
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

    def _image_url(result_url: str) -> str:
        # Append `.png` to the Ookla share URL → the shareable result image.
        # Only for a speedtest.net result page (other URLs aren't image-backed).
        if not result_url:
            return ""
        low = result_url.lower()
        if low.endswith((".png", ".jpg", ".jpeg", ".webp")):
            return result_url
        if "speedtest.net/result" in low:
            return result_url.rstrip("/") + ".png"
        return ""

    series: list[dict[str, Any]] = []
    for entry in rows:
        if not isinstance(entry, dict):
            continue
        download = _metric(entry, "download")
        upload = _metric(entry, "upload")
        ping = _metric(entry, "ping")
        ts_str = (entry.get("created_at") or entry.get("scheduled") or "").strip()
        result_url = _result_url(entry)
        series.append({
            "ts": ts_str,
            "download": download,
            "upload": upload,
            "ping": ping,
            "status": (entry.get("status") or "").strip(),
            "server": (entry.get("server_name") or entry.get("service") or "").strip(),
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
    # Averages over the last 10 points — windowed so a single
    # anomalous spike doesn't dominate the badge.
    sample = series[:10]
    n = len(sample)
    if n > 0:
        avg = {
            "download": sum(p["download"] for p in sample) / n,
            "upload": sum(p["upload"] for p in sample) / n,
            "ping": sum(p["ping"] for p in sample) / n,
            "sample_size": n,
        }
    else:
        avg = {"download": 0.0, "upload": 0.0, "ping": 0.0, "sample_size": 0}
    # SPA chart expects oldest-first so the line walks left → right.
    series.reverse()
    out: dict = {
        "latest": latest,
        "averages": avg,
        "series": series,
        "fetched_at": int(now),
    }
    _data_cache[ck] = (now, out)
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
    cached = _data_cache.get(_cache_key(host_id, service_idx))
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
        "ts": latest.get("ts") or "",
    }
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
    raise ValueError(f"unknown skill: {skill_id!r}")


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
            for method in ("GET", "POST"):
                r = await cli.request(method, url, headers=headers)
                last_status = r.status_code
                if r.status_code in (200, 201, 202, 204):
                    print(f"[speedtest] INFO run_speedtest host={host_id} "
                          f"{method} {url} -> {r.status_code} (queued)")
                    if host_id is not None and service_idx is not None:
                        _data_cache.pop(_cache_key(host_id, service_idx), None)
                    return {"ok": True, "detail": "Speed test queued", "status": r.status_code}
                if r.status_code in (401, 403):
                    print(f"[speedtest] warning: run_speedtest host={host_id} "
                          f"{method} {url} -> {r.status_code} auth rejected (check api_key)")
                    return {"ok": False, "detail": "auth failed (check api_key)", "status": r.status_code}
                # Non-2xx, non-auth (typically 404/405): log the attempt and
                # keep trying the other verb.
                print(f"[speedtest] warning: run_speedtest host={host_id} "
                      f"{method} {url} -> {r.status_code} (trying next verb)")
                last_detail = f"HTTP {r.status_code}"
    except (httpx.HTTPError, OSError) as e:  # noqa: BLE001
        print(f"[speedtest] error: run_speedtest host={host_id} url={url} "
              f"failed — {type(e).__name__}: {e}")
        return {"ok": False, "detail": f"{type(e).__name__}: {e}", "status": 0}
    print(f"[speedtest] error: run_speedtest host={host_id} url={url} — both GET "
          f"and POST failed (last={last_status}); the on-demand trigger endpoint "
          f"differs on this Speedtest Tracker version (this build's results "
          f"resource is /api/v1/results). Run the test from the Speedtest "
          f"Tracker UI / its scheduler, or confirm the correct trigger path.")
    return {"ok": False, "detail": last_detail or f"HTTP {last_status}", "status": last_status}
