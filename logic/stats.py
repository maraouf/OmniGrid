"""Live container stats (CPU / memory / disk) and time-series history.

Separate cache from :mod:`logic.gather` so a fast stats refresh doesn't
trigger the expensive registry-digest pass. Driven by:

  - ``gather_stats()`` — per-item aggregate for the UI, 30s default TTL
  - ``stats_sampler_loop()`` — lifespan task that snapshots into the
    ``stats_samples`` table every ``STATS_SAMPLE_INTERVAL_SECONDS``;
    prunes hourly to ``STATS_HISTORY_DAYS``
  - ``stats_history()`` — serves per-item sparklines from the sampled rows

Cache dict is exposed as ``_stats_cache`` for main.py's /api/stats route.
"""
import asyncio
import os
import time
from typing import Optional

import httpx

from logic import gather as _gather_mod
from logic import portainer
from logic.db import db_conn


STATS_HISTORY_DAYS = int(os.getenv("STATS_HISTORY_DAYS", "7"))
STATS_SAMPLE_INTERVAL = int(os.getenv("STATS_SAMPLE_INTERVAL_SECONDS", "300"))  # 5 min


# The cache main.py's /api/stats route reads. Structure:
#   stats: {item_id: {cpu_percent, mem_usage, mem_limit, size_root, size_rw,
#                     has_stats, has_size}}
#   ts:    epoch seconds of last successful gather
_stats_cache: dict = {"stats": {}, "ts": 0.0}


def get_stats_cache() -> dict:
    return _stats_cache


def seed_stats_cache_from_db() -> int:
    """Pre-populate ``_stats_cache`` with the most recent persisted
    sample per item_id so ``/api/stats`` has data to serve before the
    first live ``gather_stats()`` completes after a restart.

    Marks every seeded entry with ``_stale=True`` so the UI can dim
    the bar / sparkline until fresh values land. Sets ``_stats_cache``
    timestamp to 0 so the next ``/api/stats`` call still sees the TTL
    as expired and triggers an immediate live refresh — the seeded
    values are a placeholder, not authoritative.

    Returns the number of items seeded.
    """
    try:
        with db_conn() as c:
            # SQLite 3.25+ supports ROW_NUMBER OVER PARTITION BY — every
            # bundled python on a recent OS has it. The window pulls the
            # latest row per item_id in one query rather than N+1.
            rows = c.execute("""
                SELECT item_id, ts, cpu, mem_used, mem_limit FROM (
                    SELECT item_id, ts, cpu, mem_used, mem_limit,
                           ROW_NUMBER() OVER (
                               PARTITION BY item_id ORDER BY ts DESC
                           ) rn
                    FROM stats_samples
                ) WHERE rn = 1
            """).fetchall()
    except Exception as e:
        print(f"[sampler] seed_stats_cache_from_db failed: {e}")
        return 0
    if not rows:
        return 0
    seeded: dict[str, dict] = {}
    for r in rows:
        seeded[r["item_id"]] = {
            "cpu_percent": float(r["cpu"] or 0.0),
            "mem_usage":   int(r["mem_used"] or 0),
            "mem_limit":   int(r["mem_limit"] or 0),
            # size_root / size_rw aren't sampled into stats_samples (it's
            # a CPU/memory time-series table), so we report has_size=False
            # which lets the UI show "—" until the first live gather.
            "size_root":   0,
            "size_rw":     0,
            "has_stats":   True,
            "has_size":    False,
            "_stale":      True,
            "_stale_ts":   float(r["ts"] or 0.0),
        }
    _stats_cache["stats"] = seeded
    # Force the next gather_stats() to refresh — we don't want the TTL
    # to suppress a live poll just because we seeded the cache.
    _stats_cache["ts"] = 0.0
    return len(seeded)


# ---------------------------------------------------------------------
# Time-series sampler — writes `_stats_cache` into `stats_samples` on
# STATS_SAMPLE_INTERVAL, prunes old rows hourly. Runs as a lifespan task.
# ---------------------------------------------------------------------
def _snapshot_stats_to_db() -> int:
    """Write the current _stats_cache into stats_samples. Returns row count."""
    snap = _stats_cache.get("stats") or {}
    if not snap:
        return 0
    ts = time.time()
    rows = [
        (ts, item_id, s.get("cpu_percent") or 0.0,
         s.get("mem_usage") or 0, s.get("mem_limit") or 0)
        for item_id, s in snap.items()
        if s.get("has_stats")
    ]
    if not rows:
        return 0
    with db_conn() as c:
        c.executemany(
            "INSERT INTO stats_samples (ts, item_id, cpu, mem_used, mem_limit) "
            "VALUES (?, ?, ?, ?, ?)",
            rows,
        )
    return len(rows)


def _prune_old_samples() -> int:
    """Delete rows older than STATS_HISTORY_DAYS. Returns rows removed."""
    cutoff = time.time() - STATS_HISTORY_DAYS * 86400
    with db_conn() as c:
        cur = c.execute("DELETE FROM stats_samples WHERE ts < ?", (cutoff,))
        return cur.rowcount or 0


async def stats_sampler_loop() -> None:
    # Wait a beat so the first gather_stats() has a chance to populate
    # _stats_cache before we write a row of zeros.
    await asyncio.sleep(min(60, STATS_SAMPLE_INTERVAL))
    tick = 0
    while True:
        try:
            n = _snapshot_stats_to_db()
            # Prune hourly rather than every tick — single cheap DELETE,
            # but no need to churn on every 5-minute cycle.
            if tick % max(1, 3600 // STATS_SAMPLE_INTERVAL) == 0:
                pruned = _prune_old_samples()
                if pruned:
                    print(f"[sampler] pruned {pruned} rows older than {STATS_HISTORY_DAYS}d")
            if n:
                print(f"[sampler] wrote {n} samples")
        except Exception as e:
            print(f"[sampler] error: {e}")
        tick += 1
        try:
            await asyncio.sleep(STATS_SAMPLE_INTERVAL)
        except asyncio.CancelledError:
            raise


def stats_history(item_ids: list[str], since: float) -> dict[str, list[dict]]:
    """Return ``{item_id: [{ts, cpu, mem_used, mem_limit}, ...]}`` for the
    given ids back to ``since`` (epoch seconds), oldest-first. Empty list
    per missing id.
    """
    if not item_ids:
        return {}
    placeholders = ",".join("?" * len(item_ids))
    out: dict[str, list[dict]] = {i: [] for i in item_ids}
    with db_conn() as c:
        rows = c.execute(
            f"SELECT item_id, ts, cpu, mem_used, mem_limit FROM stats_samples "
            f"WHERE ts >= ? AND item_id IN ({placeholders}) "
            f"ORDER BY ts ASC",
            (since, *item_ids),
        ).fetchall()
    for r in rows:
        out[r["item_id"]].append({
            "ts": r["ts"],
            "cpu": r["cpu"],
            "mem_used": r["mem_used"],
            "mem_limit": r["mem_limit"],
        })
    return out


# ---------------------------------------------------------------------
# Live per-container stats polling.
# ---------------------------------------------------------------------
def _parse_stats_payload(s: dict) -> dict:
    cpu_now = ((s.get("cpu_stats") or {}).get("cpu_usage") or {}).get("total_usage", 0)
    cpu_prev = ((s.get("precpu_stats") or {}).get("cpu_usage") or {}).get("total_usage", 0)
    sys_now = (s.get("cpu_stats") or {}).get("system_cpu_usage", 0)
    sys_prev = (s.get("precpu_stats") or {}).get("system_cpu_usage", 0)
    online = (
        (s.get("cpu_stats") or {}).get("online_cpus")
        or len(((s.get("cpu_stats") or {}).get("cpu_usage") or {}).get("percpu_usage") or [])
        or 1
    )
    cpu_delta = cpu_now - cpu_prev
    sys_delta = sys_now - sys_prev
    cpu_percent = 0.0
    if sys_delta > 0 and cpu_delta > 0:
        cpu_percent = (cpu_delta / sys_delta) * online * 100.0

    mem = s.get("memory_stats") or {}
    mem_usage = mem.get("usage", 0) or 0
    mem_limit = mem.get("limit", 0) or 0
    # Docker's `usage` includes page cache; subtract inactive_file to match `docker stats`.
    mstat = mem.get("stats") or {}
    cache = mstat.get("inactive_file", 0) or mstat.get("cache", 0) or 0
    mem_usage = max(0, mem_usage - cache)
    return {
        "cpu_percent": round(cpu_percent, 1),
        "mem_usage": int(mem_usage),
        "mem_limit": int(mem_limit),
    }


async def _one_container_stats(
    client: httpx.AsyncClient, ep: str, cid: str, node: Optional[str] = None,
) -> Optional[dict]:
    """One-shot Docker stats for a running container. Returns None on failure.

    If ``node`` is known (Swarm task containers), first try with
    ``X-PortainerAgent-Target: <node>`` on a short timeout. Needed for
    single-replica services on worker nodes where Portainer's default
    aggregation doesn't resolve the container. On any failure, fall back
    to the untargeted call so we don't regress containers that today
    work fine without a target header.
    """
    url = f"{portainer.PORTAINER_URL}{ep}/containers/{cid}/stats?stream=false"
    if node:
        try:
            r = await client.get(url, headers=portainer.headers(agent_target=node), timeout=4.0)
            if r.status_code == 200:
                return _parse_stats_payload(r.json())
        except Exception:
            pass
    try:
        r = await client.get(url, headers=portainer.headers(), timeout=10.0)
        if r.status_code != 200:
            return None
        return _parse_stats_payload(r.json())
    except Exception as e:
        print(f"[stats] {cid[:12]}: {e}")
        return None


async def gather_stats() -> None:
    """Compute per-item CPU/memory/disk using the latest gather cache.

    Services aggregate stats across all their running task containers.
    Standalone containers map directly by ID.
    """
    items_cache = _gather_mod.get_cache()
    if not items_cache["items"]:
        # Diagnostic — surfaces the early-return that would explain
        # why /api/stats returns {} despite stats_samples being
        # populated. The note_todo #250 thread chased this for a
        # while; the operator log capture will pin the cause.
        print(f"[stats] gather_stats early-return: items_cache empty (size={len(items_cache.get('items') or [])})")
        return
    if not portainer.is_configured():
        # Mirror the gather short-circuit. Without this we'd send httpx
        # requests to an empty URL and log noise on every poll tick.
        print("[stats] gather_stats early-return: portainer.is_configured() == False")
        return
    print(f"[stats] gather_stats start: items={len(items_cache['items'])}")
    async with httpx.AsyncClient(verify=portainer.VERIFY_TLS, timeout=30.0) as client:
        ep = f"/api/endpoints/{portainer.PORTAINER_ENDPOINT_ID}/docker"
        try:
            containers = await portainer.pg(client, f"{ep}/containers/json?all=1&size=1")
        except Exception as e:
            print(f"[stats] gather_stats: containers fetch FAILED: {type(e).__name__}: {e}")
            containers = []
        print(f"[stats] gather_stats: containers fetched={len(containers)}")

        # Track two sizes per container:
        #   size_root = full image size on disk (SizeRootFs). Always non-zero and
        #               the number a user thinks of when they say "disk size".
        #   size_rw   = writable-layer delta. Useful to spot containers that are
        #               leaking data into their filesystem, but usually ~0.
        size_root_by_cid: dict[str, int] = {}
        size_rw_by_cid: dict[str, int] = {}
        svc_by_cid: dict[str, Optional[str]] = {}
        # cid → hostname. Priority order (authoritative → heuristic), same
        # as gather.py's resolver:
        #   1. `com.docker.swarm.node.id` label — Swarm's own scheduler
        #      wrote this; the most reliable signal for anything Swarm-
        #      managed (services, global, orphan task containers).
        #   2. task_node_by_id via the task-ID label — fallback for
        #      older Swarm versions that don't stamp node.id on the
        #      container itself.
        #   3. container_node_by_id from gather's per-node sweep —
        #      only signal we have for plain compose containers.
        # _one_container_stats falls back to the untargeted request on
        # failure, so a wrong hint only costs one extra call.
        nodes_by_id = items_cache.get("nodes") or {}
        task_node_by_id = items_cache.get("task_node_by_id") or {}
        container_node_by_id = items_cache.get("container_node_by_id") or {}
        node_by_cid: dict[str, Optional[str]] = {}
        running_cids: list[str] = []
        for c in containers:
            cid = c["Id"]
            size_root_by_cid[cid] = c.get("SizeRootFs", 0) or 0
            size_rw_by_cid[cid] = c.get("SizeRw", 0) or 0
            labels = c.get("Labels") or {}
            svc_by_cid[cid] = labels.get("com.docker.swarm.service.id")
            # Swarm node.id label (authoritative) first.
            node_id_label = labels.get("com.docker.swarm.node.id")
            node = nodes_by_id.get(node_id_label) if node_id_label else None
            if not node:
                task_id = labels.get("com.docker.swarm.task.id")
                node = task_node_by_id.get(task_id) if task_id else None
            if not node:
                node = container_node_by_id.get(cid)
            node_by_cid[cid] = node
            if (c.get("State") or "").lower() == "running":
                running_cids.append(cid)

        sem = asyncio.Semaphore(portainer.STATS_CONCURRENCY)

        async def fetch(cid: str):
            async with sem:
                return cid, await _one_container_stats(client, ep, cid, node_by_cid.get(cid))

        results = await asyncio.gather(*(fetch(cid) for cid in running_cids))
        stats_by_cid = {cid: s for cid, s in results if s}

        out: dict[str, dict] = {}
        for item in items_cache["items"]:
            cpu = 0.0
            mem_usage = 0
            mem_limit = 0
            # Image size is per-image, not per-container. For services with
            # multiple replicas, all replicas share the same image on disk, so
            # we keep ONE representative value instead of summing.
            size_root = 0
            size_rw = 0
            has_stats = False
            has_size = False
            if item.get("type") == "service":
                sid = item["raw_id"]
                for cid, owner in svc_by_cid.items():
                    if owner != sid:
                        continue
                    if cid in size_root_by_cid:
                        # Representative image size — same for every replica.
                        size_root = max(size_root, size_root_by_cid[cid])
                        size_rw += size_rw_by_cid.get(cid, 0)
                        has_size = True
                    st = stats_by_cid.get(cid)
                    if st:
                        cpu += st["cpu_percent"]
                        mem_usage += st["mem_usage"]
                        # Sum limits across replicas — 3 replicas at 1 GB each
                        # mean the service's effective limit is 3 GB. Without
                        # this, a perfectly-utilised service could exceed 100%.
                        mem_limit += st["mem_limit"]
                        has_stats = True
            else:
                cid = item["raw_id"]
                if cid in size_root_by_cid:
                    size_root = size_root_by_cid[cid]
                    size_rw = size_rw_by_cid.get(cid, 0)
                    has_size = True
                st = stats_by_cid.get(cid)
                if st:
                    cpu = st["cpu_percent"]
                    mem_usage = st["mem_usage"]
                    mem_limit = st["mem_limit"]
                    has_stats = True
            out[item["id"]] = {
                "cpu_percent": round(cpu, 1),
                "mem_usage": int(mem_usage),
                "mem_limit": int(mem_limit),
                "size_root": int(size_root),
                "size_rw": int(size_rw),
                "has_stats": has_stats,
                "has_size": has_size,
            }
        _stats_cache["stats"] = out
        _stats_cache["ts"] = time.time()
        with_stats = sum(1 for v in out.values() if v.get("has_stats"))
        with_size  = sum(1 for v in out.values() if v.get("has_size"))
        print(f"[stats] gather_stats wrote: items={len(out)} has_stats_true={with_stats} has_size_true={with_size}")
