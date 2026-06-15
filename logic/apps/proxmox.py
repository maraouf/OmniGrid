"""Proxmox VE per-app module.

Encapsulates everything Proxmox-specific so the route layer
(``main_pkg/apps_routes.py``) stays generic. Public surface mirrors the
``grafana.py`` / ``rundeck.py`` shape (token-header auth, ``verify_tls`` toggle,
``fetch_gate`` cache):

    SLUGS               — catalog slugs this module handles ("proxmox").
    requires_api_key()  — True (the chip's ``api_key`` stores the Proxmox API
                          token in the form ``user@realm!tokenid=secret``).
    resolve_base_url(host_row, chip) -> str   (shared helper)
    test_credential(host_row, chip, candidate_key) -> dict
    fetch_data(host_row, chip, *, host_id, service_idx, force) -> dict
    peek_latest(host_id, service_idx) -> dict | None    (AI context)
    SKILLS / run_skill  — status (read) + list VMs (read) + list containers
                          (read) + start a guest (action, arg) + stop a guest
                          (DESTRUCTIVE, arg).

Auth model: Proxmox VE authenticates via an **API token** (Datacenter →
Permissions → API Tokens → Add). The token value is ``USER@REALM!TOKENID=SECRET``
(e.g. ``root@pam!omnigrid=xxxxxxxx-xxxx-...``) and is sent in the
``Authorization: PVEAPIToken=<token>`` header — NOT the ticket + CSRF cookie
dance the web UI uses. The whole ``USER@REALM!TOKENID=SECRET`` string lives in
the chip's ``api_key`` field (write-only ``_set`` flag pattern). Proxmox ships a
self-signed cert on :8006, so every client defaults to ``verify=False`` (the
operator flips the per-chip ``verify_tls`` toggle ON for a real cert). The
credential probe hits the auth-required ``GET /api2/json/version`` so a bad /
missing token fails loudly (401). Single-instance app (NOT fleet).

The expanded card answers "is my Proxmox cluster healthy" at a glance — and the
whole inventory comes from ONE call, ``GET /api2/json/cluster/resources``, which
returns every node / VM / container / storage with its status + cpu + mem:

    nodes_online / nodes_total    — cluster nodes up
    vms_running / vms_total        — QEMU VMs running
    cts_running / cts_total        — LXC containers running
    cpu_percent / mem_percent      — cluster CPU / memory utilisation
    storage_used / storage_total   — aggregate storage (shared dedup'd by name)
    version                        — PVE version (GET /version)

Upstream API reference: ``https://<host>:8006/api2/json`` —
    GET /version                                   — version (credential probe)
    GET /cluster/resources                         — nodes + guests + storage
    POST /nodes/{node}/{type}/{vmid}/status/start  — boot a VM / CT
    POST /nodes/{node}/{type}/{vmid}/status/shutdown — graceful stop
"""
from __future__ import annotations

import time
from typing import Any, Optional

import httpx

from logic.apps._common import (
    DebugRecorder, cache_key, fetch_gate, peek_cache, resolve_base_url,
    resolve_cache_ttl, resolve_credential_target)
from logic.coerce import as_dict, as_list, safe_int

# Catalog template slugs handled by this module.
SLUGS: tuple[str, ...] = ("proxmox", "proxmox-ve", "pve")

_API = "/api2/json"

# Per-(host_id, service_idx) data cache for the expanded card. 30s default — a
# cluster's running/stopped counts move slowly; the whole fetch is one call.
DEFAULT_CACHE_TTL_S = 30
_data_cache: dict[str, tuple[float, dict]] = {}

# Cap on the rich-item rows a list skill returns.
_MAX_ROWS = 50

# Guest status → emoji for the VM / container rich rows.
_GUEST_STATUS_EMOJI = {"running": "🟢", "stopped": "⏹️", "paused": "⏸️",
                       "suspended": "⏸️", "prelaunch": "🕒", "unknown": "⚪"}

# Proxmox skills. The read skills surface as one-click drawer buttons AND AI /
# Telegram actions; the start / stop skills are arg-carrying (AI / Telegram +
# the per-row buttons). Stop is DESTRUCTIVE (gracefully shuts a guest down).
SKILLS: tuple[dict, ...] = (
    {
        "id": "proxmox_status",
        "name": "Proxmox status",
        "ai_phrases": ("proxmox status, pve status, how many vms are running, "
                       "proxmox overview, cluster health, how many nodes, "
                       "proxmox cpu and memory, is my proxmox healthy"),
        "destructive": False,
    },
    {
        "id": "proxmox_vms",
        "name": "List Proxmox VMs",
        "ai_phrases": ("list proxmox vms, show my virtual machines, what vms do "
                       "i have, which vms are running, proxmox qemu list, "
                       "list virtual machines"),
        "destructive": False,
    },
    {
        "id": "proxmox_containers",
        "name": "List Proxmox containers",
        "ai_phrases": ("list proxmox containers, show my lxc containers, what "
                       "containers do i have, which containers are running, "
                       "proxmox lxc list, list containers"),
        "destructive": False,
    },
    {
        "id": "proxmox_start_guest",
        "name": "Start a Proxmox VM / container",
        "ai_phrases": ("start the <name> vm, boot the <name> container, power on "
                       "<name>, start vm <vmid>, turn on <name>"),
        "arg": True,
        "arg_hint": "the VM / container name or VMID to start",
        "destructive": False,
    },
    {
        "id": "proxmox_stop_guest",
        "name": "Stop a Proxmox VM / container",
        "ai_phrases": ("stop the <name> vm, shut down the <name> container, power "
                       "off <name>, stop vm <vmid>, turn off <name>"),
        # DESTRUCTIVE: gracefully shuts a guest down (takes it offline).
        "arg": True,
        "arg_hint": "the VM / container name or VMID to stop",
        "destructive": True,
    },
)


def requires_api_key() -> bool:
    """Proxmox authenticates every call with an API token; the editor MUST
    render the token input (stored in the chip's api_key) + Test."""
    return True


def _verify(chip: dict) -> bool:
    """Whether to verify the upstream TLS certificate. Default False — Proxmox
    ships a self-signed cert on :8006; the operator flips ``verify_tls`` ON for
    a real cert."""
    return bool(chip.get("verify_tls"))


def _headers(token: str) -> dict:
    """Proxmox API-token auth header + JSON Accept."""
    return {"Authorization": f"PVEAPIToken={token}", "Accept": "application/json"}


async def _get(cli: "httpx.AsyncClient", url: str, token: str) -> Any:
    """GET a Proxmox endpoint; parsed JSON or None on non-2xx / parse failure."""
    try:
        r = await cli.get(url, headers=_headers(token))
    except (httpx.HTTPError, OSError):
        return None
    if not (200 <= r.status_code < 300):
        return None
    try:
        return r.json()
    except (ValueError, TypeError):
        return None


async def _fetch_version(cli: "httpx.AsyncClient", base: str, token: str) -> str:
    """Best-effort PVE version from ``GET /version``; '' on miss."""
    body = await _get(cli, base + _API + "/version", token)
    return str(as_dict(as_dict(body).get("data")).get("version") or "").strip()


# noinspection DuplicatedCode
async def test_credential(host_row: dict, chip: dict, candidate_key: str, **_kw) -> dict:
    """Probe Proxmox's auth-required ``GET /version`` with the supplied token.
    Returns ``{ok, detail, status}``. Falls back to the chip's stored ``api_key``
    when ``candidate_key`` is blank so the operator can re-test after first save
    without retyping."""
    token, base, err = resolve_credential_target(host_row, chip, candidate_key)
    if err:
        return err
    url = base + _API + "/version"
    try:
        async with httpx.AsyncClient(verify=_verify(chip), timeout=10.0,
                                     follow_redirects=True) as cli:
            r = await cli.get(url, headers=_headers(token))
    except (httpx.HTTPError, OSError) as e:  # noqa: BLE001
        return {"ok": False, "detail": f"{type(e).__name__}: {e}", "status": 0}
    if r.status_code == 200:
        try:
            ver = str(as_dict(as_dict(r.json()).get("data")).get("version") or "").strip()
        except (ValueError, TypeError):
            ver = ""
        return {"ok": True, "detail": f"OK{(' — PVE ' + ver) if ver else ''}",
                "status": 200}
    if r.status_code in (401, 403):
        return {"ok": False,
                "detail": "auth failed (check the Proxmox API token — Datacenter "
                          "→ Permissions → API Tokens; the token needs at least "
                          "PVEAuditor on /)",
                "status": r.status_code}
    if r.status_code == 404:
        return {"ok": False,
                "detail": "404 — no Proxmox API here (point the chip URL at the "
                          "Proxmox host, e.g. https://192.X.X.X:8006)",
                "status": 404}
    return {"ok": False, "detail": f"HTTP {r.status_code}", "status": r.status_code}


def _pct(used: Any, total: Any) -> int:
    """Integer percentage ``used / total``, clamped 0-100; 0 when total ≤ 0."""
    u = float(safe_int(used))
    t = float(safe_int(total))
    if t <= 0:
        return 0
    return max(0, min(100, round(u / t * 100)))


def _storage_totals(resources: list) -> "tuple[int, int]":
    """``(used_bytes, total_bytes)`` aggregated across storage resources —
    deduped so shared storage (same ``storage`` name, ``shared=1``) is counted
    ONCE while per-node local storage is counted per node (keyed on the unique
    resource ``id``)."""
    used = 0
    total = 0
    seen: set = set()
    for r in resources:
        if not isinstance(r, dict) or str(r.get("type") or "") != "storage":
            continue
        name = str(r.get("storage") or "").strip()
        key = name if r.get("shared") else str(r.get("id") or f"{r.get('node')}/{name}")
        if key in seen:
            continue
        seen.add(key)
        used += safe_int(r.get("disk"))
        total += safe_int(r.get("maxdisk"))
    return used, total


# Audit privileges PVEAuditor grants that /cluster/resources needs to list
# guests + read node CPU / memory. The token must hold at least one of these
# (on / with Propagate) for the card to populate.
_AUDIT_PRIVS = ("Sys.Audit", "VM.Audit", "Datastore.Audit", "Pool.Audit")


def _summarize_permissions(perms: Any) -> "tuple[bool, str]":
    """From ``GET /access/permissions`` output, return ``(has_audit, summary)``.

    The endpoint returns the TOKEN's effective permission tree (the intersection
    of the user's roles AND the token's own ACL when Privilege Separation is on),
    shaped ``{path: {Priv: 1, ...}}`` under ``data``. ``has_audit`` is True when
    the token holds any audit privilege on ANY path — the minimum PVEAuditor
    grants that ``/cluster/resources`` needs to surface guests + node metrics.
    ``summary`` is a short human description of what the token can actually see
    (the smoking gun for a "guests missing" report)."""
    pm = as_dict(perms)
    data = pm.get("data")
    data = data if isinstance(data, dict) else pm
    if not isinstance(data, dict) or not data:
        return False, "empty — the token has NO effective permissions"
    paths_with_audit: list = []
    for path, privs in data.items():
        if isinstance(privs, dict) and any(privs.get(p) for p in _AUDIT_PRIVS):
            paths_with_audit.append(str(path))
    if paths_with_audit:
        return True, "audit on " + ", ".join(sorted(paths_with_audit)[:6])
    return False, ("sees " + ", ".join(sorted(str(p) for p in data)[:6])
                   + " but holds NO audit privilege")


def _perm_limited_hint(perms_probe: Any) -> str:
    """Build the precise, actionable hint for the "node visible but no guests /
    metrics" case from the token's effective permissions."""
    has_audit, summary = _summarize_permissions(perms_probe)
    if perms_probe is None:
        return ("The API token sees the node but no guests or CPU / memory. "
                "This is almost always the API-token 'Privilege Separation' "
                "gotcha — a token does NOT inherit its user's roles. FIX: edit "
                "the token and UNCHECK 'Privilege Separation', OR add an API "
                "Token Permission (Datacenter → Permissions → Add → API Token "
                "Permission: path /, the token, role PVEAuditor, Propagate ON). "
                "Granting PVEAuditor to the user alone is not enough.")
    if "empty" in summary:
        return ("Confirmed: GET /access/permissions shows this token has NO "
                "effective permissions — Privilege Separation is ON and nothing "
                "is granted to the TOKEN itself, so it ignores the user's "
                "PVEAuditor role. FIX (either): (a) Datacenter → Permissions → "
                "API Tokens → edit the token → UNCHECK 'Privilege Separation'; "
                "or (b) Datacenter → Permissions → Add → API Token Permission → "
                "Path '/', this token, Role 'PVEAuditor', Propagate ON.")
    if not has_audit:
        return (f"The token {summary} — it can see paths but lacks an AUDIT "
                "privilege. Grant the PVEAuditor role (it includes Sys.Audit / "
                "VM.Audit) on path '/' with Propagate ON; a lesser role can't "
                "list guests or read node CPU / memory.")
    return (f"The token HAS audit permissions ({summary}) yet "
            "/cluster/resources returned no guests. Check the role is on path "
            "'/' (not just a sub-path) with Propagate ON, and that the guests "
            "aren't all on a node this token can't audit.")


# noinspection DuplicatedCode
async def fetch_data(host_row: dict, chip: dict, *,
                     host_id: str, service_idx: int,
                     force: bool = False) -> dict:
    """Fetch the Proxmox cluster summary for the card from a single
    ``GET /cluster/resources`` call (+ ``/version``). Returns the card payload
    (see the module docstring). Raises ``ValueError`` / ``RuntimeError`` (caller
    maps to HTTPException) when the token is unset / the base URL won't resolve /
    the load-bearing resources call errors."""
    token = (chip.get("api_key") or "").strip()
    now = time.time()
    base, hit = fetch_gate(host_row, chip, host_id, service_idx, _data_cache,
                           resolve_cache_ttl(chip, DEFAULT_CACHE_TTL_S), now, force,
                           credential=token, log_tag="proxmox")
    if hit is not None:
        return hit
    res_url = base + _API + "/cluster/resources"
    dbg = DebugRecorder()
    perms_probe: Any = None
    perms_status = 0
    perms_snippet = ""
    perms_probed = False
    try:
        async with httpx.AsyncClient(verify=_verify(chip), timeout=20.0,
                                     follow_redirects=True) as cli:
            rr = await cli.get(res_url, headers=_headers(token))
            if rr.status_code != 200:
                print(f"[proxmox] error: fetch host={host_id} url={rr.request.url} "
                      f"returned HTTP {rr.status_code} (check the chip URL points "
                      f"at the Proxmox host, e.g. https://192.X.X.X:8006)")
                if rr.status_code in (401, 403):
                    raise RuntimeError(f"upstream auth failed: HTTP {rr.status_code} "
                                       f"(check the Proxmox API token) — {res_url}")
                raise RuntimeError(f"upstream returned HTTP {rr.status_code} for {res_url}")
            try:
                resources = as_list(as_dict(rr.json()).get("data"))
            except (ValueError, TypeError):
                raise RuntimeError("upstream returned non-JSON")
            # When the token sees the node(s) but no guests, the card looks
            # broken. Ask Proxmox what this TOKEN can actually audit (GET
            # /access/permissions returns the token's EFFECTIVE permission tree)
            # so we can name the exact gap (Privilege Separation vs missing
            # PVEAuditor vs wrong path) instead of guessing.
            _has_guests = any(
                isinstance(r, dict)
                and str(r.get("type") or "").lower() in ("qemu", "lxc")
                for r in resources)
            if not _has_guests:
                perms_probed = True
                try:
                    pr = await cli.get(base + _API + "/access/permissions",
                                       headers=_headers(token))
                    perms_status = pr.status_code
                    perms_snippet = (pr.text or "").strip()[:200]
                    if 200 <= pr.status_code < 300:
                        try:
                            perms_probe = pr.json()
                        except (ValueError, TypeError):
                            perms_probe = None
                except (httpx.HTTPError, OSError) as e:
                    perms_status = 0
                    perms_snippet = type(e).__name__
            version = await _fetch_version(cli, base, token)
    except (httpx.HTTPError, OSError) as e:  # noqa: BLE001
        print(f"[proxmox] error: fetch host={host_id} url={res_url} "
              f"failed — {type(e).__name__}: {e}")
        raise RuntimeError(f"upstream fetch failed: {type(e).__name__}: {e}")

    nodes_online = nodes_total = 0
    vms_running = vms_total = 0
    cts_running = cts_total = 0
    node_cpu_sum = 0.0
    node_mem = node_maxmem = 0
    type_counts: dict[str, int] = {}
    for r in resources:
        if not isinstance(r, dict):
            continue
        typ = str(r.get("type") or "").strip().lower()
        type_counts[typ] = type_counts.get(typ, 0) + 1
        status = str(r.get("status") or "").strip().lower()
        if typ == "node":
            nodes_total += 1
            if status == "online":
                nodes_online += 1
                node_cpu_sum += float(r.get("cpu") or 0.0)
                node_mem += safe_int(r.get("mem"))
                node_maxmem += safe_int(r.get("maxmem"))
        elif typ == "qemu":
            vms_total += 1
            if status == "running":
                vms_running += 1
        elif typ == "lxc":
            cts_total += 1
            if status == "running":
                cts_running += 1
    storage_used, storage_total = _storage_totals(resources)
    cpu_percent = round(node_cpu_sum / nodes_online * 100) if nodes_online else 0
    mem_percent = _pct(node_mem, node_maxmem)
    # Permission-scope heuristic: GET /cluster/resources returns ONLY the
    # objects the API token may audit. When the token sees the node(s) but no
    # guests AND no node cpu/mem, it almost certainly lacks the propagated
    # PVEAuditor role on '/', so qemu/lxc + per-node metrics are filtered out
    # (the "Containers 0/0 + CPU/Memory 0%" report). Surface an actionable hint
    # rather than a silently-wrong card.
    perm_limited = bool(
        nodes_online > 0
        and (vms_total + cts_total) == 0
        and node_maxmem == 0)
    # Diagnostics block — record the load-bearing resources call + (when guests
    # were missing) the token-permissions probe, then derive a precise,
    # actionable hint from the token's effective permissions. Surfaced via the
    # generic app-drawer debug pane (out['_debug']) AND as perm_summary on the
    # card's perm-limited warning so the exact gap is visible without it.
    _types_str = ",".join(f"{k}={v}" for k, v in sorted(type_counts.items())) or "none"
    dbg.record("Cluster resources", "GET", "/api2/json/cluster/resources",
               status=200, rows=len(resources), ok=True,
               snippet=f"types: {_types_str}")
    perm_summary = ""
    perm_hint = ""
    if perms_probed:
        _has_audit, perm_summary = _summarize_permissions(perms_probe)
        dbg.record("Token permissions", "GET", "/api2/json/access/permissions",
                   status=perms_status, ok=(200 <= perms_status < 300),
                   snippet=perms_snippet or perm_summary)
    if perm_limited:
        perm_hint = _perm_limited_hint(perms_probe if perms_probed else None)
    out: dict[str, Any] = {
        "available": True,
        "nodes_online": nodes_online,
        "nodes_total": nodes_total,
        "vms_running": vms_running,
        "vms_total": vms_total,
        "cts_running": cts_running,
        "cts_total": cts_total,
        "cpu_percent": cpu_percent,
        "mem_percent": mem_percent,
        "storage_used": storage_used,
        "storage_total": storage_total,
        "storage_percent": _pct(storage_used, storage_total),
        "perm_limited": perm_limited,
        # What the token was actually allowed to SEE in /cluster/resources —
        # surfaced in the app-data debug JSON so a "guests missing" report is
        # self-diagnosing ({'node': 1} with no 'lxc'/'qemu' == a token that
        # can't audit guests, almost always Privilege Separation).
        "resource_types": type_counts,
        # Effective-permissions summary from GET /access/permissions (only
        # probed when guests were missing) + the precise fix hint — shown on the
        # card's perm-limited warning so the exact gap is visible without
        # opening the debug pane.
        "perm_summary": perm_summary,
        "perm_hint": perm_hint,
        "_debug": dbg.result(hint=perm_hint),
        "version": version,
        "fetched_at": int(now),
    }
    # Resource-type breakdown (_types_str, computed above) is the fastest way to
    # diagnose a "guests missing" report from Admin -> Logs — it shows exactly
    # what the token was allowed to see (e.g. {'node': 1} with no 'lxc'/'qemu'
    # == a permission scope gap); perm_summary adds the effective-permissions
    # readout when the token couldn't audit guests.
    print(f"[proxmox] INFO fetched host={host_id} nodes={nodes_online}/{nodes_total} "
          f"vms={vms_running}/{vms_total} cts={cts_running}/{cts_total} "
          f"cpu={cpu_percent}% mem={mem_percent}% ver={version or '-'} "
          f"resources=[{_types_str}]"
          f"{' PERM-LIMITED perms=[' + perm_summary + ']' if perm_limited else ''}")
    _data_cache[cache_key(host_id, service_idx)] = (now, out)
    return out


def peek_latest(host_id: str, service_idx: int) -> Optional[dict]:
    """Cache-only peek (no upstream call) for the AI context's
    ``app_skills[].last``."""
    data = peek_cache(_data_cache, host_id, service_idx)
    if not isinstance(data, dict) or not data.get("available"):
        return None
    return {
        "nodes_online": safe_int(data.get("nodes_online")),
        "nodes_total": safe_int(data.get("nodes_total")),
        "vms_running": safe_int(data.get("vms_running")),
        "vms_total": safe_int(data.get("vms_total")),
        "cts_running": safe_int(data.get("cts_running")),
        "cts_total": safe_int(data.get("cts_total")),
        "cpu_percent": safe_int(data.get("cpu_percent")),
        "mem_percent": safe_int(data.get("mem_percent")),
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
    Raises ValueError on an unknown skill id (route maps to HTTP 404)."""
    if skill_id == "proxmox_status":
        return await _status_skill(host_row, chip, host_id=host_id,
                                   service_idx=service_idx)
    if skill_id == "proxmox_vms":
        return await _guests_skill(host_row, chip, kind="qemu", host_id=host_id)
    if skill_id == "proxmox_containers":
        return await _guests_skill(host_row, chip, kind="lxc", host_id=host_id)
    if skill_id == "proxmox_start_guest":
        return await _power_skill(host_row, chip, arg=arg, action="start", host_id=host_id)
    if skill_id == "proxmox_stop_guest":
        return await _power_skill(host_row, chip, arg=arg, action="shutdown", host_id=host_id)
    raise ValueError(f"unknown skill: {skill_id!r}")


def _resolve_skill_target(host_row: dict, chip: dict) -> "tuple[str, str, Optional[dict]]":
    """Resolve ``(token, base)`` or a ready ``{ok: False, detail}`` error."""
    token = (chip.get("api_key") or "").strip()
    if not token:
        return "", "", {"ok": False, "status": 0, "detail": "Proxmox API token not set"}
    base = resolve_base_url(host_row, chip)
    if not base:
        return "", "", {"ok": False, "status": 0, "detail": "no upstream URL configured"}
    return token, base, None


# noinspection DuplicatedCode
def _fmt_bytes(n: Any) -> str:
    """Humanise a byte count (B / KB / MB / GB / TB, 1024-base)."""
    v = float(max(0, safe_int(n)))
    for unit in ("B", "KB", "MB", "GB"):
        if v < 1024:
            return f"{v:.0f} {unit}" if unit == "B" else f"{v:.1f} {unit}"
        v /= 1024
    return f"{v:.1f} TB"


# noinspection DuplicatedCode
async def _status_skill(host_row: dict, chip: dict, *,
                        host_id: Optional[str] = None,
                        service_idx: Optional[int] = None) -> dict:
    """Read-only: live-fetch the cluster summary (force-bypasses the cache).
    Never raises."""
    print(f"[proxmox] INFO proxmox_status host={host_id} svc_idx={service_idx} (live fetch)")
    try:
        data = await fetch_data(host_row, chip, host_id=str(host_id or ""),
                                service_idx=int(service_idx or 0), force=True)
    except (ValueError, RuntimeError) as e:
        return {"ok": False, "detail": str(e), "status": 0}
    lines = [
        f"🖥️ Nodes: {safe_int(data.get('nodes_online'))}/"
        f"{safe_int(data.get('nodes_total'))} online",
        f"💻 VMs: {safe_int(data.get('vms_running'))}/"
        f"{safe_int(data.get('vms_total'))} running",
        f"📦 Containers: {safe_int(data.get('cts_running'))}/"
        f"{safe_int(data.get('cts_total'))} running",
        f"⚙️ CPU: {safe_int(data.get('cpu_percent'))}% · "
        f"🧠 Memory: {safe_int(data.get('mem_percent'))}%",
    ]
    if safe_int(data.get("storage_total")):
        lines.append(f"💾 Storage: {_fmt_bytes(data.get('storage_used'))} / "
                     f"{_fmt_bytes(data.get('storage_total'))} "
                     f"({safe_int(data.get('storage_percent'))}%)")
    ver = str(data.get("version") or "").strip()
    if ver:
        lines.append(f"· PVE {ver}")
    return {"ok": True, "detail": "\n".join(lines), "status": 200,
            "nodes_online": safe_int(data.get("nodes_online")),
            "vms_running": safe_int(data.get("vms_running")),
            "cts_running": safe_int(data.get("cts_running"))}


def _guest_row(r: dict) -> Optional[dict]:
    """One VM / container as a rich skill-result item: name title + a
    status / node / cpu / mem subtitle + a per-row start ▶ / stop ⏹ button."""
    if not isinstance(r, dict):
        return None
    typ = str(r.get("type") or "").strip().lower()
    vmid = safe_int(r.get("vmid"))
    name = str(r.get("name") or "").strip() or (f"{typ.upper()} {vmid}" if vmid else typ)
    status = str(r.get("status") or "").strip().lower()
    emoji = _GUEST_STATUS_EMOJI.get(status, "⚪")
    bits = [f"{emoji} {status or 'unknown'}"]
    node = str(r.get("node") or "").strip()
    if node:
        bits.append(f"node {node}")
    if status == "running":
        bits.append(f"{round(float(r.get('cpu') or 0.0) * 100)}% cpu")
        mem = safe_int(r.get("mem"))
        maxmem = safe_int(r.get("maxmem"))
        if maxmem:
            bits.append(f"{_fmt_bytes(mem)} / {_fmt_bytes(maxmem)}")
    out: dict = {"title": name, "subtitle": " · ".join(bits)}
    # Per-row power button — Start when stopped, Stop (graceful shutdown) when
    # running. arg = the VMID (cluster-unique, unambiguous). Stop is destructive.
    if vmid:
        if status == "running":
            out["row_action"] = {
                "skill_id": "proxmox_stop_guest", "arg": str(vmid),
                "destructive": True, "icon": "x",
                "title_i18n": "apps.proxmox.stop_guest",
                "confirm_i18n": "apps.proxmox.stop_guest_confirm",
                "confirm_text_i18n": "apps.proxmox.stop_guest"}
        elif status in ("stopped", "paused", "suspended"):
            out["row_action"] = {
                "skill_id": "proxmox_start_guest", "arg": str(vmid),
                "destructive": False, "icon": "play",
                "title_i18n": "apps.proxmox.start_guest",
                "confirm_i18n": "apps.proxmox.start_guest_confirm",
                "confirm_text_i18n": "apps.proxmox.start_guest"}
    return out


async def _guests_skill(host_row: dict, chip: dict, *, kind: str,
                        host_id: Optional[str] = None) -> dict:
    """Read-only: list VMs (``kind='qemu'``) or containers (``kind='lxc'``) as
    rich rows, running first then by name. Never raises."""
    token, base, err = _resolve_skill_target(host_row, chip)
    if err:
        return err
    label = "VMs" if kind == "qemu" else "containers"
    print(f"[proxmox] INFO proxmox_{kind} host={host_id} (live fetch)")
    try:
        async with httpx.AsyncClient(verify=_verify(chip), timeout=20.0,
                                     follow_redirects=True) as cli:
            body = await _get(cli, base + _API + "/cluster/resources", token)
    except (httpx.HTTPError, OSError) as e:  # noqa: BLE001
        return {"ok": False, "status": 0, "detail": f"fetch failed: {type(e).__name__}: {e}"}
    guests = [r for r in as_list(as_dict(body).get("data"))
              if isinstance(r, dict) and str(r.get("type") or "").lower() == kind]
    if not guests:
        return {"ok": True, "status": 200, "detail": f"📦 No Proxmox {label} found."}
    guests.sort(key=lambda _r: (str(_r.get("status") or "") != "running",
                                str(_r.get("name") or "").lower()))
    items: list = []
    lines: list = []
    for r in guests[:_MAX_ROWS]:
        row = _guest_row(r)
        if row:
            items.append(row)
            lines.append(f"• {row['title']}  ({row['subtitle']})")
    icon = "💻" if kind == "qemu" else "📦"
    out: dict = {"ok": True, "status": 200,
                 "detail": f"{icon} Proxmox {label}:\n" + "\n".join(lines)}
    return _attach_items(out, items, "apps.proxmox.guests_count")


def _attach_items(out: dict, items: list, count_i18n: str) -> dict:
    """Attach the rich-item list + count + count-i18n key (no-op when empty)."""
    if items:
        out["items"] = items
        out["count"] = len(items)
        out["count_i18n"] = count_i18n
    return out


async def _power_skill(host_row: dict, chip: dict, *, arg: Optional[str],
                       action: str, host_id: Optional[str] = None) -> dict:
    """Start (``action='start'``) or gracefully stop (``action='shutdown'``) ONE
    guest. Resolves the target from ``/cluster/resources`` by exact VMID (the
    per-row button) else a name substring (AI / Telegram), then
    ``POST /nodes/{node}/{type}/{vmid}/status/{action}``. Never raises."""
    needle = (arg or "").strip()
    if not needle:
        return {"ok": False, "status": 0,
                "detail": "no VM / container given (say e.g. \"start the web vm\")"}
    token, base, err = _resolve_skill_target(host_row, chip)
    if err:
        return err
    nl = needle.lower()
    print(f"[proxmox] INFO proxmox_{action} host={host_id} target={needle!r}")
    try:
        async with httpx.AsyncClient(verify=_verify(chip), timeout=20.0,
                                     follow_redirects=True) as cli:
            body = await _get(cli, base + _API + "/cluster/resources", token)
            guests = [r for r in as_list(as_dict(body).get("data"))
                      if isinstance(r, dict)
                      and str(r.get("type") or "").lower() in ("qemu", "lxc")]
            target = None
            for r in guests:
                if str(safe_int(r.get("vmid"))) == needle:  # exact VMID (button)
                    target = r
                    break
                if target is None and nl in str(r.get("name") or "").lower():
                    target = r
            if target is None:
                return {"ok": False, "status": 404,
                        "detail": f"no Proxmox VM / container matched \"{needle}\""}
            node = str(target.get("node") or "").strip()
            typ = str(target.get("type") or "").strip().lower()
            vmid = safe_int(target.get("vmid"))
            gname = str(target.get("name") or f"{typ.upper()} {vmid}").strip()
            if not (node and typ and vmid):
                return {"ok": False, "status": 502,
                        "detail": "couldn't resolve the guest's node / id"}
            pr = await cli.post(
                base + _API + f"/nodes/{node}/{typ}/{vmid}/status/{action}",
                headers=_headers(token))
    except (httpx.HTTPError, OSError) as e:  # noqa: BLE001
        return {"ok": False, "status": 0, "detail": f"{action} failed: {type(e).__name__}: {e}"}
    if pr.status_code in (401, 403):
        return {"ok": False, "status": pr.status_code,
                "detail": "auth failed (the API token needs VM.PowerMgmt on the guest)"}
    if pr.status_code not in (200, 201, 204):
        return {"ok": False, "status": pr.status_code,
                "detail": f"Proxmox didn't accept the {action} (HTTP {pr.status_code})"}
    verb = "Starting" if action == "start" else "Shutting down"
    emoji = "▶️" if action == "start" else "🛑"
    return {"ok": True, "status": 200, "detail": f"{emoji} {verb} {gname}."}
