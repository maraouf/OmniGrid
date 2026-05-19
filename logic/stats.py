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
import time
from typing import Any, Optional

import httpx

from logic import gather as _gather_mod
from logic import portainer
from logic import tuning
from logic.tuning import Tunable
from logic.db import db_conn

# The cache main.py's /api/stats route reads. Structure:
# stats: {item_id: {cpu_percent, mem_usage, mem_limit, size_root, size_rw,
#                   has_stats, has_size}}
# ts:    epoch seconds of last successful gather
_stats_cache: dict = {"stats": {}, "ts": 0.0}

# Per-Swarm-node "agent appears unreachable" tracker — populated at
# the end of every successful `gather_stats` based on what fraction
# of task-derived cids on each node returned stats. After N consecutive
# gathers where the node had running tasks but ZERO successful stats
# calls, surfaces in `/api/stats` as `unhealthy_agents` so the SPA
# banner can flag the operator. Common cause: Swarm manager bounced,
# Portainer agents on workers didn't re-register cleanly. Operator
# fix: `docker service update --force <portainer-agent-service>` on
# the manager. Auto-restart is tracked separately for LATER.
#
# Shape: {hostname: {fails: int, since_ts: float, task_cids: int}}
# - fails: consecutive bad gathers
# - since_ts: epoch of FIRST bad gather (how long has this been broken?)
# - task_cids: how many task-derived cids were observed on the node
#              during the most recent bad gather (operator-facing
#              hint for "N containers worth of metrics are missing")
_agent_health: dict[str, dict[str, Any]] = {}


def get_stats_cache() -> dict:
    """Return the shared module-level `_stats_cache` dict (per-item resource samples)."""
    return _stats_cache


def get_agent_health() -> dict:
    """Return the shared module-level `_agent_health` dict (per-node bad-gather state)."""
    return _agent_health


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
                             SELECT item_id, ts, cpu, mem_used, mem_limit
                             FROM (SELECT item_id,
                                          ts,
                                          cpu,
                                          mem_used,
                                          mem_limit,
                                          ROW_NUMBER() OVER (
                               PARTITION BY item_id ORDER BY ts DESC
                           ) rn
                                   FROM stats_samples)
                             WHERE rn = 1
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
            "mem_usage": int(r["mem_used"] or 0),
            "mem_limit": int(r["mem_limit"] or 0),
            # size_root / size_rw aren't sampled into stats_samples (it's
            # a CPU/memory time-series table), so we report has_size=False
            # which lets the UI show "—" until the first live gather.
            "size_root": 0,
            "size_rw": 0,
            "has_stats": True,
            "has_size": False,
            "_stale": True,
            "_stale_ts": float(r["ts"] or 0.0),
        }
    _stats_cache["stats"] = seeded
    # Force the next gather_stats() to refresh — we don't want the TTL
    # to suppress a live poll just because we seeded the cache.
    _stats_cache["ts"] = 0.0
    return len(seeded)


# ---------------------------------------------------------------------
# Time-series sampler — writes `_stats_cache` into `stats_samples` every
# tuning_stats_sample_interval_seconds (DB > env > default), prunes old
# rows hourly. Runs as a lifespan task.
# ---------------------------------------------------------------------
def _snapshot_stats_to_db() -> int:
    """Write the current _stats_cache into stats_samples. Returns row count."""
    snap = _stats_cache.get("stats") or {}
    if not snap:
        return 0
    ts = time.time()
    # Skip entries seeded from disk by ``seed_stats_cache_from_db`` —
    # they're flagged ``_stale=True`` and have not been overwritten by
    # a live ``gather_stats()`` yet. Persisting them would re-INSERT
    # the most-recent pre-restart sample with ``ts=now``, polluting
    # the time-series with phantom duplicates.
    rows = [
        (ts, item_id, s.get("cpu_percent") or 0.0,
         s.get("mem_usage") or 0, s.get("mem_limit") or 0,
         s.get("size_root") or 0)
        for item_id, s in snap.items()
        if s.get("has_stats") and not s.get("_stale")
    ]
    if not rows:
        return 0
    with db_conn() as c:
        c.executemany(
            "INSERT INTO stats_samples (ts, item_id, cpu, mem_used, mem_limit, size_root) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            rows,
        )
    return len(rows)


def _prune_old_samples() -> int:
    """Delete rows older than the current history-days setting. Returns rows removed."""
    days = tuning.tuning_int(Tunable.STATS_HISTORY_DAYS)
    cutoff = time.time() - days * 86400
    with db_conn() as c:
        cur = c.execute("DELETE FROM stats_samples WHERE ts < ?", (cutoff,))
        return cur.rowcount or 0


async def stats_sampler_loop() -> None:
    """Lifespan-managed loop that snapshots `_stats_cache` into `stats_samples` + prunes hourly."""
    # Wait a beat so the first gather_stats() has a chance to populate
    # _stats_cache before we write a row of zeros.
    interval = tuning.tuning_int(Tunable.STATS_SAMPLE_INTERVAL_SECONDS)
    await asyncio.sleep(min(60, interval))
    tick = 0
    while True:
        try:
            n = _snapshot_stats_to_db()
            interval = tuning.tuning_int(Tunable.STATS_SAMPLE_INTERVAL_SECONDS)
            days = tuning.tuning_int(Tunable.STATS_HISTORY_DAYS)
            # Prune hourly rather than every tick — single cheap DELETE,
            # but no need to churn on every 5-minute cycle.
            if tick % max(1, 3600 // interval) == 0:
                pruned = _prune_old_samples()
                if pruned:
                    print(f"[sampler] pruned {pruned} rows older than {days}d")
            if n:
                print(f"[sampler] wrote {n} samples")
        except Exception as e:
            print(f"[sampler] error: {e}")
        tick += 1
        try:
            await asyncio.sleep(interval)
        except asyncio.CancelledError:
            raise


def stats_history(item_ids: list[str], since: float) -> dict[str, list[dict]]:
    """Return ``{item_id: [{ts, cpu, mem_used, mem_limit, size_root}, ...]}``
    for the given ids back to ``since`` (epoch seconds), oldest-first.
    Empty list per missing id. `size_root` is the per-item image-disk
    footprint snapshot (bytes) — feeds the Disk sparkline on Stacks /
    Services / Nodes rows.
    """
    if not item_ids:
        return {}
    placeholders = ",".join("?" * len(item_ids))
    out: dict[str, list[dict]] = {i: [] for i in item_ids}
    with db_conn() as c:
        rows = c.execute(
            f"SELECT item_id, ts, cpu, mem_used, mem_limit, size_root FROM stats_samples "
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
            "size_root": r["size_root"],
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
    *, fallback_nodes: Optional[list[str]] = None,
) -> Optional[dict]:
    """One-shot Docker stats for a running container. Returns None on failure.

    If ``node`` is known (Swarm task containers), first try with
    ``X-PortainerAgent-Target: <node>`` on a short timeout. Needed for
    single-replica services on worker nodes where Portainer's default
    aggregation doesn't resolve the container. On any failure, fall back
    to the untargeted call so we don't regress containers that today
    work fine without a target header.

    Both branches log the response status / error code on failure —
    when the per-service stats row reads as ``—`` for a worker-node
    container, the diagnostic line in Admin → Logs is the primary
    path for figuring out why (e.g. ``404`` means the agent doesn't
    have the cid; a connection error means the agent isn't deployed
    on that node at all and there's no API path through Portainer).
    """
    # Per-use reads of the timeout knobs so a Save in Admin → Config
    # takes effect on the next gather without a restart. Defaults are
    # 12s targeted / 10s untargeted (see TUNABLES). Pre-fix the
    # targeted timeout was hardcoded 4s — operator-reported that
    # Portainer's agent forwarding to busy worker nodes routinely
    # exceeded 4s, so the call would time out and the untargeted
    # fallback would 404 (manager doesn't have the worker's cid),
    # ultimately rendering as `—` in the UI even though the agent
    # would have responded if given more time.
    targeted_to = float(tuning.tuning_int(Tunable.STATS_TARGETED_TIMEOUT_SECONDS))
    untargeted_to = float(tuning.tuning_int(Tunable.STATS_UNTARGETED_TIMEOUT_SECONDS))
    url = f"{portainer.PORTAINER_URL}{ep}/containers/{cid}/stats?stream=false"
    targeted_status: Optional[int] = None
    targeted_err: Optional[str] = None
    if node:
        # Retry-on-500 loop for the agent-targeted call. Operator-
        # observed pattern: with 16 concurrent stats requests fanning
        # through one Portainer Swarm-agent forwarder, a small subset
        # 500s with empty body — likely transient overload of the
        # agent's per-target queue rather than the container being
        # gone (the container WAS in the per-node sweep moments
        # earlier, and `?stream=true` with one frame typically works
        # on retry). Two short retries with linear backoff catch the
        # transient case without hanging the gather on a genuinely
        # broken cid (which 500s persistently → falls through to the
        # untargeted call → 404 → diagnostic log).
        for attempt in range(3):
            try:
                r = await client.get(
                    url, headers=portainer.headers(agent_target=node),
                    timeout=targeted_to,
                )
                if r.status_code == 200:
                    return _parse_stats_payload(r.json())
                targeted_status = r.status_code
                # Only retry on 5xx — 404 means the agent doesn't have
                # the cid, which won't change with a retry.
                if r.status_code < 500 or attempt == 2:
                    break
            except Exception as e:
                targeted_err = f"{type(e).__name__}: {e}"
                if attempt == 2:
                    break
            # Linear backoff between attempts: 0.3s, 0.7s. Keeps the
            # total worst-case at targeted_to + 1s overhead so a fleet
            # of 100 stats calls doesn't blow the gather wall-clock.
            await asyncio.sleep(0.3 + 0.4 * attempt)
    untargeted_status: Optional[int] = None
    untargeted_err: Optional[str] = None
    try:
        r = await client.get(url, headers=portainer.headers(), timeout=untargeted_to)
        if r.status_code == 200:
            return _parse_stats_payload(r.json())
        untargeted_status = r.status_code
    except Exception as e:
        untargeted_err = f"{type(e).__name__}: {e}"

    # Brute-force fallback — try each OTHER known Swarm hostname as
    # agent_target. Catches the case where the tasks-endpoint NodeID
    # points at a node whose Portainer agent doesn't actually host
    # the cid (stale scheduler state, container moved between nodes
    # mid-gather, or per-node /containers/json gap that left the cid
    # off the resolved-node sweep). Single-attempt per fallback host
    # — no retry — so a 5-node fleet pays at most 4 extra calls per
    # failing cid. Skips the originally-tried `node` to avoid double-
    # billing the retry that already happened.
    fallback_status_map: dict[str, int] = {}
    if fallback_nodes:
        for alt in fallback_nodes:
            if alt == node or not alt:
                continue
            try:
                r = await client.get(
                    url, headers=portainer.headers(agent_target=alt),
                    timeout=targeted_to,
                )
                if r.status_code == 200:
                    payload = _parse_stats_payload(r.json())
                    print(f"[stats] {cid[:12]} fallback agent_target={alt} succeeded "
                          f"(originally resolved to {node!r})")
                    return payload
                fallback_status_map[alt] = r.status_code
            except (httpx.HTTPError, OSError, ValueError) as fb_err:
                fallback_status_map[alt] = -1
                print(f"[stats] {cid[:12]} fallback agent_target={alt} err: "
                      f"{type(fb_err).__name__}: {fb_err}")

    # All paths exhausted — log a single diagnostic line that captures
    # every attempt's status code so the operator can see exactly
    # which agent has the cid (or doesn't).
    parts = []
    if node:
        parts.append(f"agent_target={node} status={targeted_status or 'err'}"
                     + (f" ({targeted_err})" if targeted_err else ""))
    if untargeted_status is not None:
        parts.append(f"untargeted status={untargeted_status}")
    elif untargeted_err:
        parts.append(f"untargeted err: {untargeted_err}")
    if fallback_status_map:
        fb_parts = ", ".join(f"{h}={s}" for h, s in fallback_status_map.items())
        parts.append(f"fallback {{{fb_parts}}}")
    if parts:
        print(f"[stats] {cid[:12]} no stats — " + "; ".join(parts))
    else:
        print(f"[stats] {cid[:12]} no stats")
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
        # populated. The operator log capture will pin the cause.
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

        # Container LIST — per-node sweep when the Swarm has 2+ nodes. The
        # unrouted ``/containers/json`` call returns only the manager's
        # containers in modern Portainer agent-mode endpoints, which means
        # worker-node containers (e.g. a single-replica service pinned to a
        # worker) never enter ``svc_by_cid`` / ``size_root_by_cid`` /
        # ``running_cids`` — the per-item walk finds no CID matching the
        # service and ``has_stats`` / ``has_size`` stay False. The
        # ``_one_container_stats`` agent-target dance below is correct but
        # never gets invoked because the CID is missing from the LIST to
        # begin with. Solution mirrors ``logic.gather.py``'s own per-node
        # sweep at line 1142+: fan out per host, dedup by Id, derive
        # cid → host directly from which sweep returned the entry.
        # Single-node deploys keep the unrouted fast-path.
        nodes_by_id = items_cache.get("nodes") or {}
        hostnames = [h for h in nodes_by_id.values() if h]
        sweep_node_by_cid: dict[str, str] = {}
        try:
            if len(hostnames) >= 2:
                async def _per_node(h: str):
                    """Per-host /containers/json fan-out; swallows per-node errors to keep the sweep alive."""
                    try:
                        return h, await portainer.pg(
                            client,
                            f"{ep}/containers/json?all=1&size=1",
                            agent_target=h,
                        )
                    except (httpx.HTTPError, OSError, ValueError) as node_err:
                        print(f"[stats] gather_stats: per-node list for {h} FAILED: "
                              f"{type(node_err).__name__}: {node_err}")
                        return h, []

                per_node = await asyncio.gather(*(_per_node(h) for h in hostnames))
                seen: dict[str, dict[str, Any]] = {}
                for h, lst in per_node:
                    for c in (lst or []):
                        cid = c.get("Id")
                        if not cid or cid in seen:
                            continue
                        seen[cid] = c
                        sweep_node_by_cid[cid] = h
                containers: list[dict[str, Any]] = list(seen.values())
                print(f"[stats] gather_stats: per-node sweep hosts={hostnames} "
                      f"sizes={[len(lst) for _, lst in per_node]} "
                      f"merged={len(containers)}")
            else:
                containers = await portainer.pg(
                    client, f"{ep}/containers/json?all=1&size=1",
                )
                print(f"[stats] gather_stats: containers fetched={len(containers)}")
        except Exception as e:
            print(f"[stats] gather_stats: containers fetch FAILED: {type(e).__name__}: {e}")
            containers = []

        # Track two sizes per container:
        # size_root = full image size on disk (SizeRootFs). Always non-zero and
        #             the number a user thinks of when they say "disk size".
        # size_rw   = writable-layer delta. Useful to spot containers that are
        #             leaking data into their filesystem, but usually ~0.
        size_root_by_cid: dict[str, int] = {}
        size_rw_by_cid: dict[str, int] = {}
        svc_by_cid: dict[str, Optional[str]] = {}
        # cid → hostname. Priority order (authoritative → heuristic):
        # 1. per-node sweep result above — when present, this is the
        #    DEFINITIVE answer because the container only appeared in
        #    that node's per-node response.
        # 2. ``com.docker.swarm.node.id`` label — Swarm's own scheduler
        #    wrote this; reliable for anything Swarm-managed.
        # 3. task_node_by_id via the task-ID label — fallback for
        #    older Swarm versions that don't stamp node.id on the
        #    container itself.
        # 4. container_node_by_id from gather's per-node sweep —
        #    only signal we have for plain compose containers.
        # _one_container_stats falls back to the untargeted request on
        # failure, so a wrong hint only costs one extra call.
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
            node = sweep_node_by_cid.get(cid)
            if not node:
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

        # Tasks-driven backfill — load-bearing for Swarm setups where
        # Portainer's ``/containers/json`` (even per-node with
        # ``X-PortainerAgent-Target``) doesn't expose worker-node
        # containers at all. The Tasks API is Swarm's own state, not
        # the per-node Docker daemon's, so it's visible from the
        # manager regardless of agent topology — every running task
        # carries ``Status.ContainerStatus.ContainerID`` + ``NodeID``.
        # We use that to build (cid, node, service_id) tuples
        # AUTHORITATIVELY, then call ``_one_container_stats`` per
        # tuple. CPU + memory will work; ``size_root`` / ``size_rw``
        # remain 0 for tasks whose cid was never in the containers
        # list (no per-node ``size=1`` data to read), which is the
        # acceptable trade-off — disk size is secondary; CPU + memory
        # are what the operator reads first.
        try:
            tasks = await portainer.pg(client, f"{ep}/tasks")
        except Exception as e:
            print(f"[stats] gather_stats: tasks fetch FAILED: {type(e).__name__}: {e}")
            tasks = []
        tasks_added = 0
        # Cids known ONLY via the Swarm tasks endpoint (not in the
        # per-node container sweep). Used by the agent-health
        # detection at the end of this function — see comment there.
        _task_derived_cids: set[str] = set()
        for t in (tasks or []):
            status = t.get("Status") or {}
            if status.get("State") != "running":
                continue
            cstat = status.get("ContainerStatus") or {}
            tcid = cstat.get("ContainerID") or ""
            if not tcid:
                continue
            nid = t.get("NodeID")
            tnode = nodes_by_id.get(nid) if nid else None
            tsid = t.get("ServiceID") or None
            # Wire the task into the running-stats pipeline regardless
            # of whether the cid was already in the merged container
            # list. If it WAS in the list, we just refine the node
            # mapping (tasks-derived NodeID is more authoritative than
            # the per-node sweep when both disagree). If it WASN'T, we
            # add it WITHOUT a size_root_by_cid entry — leaving size
            # dicts unpopulated keeps ``has_size=False`` on the per-
            # item walk so disk renders as ``—`` (unknown) rather than
            # the misleading ``0 B``. CPU + memory go through the
            # separate stats fetch which doesn't depend on size data.
            if tcid not in svc_by_cid:
                svc_by_cid[tcid] = tsid
                running_cids.append(tcid)
                tasks_added += 1
                # Mark this cid as TASK-DERIVED (i.e. only visible
                # via the Swarm tasks API, not the per-node container
                # sweep). The agent-health detection below counts
                # ONLY these toward "worker agent unhealthy" because
                # they're the ONLY cids whose /stats call genuinely
                # requires the worker's Portainer agent to respond.
                # Manager-aggregated cids (in the per-node sweep)
                # might respond via Swarm visibility on the manager
                # even when the worker's agent is dead — counting
                # them would reset the failure tally and suppress
                # the banner.
                _task_derived_cids.add(tcid)
            elif tsid and not svc_by_cid.get(tcid):
                svc_by_cid[tcid] = tsid
            # Task-derived node beats every other source — the
            # scheduler authored the assignment.
            if tnode:
                node_by_cid[tcid] = tnode
        if tasks_added:
            print(f"[stats] gather_stats: tasks added {tasks_added} cid(s) "
                  f"not present in container list (worker-node visibility gap)")

        sem = asyncio.Semaphore(portainer.stats_concurrency())
        # Brute-force fallback list — every Swarm hostname OmniGrid
        # knows about. Passed to `_one_container_stats` as the last-
        # ditch retry pool when both the resolved-node agent_target
        # and untargeted call fail. Built once and reused across
        # every fetch so we don't pay the dict.values() cost per cid.
        all_hostnames: list[str] = list(hostnames)

        async def fetch(fetch_cid: str):
            """Semaphore-bounded wrapper around `_one_container_stats` for one container id."""
            async with sem:
                return fetch_cid, await _one_container_stats(
                    client, ep, fetch_cid, node_by_cid.get(fetch_cid),
                    fallback_nodes=all_hostnames,
                )

        results = await asyncio.gather(*(fetch(cid) for cid in running_cids))
        stats_by_cid: dict[str, dict[str, Any]] = {cid: s for cid, s in results if s}

        # Per-Swarm-node agent-health bookkeeping. After every gather,
        # tally per-host stats success vs total TASK-DERIVED cids
        # (cids that are only visible via the Swarm tasks API, NOT
        # the per-node container sweep). Counting only task-derived
        # cids is critical: a worker node typically has BOTH manager-
        # aggregated cids (visible to the manager via Swarm
        # visibility, /stats responds via the manager) AND task-only
        # cids (the worker's actual containers — /stats requires the
        # worker's agent). The manager-aggregated cids respond fine
        # even when the worker's agent is dead, so counting them
        # would reset the failure tally and suppress the banner. A
        # node is "tried this gather" if it has ≥1 task-derived cid;
        # "passing" if at least one of those task-derived cids
        # returned stats; "failing" if every task-derived cid
        # returned None. Failing nodes increment the consecutive-
        # failure counter; passing nodes reset to 0. Once the counter
        # crosses `tuning_swarm_agent_unhealthy_threshold`, the SPA
        # banner fires. Common cause: Swarm manager bounced, Portainer
        # agents on workers didn't re-register cleanly.
        per_node_total: dict[str, int] = {}
        per_node_passed: dict[str, int] = {}
        for cid in running_cids:
            if cid not in _task_derived_cids:
                continue
            n = node_by_cid.get(cid)
            if not n:
                continue
            per_node_total[n] = per_node_total.get(n, 0) + 1
            if cid in stats_by_cid:
                per_node_passed[n] = per_node_passed.get(n, 0) + 1
        now_ts = time.time()
        # Diagnostic log line — operator-requested visibility into why
        # the agent-unhealthy banner does or doesn't fire after an
        # agent recovery. Surfaces the per-host task-derived tally
        # this gather + which hosts are currently in `_agent_health`.
        # Empty per_node_total + non-empty `_agent_health` would
        # indicate a stale entry that should be popped this tick.
        if _agent_health or per_node_total:
            current_health = ",".join(
                f"{h}:{(_agent_health[h] or {}).get('fails', 0)}"
                for h in _agent_health.keys()
            ) or "<none>"
            tally = ",".join(
                f"{h}:{p}/{t}"
                for h, t in per_node_total.items()
                for p in (per_node_passed.get(h, 0),)
            ) or "<none>"
            print(f"[stats] agent_health: tally task_cids/passed={tally} current={current_health}")
        for host in list(_agent_health.keys()):
            # Stale entry — host no longer in this gather's set; let
            # it age out so a removed node doesn't pin the banner.
            if host not in per_node_total:
                popped = _agent_health.pop(host, None)
                if popped:
                    print(f"[stats] agent_health: cleared {host} (no task-derived cids this gather; "
                          f"prior fails={popped.get('fails', 0)})")
        for host, total in per_node_total.items():
            passed = per_node_passed.get(host, 0)
            if passed > 0:
                # Any single success resets the counter — agent is alive.
                popped = _agent_health.pop(host, None)
                if popped:
                    print(f"[stats] agent_health: cleared {host} (recovered, {passed}/{total} cids "
                          f"passed; prior fails={popped.get('fails', 0)})")
            else:
                cur = _agent_health.get(host) or {
                    "fails": 0, "since_ts": now_ts, "task_cids": total,
                }
                cur["fails"] = cur.get("fails", 0) + 1
                cur["task_cids"] = total
                # since_ts is set on the FIRST bad gather, kept stable
                # so the SPA can show "broken for X minutes".
                cur.setdefault("since_ts", now_ts)
                _agent_health[host] = cur

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
        with_size = sum(1 for v in out.values() if v.get("has_size"))
        print(f"[stats] gather_stats wrote: items={len(out)} has_stats_true={with_stats} has_size_true={with_size}")
        # SSE — hint-only event. Stats payload is small but the SPA
        # already has /api/stats wired with TTL-aware caching, so
        # fire-and-forget with item count + ts is enough; the live
        # client refreshes via the existing endpoint.
        try:
            from logic import events as _events
            _events.publish("stats:refreshed", {
                "items": len(out),
                "with_stats": with_stats,
                "with_size": with_size,
                "ts": _stats_cache["ts"],
            })
        except Exception as e:
            print(f"[events] gather_stats publish failed: {e}")
