"""Data aggregation — the fleet snapshot.

Owns ``_cache``, the single source of truth for "what OmniGrid saw
on its last poll". Other logic modules read via :func:`get_cache` and
mutate via :func:`invalidate_cache` so nobody else has to import the
dict directly (easier to change the storage later if needed).

``_gather()`` fans out five parallel Portainer reads, builds items and
stack groups, and enriches each item's registry digest concurrently. The
dense logic lives here intentionally — this is the one function whose
correctness matters most, and splitting it across modules would just
add import gymnastics without reducing real complexity.
"""
import asyncio
import json
import time
from typing import Optional

import httpx

from logic import metrics, portainer, registry
from logic.db import db_conn


# Module-level cache. Keys:
#   items             — list of item dicts (services + orphans + standalones)
#   stacks            — list of stack groups, sorted alphabetically
#   nodes             — {NodeID: hostname}
#   task_node_by_id   — {TaskID: hostname}, used by ops handlers to target
#                       the right Swarm node's daemon via X-PortainerAgent-Target
#   container_node_by_id — {ContainerID: hostname} for PLAIN compose
#                          containers discovered via the per-node sweep;
#                          stats polling consults this to target the right
#                          worker for its /containers/{id}/stats call.
#   ts                — epoch seconds of last successful gather (0 when stale)
_cache: dict = {
    "items": [],
    "ts": 0.0,
    "nodes": {},
    "nodes_info": {},
    "stacks": [],
    "task_node_by_id": {},
    "container_node_by_id": {},
}


def get_cache() -> dict:
    """Return the live cache dict. Callers may read fields but should
    treat it as read-only — use :func:`invalidate_cache` to force a
    refresh on the next gather tick.
    """
    return _cache


def invalidate_cache() -> None:
    """Mark the cache stale so the next gather request rebuilds it."""
    _cache["ts"] = 0


def _parse_docker_ts(ts) -> Optional[float]:
    """Parse a Docker API timestamp (ISO 8601 with nanos, e.g.
    '2026-04-22T13:40:16.123456789Z') to epoch seconds.

    Python's fromisoformat chokes on the nanosecond precision before 3.11,
    and on the trailing 'Z' before 3.11 too, so we trim both defensively.
    Returns None on anything unparseable.
    """
    if not ts:
        return None
    if isinstance(ts, (int, float)):
        return float(ts)
    if not isinstance(ts, str):
        return None
    # Strip trailing Z (UTC), truncate fractional seconds to microseconds.
    s = ts.rstrip("Z")
    if "." in s:
        head, frac = s.split(".", 1)
        frac = frac[:6]  # microseconds max
        s = f"{head}.{frac}"
    try:
        from datetime import datetime, timezone
        # Parse as naive then attach UTC — Docker always emits UTC.
        return datetime.fromisoformat(s).replace(tzinfo=timezone.utc).timestamp()
    except (ValueError, TypeError):
        return None


def _node_attr(node: dict, key: str):
    """Resolve a Swarm placement-constraint attribute against a raw node dict."""
    spec = node.get("Spec") or {}
    desc = node.get("Description") or {}
    if key == "node.id":
        return node.get("ID")
    if key == "node.role":
        return spec.get("Role")
    if key == "node.hostname":
        return desc.get("Hostname")
    if key == "node.platform.os":
        return (desc.get("Platform") or {}).get("OS")
    if key == "node.platform.arch":
        return (desc.get("Platform") or {}).get("Architecture")
    if key.startswith("node.labels."):
        return (spec.get("Labels") or {}).get(key[len("node.labels."):])
    if key.startswith("engine.labels."):
        return ((desc.get("Engine") or {}).get("Labels") or {}).get(key[len("engine.labels."):])
    return None


def _node_matches(node: dict, constraints: list[str]) -> bool:
    """Return True if the node satisfies every Swarm placement constraint."""
    for c in constraints or []:
        op = None
        for candidate in ("==", "!="):
            if candidate in c:
                op = candidate
                break
        if not op:
            continue  # unrecognised — don't filter it out
        left, right = c.split(op, 1)
        actual = _node_attr(node, left.strip())
        equal = (str(actual) == right.strip())
        if op == "==" and not equal:
            return False
        if op == "!=" and equal:
            return False
    return True


async def gather() -> None:
    """Rebuild the cache. Timed; errors inside _gather_impl surface but
    don't stop the metrics population step from running."""
    _t0 = time.monotonic()
    try:
        await _gather_impl()
    finally:
        metrics.GATHER_DURATION.observe(time.monotonic() - _t0)
        metrics.populate_from_cache(_cache)


async def _gather_impl() -> None:
    # Short-circuit on empty Portainer config — brand-new deploys where the
    # admin hasn't set URL + API key yet. Produces an empty snapshot
    # instead of a pile of connection errors; the UI renders its "go to
    # Settings → Portainer" banner off the empty items list.
    if not portainer.is_configured():
        _cache["items"] = []
        _cache["stacks"] = []
        _cache["nodes"] = {}
        _cache["nodes_info"] = {}
        _cache["task_node_by_id"] = {}
        _cache["ts"] = time.time()
        return
    async with httpx.AsyncClient(verify=portainer.VERIFY_TLS, timeout=60.0) as client:
        ep = f"/api/endpoints/{portainer.PORTAINER_ENDPOINT_ID}/docker"

        async def safe(coro, fb):
            try:
                return await coro
            except Exception as e:
                print(f"[gather] {e}")
                return fb

        services = await safe(portainer.pg(client, f"{ep}/services"), [])
        containers = await safe(portainer.pg(client, f"{ep}/containers/json?all=1"), [])
        tasks = await safe(portainer.pg(client, f"{ep}/tasks"), [])
        nodes = await safe(portainer.pg(client, f"{ep}/nodes"), [])
        stacks_list = await safe(portainer.pg(client, "/api/stacks"), [])

        node_map = {n["ID"]: n["Description"]["Hostname"] for n in nodes}
        stack_by_name = {s["Name"]: s for s in stacks_list}

        # Per-node capacity + oldest-running-task timestamp. Keyed by
        # hostname so the frontend doesn't need to join NodeID → host
        # separately. Structure shipped in _cache["nodes_info"]:
        #   {
        #     hostname: {
        #       id:            node UUID
        #       role:          "manager" | "worker"
        #       state:         "ready" | "down" | ... (Swarm Status.State)
        #       availability:  "active" | "pause" | "drain"
        #       cpu_cores:     int  (Description.Resources.NanoCPUs / 1e9)
        #       mem_bytes:     int  (Description.Resources.MemoryBytes)
        #       os:            e.g. "linux"
        #       arch:          e.g. "x86_64"
        #       engine:        docker engine version
        #       oldest_running_ts: epoch seconds of the oldest task whose
        #                          Status.State='running' on this node —
        #                          serves as a per-node uptime proxy
        #                          (Docker doesn't expose host boot time).
        #     }
        #   }
        nodes_info: dict[str, dict] = {}
        for n in nodes:
            desc = n.get("Description") or {}
            spec = n.get("Spec") or {}
            status = n.get("Status") or {}
            res = desc.get("Resources") or {}
            plat = desc.get("Platform") or {}
            host = desc.get("Hostname")
            if not host:
                continue
            nanocpus = int(res.get("NanoCPUs") or 0)
            # Swarm's advertised IP for this node — stable in a homelab
            # and dodges DNS entirely when used as the exporter target.
            # Managers expose it at ManagerStatus.Addr (with :2377);
            # workers expose it at Status.Addr. Strip any port suffix.
            raw_addr = (status.get("Addr")
                        or ((n.get("ManagerStatus") or {}).get("Addr") or ""))
            ip_only = str(raw_addr).split(":", 1)[0].strip()
            nodes_info[host] = {
                "id":           n.get("ID"),
                "role":         spec.get("Role"),
                "state":        status.get("State"),
                "availability": spec.get("Availability"),
                # NanoCPUs is in billionths of a core. Round to the nearest
                # whole core — these values are always clean multiples in
                # practice (Docker reports them straight from the kernel).
                "cpu_cores":    nanocpus // 1_000_000_000 if nanocpus else 0,
                "nano_cpus":    nanocpus,
                "mem_bytes":    int(res.get("MemoryBytes") or 0),
                "os":           plat.get("OS"),
                "arch":         plat.get("Architecture"),
                "engine":       ((desc.get("Engine") or {}).get("EngineVersion")),
                "ip":           ip_only or None,
                "oldest_running_ts": None,  # filled in by the tasks pass below
            }

        tasks_by_service: dict[str, list] = {}
        # task.ID → hostname — used later to pin orphan Swarm task containers
        # to their actual worker node. Without this, `/api/containers/{id}`
        # routes to the manager's Docker daemon and 404s for containers that
        # live on a worker. Sending `X-PortainerAgent-Target: <node>` fixes it.
        task_node_by_id: dict[str, str] = {}
        # Per-node "oldest running task" tracker — for each hostname, keep
        # the earliest Status.Timestamp of any running task on that node.
        # Beats the client-side "min of item.created" approach because a
        # global service's item.created is the same on every node it's on,
        # which made all nodes show an identical uptime.
        oldest_running_by_node: dict[str, float] = {}
        for t in tasks:
            sid = t.get("ServiceID")
            if sid:
                tasks_by_service.setdefault(sid, []).append(t)
            tid = t.get("ID")
            nid = t.get("NodeID")
            if tid and nid and nid in node_map:
                task_node_by_id[tid] = node_map[nid]
            # Oldest-running-task tracking — only RUNNING tasks count
            # (pending/failed/shutdown don't say anything about uptime).
            st = t.get("Status") or {}
            if nid in node_map and st.get("State") == "running":
                ts_raw = st.get("Timestamp") or t.get("CreatedAt")
                ts = _parse_docker_ts(ts_raw)
                if ts:
                    host = node_map[nid]
                    prev = oldest_running_by_node.get(host)
                    if prev is None or ts < prev:
                        oldest_running_by_node[host] = ts

        # Back-fill nodes_info with the timestamps we just computed.
        for host, ts in oldest_running_by_node.items():
            if host in nodes_info:
                nodes_info[host]["oldest_running_ts"] = ts

        # Per-node Docker disk footprint via /system/df, routed to each
        # node's daemon with X-PortainerAgent-Target. Totals span images
        # (deduplicated layers), containers' writable layers, volumes,
        # and build cache — i.e. ALL the disk Docker is using on that
        # host. Still Docker-only: reading the VM's /proc/mounts or df
        # for non-Docker mounts would require a node-agent.
        #
        # Errors per-node are swallowed — a 500 on one daemon shouldn't
        # blank the whole Nodes view. Missing nodes keep docker_disk_bytes=0.
        async def _one_df(host: str):
            try:
                r = await client.get(
                    f"{portainer.PORTAINER_URL}{ep}/system/df",
                    headers=portainer.headers(agent_target=host),
                )
                if r.status_code >= 400:
                    return host, 0
                j = r.json() or {}
                total = int(j.get("LayersSize") or 0)
                for c in (j.get("Containers") or []):
                    total += int(c.get("SizeRw") or 0)
                for v in (j.get("Volumes") or []):
                    usage = (v.get("UsageData") or {}).get("Size", 0)
                    if isinstance(usage, (int, float)) and usage > 0:
                        total += int(usage)
                for bc in (j.get("BuildCache") or []):
                    total += int(bc.get("Size") or 0)
                return host, total
            except Exception as e:
                print(f"[gather] /system/df for {host}: {e}")
                return host, 0

        df_hosts = [h for h, info in nodes_info.items()
                    if info.get("state") == "ready"]
        if df_hosts:
            df_results = await asyncio.gather(
                *(_one_df(h) for h in df_hosts), return_exceptions=False,
            )
            for host, total in df_results:
                if host in nodes_info:
                    nodes_info[host]["docker_disk_bytes"] = total

        # Host-stats integration — surfaces real host disk / memory /
        # uptime that Portainer doesn't expose. ``host_stats_source`` is
        # a CSV so operators can enable multiple providers that merge
        # into one picture per host:
        #   ""                                → none
        #   "beszel" / "node_exporter" / "pulse" → single source
        #   "beszel,pulse,node_exporter"      → merged, best-of
        # Merge order runs providers in increasing "authority" for
        # their specialty:
        #   1. Beszel          (broad coverage, cross-platform)
        #   2. Pulse           (deep on PVE, silent on non-PVE)
        #   3. node-exporter   (deep on Linux — per-mount disks, NICs)
        # The ``_merge_best`` helper (below) only overwrites when the
        # new source has a meaningful value, so enabling Pulse on a
        # mixed fleet doesn't wipe Beszel's cpu/mem reading on hosts
        # Pulse doesn't know about. Legacy single-value strings stay
        # valid.
        def _meaningful(v) -> bool:
            if v is None:
                return False
            if isinstance(v, (int, float)):
                return v != 0
            if isinstance(v, str):
                return v.strip() != ""
            if isinstance(v, (list, dict)):
                return len(v) > 0
            return True

        def _merge_best(dst: dict, src: dict) -> None:
            """Copy meaningful values from src into dst, leaving dst's
            existing non-zero fields intact when src is empty/zero."""
            if not src:
                return
            for k, v in src.items():
                if _meaningful(v):
                    dst[k] = v
                elif k not in dst:
                    dst[k] = v  # seed zero only if nothing there yet
        from logic.db import get_setting
        from logic import beszel as _beszel
        from logic import node_exporter as _ne
        raw_source = (get_setting("host_stats_source", "") or "").strip()
        if not raw_source:
            # Legacy bootstrap: only the node_exporter_enabled bool existed.
            raw_source = ("node_exporter"
                          if (get_setting("node_exporter_enabled", "false") or "false").lower() == "true"
                          else "none")
        active_sources = {
            s.strip().lower()
            for s in raw_source.split(",")
            if s.strip() and s.strip().lower() != "none"
        }

        if "beszel" in active_sources and df_hosts:
            # One HTTP call to the hub fetches every system's latest
            # snapshot. Docker hostname → Beszel ``host`` field via
            # ``beszel_aliases`` (JSON map in the settings table) so
            # operators don't have to rename a host on either side when
            # the two naturally differ (e.g. Swarm hostname
            # ``debian13docker`` but Beszel host ``docker.home.lan``).
            # Nodes absent from the alias map fall back to identity.
            # NOTE: we match against Beszel's ``host`` (agent hostname),
            # not ``name`` (user-editable label), because ``host`` is
            # stable and typically matches what Docker reports.
            import json as _json
            hub_url = get_setting("beszel_hub_url", "") or ""
            ident = get_setting("beszel_identity", "") or ""
            passw = get_setting("beszel_password", "") or ""
            verify = (get_setting("beszel_verify_tls", "true") or "true").lower() == "true"
            try:
                aliases = _json.loads(get_setting("beszel_aliases", "{}") or "{}")
                if not isinstance(aliases, dict):
                    aliases = {}
            except ValueError:
                aliases = {}
            result = await _beszel.probe_hub(hub_url, ident, passw, verify_tls=verify)
            err = result.get("error")
            systems = result.get("systems") or {}
            for host in df_hosts:
                if host in nodes_info:
                    if err:
                        nodes_info[host]["exporter_error"] = f"beszel: {err}"
                        continue
                    beszel_name = aliases.get(host, host)
                    stats = systems.get(beszel_name)
                    if stats is None:
                        # No matching Beszel system — surface the miss
                        # with both names in the error so the operator
                        # knows whether to add an alias or rename in
                        # Beszel.
                        hint = (
                            f"'{beszel_name}' (aliased from '{host}')"
                            if beszel_name != host else f"'{host}'"
                        )
                        nodes_info[host]["exporter_error"] = (
                            f"beszel: no system named {hint} in the hub"
                        )
                        continue
                    _merge_best(nodes_info[host], stats)

        # Pulse (rcourtman/Pulse) — Proxmox VE monitoring. Runs BETWEEN
        # Beszel and node-exporter: overwrites Beszel for PVE hosts
        # where Pulse has the authoritative view (cpu / mem / disk /
        # uptime from the hypervisor itself), but node-exporter still
        # wins if both are enabled.
        if "pulse" in active_sources and df_hosts:
            import json as _json
            from logic import pulse as _pulse
            pulse_url = get_setting("pulse_url", "") or ""
            pulse_token = get_setting("pulse_token", "") or ""
            pulse_verify = (get_setting("pulse_verify_tls", "true")
                            or "true").lower() == "true"
            try:
                pulse_aliases_raw = _json.loads(
                    get_setting("pulse_aliases", "{}") or "{}")
                if not isinstance(pulse_aliases_raw, dict):
                    pulse_aliases_raw = {}
            except ValueError:
                pulse_aliases_raw = {}
            pulse_res = await _pulse.probe_pulse(
                pulse_url, pulse_token, verify_tls=pulse_verify,
            )
            p_err = pulse_res.get("error")
            p_hosts = pulse_res.get("hosts") or {}
            for host in df_hosts:
                if host not in nodes_info:
                    continue
                if p_err:
                    # Only surface the pulse error if nothing else
                    # populated host_* fields — keeps the pill honest
                    # when one provider is flaky but another succeeded.
                    if not nodes_info[host].get("host_mem_total"):
                        nodes_info[host]["exporter_error"] = f"pulse: {p_err}"
                    continue
                pulse_name = pulse_aliases_raw.get(host, host)
                stats = _pulse.lookup(p_hosts, pulse_name)
                if stats is None:
                    continue  # not a PVE node — legit miss, no error
                _merge_best(nodes_info[host], stats)

        # node-exporter runs AFTER beszel + pulse when enabled, so its
        # richer Linux-native fields (per-mount disks via node_filesystem_*,
        # NIC list via node_network_info, detailed kernel / arch from
        # node_uname_info) overwrite the earlier providers where they
        # overlap. Fields only provided by Beszel/Pulse (e.g. their
        # status strings) are preserved by the dict.update.
        if "node_exporter" in active_sources and df_hosts:
            tpl = get_setting("node_exporter_url_template", "http://{host}:9100/metrics") \
                  or "http://{host}:9100/metrics"
            # Per-host URL overrides for nodes where the template's {host}
            # substitution can't reach the exporter (DNS, alternate IP,
            # different port, etc.). Operator edits this JSON via the
            # Host stats settings panel.
            overrides_raw = get_setting("node_exporter_overrides", "{}") or "{}"
            try:
                overrides = json.loads(overrides_raw)
                if not isinstance(overrides, dict):
                    overrides = {}
            except Exception:
                overrides = {}
            async with httpx.AsyncClient(verify=False, timeout=10.0) as ne_client:
                async def _ne_probe(h):
                    # Override wins over template. The template supports
                    # both {host} (Docker hostname) and {ip} (Swarm-
                    # advertised IP) — pick whichever works in your
                    # network. Mixed strings like
                    # "http://{host}.home.lan:9100/metrics" are fine.
                    info = nodes_info.get(h) or {}
                    ip = info.get("ip") or ""
                    url = overrides.get(h) or (
                        tpl.replace("{host}", h).replace("{ip}", ip)
                    )
                    return h, await _ne.probe_node(ne_client, url)
                results = await asyncio.gather(
                    *(_ne_probe(h) for h in df_hosts),
                    return_exceptions=False,
                )
                for host, stats in results:
                    if host in nodes_info:
                        _merge_best(nodes_info[host], stats)

        # Per-node container sweep — gives us a containerID → hostname map
        # that covers PLAIN compose containers on worker nodes too. The
        # Swarm-task-ID approach above only works for Swarm-managed
        # containers; anything deployed with `docker compose up` on a
        # worker has no task ID and shows up as "local" without this.
        #
        # When the Portainer endpoint is in AGENT mode, targeting each node
        # returns only that node's containers — disjoint sets, so we can
        # build a definitive ID → node map. When the endpoint is NOT in
        # agent mode (plain standalone Docker), every per-node call is
        # routed to the same daemon and the lists are identical; we detect
        # that and skip the map so we don't mislabel everything.
        hostnames = [h for h in node_map.values() if h]
        container_node_by_id: dict[str, str] = {}
        if len(hostnames) >= 2:
            per_node = await asyncio.gather(*(
                safe(portainer.pg(client, f"{ep}/containers/json?all=1", agent_target=h), [])
                for h in hostnames
            ))
            id_sets = [{c["Id"] for c in lst} for lst in per_node]
            sizes = [len(s) for s in id_sets]
            # If Portainer is NOT honouring the agent-target header, every
            # per-node call returns the same set and sizes are identical.
            # If sizes differ, the header IS being routed per node.
            some_differ = len(set(sizes)) > 1
            # Some containers (Swarm global services, Portainer's own
            # agent) intentionally run on every node with different
            # container IDs. But some container IDs end up in multiple
            # per-node responses because of Portainer's routing quirks
            # — when that happens, we can't say which node owns the ID,
            # so we leave it out of the map and let the stats fallback
            # (targeted-then-untargeted) do its job. Only containers
            # that appear in EXACTLY ONE per-node response get pinned.
            from collections import Counter as _C
            appearances = _C()
            for s in id_sets:
                appearances.update(s)
            pinned = 0
            ambiguous = 0
            for h, s in zip(hostnames, id_sets):
                for cid in s:
                    if appearances[cid] == 1:
                        container_node_by_id[cid] = h
                        pinned += 1
                    else:
                        ambiguous += 1
            # `ambiguous` counts duplicated IDs across all their
            # appearances, so divide by 2+ to get the actual container
            # count. Printed as-is for easy eyeballing in logs.
            print(f"[gather] per-node sweep: hostnames={hostnames} "
                  f"sizes={sizes} agent_routing={some_differ} "
                  f"pinned={pinned} ambiguous_refs={ambiguous}")
            if not some_differ:
                # Header being ignored for every call — no signal.
                container_node_by_id.clear()

        # Resolve-by-probe. For containers the sweep left ambiguous AND
        # that have NO Swarm node-id label (plain compose containers
        # are our biggest consumer here), hit /containers/{cid}/json
        # with each hostname as the agent target. First 200 = true
        # node, because Portainer's per-container inspect is per-node
        # even when its list-aggregation is lenient. Happens once per
        # gather and only for containers not already pinned — bounded.
        unresolved_ids = []
        for c in containers:
            cid = c["Id"]
            if cid in container_node_by_id:
                continue
            if (c.get("Labels") or {}).get("com.docker.swarm.node.id"):
                # Will be resolved via the Swarm-node-id label downstream
                # in the item walk — no probe needed.
                continue
            unresolved_ids.append(cid)

        if unresolved_ids and len(hostnames) >= 2:
            async def _probe_one(cid: str) -> tuple[str, Optional[str]]:
                # Try each hostname in turn. Use a short timeout — a
                # 404 should come back fast. First 200 wins.
                for h in hostnames:
                    try:
                        r = await client.get(
                            f"{portainer.PORTAINER_URL}{ep}/containers/{cid}/json",
                            headers=portainer.headers(agent_target=h),
                            timeout=3.0,
                        )
                        if r.status_code == 200:
                            return cid, h
                    except Exception:
                        continue
                return cid, None

            sem = asyncio.Semaphore(portainer.STATS_CONCURRENCY)

            async def _probe_bounded(cid: str):
                async with sem:
                    return await _probe_one(cid)

            probe_results = await asyncio.gather(*(_probe_bounded(cid) for cid in unresolved_ids))
            probed_hits = 0
            for cid, h in probe_results:
                if h:
                    container_node_by_id[cid] = h
                    probed_hits += 1
            print(f"[gather] resolve-by-probe: tried={len(unresolved_ids)} "
                  f"resolved={probed_hits}")

        # Fallback: if per-node routing didn't fire (all sizes identical or
        # only one node) but Portainer's aggregated response carries a
        # node hint on each container, scrape that. Shapes vary across
        # Portainer versions — probe every known location.
        if not container_node_by_id:
            probed_keys: set[str] = set()
            for c in containers:
                labels = c.get("Labels") or {}
                candidate = (
                    labels.get("com.portainer.agent.node")
                    or labels.get("com.portainer.agent.target")
                    or labels.get("io.portainer.agent.target")
                )
                if not candidate:
                    pa = c.get("Portainer") or {}
                    ag = (pa.get("Agent") or {}) if isinstance(pa, dict) else {}
                    candidate = ag.get("Target") if isinstance(ag, dict) else None
                if candidate:
                    container_node_by_id[c["Id"]] = candidate
                probed_keys.update(k for k in labels.keys() if "portainer" in k.lower())
            if probed_keys:
                print(f"[gather] portainer-ish container labels seen: "
                      f"{sorted(probed_keys)[:8]}")

        # Build service-id → running containers map. Swarm stamps every task
        # container with `com.docker.swarm.service.id`, so we can go from service
        # → container → image → RepoDigests when neither the service spec nor the
        # task spec carries a digest pin.
        containers_by_service: dict[str, list] = {}
        for c in containers:
            sid = (c.get("Labels") or {}).get("com.docker.swarm.service.id")
            if sid:
                containers_by_service.setdefault(sid, []).append(c)

        # Cache image-inspect results within this gather so services sharing an
        # image don't trigger N image-inspect calls.
        image_digest_cache: dict[str, Optional[str]] = {}

        async def _digest_for_image_id(image_id: str) -> Optional[str]:
            if not image_id:
                return None
            if image_id in image_digest_cache:
                return image_digest_cache[image_id]
            try:
                img = await portainer.pg(client, f"{ep}/images/{image_id}/json")
                for rd in img.get("RepoDigests") or []:
                    if "@" in rd:
                        digest = rd.split("@", 1)[1]
                        image_digest_cache[image_id] = digest
                        return digest
            except Exception as e:
                print(f"[digest-fallback] {image_id[:12]}: {e}")
            image_digest_cache[image_id] = None
            return None

        with db_conn() as c:
            ignores = [dict(r) for r in c.execute("SELECT * FROM ignores").fetchall()]

        def is_ignored(image, stack):
            for ig in ignores:
                p = ig["pattern"]
                if ig["kind"] == "image" and p and p in (image or ""):
                    return True
                if ig["kind"] == "stack" and p and p == (stack or ""):
                    return True
            return False

        items: list[dict] = []

        # --- Swarm services ---
        for svc in services:
            spec = svc.get("Spec", {}) or {}
            cs = (spec.get("TaskTemplate") or {}).get("ContainerSpec") or {}
            full_image = cs.get("Image", "") or ""
            image_name_tag = full_image.split("@", 1)[0] if "@" in full_image else full_image
            current_digest = full_image.split("@", 1)[1] if "@" in full_image else None
            labels = spec.get("Labels") or {}
            stack_name = labels.get("com.docker.stack.namespace")
            stack = stack_by_name.get(stack_name) if stack_name else None

            svc_tasks = tasks_by_service.get(svc["ID"], [])
            # If the service-level spec isn't digest-pinned (common when the image
            # failed to resolve at deploy time), fall back to a task-level digest.
            # Swarm stamps each dispatched task's ContainerSpec.Image with the digest
            # it actually scheduled, so a running task is authoritative for "what's
            # deployed right now."
            if not current_digest:
                for t in svc_tasks:
                    t_img = ((t.get("Spec") or {}).get("ContainerSpec") or {}).get("Image", "") or ""
                    if "@" in t_img:
                        # Prefer a running task, else take the first digest we see.
                        if (t.get("Status") or {}).get("State") == "running":
                            current_digest = t_img.split("@", 1)[1]
                            break
                        if not current_digest:
                            current_digest = t_img.split("@", 1)[1]
            if not current_digest:
                # Final fallback: inspect the running container for this service on
                # any node. The container's image ID (sha256:...) maps to the image's
                # RepoDigests, which gives us the actual `@sha256:...` that this
                # service is currently executing. This covers services deployed
                # with an unpinned tag that Swarm never resolved.
                svc_containers = containers_by_service.get(svc["ID"], [])
                for c in svc_containers:
                    if (c.get("State") or "").lower() == "running":
                        current_digest = await _digest_for_image_id(c.get("ImageID") or c.get("Image"))
                        if current_digest:
                            break
                if not current_digest:
                    # Even a stopped/crashlooping container's image tells us what
                    # the service last tried to run.
                    for c in svc_containers:
                        current_digest = await _digest_for_image_id(c.get("ImageID") or c.get("Image"))
                        if current_digest:
                            break
            running = sum(
                1 for t in svc_tasks
                if (t.get("Status") or {}).get("State") == "running"
                and t.get("DesiredState") == "running"
            )
            mode = spec.get("Mode", {}) or {}
            if "Replicated" in mode:
                desired = mode["Replicated"].get("Replicas", 1)
            elif "Global" in mode:
                # Only count nodes that actually satisfy the service's placement
                # constraints, so a manager-pinned global service isn't flagged as
                # degraded just because worker nodes exist.
                placement = ((spec.get("TaskTemplate") or {}).get("Placement") or {})
                constraints = placement.get("Constraints") or []
                eligible = [n for n in nodes if _node_matches(n, constraints)]
                desired = len(eligible) or 1
            else:
                desired = 1
            placements = []
            for t in svc_tasks:
                if t.get("DesiredState") == "shutdown":
                    continue
                st = t.get("Status") or {}
                placements.append({
                    "node": node_map.get(t.get("NodeID"), "?"),
                    "state": st.get("State"),
                    "err": st.get("Err"),
                })

            if desired == 0:
                health = "offline"
            elif running == 0:
                health = "offline"
            elif running < desired:
                health = "degraded"
            else:
                health = "healthy"

            items.append({
                "id": f"svc:{svc['ID'][:12]}",
                "raw_id": svc["ID"],
                "name": spec.get("Name", ""),
                "type": "service",
                "image": image_name_tag,
                "tag": registry.tag_of(image_name_tag),
                "current_digest": current_digest,
                "stack": stack_name,
                "stack_id": stack["Id"] if stack else None,
                "replicas": {"desired": desired, "running": running},
                "placements": placements,
                "health": health,
                "state": "running" if running > 0 else "stopped",
                "removable": False,
                "hub_link": registry.hub_link(image_name_tag),
                "ignored": is_ignored(image_name_tag, stack_name),
                "created": spec.get("CreatedAt") or svc.get("CreatedAt"),
                "updated": spec.get("UpdatedAt") or svc.get("UpdatedAt"),
            })

        # --- Standalone / compose (non-Swarm) containers + orphan Swarm task containers ---
        # We intentionally include Swarm task containers that are NOT currently
        # running (exited / dead). Swarm often leaves these behind after replacing
        # a task and they accumulate over time. Listing them here lets the user
        # bulk-remove the orphans. Running Swarm task containers are still skipped
        # because they're already represented via their parent service.
        for cont in containers:
            labels = cont.get("Labels") or {}
            state = (cont.get("State") or "").lower()
            is_swarm_task = bool(labels.get("com.docker.swarm.service.id"))
            if is_swarm_task and state == "running":
                continue
            image_ref = cont.get("Image", "") or ""
            # Orphan Swarm task containers report their image as
            # `repo:tag@sha256:...` — keep just the `repo:tag` for display so the
            # UI cell doesn't overflow. The digest goes into current_digest.
            if "@" in image_ref:
                head, _, digest_suffix = image_ref.partition("@")
                image_ref = head
                # If the container's Image field already carried a digest, use it
                # as a fallback for current_digest (the RepoDigests lookup below
                # is the primary source).
                if digest_suffix.startswith("sha256:"):
                    cont.setdefault("_pu_fallback_digest", digest_suffix)
            compose_project = (
                labels.get("com.docker.compose.project")
                or labels.get("com.docker.stack.namespace")
            )
            stack = stack_by_name.get(compose_project) if compose_project else None

            current_digest = None
            try:
                img = await portainer.pg(client, f"{ep}/images/{cont['ImageID']}/json")
                for rd in img.get("RepoDigests") or []:
                    if "@" in rd:
                        current_digest = rd.split("@", 1)[1]
                        break
                # Recover a real image name when Docker reports the Image field as a raw
                # sha256 digest (happens when the image was pulled by digest or later untagged)
                if image_ref.startswith("sha256:") or (image_ref and "/" not in image_ref and ":" not in image_ref):
                    real_tags = [t for t in (img.get("RepoTags") or []) if t and "<none>" not in t]
                    if real_tags:
                        image_ref = real_tags[0]
            except Exception:
                pass
            # Fallback digest from the Image field (e.g. orphan task containers
            # whose image was purged and image-inspect now 404s).
            if not current_digest and cont.get("_pu_fallback_digest"):
                current_digest = cont["_pu_fallback_digest"]

            name = (cont.get("Names") or ["?"])[0].lstrip("/")
            state = (cont.get("State") or "").lower()
            if state == "running":
                health = "healthy"
            elif state in ("restarting", "paused"):
                health = "degraded"
            else:
                health = "offline"

            # Resolve the real node. Priority order (authoritative first):
            #   1. `com.docker.swarm.node.id` label — Swarm stamps every
            #      managed container (services, global services, even
            #      orphan task containers whose tasks were shut down)
            #      with this. Authoritative; comes from the scheduler.
            #   2. Swarm task-ID → NodeID via task_node_by_id — covers
            #      the rare case where the container has the task-id
            #      label but not the node-id label (older Swarm versions).
            #   3. Per-node agent-targeted container sweep — only signal
            #      we have for plain compose containers on worker nodes.
            #      Not perfect (overlaps between per-node responses can
            #      mis-attribute a container) but self-heals via stats'
            #      untargeted fallback on failure.
            #   4. Fallback "local" — genuine single-node / non-agent
            #      setups where we can't tell.
            node_id_label = labels.get("com.docker.swarm.node.id")
            node_name = node_map.get(node_id_label) if node_id_label else None
            if not node_name:
                swarm_task_id = labels.get("com.docker.swarm.task.id")
                node_name = task_node_by_id.get(swarm_task_id) if swarm_task_id else None
            if not node_name:
                node_name = container_node_by_id.get(cont["Id"])
            if not node_name:
                node_name = "local"

            items.append({
                "id": f"ctn:{cont['Id'][:12]}",
                "raw_id": cont["Id"],
                "name": name,
                "type": "orphan" if is_swarm_task else "container",
                "image": image_ref,
                "tag": registry.tag_of(image_ref),
                "current_digest": current_digest,
                "stack": compose_project,
                "stack_id": stack["Id"] if stack else None,
                "replicas": {"desired": 1, "running": 1 if state == "running" else 0},
                "placements": [{"node": node_name, "state": state}],
                "node": node_name,
                "health": health,
                "state": state,
                "removable": health == "offline",
                "hub_link": registry.hub_link(image_ref),
                "ignored": is_ignored(image_ref, compose_project),
                "created": cont.get("Created"),
            })

        # --- Enrich with remote digests ---
        sem = asyncio.Semaphore(portainer.REGISTRY_CONCURRENCY)

        async def enrich(it):
            async with sem:
                remote = await registry.get_remote_digest(client, it["image"])
            it["remote_digest"] = remote
            if it["ignored"]:
                it["status"] = "ignored"
            elif not it["current_digest"]:
                it["status"] = "unknown"
            elif not remote:
                it["status"] = "error"
            elif it["current_digest"] == remote:
                it["status"] = "up-to-date"
            else:
                it["status"] = "update"
            return it

        items = list(await asyncio.gather(*(enrich(i) for i in items)))

        # Build stack-grouped view
        groups: dict[str, dict] = {}
        for it in items:
            key = it["stack"] or "__standalone__"
            groups.setdefault(key, {
                "name": it["stack"] or "Standalone",
                "stack_id": it["stack_id"],
                "items": [],
                "is_standalone": not it["stack"],
            })["items"].append(it)

        for g in groups.values():
            its = g["items"]
            its.sort(key=lambda i: (i.get("name") or "").lower())
            g["total"] = len(its)
            g["updates"] = sum(1 for i in its if i["status"] == "update")
            g["errors"] = sum(1 for i in its if i["status"] == "error")
            g["unknowns"] = sum(1 for i in its if i["status"] == "unknown")
            g["uptodate"] = sum(1 for i in its if i["status"] == "up-to-date")
            g["offline"] = sum(1 for i in its if i.get("health") == "offline")
            g["degraded"] = sum(1 for i in its if i.get("health") == "degraded")

        items.sort(key=lambda i: (i.get("name") or "").lower())
        _cache["items"] = items
        _cache["nodes"] = node_map
        _cache["nodes_info"] = nodes_info
        _cache["task_node_by_id"] = task_node_by_id
        _cache["container_node_by_id"] = container_node_by_id
        _cache["stacks"] = sorted(
            groups.values(),
            key=lambda s: (s["name"] or "").lower(),
        )
        _cache["ts"] = time.time()
