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
from typing import Any, Optional

import httpx

from logic import metrics, portainer, registry
from logic.coerce import safe_int
from logic.db import db_conn
from logic.settings_keys import Settings

# Canonical label-value sets for `omnigrid_items_total{status, type}`
# . Single source of truth for the label cartesian product so
# `metrics.populate_from_cache` can pre-seed every known combination
# at zero — Grafana queries against any specific combination always
# match a series. Adding a new value (e.g. `swarm-task` for raw task
# rows) is one edit here; the metrics pre-seeding picks it up
# automatically without churn at the consumer.
ITEM_STATUSES: tuple[str, ...] = (
    "up-to-date", "update", "error", "unknown", "ignored",
)
ITEM_TYPES: tuple[str, ...] = (
    "service", "container", "orphan",
)

# Module-level cache. Keys:
# items             — list of item dicts (services + orphans + standalones)
# stacks            — list of stack groups, sorted alphabetically
# nodes             — {NodeID: hostname}
# task_node_by_id   — {TaskID: hostname}, used by ops handlers to target
#                     the right Swarm node's daemon via X-PortainerAgent-Target
# container_node_by_id — {ContainerID: hostname} for PLAIN compose
#                        containers discovered via the per-node sweep;
#                        stats polling consults this to target the right
#                        worker for its /containers/{id}/stats call.
# ts                — epoch seconds of last successful gather (0 when stale)
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


def _load_hosts_config_for_gather() -> list[dict]:
    """Read the curated ``hosts_config`` setting as a list of dicts.

    Kept local to gather.py — the canonical loader lives in
    ``main._load_hosts_config`` but we deliberately don't import it
    (would create a main → logic → main cycle). Tolerant: blank /
    malformed settings return ``[]`` and node-level probes fall back
    to their existing host-string behaviour.
    """
    from logic.db import get_setting
    raw = get_setting(Settings.HOSTS_CONFIG) or ""
    if not raw.strip():
        return []
    try:
        parsed = json.loads(raw)
    except ValueError:
        return []
    return parsed if isinstance(parsed, list) else []


def _match_hosts_row(host: str, hosts_cfg: list[dict]) -> Optional[dict]:
    """Resolve a Swarm/Docker node name to a ``hosts_config`` row.

    Strategies in order of preference (first match wins):

        1. Exact match on ``id``.
        2. Short-hostname match: `host.split('.')[0] == id.split('.')[0]`
           — catches the "Docker reports bare hostname, operator
           configured FQDN" and vice-versa cases that produce            "3 sources error" symptom.
        3. Provider-name match: any of the row's provider fields
           (``beszel_name`` / ``pulse_name`` / ``webmin_name``) equals
           the Docker hostname short form. Useful when the Docker
           hostname differs from the operator's chosen `id` (e.g.
           `id="docker"`, `beszel_name="docker.example.com"`).

    Returns the matched row, or ``None`` when nothing matches.
    Callers decide whether to use the row's provider fields.
    """
    if not host or not isinstance(hosts_cfg, list):
        return None
    host_short = str(host).split(".", 1)[0].lower()
    host_low = str(host).lower()
    # Pass 1: exact id match.
    for h in hosts_cfg:
        if not isinstance(h, dict):
            continue
        if str(h.get("id") or "").lower() == host_low:
            return h
    # Pass 2: short-hostname match against id.
    for h in hosts_cfg:
        if not isinstance(h, dict):
            continue
        hid = str(h.get("id") or "").lower()
        if hid and hid.split(".", 1)[0] == host_short:
            return h
    # Pass 3: provider-name match (short form).
    for h in hosts_cfg:
        if not isinstance(h, dict):
            continue
        for key in ("beszel_name", "pulse_name", "webmin_name"):
            v = str(h.get(key) or "").lower()
            if v and v.split(".", 1)[0] == host_short:
                return h
    return None


def invalidate_cache(reason: Optional[str] = None) -> None:
    """Mark the cache stale so the next gather request rebuilds it.

    Optional ``reason`` is passed through to the SSE ``cache:invalidated``
    event payload so the SPA can log "post-op refresh" / "settings save"
    / etc. without sprinkling extra publish() calls at every caller.
    """
    _cache["ts"] = 0
    # SSE — tell live SPA clients the items dataset is stale. They
    # react with a /api/items?force=true refresh. Imported lazily to
    # keep this hot path independent of the events module's load
    # state (the ``logic`` package import order has gather pulled in
    # before events on first boot).
    try:
        from logic import events as _events
        _events.publish("cache:invalidated", {"reason": reason or ""})
    except Exception as e:  # noqa: BLE001
        print(f"[events] invalidate_cache publish failed: {e}")


# ---------------------------------------------------------------------
# Host snapshot persistence — last-known nodes_info[host] blob in DB.
#
# Goal: when a provider (Beszel / Pulse / node-exporter / Webmin) goes
# offline, OmniGrid keeps showing its previous values flagged as stale
# instead of silently dropping CPU / memory / disk bars to empty. Same
# idea as ``stats_samples`` but for host-level data.
#
# Wire-up:
# - End of every successful gather → ``save_host_snapshots(nodes_info)``
#   persists the merged blob (JSON column on a single row per host).
# - Inside ``_gather_impl``, AFTER providers run AND BEFORE we publish
#   to ``_cache`` → ``apply_host_snapshot_fallback`` fills missing
#   ``host_*`` fields from the persisted blob and tags the entry with
#   ``_stale_fields=[...]`` so the UI can dim those bars.
# - At lifespan startup → ``load_host_snapshots()`` seeds
#   ``_cache["nodes_info"]`` so the very first ``/api/items`` after a
#   restart has data while the live gather is still running.
# ---------------------------------------------------------------------
# Field families that are RUNTIME provider data (not Swarm-level
# inventory). Snapshot fallback only fills these — Swarm fields like
# `cpu_cores` / `mem_bytes` / `role` come from the Portainer node
# list every gather and don't need a fallback.
#
# previously a hand-
# maintained tuple that silently drifted from extract_stats every time
# a provider sprouted a new ``host_*`` field (root cause of — # load_*, swap, temperatures all blanked on snapshot fallback because
# they were missing from the whitelist). Replaced with a prefix +
# bare-exception predicate so any ``host_*`` field the providers emit
# gets snapshotted AND restored automatically. Bare-key exceptions
# stay as a small frozenset (mounts / interfaces / package_updates*)
# because those are emitted without the prefix for legacy reasons.
_BARE_SNAPSHOT_KEYS = frozenset({
    "mounts", "interfaces",
    "package_updates_count", "package_updates",
    # Printer state preserved across SNMP probe outages so the
    # Printer card keeps showing the last-known supplies / page count
    # / console message instead of disappearing when the device goes
    # offline. Same stale-marker treatment the chart cards use.
    "printer_supplies", "printer_page_count", "printer_console_msg",
    # Every provider (Beszel / Pulse / SNMP / NE / Webmin) emits
    # `network_ifaces`. Pre-fix it wasn't in the whitelist and
    # didn't auto-qualify (no `host_` prefix), so per-iface chip strip
    # + per-port heatmap blanked out on probe outage instead of
    # falling back to the snapshot with the stale-data warning.
    "network_ifaces",
})


def _is_snapshot_key(key) -> bool:
    """True when ``key`` is a runtime provider field that should be
    snapshotted + restored on provider outage. ``host_*`` prefix
    auto-qualifies; the small ``_BARE_SNAPSHOT_KEYS`` set covers the
    legacy un-prefixed exceptions emitted by node-exporter / providers.
    Underscore-prefixed keys (``_stale_fields`` / ``_stale_ts``) are
    bookkeeping and intentionally excluded.
    """
    if not isinstance(key, str) or not key:
        return False
    if key.startswith("_"):
        return False
    if key.startswith("host_"):
        return True
    return key in _BARE_SNAPSHOT_KEYS


# Legacy `_HOST_SNAPSHOT_KEYS` tuple removed. Callers should use
# `_is_snapshot_key(key)` directly. The hand-maintained tuple drifted
# stale every time a new `host_*` field shipped (gpus / battery_*  /
# load_percent / ups_status / temperatures / etc.) — `_is_snapshot_key`
# auto-qualifies via the `host_` prefix + `_BARE_SNAPSHOT_KEYS` so
# there's no drift to maintain.


def _is_urlish(v: Any) -> bool:
    """True when v looks like an http(s) URL string. Used to keep a
    stale iDRAC chassis URL from sticking in ``host_firmware`` across
    restarts — pre-fix the URL OID was mapped to host_firmware before
    the URL-detection routing landed, and the persisted value lingered
    in ``host_snapshots`` even after probes correctly route it to
    ``host_idrac_url``. Strip on both write AND read paths so the next
    successful gather scrubs the stale row idempotently.
    """
    if not isinstance(v, str):
        return False
    s = v.strip().lower()
    return s.startswith(("http://", "https://"))


def save_host_snapshots(nodes_info: dict) -> int:
    """Upsert one row per host into ``host_snapshots``.

    JSON-encodes the merged ``nodes_info[host]`` dict. Strips fields
    starting with ``_`` (the stale-marker bookkeeping) so a restart
    doesn't read its own marker noise back as canonical data.
    EXCEPTION: ``_meta_*`` keys ARE persisted — they're snapshot-
    internal metadata that must round-trip (currently just
    ``_meta_stale_since``, the per-field first-stale-ts map that
    drives the stale-grace pruning in
    ``apply_host_snapshot_fallback``). Without round-tripping it we
    can't tell "field has been stale for 25 hours" from "field just
    went stale this gather", and the grace window can't fire.
    Also strips URL-shaped values from ``host_firmware`` so a
    pre-fix stale URL leak gets cleaned up on the next gather.
    Returns the number of rows written.
    """
    if not nodes_info:
        return 0
    ts = time.time()
    rows = []
    for host, info in nodes_info.items():
        if not isinstance(info, dict) or not info:
            continue
        # Stale-restored fields ARE re-saved. The earlier "drop stale
        # before save" filter (intended to break the APC UPS phantom-
        # 100% loop) made the 24h grace cap in
        # `apply_host_snapshot_fallback` unreachable — stale fields
        # vanished after one cycle, so `_meta_stale_since` never had
        # a chance to fire its age-cap. The phantom-value issue is
        # addressed UPSTREAM by the SNMP probe fix that gates
        # `host_cpu_percent` on explicit OID presence; the read-side
        # grace cap handles long-term cleanup of any value that's
        # still stuck stale past the configured window
        # (`tuning_host_snapshot_stale_field_max_age_hours`, default
        # 24h). Belt-and-braces, not duplicated.
        clean = {
            k: v for k, v in info.items()
            if (not str(k).startswith("_")) or str(k).startswith("_meta_")
        }
        if _is_urlish(clean.get("host_firmware")):
            clean.pop("host_firmware", None)
        try:
            blob = json.dumps(clean, default=str)
        except (TypeError, ValueError):
            continue
        rows.append((host, ts, blob))
    if not rows:
        return 0
    try:
        with db_conn() as c:
            c.executemany(
                "INSERT OR REPLACE INTO host_snapshots(host, ts, data) "
                "VALUES (?, ?, ?)",
                rows,
            )
    except Exception as e:  # noqa: BLE001
        print(f"[gather] save_host_snapshots failed: {e}")
        return 0
    # Bust the read-side cache so the next caller sees the freshest
    # snapshots immediately after a write. Pre-fix the cache
    # was time-based only and could serve a stale map for up to TTL.
    _snapshots_cache["map"] = None
    _snapshots_cache["ts"] = 0.0
    return len(rows)


# Short-TTL cache for ``load_host_snapshots``. The SPA fans out
# N parallel ``/api/hosts/one/{id}`` calls per refresh, each of which
# triggers ``apply_host_snapshot_fallback`` → ``load_host_snapshots()``
# → a full SELECT against ``host_snapshots``. With 50 hosts that's 50
# full-table reads per refresh on a hot path. Caching collapses N
# reads into 1 without serving stale data (the snapshot table is
# written once per gather tick; the cache is also busted on every
# save). TTL is admin-tunable — `tuning_host_snapshots_cache_ttl_seconds`
# (default 5s, range 0–300s; 0 disables the cache for debugging).
_snapshots_cache: dict = {"map": None, "ts": 0.0}


def load_host_snapshots() -> dict[str, dict]:
    """Read every persisted host snapshot.

    Returns ``{host: {"ts": float, "data": {...}}}`` — JSON parse
    errors are skipped per-row so a single malformed blob doesn't
    poison the lookup. Empty dict on table-missing (first boot before
    init_db has run) or any other DB failure.

    Cached for the operator-tunable
    ``tuning_host_snapshots_cache_ttl_seconds`` window — the
    snapshot table is written once per gather tick and read O(N) times
    per SPA refresh, so caching the read is a strict win. The cache is
    busted on every ``save_host_snapshots`` write so write→read
    consistency is immediate after a successful gather. Setting the
    tunable to 0 disables the cache entirely.
    """
    now = time.time()
    try:
        from logic.tuning import Tunable, tuning_int
        ttl = float(tuning_int(Tunable.HOST_SNAPSHOTS_CACHE_TTL_SECONDS))
    except (ImportError, KeyError, ValueError, TypeError):
        ttl = 5.0
    cached_map = _snapshots_cache.get("map")
    cached_ts = _snapshots_cache.get("ts") or 0.0
    if ttl > 0 and isinstance(cached_map, dict) and (now - cached_ts) < ttl:
        return cached_map
    out: dict[str, dict] = {}
    try:
        with db_conn() as c:
            rows = c.execute(
                "SELECT host, ts, data FROM host_snapshots"
            ).fetchall()
    except Exception as e:  # noqa: BLE001
        print(f"[gather] load_host_snapshots failed: {e}")
        return out
    for r in rows:
        try:
            data = json.loads(r["data"]) if r["data"] else {}
        except (TypeError, ValueError):
            continue
        if isinstance(data, dict):
            out[r["host"]] = {"ts": float(r["ts"] or 0.0), "data": data}
    # Cache the freshly-built map so parallel callers in the same TTL
    # window share it. Mutating callers (`apply_host_snapshot_fallback`)
    # already treat the returned dict as read-only.
    _snapshots_cache["map"] = out
    _snapshots_cache["ts"] = now
    return out


def apply_host_snapshot_fallback(
    nodes_info: dict, snapshots: Optional[dict[str, dict]] = None,
) -> None:
    """Fill missing host_* fields from the persisted snapshot.

    For each known host, when the live ``nodes_info[host]`` is missing
    a runtime field we have a snapshot of, copy it over and tag the
    field name in ``_stale_fields`` (a list). Also stamps
    ``_stale_ts`` with the snapshot's persistence timestamp so the
    UI can show "last known X minutes ago" if it wants.

    Mutates ``nodes_info`` in place. Loads snapshots itself when the
    caller doesn't pass one (e.g. lifespan seeding) so we read once
    per gather not once per host.
    """
    if not nodes_info:
        return
    if snapshots is None:
        snapshots = load_host_snapshots()
    if not snapshots:
        return

    # Single source of truth for "this value carries information" —
    # the same helper backs the live merge path at the bottom of this
    # module (logic/merge.py). Importing here instead of redefining
    # locally keeps the snapshot-fallback semantics byte-identical to
    # the merge_best path; future tweaks to is_meaningful (e.g. Decimal
    # support) flow through both call sites automatically.
    from logic.merge import is_meaningful as _is_meaningful

    # Per-field "stale grace" cap. Resolved per-call (DB > env >
    # default) so an Admin → Config edit takes effect on the next
    # gather without restart. Hours converted to seconds for the
    # `now - first_stale_ts > grace` comparison below.
    try:
        from logic.tuning import Tunable, tuning_int
        grace_hours = float(tuning_int(Tunable.HOST_SNAPSHOT_STALE_FIELD_MAX_AGE_HOURS))
    except (ImportError, KeyError, ValueError, TypeError):
        grace_hours = 24.0
    grace_window_s = max(1.0, grace_hours) * 3600.0
    now = time.time()

    for host, info in nodes_info.items():
        if not isinstance(info, dict):
            continue
        snap = snapshots.get(host)
        if not snap:
            # Try short-hostname match — Docker reports `docker.example.com`
            # but the snapshot might have been keyed under `docker`.
            short = str(host).split(".", 1)[0]
            for k, v in snapshots.items():
                if k == short or str(k).split(".", 1)[0] == short:
                    snap = v
                    break
        if not snap:
            continue
        snap_data = snap.get("data") or {}
        snap_ts = snap.get("ts") or 0.0
        # Read prior `_meta_stale_since` map from the snapshot blob —
        # it round-trips because save_host_snapshots keeps `_meta_*`
        # keys (the strip filter only drops single-underscore non-meta
        # bookkeeping). Each key is a host_* field name, value is the
        # epoch-seconds ts when it FIRST went stale (before any
        # subsequent re-fallback). Defensive isinstance check covers
        # legacy snapshots that pre-date this map.
        prior_stale_since = snap_data.get("_meta_stale_since") or {}
        if not isinstance(prior_stale_since, dict):
            prior_stale_since = {}
        stale_fields: list[str] = []
        meta_stale_since: dict[str, float] = {}
        # Iterate the SNAPSHOT's keys instead of a hand-maintained
        # whitelist. Any ``host_*`` field — plus the small
        # bare-exception set — auto-qualifies, so a provider that
        # sprouts a new field (e.g. ``host_temperatures``)
        # gets restored on provider outage without a parallel edit
        # to a whitelist tuple. Operator-config fields written
        # alongside (label / icon / etc.) are excluded by the prefix
        # gate.
        for key, v in snap_data.items():
            if not _is_snapshot_key(key):
                continue
            if _is_meaningful(info.get(key)):
                # Field has a fresh live value — clear any prior
                # stale-since marker; recovery means the orphan
                # accounting resets to zero for this field.
                continue
            # Skip URL-shaped values for host_firmware — pre-fix
            # the iDRAC chassis URL was mapped to host_firmware before
            # URL detection routed it to host_idrac_url. Already-persisted
            # snapshots can carry the stale URL forever; the write-path
            # filter in save_host_snapshots scrubs it on the next gather,
            # but until then this guard keeps the SPA from rendering
            # "Firmware: https://<host>:443" in the Hardware card.
            if key == "host_firmware" and _is_urlish(v):
                continue
            if not _is_meaningful(v):
                continue
            # Carry-over first_stale_ts if this field was already stale
            # on a prior gather; otherwise stamp NOW (this is the
            # first tick that detected the field as stale). Defensive
            # numeric coerce — operator-edited or migrated snapshot
            # data could carry a string here.
            try:
                first_stale_ts = float(prior_stale_since.get(key, 0)) or now
            except (TypeError, ValueError):
                first_stale_ts = now
            age_s = max(0.0, now - first_stale_ts)
            if age_s > grace_window_s:
                # Past grace — drop the orphan from BOTH the merged
                # dict (so the SPA renders "no data" instead of stale)
                # AND from `meta_stale_since` (so the next save
                # doesn't carry it forward). The next save's `clean`
                # filter will exclude this key naturally because
                # `info` doesn't carry it; the cycle finally breaks.
                continue
            info[key] = v
            stale_fields.append(key)
            meta_stale_since[key] = first_stale_ts
        if stale_fields:
            info["_stale_fields"] = stale_fields
            info["_stale_ts"] = snap_ts
        # Always stamp `_meta_stale_since` (even when empty) so save
        # round-trips a clean dict — without this an empty map after
        # full recovery would leave a stale prior map behind in the
        # snapshot, only updated on the next outage.
        info["_meta_stale_since"] = meta_stale_since
        # Derived per-field grace-remaining map — operator-facing UX:
        # when a stale field is within 20% of its grace expiry, the
        # SPA renders a countdown banner ("stale data will be
        # discarded in 4h") so operators investigate BEFORE the
        # data silently drops. Computed at apply-time from
        # `_meta_stale_since` + the configured `grace_window_s`;
        # not stored (re-derived every gather). Empty when no
        # stale field is within the warning window.
        warn_threshold_s = grace_window_s * 0.8
        grace_remaining: dict[str, float] = {}
        for key, first_ts in meta_stale_since.items():
            try:
                age = max(0.0, now - float(first_ts))
            except (TypeError, ValueError):
                continue
            if age >= warn_threshold_s:
                remaining = max(0.0, grace_window_s - age)
                grace_remaining[key] = remaining
        if grace_remaining:
            info["_meta_stale_grace_remaining_s"] = grace_remaining


# noinspection DuplicatedCode
def seed_nodes_info_from_snapshots() -> int:
    """Populate ``_cache["nodes_info"]`` from persisted snapshots.

    Called at lifespan startup so the first ``/api/items`` after a
    restart shows the previous gather's host stats while the live one
    runs in parallel. Every seeded entry is tagged with
    ``_stale_fields`` listing every field present so the UI can dim
    the corresponding bar / value until the live gather overwrites.

    Returns the number of hosts seeded.
    """
    snapshots = load_host_snapshots()
    if not snapshots:
        return 0
    # Per-field stale grace cap — same tunable + semantics as
    # apply_host_snapshot_fallback. Resolved once for the whole seed
    # pass since startup is a single moment in wall-clock time.
    try:
        from logic.tuning import Tunable, tuning_int
        grace_hours = float(tuning_int(Tunable.HOST_SNAPSHOT_STALE_FIELD_MAX_AGE_HOURS))
    except (ImportError, KeyError, ValueError, TypeError):
        grace_hours = 24.0
    grace_window_s = max(1.0, grace_hours) * 3600.0
    now = time.time()
    seeded: dict[str, dict] = {}
    for host, snap in snapshots.items():
        data = dict(snap.get("data") or {})
        if not data:
            continue
        # Drop past-grace orphan fields BEFORE seeding so the first
        # paint after a restart matches what apply_host_snapshot_fallback
        # would render. Without this, fields whose first_stale_ts is
        # already > grace_window_s old would briefly render as
        # fresh-stale until the first gather's fallback runs and
        # cleans them up.
        prior_stale_since = data.get("_meta_stale_since") or {}
        if not isinstance(prior_stale_since, dict):
            prior_stale_since = {}
        for key, first_stale_ts_raw in list(prior_stale_since.items()):
            try:
                first_stale_ts = float(first_stale_ts_raw)
            except (TypeError, ValueError):
                continue
            if first_stale_ts <= 0:
                continue
            if (now - first_stale_ts) > grace_window_s:
                data.pop(key, None)
                prior_stale_since.pop(key, None)
        # Round-trip the cleaned meta map so the next save reflects
        # the post-grace state.
        data["_meta_stale_since"] = prior_stale_since
        # Tag every snapshot-eligible field present so the UI can show
        # every seeded value as stale. The next gather's
        # apply_host_snapshot_fallback recomputes this list against
        # the live state. Same prefix + bare-exception predicate as
        # the apply path so seed and restore stay in lock-step.
        stale = [k for k in data.keys() if _is_snapshot_key(k)]
        if stale:
            data["_stale_fields"] = stale
            _ts_raw = snap.get("ts") if isinstance(snap, dict) else None
            if isinstance(_ts_raw, (int, float)):
                data["_stale_ts"] = float(_ts_raw)
            elif isinstance(_ts_raw, str):
                try:
                    data["_stale_ts"] = float(_ts_raw)
                except ValueError:
                    data["_stale_ts"] = 0.0
            else:
                data["_stale_ts"] = 0.0
        seeded[host] = data
    if seeded:
        _cache["nodes_info"] = seeded
    return len(seeded)


# ---------------------------------------------------------------------------
# Items / stacks / nodes snapshot — cross-restart cache persistence.
#
# Pre-fix: ``_cache`` was in-memory only. After a container restart the
# first ``/api/items`` call had nothing to serve and blocked on the
# full Portainer fan-out + image-digest probe (10-30s on a busy
# cluster). With the instant-paint fallback in place subsequent
# poll cycles never block, but the FIRST request after restart still
# did because the only fallback was the in-memory cache.
#
# Fix: persist ``_cache`` to ``items_snapshot`` at the end of every
# successful gather; seed it back into ``_cache`` at lifespan startup
# so the FIRST request finds data and serves instantly while the live
# gather runs in background. Mirrors the host_snapshots pattern; the
# whole snapshot is one JSON blob in a single-row table because the
# data is wholesale-replaced every gather (no per-item upsert needed).
#
# Stale markers: every item / stack / nodes_info entry seeded from the
# snapshot gets ``_stale: True`` so the SPA can dim them until the
# live gather overwrites. ``_cache["_stale"]`` carries the same flag at
# the top level — set when seeded, cleared at the start of every fresh
# gather so the cache itself reads as fresh while the gather runs.
# ---------------------------------------------------------------------------


# Cap the items_snapshot blob to a reasonable upper bound so a runaway
# gather (e.g. on a misconfigured fleet with N×100 phantom containers)
# doesn't produce a multi-MB JSON blob that strains SQLite's blob
# handling on the read-back path. 5 MiB easily covers a few hundred
# hosts × dozens of items each with full Pulse / Beszel / NE
# enrichment; anything above that is more likely a bug than legitimate
# scale. When tripped, we log + skip the write — the prior snapshot
# stays canonical and operators see "stale" rendering on the next
# restart instead of an oversized blob the read path can't handle.
_ITEMS_SNAPSHOT_MAX_BYTES = 5 * 1024 * 1024

# Schema version stamped into every persisted blob. Bump when the
# items / stacks / nodes_info shape gains a REQUIRED field that older
# snapshots wouldn't have (e.g. a numeric column the SPA reads via
# `Number.isFinite`). Mismatched versions on the read path drop the
# snapshot rather than seeding a partial cache that would render
# subtly wrong until the live gather replaces it. Current shape
# matches the items-snapshot persistence shipped earlier — items /
# stacks / nodes / nodes_info / ts.
_ITEMS_SNAPSHOT_SCHEMA_VERSION = 1


def save_items_snapshot() -> bool:
    """Persist ``_cache`` to the ``items_snapshot`` row.

    Called at the END of every successful ``_gather_impl`` so the
    persisted blob always reflects the latest known good state. Errors
    are logged + swallowed — a failed snapshot must never break the
    gather (e.g. transient SQLite lock during a backup).

    Size-capped to ``_ITEMS_SNAPSHOT_MAX_BYTES`` (5 MiB). When the
    serialised payload exceeds the cap we log a WARN line and skip
    the write; the prior snapshot row stays canonical so the next
    restart still has SOMETHING to seed from (just slightly older
    data), and operators see the bound's-tripped log line as a
    diagnostic for the runaway gather.

    Returns ``True`` on success, ``False`` on any error.
    """
    try:
        # Strip the ``_stale`` markers off the cache before persisting —
        # otherwise a save-load-save cycle would persist them as
        # canonical state. Items / stacks already drop their per-row
        # stale flag the moment a fresh gather replaces them, so
        # serialising the whole cache as-is here is correct (we're
        # writing AFTER a fresh gather, so nothing is stale).
        _items_l: list = list(_cache.get("items") or [])
        _stacks_l: list = list(_cache.get("stacks") or [])
        _nodes_d: dict = dict(_cache.get("nodes") or {})
        # nodes_info carries host_* fields with their own
        # _stale_fields markers from the host_snapshots pipeline —
        # those are PER-FIELD freshness hints distinct from the
        # cache-level _stale flag and we want to preserve them.
        _nodes_info_d: dict = dict(_cache.get("nodes_info") or {})
        payload = {
            # Schema version — read path drops the snapshot entirely
            # when this doesn't match the current expected version, so
            # an upgrade that adds a required field never seeds a
            # partial cache that renders subtly wrong.
            "v": _ITEMS_SNAPSHOT_SCHEMA_VERSION,
            "items": _items_l,
            "stacks": _stacks_l,
            "nodes": _nodes_d,
            "nodes_info": _nodes_info_d,
            "ts": float(_cache.get("ts") or 0.0),
        }
        blob = json.dumps(payload, default=str)
        blob_bytes = len(blob.encode())
        if blob_bytes > _ITEMS_SNAPSHOT_MAX_BYTES:
            print(
                f"[gather] save_items_snapshot SKIPPED — payload size "
                f"{blob_bytes // 1024} KiB exceeds the {_ITEMS_SNAPSHOT_MAX_BYTES // 1024} KiB cap. "
                f"items={len(_items_l)}, stacks={len(_stacks_l)}, "
                f"nodes={len(_nodes_d)}, nodes_info={len(_nodes_info_d)}. "
                f"Prior snapshot stays canonical."
            )
            return False
        with db_conn() as c:
            c.execute(
                "INSERT OR REPLACE INTO items_snapshot(id, ts, data) "
                "VALUES (1, ?, ?)",
                (payload["ts"], blob),
            )
        return True
    except Exception as e:  # noqa: BLE001
        print(f"[gather] save_items_snapshot failed: {e}")
        return False


def seed_items_cache_from_snapshot() -> int:
    """Populate ``_cache`` from the persisted ``items_snapshot`` row.

    Called at lifespan startup. Stamps ``_stale: True`` on every
    seeded item / stack / nodes_info entry so the SPA's dimmed-fallback
    rendering kicks in until the live gather replaces the cache.
    ``_cache["_stale"]`` carries the same flag at the top level so the
    SPA can render a "refreshing…" hint without walking every row.

    Returns the number of items seeded (0 if no snapshot exists or
    the read fails — first-ever boot or a clean DB falls through to
    the legacy block-on-gather behaviour).
    """
    try:
        with db_conn() as c:
            row = c.execute(
                "SELECT ts, data FROM items_snapshot WHERE id = 1"
            ).fetchone()
        if not row:
            return 0
        payload = json.loads(row["data"]) if row["data"] else {}
    except Exception as e:  # noqa: BLE001
        print(f"[gather] seed_items_cache_from_snapshot failed: {e}")
        return 0

    # Schema-version gate — drop the snapshot entirely when the on-disk
    # version doesn't match what this build expects. Pre-fix an older
    # container's snapshot would seed `_cache` with a partial shape and
    # the SPA would render subtle inconsistencies until the live gather
    # replaced it; the gate is cheaper + safer. Missing `v` key means
    # the snapshot pre-dates the version stamp (legacy from the
    # initial items-snapshot release) — accept it as v1 to avoid forcing
    # a one-off block-on-gather on the first boot post-upgrade. Future versions
    # bump the constant; the gate then drops legacy snapshots cleanly.
    snap_v = payload.get("v")
    if snap_v is None:
        snap_v = 1
    if int(snap_v) != _ITEMS_SNAPSHOT_SCHEMA_VERSION:
        print(
            f"[gather] seed_items_cache_from_snapshot DROPPED — "
            f"snapshot schema_version={snap_v}, expected "
            f"{_ITEMS_SNAPSHOT_SCHEMA_VERSION}. Falling back to "
            f"block-on-gather; the next successful gather will "
            f"overwrite the snapshot at the current version."
        )
        return 0

    items = list(payload.get("items") or [])
    stacks = list(payload.get("stacks") or [])
    nodes = dict(payload.get("nodes") or {})
    nodes_info = dict(payload.get("nodes_info") or {})
    ts = float(payload.get("ts") or row["ts"] or 0.0)

    # Tag every row + the cache itself as stale until the live gather
    # overwrites. Per-row mutation is safe — these came from JSON, no
    # shared references with anything else.
    for it in items:
        if isinstance(it, dict):
            it["_stale"] = True
    for st in stacks:
        if isinstance(st, dict):
            st["_stale"] = True
    for k, v in nodes_info.items():
        if isinstance(v, dict):
            v["_stale"] = True

    _cache["items"] = items
    _cache["stacks"] = stacks
    _cache["nodes"] = nodes
    # Don't clobber an already-seeded nodes_info from
    # seed_nodes_info_from_snapshots if it ran first — merge instead so
    # the host_snapshots-derived per-field _stale_fields markers stay
    # intact for hosts present in both seed paths.
    existing_ni = _cache.get("nodes_info") or {}
    for k, v in nodes_info.items():
        if k not in existing_ni:
            existing_ni[k] = v
    _cache["nodes_info"] = existing_ni
    _cache["ts"] = ts
    _cache["_stale"] = True
    return len(items)


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


# OCI / common image-version label keys, highest-confidence first. The
# OpenContainers spec key is the modern standard; org.label-schema is its
# deprecated predecessor; bare `version` / `app.version` cover images that set
# a plain label. Used to surface a RUNNING app version in the item drawer when
# the image tag itself is uninformative (e.g. every app pinned to `:latest`).
_IMAGE_VERSION_LABEL_KEYS: tuple[str, ...] = (
    "org.opencontainers.image.version",
    "org.label-schema.version",
    "version",
    "app.version",
)

# Non-version label values: branch / channel names that carry no version
# signal (so we don't surface "main" as a version after ref-cleaning).
_NON_VERSION_VALUES = frozenset({
    "latest", "unknown", "stable", "edge", "nightly", "main", "master",
    "develop", "dev", "head", "rolling",
})


def _clean_version_ref(v: str) -> str:
    """Strip git-ref noise some CI label injectors leave on
    ``org.opencontainers.image.version`` (GitHub's docker/metadata-action with
    ``type=ref,event=tag`` writes the full ref). Examples:
    ``refs/tags/version/2026.5.0`` → ``2026.5.0``; ``refs/tags/v1.2.3`` →
    ``v1.2.3``. Leaves a plain version untouched."""
    s = (v or "").strip()
    for pre in ("refs/tags/", "refs/heads/", "refs/"):
        if s.startswith(pre):
            s = s[len(pre):]
            break
    # Drop a leading path segment used as a tag namespace (authentik tags as
    # `version/<semver>`); only the first segment, and only known ones so a
    # real version with a slash (rare) isn't mangled.
    for pre in ("version/", "release/", "releases/", "tags/", "v/"):
        if s.lower().startswith(pre):
            s = s[len(pre):]
            break
    return s.strip().strip("/")


def _extract_app_version(labels: Any) -> str:
    """Pull a human app version out of a Docker labels dict, trying the OCI
    version label first, then common fallbacks. Returns ``""`` when no label
    is present or the value is a digest / branch / obviously-not-a-version
    string. Caps length so a stray long label can't bloat the item payload."""
    if not isinstance(labels, dict):
        return ""
    for key in _IMAGE_VERSION_LABEL_KEYS:
        v = labels.get(key)
        if not isinstance(v, str):
            continue
        # Clean git-ref noise FIRST (refs/tags/version/X → X), then validate.
        v = _clean_version_ref(v)
        if not v or v.startswith("sha256:") or v.lower() in _NON_VERSION_VALUES:
            continue
        # A real version contains at least one digit — rejects bare branch /
        # channel names (main / master / …) that survived ref-cleaning.
        if not any(ch.isdigit() for ch in v):
            continue
        return v[:40]
    return ""


def _extract_service_ports(svc: dict) -> list[dict]:
    """Return ``[{published, target, protocol}, …]`` for a Swarm service.

    Reads ``Endpoint.Ports`` (Portainer / Docker mirrors this from
    ``Spec.EndpointSpec.Ports`` for ingress-mode publishes). Skips
    entries with no PublishedPort (port is declared on the service
    spec but not actually published to the host).

    Uses ``try/except (ValueError, TypeError)`` around the int casts
    rather than truthy-narrowing because pyright can't statically
    narrow ``dict.get(...)`` from ``Any | None`` to a concrete
    SupportsInt — the try/except also gracefully drops malformed
    payloads (string ``"abc"``, ``None``, etc.).
    """
    out: list[dict] = []
    seen: set = set()
    raw = ((svc.get("Endpoint") or {}).get("Ports") or [])
    for p in raw:
        if not isinstance(p, dict):
            continue
        try:
            published = int(p.get("PublishedPort") or 0)
            target = int(p.get("TargetPort") or 0)
        except (ValueError, TypeError):
            continue
        if not published or not target:
            continue
        proto = (p.get("Protocol") or "tcp").lower()
        key = (published, target, proto)
        if key in seen:
            continue
        seen.add(key)
        out.append({"published": published, "target": target, "protocol": proto})
    return out


# noinspection DuplicatedCode
def _extract_container_ports(cont: dict) -> list[dict]:
    """Return ``[{published, target, protocol}, …]`` for a container.

    Portainer's container list returns a ``Ports`` array of
    ``{IP, PrivatePort, PublicPort, Type}`` entries. Containers
    bound to both IPv4 (``0.0.0.0``) and IPv6 (``::``) produce
    duplicate entries — we de-dupe on ``(published, target, proto)``.
    Skips entries with no PublicPort (private-only, no host bind,
    so no clickable URL can be constructed).

    Same ``try/except`` shape as :func:`_extract_service_ports` —
    pyright can't narrow ``Any | None`` through truthy checks; the
    explicit cast + catch is both type-clean and tolerant of
    malformed payloads.
    """
    out: list[dict] = []
    seen: set = set()
    raw = cont.get("Ports") or []
    for p in raw:
        if not isinstance(p, dict):
            continue
        try:
            published = int(p.get("PublicPort") or 0)
            target = int(p.get("PrivatePort") or 0)
        except (ValueError, TypeError):
            continue
        if not target or not published:
            continue
        proto = (p.get("Type") or "tcp").lower()
        key = (published, target, proto)
        if key in seen:
            continue
        seen.add(key)
        out.append({"published": published, "target": target, "protocol": proto})
    return out


# ---------------------------------------------------------------------------
# Direct-Docker nodes — merge a Portainer-less node's
# containers into the gather cache, tagged ``backend="docker:<id>"``. The node
# is reached over SSH via ``logic/docker_direct.py`` (no Portainer); its items
# flow through the SAME stack-grouping + status classification as the Portainer
# fleet so they render identically in the Stacks / Services / Nodes views.
# ---------------------------------------------------------------------------
def _load_docker_nodes_cfg() -> list[dict]:
    """Enabled direct-Docker nodes from the ``docker_nodes`` setting (those with
    a master ``enabled`` and an ``address``). ``[]`` on a corrupt blob."""
    from logic.db import get_setting  # noqa: PLC0415
    raw = get_setting(Settings.DOCKER_NODES) or ""
    try:
        data = json.loads(raw) if raw else []
    except (ValueError, TypeError):
        return []
    if not isinstance(data, list):
        return []
    return [n for n in data
            if isinstance(n, dict) and n.get("enabled", True) and n.get("address")]


def _docker_df_bytes(df: Any) -> int:
    """Total Docker disk usage from a ``/system/df`` payload — image layers +
    container writable layers + volumes + build cache."""
    d = df if isinstance(df, dict) else {}
    total = safe_int(d.get("LayersSize"))
    for cont in d.get("Containers") or []:
        if isinstance(cont, dict):
            total += safe_int(cont.get("SizeRw"))
    for vol in d.get("Volumes") or []:
        if isinstance(vol, dict):
            ud = vol.get("UsageData")
            if isinstance(ud, dict):
                total += max(0, safe_int(ud.get("Size")))
    for bc in d.get("BuildCache") or []:
        if isinstance(bc, dict):
            total += max(0, safe_int(bc.get("Size")))
    return max(0, total)


def _is_ignored_row(image: Any, ig_stack: Any, ignores: list) -> bool:
    """Standalone ``is_ignored`` over a pre-loaded ``ignores`` table (the
    direct-Docker merge runs outside the Portainer gather's closure)."""
    for ig in ignores:
        p = ig.get("pattern")
        if ig.get("kind") == "image" and p and p in (str(image) or ""):
            return True
        if ig.get("kind") == "stack" and p and p == (str(ig_stack) if ig_stack else ""):
            return True
    return False


def _group_items_into_stacks(items: list) -> list:
    """Group items by stack into the stack-card list (the same shape the
    Portainer finalize produces) — shared so the direct-Docker merge re-groups
    the COMBINED item list consistently. Returns the alphabetically-sorted
    stack list with per-stack roll-up counts."""
    groups: dict[str, dict[str, Any]] = {}
    for it in items:
        key = it.get("stack") or "__standalone__"
        g = groups.setdefault(key, {
            "name": it.get("stack") or "Standalone",
            "stack_id": it.get("stack_id"),
            "items": [],
            "is_standalone": not it.get("stack"),
        })
        gi = g["items"]
        if isinstance(gi, list):
            gi.append(it)
    for g in groups.values():
        its_raw = g.get("items")
        its: list = its_raw if isinstance(its_raw, list) else []
        its.sort(key=lambda i: (i.get("name") or "").lower())
        g["total"] = len(its)
        g["updates"] = sum(1 for i in its
                           if i.get("status") == "update" and i.get("health") != "offline")
        g["updates_offline"] = sum(1 for i in its
                                   if i.get("status") == "update" and i.get("health") == "offline")
        g["errors"] = sum(1 for i in its if i.get("status") == "error")
        g["unknowns"] = sum(1 for i in its if i.get("status") == "unknown")
        g["uptodate"] = sum(1 for i in its if i.get("status") == "up-to-date")
        g["offline"] = sum(1 for i in its if i.get("health") == "offline")
        g["degraded"] = sum(1 for i in its if i.get("health") == "degraded")
    return sorted(groups.values(), key=lambda _s: (_s.get("name") or "").lower())


async def _classify_item_status(reg_client: "httpx.AsyncClient", item: dict) -> None:
    """Resolve ``remote_digest`` + ``status`` for one direct-Docker item — the
    same digest-comparison rules as the Portainer enrich step (the registry
    probe is transport-agnostic)."""
    try:
        remote = await registry.get_remote_digest(reg_client, item["image"])
    except (httpx.HTTPError, OSError, ValueError):
        remote = None
    item["remote_digest"] = remote
    if item.get("ignored"):
        item["status"] = "ignored"
    elif not item.get("current_digest"):
        item["status"] = "unknown"
    elif not remote:
        item["status"] = "error"
    elif item["current_digest"] == remote:
        item["status"] = "up-to-date"
    else:
        item["status"] = "update"


# noinspection DuplicatedCode
async def _gather_one_docker_node(node: dict, ignores: list) -> "tuple[list, str, dict]":
    """Connect to ONE direct-Docker node over SSH and build its container items
    (reusing the standalone-container shape) + a synthesized Node card entry.
    Returns ``(items, node_label, node_info)``. Raises ``DockerDirectError`` on a
    transport failure (the caller skips that node)."""
    from logic import docker_direct  # noqa: PLC0415
    node_id = str(node.get("id") or "")
    backend = f"docker:{node_id}"
    async with docker_direct.connect(node) as cli:
        cstatus, containers, _snip = await cli.get("/containers/json?all=1")
        istatus, info, _i = await cli.get("/info")
        vstatus, version, _v = await cli.get("/version")
        dstatus, df, _d = await cli.get("/system/df")
        if cstatus != 200 or not isinstance(containers, list):
            raise docker_direct.DockerDirectError(
                f"GET /containers/json returned HTTP {cstatus}")
        info = info if isinstance(info, dict) else {}
        version = version if isinstance(version, dict) else {}
        node_label = (str(node.get("label") or info.get("Name") or node_id
                          or node.get("address") or "docker")).strip() or "docker"
        items: list = []
        img_cache: dict = {}
        for cont in containers:
            if not isinstance(cont, dict) or not cont.get("Id"):
                continue
            labels = cont.get("Labels") or {}
            image_ref = cont.get("Image") or ""
            if "@" in image_ref:
                image_ref = image_ref.partition("@")[0]
            compose_project = (labels.get("com.docker.compose.project")
                               or labels.get("com.docker.stack.namespace"))
            current_digest = None
            cont_app_version = ""
            image_id = cont.get("ImageID") or ""
            if image_id:
                if image_id not in img_cache:
                    try:
                        _ist, _img, _is = await cli.get(f"/images/{image_id}/json")
                        img_cache[image_id] = _img if isinstance(_img, dict) else {}
                    except docker_direct.DockerDirectError:
                        img_cache[image_id] = {}
                img = img_cache[image_id]
                cont_app_version = _extract_app_version(
                    (img.get("Config") or {}).get("Labels") or {})
                for rd in img.get("RepoDigests") or []:
                    if "@" in rd:
                        current_digest = rd.split("@", 1)[1]
                        break
                if image_ref.startswith("sha256:") or (
                        image_ref and "/" not in image_ref and ":" not in image_ref):
                    real = [t for t in (img.get("RepoTags") or [])
                            if t and "<none>" not in t]
                    if real:
                        image_ref = real[0]
            name = (cont.get("Names") or ["?"])[0].lstrip("/")
            state = (cont.get("State") or "").lower()
            if state == "running":
                health = "healthy"
            elif state in ("restarting", "paused"):
                health = "degraded"
            else:
                health = "offline"
            items.append({
                "id": f"ctn:{cont['Id'][:12]}",
                "raw_id": cont["Id"],
                "name": name,
                "type": "container",
                "app_version": cont_app_version,
                "image": image_ref,
                "tag": registry.tag_of(image_ref),
                "current_digest": current_digest,
                "stack": compose_project,
                "stack_id": None,
                "replicas": {"desired": 1, "running": 1 if state == "running" else 0},
                "placements": [{"node": node_label, "state": state}],
                "node": node_label,
                "health": health,
                "state": state,
                "removable": health == "offline",
                "hub_link": registry.hub_link(image_ref),
                "ignored": _is_ignored_row(image_ref, compose_project, ignores),
                "created": cont.get("Created"),
                "ports": _extract_container_ports(cont),
                # Direct-Docker backend tag — drives write-op routing + the
                # "Direct" pill in the SPA. Format: "docker:<node_id>".
                "backend": backend,
            })
    ncpu = safe_int(info.get("NCPU"))
    running = sum(1 for c in containers
                  if isinstance(c, dict) and (c.get("State") or "").lower() == "running")
    node_info = {
        "id": info.get("ID") or node_id,
        "role": "standalone",
        "state": "ready",
        "availability": "active",
        "cpu_cores": ncpu,
        "nano_cpus": ncpu * 1_000_000_000,
        "mem_bytes": safe_int(info.get("MemTotal")),
        "os": str(info.get("OperatingSystem") or "").strip(),
        "arch": str(info.get("Architecture") or "").strip(),
        "engine": str(version.get("Version") or info.get("ServerVersion") or "").strip(),
        "ip": str(node.get("address") or "").strip(),
        "docker_disk_bytes": _docker_df_bytes(df) if dstatus == 200 else 0,
        # Backend + icon for the SPA's Node card "Direct" pill + brand icon.
        "backend": backend,
        "icon": str(node.get("icon") or "").strip(),
        "containers_total": sum(1 for c in containers if isinstance(c, dict)),
        "containers_running": running,
    }
    return items, node_label, node_info


async def merge_docker_nodes_into_cache() -> None:
    """Append every enabled direct-Docker node's containers to the gather cache
    (tagged ``backend="docker:<id>"``), synthesize each node's Node card entry,
    and re-group the COMBINED item list into stacks. Always stamps
    ``backend="portainer"`` on the pre-existing Portainer items / nodes. Best-
    effort per node — a down / unreachable node is logged + skipped, never
    failing the whole gather. A no-op (beyond the backend stamp) when no Docker
    node is configured."""
    for it in _cache.get("items") or []:
        if isinstance(it, dict):
            it.setdefault("backend", "portainer")
    for ni in (_cache.get("nodes_info") or {}).values():
        if isinstance(ni, dict):
            ni.setdefault("backend", "portainer")
    nodes_cfg = _load_docker_nodes_cfg()
    if not nodes_cfg:
        return
    try:
        with db_conn() as c:
            ignores = [dict(r) for r in c.execute("SELECT * FROM ignores").fetchall()]
    except Exception:  # noqa: BLE001
        ignores = []
    try:
        from logic.tuning import Tunable as _T, tuning_int as _ti  # noqa: PLC0415
        reg_to = float(_ti(_T.GATHER_CLIENT_TIMEOUT_SECONDS))
    except (ImportError, KeyError, ValueError, TypeError):
        reg_to = 60.0
    new_items: list = []
    nodes_info = _cache.setdefault("nodes_info", {})
    async with httpx.AsyncClient(timeout=reg_to) as reg_client:
        for node in nodes_cfg:
            try:
                node_items, label, ninfo = await _gather_one_docker_node(node, ignores)
            except (asyncio.CancelledError, KeyboardInterrupt):
                raise
            except Exception as e:  # noqa: BLE001
                print(f"[docker] gather node {node.get('id')!r} failed: "
                      f"{type(e).__name__}: {e}")
                continue
            await asyncio.gather(*(_classify_item_status(reg_client, it) for it in node_items))
            new_items.extend(node_items)
            nodes_info[label] = ninfo
            print(f"[docker] INFO node {node.get('id')!r} ({label}): "
                  f"{len(node_items)} container(s) running="
                  f"{ninfo.get('containers_running')} engine={ninfo.get('engine') or '-'}")
    if not new_items:
        return
    combined = (_cache.get("items") or []) + new_items
    combined.sort(key=lambda i: (i.get("name") or "").lower())
    _cache["items"] = combined
    _cache["stacks"] = _group_items_into_stacks(combined)


async def _safe_merge_docker(label: str) -> None:
    """Run ``merge_docker_nodes_into_cache`` swallowing any error (a down Docker
    node must never fail the whole gather). Cancellation propagates."""
    try:
        await merge_docker_nodes_into_cache()
    except (asyncio.CancelledError, KeyboardInterrupt):
        raise
    except Exception as e:  # noqa: BLE001
        print(f"[docker] merge ({label}) failed: {type(e).__name__}: {e}")


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


# Public aliases for cross-module use. main.py reaches for these to compute
# service desired-replica counts (`_node_matches` / `_node_attr`) and for the
# snapshot-eligibility predicate (`_is_snapshot_key`); the underscore-prefixed
# names stay as the canonical in-module declarations.
node_attr = _node_attr
node_matches = _node_matches
is_snapshot_key = _is_snapshot_key

_default_schedules_seeded = False


def _seed_default_schedules_after_first_gather() -> None:
    """One-shot deferred seeding once the cache actually has nodes.

    The lifespan-time call to ``schedules.seed_default_schedules``
    runs BEFORE any gather has populated ``_cache["nodes"]``, so the
    "Prune <hostname>" sample schedule never gets created on a fresh
    install. This hook fires after the first successful
    gather that produced a non-empty node list, then sets the flag so
    we don't re-check on every subsequent gather. The schedules.seed
    helper is itself idempotent now (gates per-name), so even if this
    flag were lost the worst case is one extra existence check.

    Imported lazily because logic.schedules imports logic.gather at
    module load time — a top-level import here would create a cycle.
    """
    global _default_schedules_seeded
    if _default_schedules_seeded:
        return
    nodes = _cache.get("nodes") or {}
    if not nodes:
        return
    try:
        from logic import schedules as _sched
        node_names = sorted(set(nodes.values()))
        with db_conn() as c:
            _sched.seed_default_schedules(c, node_names)
        _default_schedules_seeded = True
    except Exception as e:  # noqa: BLE001
        print(f"[scheduler] deferred seed_default_schedules failed: {e}")


async def gather() -> None:
    """Rebuild the cache. Timed; errors inside _gather_impl surface but
    don't stop the metrics population step from running."""
    _t0 = time.monotonic()
    try:
        await _gather_impl()
    finally:
        metrics.GATHER_DURATION.observe(time.monotonic() - _t0)
        metrics.populate_from_cache(_cache)
        # Idempotent first-success seed for the prune-node sample
        # schedule. No-op once seeded; cheap when nodes are still empty.
        _seed_default_schedules_after_first_gather()


async def _gather_impl() -> None:
    """Core gather: fan out the Portainer reads, build items + stack rollups, enrich each with its remote-digest status, and repopulate the cache."""
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
        # Portainer not configured — direct-Docker nodes (if any) are the SOLE
        # backend. Merge them into the (otherwise empty) cache so the operator
        # still sees their containers / Node card.
        await _safe_merge_docker("no-portainer path")
        return
    # Per-use TUNABLE read so a Save in Admin → Portainer takes
    # effect on the next gather. Defensive fallback to legacy 60s
    # on tunable-resolver failure (keeps gather working if tuning
    # module is misconfigured).
    try:
        from logic.tuning import Tunable, tuning_int as _tuning_int
        _gather_client_to = float(_tuning_int(Tunable.GATHER_CLIENT_TIMEOUT_SECONDS))
    except (ImportError, KeyError, ValueError, TypeError):
        _gather_client_to = 60.0
    async with httpx.AsyncClient(verify=bool(portainer.VERIFY_TLS), timeout=_gather_client_to) as client:
        ep = f"/api/endpoints/{portainer.PORTAINER_ENDPOINT_ID}/docker"

        async def safe(coro, fb):
            """Await `coro` and return its result; on any failure log + return `fb`.

            CancelledError + KeyboardInterrupt propagate per the project's
            "broad except MUST carve out cancellation" rule so lifespan
            shutdown of the gather task doesn't get swallowed.
            """
            try:
                return await coro
            except (asyncio.CancelledError, KeyboardInterrupt):
                raise
            except Exception as gather_err:  # noqa: BLE001
                print(f"[gather] {gather_err}")
                return fb

        # Parallel fan-out — closes the doc/code drift the project conventions
        # already documents at the "Data gathering flow" section.
        # `safe()` swallows exceptions per call so `asyncio.gather`
        # can run with `return_exceptions=False` (no exception can
        # escape a `safe(...)` task). Net cost: max-of-five
        # instead of sum-of-five round-trips per gather.
        services, containers, tasks, nodes, stacks_list = await asyncio.gather(
            safe(portainer.pg(client, f"{ep}/services"), []),
            safe(portainer.pg(client, f"{ep}/containers/json?all=1"), []),
            safe(portainer.pg(client, f"{ep}/tasks"), []),
            safe(portainer.pg(client, f"{ep}/nodes"), []),
            safe(portainer.pg(client, "/api/stacks"), []),
        )

        node_map = {n["ID"]: n["Description"]["Hostname"] for n in nodes}
        stack_by_name = {s["Name"]: s for s in stacks_list}

        # Per-node capacity + oldest-running-task timestamp. Keyed by
        # hostname so the frontend doesn't need to join NodeID → host
        # separately. Structure shipped in _cache["nodes_info"]:
        # {
        #   hostname: {
        #     id:            node UUID
        #     role:          "manager" | "worker"
        #     state:         "ready" | "down" | ... (Swarm Status.State)
        #     availability:  "active" | "pause" | "drain"
        #     cpu_cores:     int  (Description.Resources.NanoCPUs / 1e9)
        #     mem_bytes:     int  (Description.Resources.MemoryBytes)
        #     os:            e.g. "linux"
        #     arch:          e.g. "x86_64"
        #     engine:        docker engine version
        #     oldest_running_ts: epoch seconds of the oldest task whose
        #                        Status.State='running' on this node —
        #                        serves as a per-node uptime proxy
        #                        (Docker doesn't expose host boot time).
        #   }
        # }
        nodes_info: dict[str, dict] = {}
        for n in nodes:
            desc = n.get("Description") or {}
            spec = n.get("Spec") or {}
            status = n.get("Status") or {}
            res = desc.get("Resources") or {}
            plat = desc.get("Platform") or {}
            host = desc.get("Hostname")
            if not host or not isinstance(host, str):
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
                "id": n.get("ID"),
                "role": spec.get("Role"),
                "state": status.get("State"),
                "availability": spec.get("Availability"),
                # NanoCPUs is in billionths of a core. Round to the nearest
                # whole core — these values are always clean multiples in
                # practice (Docker reports them straight from the kernel).
                "cpu_cores": nanocpus // 1_000_000_000 if nanocpus else 0,
                "nano_cpus": nanocpus,
                "mem_bytes": int(res.get("MemoryBytes") or 0),
                "os": plat.get("OS"),
                "arch": plat.get("Architecture"),
                "engine": ((desc.get("Engine") or {}).get("EngineVersion")),
                "ip": ip_only or None,
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
        # noinspection PyShadowingNames
        async def _one_df(target_host: str):
            """Fetch /system/df for ONE Docker host via Portainer-agent routing.

            Returns ``(host, total_bytes)``. Any per-node failure swallows
            to ``(host, 0)`` so one bad daemon doesn't blank the Nodes view.
            CancelledError + KeyboardInterrupt propagate per the project's
            "broad except MUST carve out cancellation" rule.
            """
            try:
                r = await client.get(
                    f"{portainer.PORTAINER_URL}{ep}/system/df",
                    headers=portainer.headers(agent_target=target_host),
                )
                if r.status_code >= 400:
                    return target_host, 0
                j = r.json() or {}
                df_total = int(j.get("LayersSize") or 0)
                for cont in (j.get("Containers") or []):
                    df_total += int(cont.get("SizeRw") or 0)
                for v in (j.get("Volumes") or []):
                    usage = (v.get("UsageData") or {}).get("Size", 0)
                    if isinstance(usage, (int, float)) and usage > 0:
                        df_total += int(usage)
                for bc in (j.get("BuildCache") or []):
                    df_total += int(bc.get("Size") or 0)
                return target_host, df_total
            except (asyncio.CancelledError, KeyboardInterrupt):
                raise
            except Exception as df_err:  # noqa: BLE001
                print(f"[gather] /system/df for {target_host}: {df_err}")
                return target_host, 0

        df_hosts = [h for h, info in nodes_info.items()
                    if info.get("state") == "ready"]
        if df_hosts:
            df_results = await asyncio.gather(
                *(_one_df(h) for h in df_hosts),
            )
            for host, total in df_results:
                if host in nodes_info:
                    nodes_info[host]["docker_disk_bytes"] = total

        # Host-stats integration — surfaces real host disk / memory /
        # uptime that Portainer doesn't expose. ``host_stats_source`` is
        # a CSV so operators can enable multiple providers that merge
        # into one picture per host:
        # ""                                → none
        # "beszel" / "node_exporter" / "pulse" / "webmin" → single
        # "beszel,pulse,node_exporter,webmin" → merged, best-of
        # Merge order runs providers in increasing "authority" for
        # their specialty:
        # 1. Beszel          (broad coverage, cross-platform)
        # 2. Pulse           (deep on PVE, silent on non-PVE)
        # 3. node-exporter   (deep on Linux — per-mount disks, NICs)
        # 4. Webmin          (distro-native — pending updates, mounts
        #                     per-host API, runs last as tiebreaker)
        # The ``_merge_best`` helper (below) only overwrites when the
        # new source has a meaningful value, so enabling Pulse on a
        # mixed fleet doesn't wipe Beszel's cpu/mem reading on hosts
        # Pulse doesn't know about. Legacy single-value strings stay
        # valid.
        # Use the canonical merge helpers from logic/merge.py — the
        # same module main.py imports from. Single source of truth
        # for the "fold provider into nodes_info row" merge semantics
        # so the Hosts endpoint and the gather flow stay byte-
        # identical. See for the dedup rationale.
        from logic.merge import is_meaningful as _meaningful, merge_best as _merge_best
        from logic.db import get_setting, active_host_stats_providers
        from logic import beszel as _beszel
        from logic import node_exporter as _ne
        # Single helper covers the CSV-with-legacy-fallback parse —
        # see logic/db.py:active_host_stats_providers.
        active_sources = active_host_stats_providers()

        # Per-node provider-hit tracker. Drives the SPA chip in
        # the Nodes view ("3 sources" / "exporter") so the count
        # reflects what actually probed THIS node, not the global CSV
        # of enabled providers. An ``exporter_error``-only payload from
        # a probe doesn't count as a hit (the chip flips red via the
        # error path; no need to inflate the green count). Filtered
        # out of host_snapshots at save time by the leading-underscore
        # rule, so per-gather hits don't get restored from a stale
        # snapshot.
        def _provider_returned_data(provider_stats: dict) -> bool:
            """True iff the provider returned at least one meaningful host_* field."""
            if not isinstance(provider_stats, dict):
                return False
            for k, v in provider_stats.items():
                if k == "exporter_error":
                    continue
                if _meaningful(v):
                    return True
            return False

        if "beszel" in active_sources and df_hosts:
            # One HTTP call to the hub fetches every system's latest
            # snapshot. Docker hostname → Beszel ``host`` field via
            # ``beszel_aliases`` (JSON map in the settings table) so
            # operators don't have to rename a host on either side when
            # the two naturally differ (e.g. Swarm hostname
            # ``docker01`` but Beszel host ``docker.example.com``).
            # Nodes absent from the alias map fall back to identity.
            # NOTE: we match against Beszel's ``host`` (agent hostname),
            # not ``name`` (user-editable label), because ``host`` is
            # stable and typically matches what Docker reports.
            import json as _json
            hub_url = get_setting(Settings.BESZEL_HUB_URL) or ""
            ident = get_setting(Settings.BESZEL_IDENTITY) or ""
            passw = get_setting(Settings.BESZEL_PASSWORD) or ""
            verify = (get_setting(Settings.BESZEL_VERIFY_TLS, "true") or "true").lower() == "true"
            try:
                aliases = _json.loads(get_setting(Settings.BESZEL_ALIASES, "{}") or "{}")
                if not isinstance(aliases, dict):
                    aliases = {}
            except ValueError:
                aliases = {}
            result = await _beszel.probe_hub(hub_url, ident, passw, verify_tls=verify)
            err = result.get("error")
            systems = result.get("systems") or {}
            hosts_cfg = _load_hosts_config_for_gather()
            for host in df_hosts:
                if host in nodes_info:
                    if err:
                        nodes_info[host]["exporter_error"] = f"beszel: {err}"
                        continue
                    # Resolution order: explicit alias → hosts_config
                    # row's beszel_name (for short-vs-FQDN case) →
                    # bare Docker hostname. First meaningful value wins.
                    beszel_name = aliases.get(host, "")
                    if not beszel_name:
                        row = _match_hosts_row(host, hosts_cfg)
                        if row and (row.get("beszel_name") or "").strip():
                            beszel_name = row["beszel_name"].strip()
                    if not beszel_name:
                        beszel_name = host
                    stats = systems.get(beszel_name)
                    if not isinstance(stats, dict):
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
                    if _provider_returned_data(stats):
                        nodes_info[host].setdefault("_providers", []).append("beszel")

        # Pulse (rcourtman/Pulse) — Proxmox VE monitoring. Runs BETWEEN
        # Beszel and node-exporter: overwrites Beszel for PVE hosts
        # where Pulse has the authoritative view (cpu / mem / disk /
        # uptime from the hypervisor itself), but node-exporter still
        # wins if both are enabled.
        if "pulse" in active_sources and df_hosts:
            import json as _json
            from logic import pulse as _pulse
            pulse_url = get_setting(Settings.PULSE_URL) or ""
            pulse_token = get_setting(Settings.PULSE_TOKEN) or ""
            pulse_verify = (get_setting(Settings.PULSE_VERIFY_TLS, "true")
                            or "true").lower() == "true"
            try:
                pulse_aliases_raw = _json.loads(
                    get_setting(Settings.PULSE_ALIASES, "{}") or "{}")
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
                pulse_name = pulse_aliases_raw.get(host, "")
                if not pulse_name:
                    row = _match_hosts_row(host, _load_hosts_config_for_gather())
                    if row and (row.get("pulse_name") or "").strip():
                        pulse_name = row["pulse_name"].strip()
                if not pulse_name:
                    pulse_name = host
                stats = _pulse.lookup(p_hosts, pulse_name)
                if stats is None:
                    continue  # not a PVE node — legit miss, no error
                _merge_best(nodes_info[host], stats)
                if _provider_returned_data(stats):
                    nodes_info[host].setdefault("_providers", []).append("pulse")

        # SNMP — sixth provider. Per-host probe (no central hub).
        # Slots BETWEEN Pulse and Beszel in the merge order: SNMP carries
        # coarser data than Beszel/NE/Webmin (no per-mount disk on most
        # embedded gear, no kernel/arch reporting, only standard MIB-II
        # + Host Resources MIB), so the unix-style providers should win
        # on hosts where they ALSO have data. SNMP's value is on
        # devices that none of the others can reach: managed switches,
        # routers, UPSes, network printers. Resolution order mirrors
        # Webmin: per-host alias from `snmp_aliases` → curated row's
        # `snmp_name` → bare Docker hostname. Per-host overrides on
        # `hosts_config[].snmp` (community / version / port / v3 keys)
        # win over the global defaults.
        if "snmp" in active_sources and df_hosts:
            from logic import snmp as _snmp
            from logic import tuning as _tuning
            from logic.tuning import Tunable
            default_community = get_setting(Settings.SNMP_DEFAULT_COMMUNITY) or "public"
            default_version = (get_setting(Settings.SNMP_DEFAULT_VERSION) or "v2c").strip().lower()
            try:
                default_port = _tuning.tuning_int(Tunable.SNMP_DEFAULT_PORT)
            except (TypeError, ValueError):
                default_port = 161
            v3_user = get_setting(Settings.SNMP_V3_USER) or ""
            v3_auth_key = get_setting(Settings.SNMP_V3_AUTH_KEY) or ""
            v3_priv_key = get_setting(Settings.SNMP_V3_PRIV_KEY) or ""
            try:
                snmp_aliases_raw = json.loads(get_setting(Settings.SNMP_ALIASES, "{}") or "{}")
                if not isinstance(snmp_aliases_raw, dict):
                    snmp_aliases_raw = {}
            except ValueError:
                snmp_aliases_raw = {}
            snmp_hosts_cfg = _load_hosts_config_for_gather()
            # per-tick reads of probe-timeout + concurrency-cap tunables.
            snmp_timeout = float(_tuning.tuning_int(Tunable.SNMP_PROBE_TIMEOUT_SECONDS))
            snmp_sem = asyncio.Semaphore(_tuning.tuning_int(Tunable.SNMP_CONCURRENCY))

            # noinspection PyShadowingNames
            async def _one_snmp(h: str):
                snmp_row = _match_hosts_row(h, snmp_hosts_cfg)
                # per-host opt-in gate. Skip when the row lacks
                # `snmp.enabled === True` so disabled hosts (and the
                # default) don't fan out probes.
                _row_snmp_raw = snmp_row.get("snmp") if isinstance(snmp_row, dict) else None
                row_snmp: dict = _row_snmp_raw if isinstance(_row_snmp_raw, dict) else {}
                # `is not True` is intentional — rejects truthy non-bool values
                # noinspection PySimplifyBooleanCheck,PyComparisonWithCallableTrueFalse
                if row_snmp.get("enabled") is not True:
                    return h, None
                # HARD-GATE on alias OR snmp_name. Bare-`h` fallthrough
                # was a perf cliff: fleet-enable on a 200-host fleet fanned
                # out 200 SNMP probes, ~all-but-mapped of which timed out.
                target_host = (
                    snmp_aliases_raw.get(h)
                    or (snmp_row.get("snmp_name") if isinstance(snmp_row, dict) else "")
                    or ""
                )
                target_host = (target_host or "").strip()
                if not target_host:
                    return h, None
                community = (row_snmp.get("community") or "").strip() or default_community
                version = (row_snmp.get("version") or "").strip().lower() or default_version
                try:
                    port = int(row_snmp.get("port") or default_port)
                except (TypeError, ValueError):
                    port = default_port
                row_v3_user = (row_snmp.get("v3_user") or "").strip() or v3_user
                row_v3_auth = (row_snmp.get("v3_auth_key") or "").strip() or v3_auth_key
                row_v3_priv = (row_snmp.get("v3_priv_key") or "").strip() or v3_priv_key
                # Per-host walk_concurrency override (server-class BMCs
                # like Dell iDRAC handle parallel queries fine and need
                # > 1 to fit pysnmp v7's per-walk overhead inside the
                # probe budget; safety-floor concurrency=1 stays the
                # default for low-power embedded snmpd's).
                _row_walk_conc_raw = row_snmp.get("walk_concurrency")
                row_walk_conc: Optional[int] = None
                if _row_walk_conc_raw:
                    try:
                        row_walk_conc = int(str(_row_walk_conc_raw))
                    except (TypeError, ValueError):
                        row_walk_conc = None
                # Per-host vendor MIB selector. None = auto-detect from
                # sysDescr at probe time.
                row_vendors_raw = row_snmp.get("vendors")
                row_vendors = (
                    set(row_vendors_raw)
                    if isinstance(row_vendors_raw, list) and row_vendors_raw
                    else None
                )
                async with snmp_sem:
                    snmp_result = await _snmp.probe_snmp(
                        target_host,
                        community=community,
                        version=version,
                        port=port,
                        v3_user=row_v3_user,
                        v3_auth_key=row_v3_auth,
                        v3_priv_key=row_v3_priv,
                        active_sources=active_sources,
                        timeout=snmp_timeout,
                        walk_concurrency=row_walk_conc,
                        vendors=row_vendors,
                    )
                if snmp_result.get("error") and not snmp_result.get("hosts"):
                    return h, {"exporter_error": f"snmp: {snmp_result['error']}"}
                hosts_map = snmp_result.get("hosts") or {}
                if not hosts_map:
                    return h, None
                snmp_stats = next(iter(hosts_map.values()))
                return h, snmp_stats

            snmp_results = await asyncio.gather(*(
                _one_snmp(h) for h in df_hosts
            ))
            for host, stats in snmp_results:
                if host not in nodes_info or not stats:
                    continue
                _merge_best(nodes_info[host], stats)
                if _provider_returned_data(stats):
                    nodes_info[host].setdefault("_providers", []).append("snmp")

        # node-exporter runs AFTER beszel + pulse when enabled, so its
        # richer Linux-native fields (per-mount disks via node_filesystem_*,
        # NIC list via node_network_info, detailed kernel / arch from
        # node_uname_info) overwrite the earlier providers where they
        # overlap. Fields only provided by Beszel/Pulse (e.g. their
        # status strings) are preserved by ``_merge_best``'s
        # _meaningful() guard — empty / zero / missing values from a
        # later provider don't clobber a meaningful earlier value.
        # (Comment previously said "the dict.update" but the actual
        # call is `_merge_best`; same merge semantics, accurate name.)
        if "node_exporter" in active_sources and df_hosts:
            tpl = get_setting(Settings.NODE_EXPORTER_URL_TEMPLATE, "http://{host}:9100/metrics") \
                  or "http://{host}:9100/metrics"
            # Per-host URL overrides for nodes where the template's {host}
            # substitution can't reach the exporter (DNS, alternate IP,
            # different port, etc.). Operator edits this JSON via the
            # Host stats settings panel.
            overrides_raw = get_setting(Settings.NODE_EXPORTER_OVERRIDES, "{}") or "{}"
            try:
                overrides = json.loads(overrides_raw)
                if not isinstance(overrides, dict):
                    overrides = {}
            except (ValueError, TypeError):
                overrides = {}
            ne_hosts_cfg = _load_hosts_config_for_gather()
            # Per-use read so Admin → Config edits land on the next gather
            # without a restart. Defensive fallback to legacy 10s if the
            # tunable resolver raises (corrupt DB state).
            try:
                from logic.tuning import Tunable as _Tunable, tuning_int as _tuning_int
                _ne_to = float(_tuning_int(_Tunable.NODE_EXPORTER_PROBE_TIMEOUT_SECONDS))
            except (ImportError, KeyError, ValueError, TypeError):
                _ne_to = 10.0
            async with httpx.AsyncClient(verify=False, timeout=_ne_to) as ne_client:
                # noinspection PyShadowingNames
                async def _ne_probe(h):
                    # Resolution order for the target URL:
                    # 1. explicit per-host override from the overrides map
                    # 2. hosts_config row's `ne_url` (lets
                    #    operators curate the exporter URL per host
                    #    without touching the global template)
                    # 3. template with {host} + {ip} substitution
                    # The template supports both placeholders so mixed
                    # strings like "http://{host}.example.com:9100/metrics"
                    # still work when we fall through.
                    info = nodes_info.get(h) or {}
                    ip = str(info.get("ip") or "")
                    url = overrides.get(h) or ""
                    if not url:
                        ne_row = _match_hosts_row(h, ne_hosts_cfg)
                        if isinstance(ne_row, dict) and (ne_row.get("ne_url") or "").strip():
                            url = ne_row["ne_url"].strip()
                    if not url:
                        url = tpl.replace("{host}", h).replace("{ip}", ip)
                    return h, await _ne.probe_node(ne_client, url)

                results = await asyncio.gather(
                    *(_ne_probe(h) for h in df_hosts),
                )
                for host, stats in results:
                    if host in nodes_info:
                        _merge_best(nodes_info[host], stats)
                        if _provider_returned_data(stats):
                            nodes_info[host].setdefault("_providers", []).append("node_exporter")

        # Webmin runs LAST (most-specific). Supplies distro-native data
        # the other providers can't see — pending package updates, per-
        # mount filesystems via Miniserv's `mount` module, NIC list via
        # `net`. Skipped for hosts with no webmin URL configured so
        # hosts-without-Webmin keep working unchanged.
        if "webmin" in active_sources and df_hosts:
            from logic import webmin as _webmin
            user = get_setting(Settings.WEBMIN_USER) or ""
            passw = get_setting(Settings.WEBMIN_PASSWORD) or ""
            webmin_verify = (get_setting(Settings.WEBMIN_VERIFY_TLS, "false")
                             or "false").lower() == "true"
            try:
                webmin_aliases = json.loads(
                    get_setting(Settings.WEBMIN_ALIASES, "{}") or "{}"
                )
                if not isinstance(webmin_aliases, dict):
                    webmin_aliases = {}
            except ValueError:
                webmin_aliases = {}

            webmin_hosts_cfg = _load_hosts_config_for_gather()

            # noinspection PyShadowingNames
            async def _one_webmin(h: str):
                url = webmin_aliases.get(h) or ""
                if not url:
                    # fallback — check hosts_config for a webmin_url.
                    # Not every hosts_config row carries one; when blank
                    # the existing "skip this host" behaviour wins.
                    webmin_row = _match_hosts_row(h, webmin_hosts_cfg)
                    if webmin_row:
                        url = (webmin_row.get("webmin_url") or "").strip()
                if not url:
                    return h, None
                webmin_result = await _webmin.probe_webmin(
                    url, user, passw,
                    verify_tls=webmin_verify,
                    active_sources=active_sources,
                )
                if webmin_result.get("error") and not webmin_result.get("hosts"):
                    return h, {"exporter_error": f"webmin: {webmin_result['error']}"}
                hosts_map = webmin_result.get("hosts") or {}
                if not hosts_map:
                    return h, None
                webmin_stats = next(iter(hosts_map.values()))
                return h, webmin_stats

            webmin_results = await asyncio.gather(*(
                _one_webmin(h) for h in df_hosts
            ))
            for host, stats in webmin_results:
                if host not in nodes_info or not stats:
                    continue
                _merge_best(nodes_info[host], stats)
                if _provider_returned_data(stats):
                    nodes_info[host].setdefault("_providers", []).append("webmin")

        # Ping — fifth provider. LAST in the merge order because
        # its data is the coarsest (reachability + RTT only); a richer
        # provider's CPU% / mem / disk values must never be overwritten
        # by Ping's empty-handed merge. Per-host opt-in via
        # ``hosts_config[].ping.enabled`` — most operators don't want
        # OmniGrid TCP-syncing every router by default. The merge here
        # surfaces the LAST stored sample's signal into ``nodes_info``
        # so the SPA's row chip gets a value without waiting on the
        # sampler's next tick.
        if "ping" in active_sources and df_hosts:
            from logic import ping_sampler as _ping_sampler
            from logic import ping as _ping
            hosts_cfg = _load_hosts_config_for_gather()
            for host in df_hosts:
                if host not in nodes_info:
                    continue
                row = _match_hosts_row(host, hosts_cfg)
                if not row:
                    continue
                _pcfg_raw = row.get("ping")
                pcfg: dict = _pcfg_raw if isinstance(_pcfg_raw, dict) else {}
                if not pcfg.get("enabled"):
                    continue
                hid = (row.get("id") or "").strip()
                if not hid:
                    continue
                # Read the most recent ping_samples row — the live probe
                # is owned by the sampler. This branch is read-only:
                # it folds the sampler's last result into nodes_info so
                # operators see the chip immediately after enabling the
                # provider, without waiting up to one tick.
                recent = _ping_sampler.last_samples(hid, limit=1)
                if not recent:
                    continue
                last = recent[0]
                stats = _ping.to_host_stats({
                    "alive": last.get("alive"),
                    "rtt_ms": last.get("rtt_ms"),
                    "loss_pct": last.get("loss_pct"),
                })
                if stats:
                    _merge_best(nodes_info[host], stats)
                    if _provider_returned_data(stats):
                        nodes_info[host].setdefault("_providers", []).append("ping")

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
            from collections import Counter as _Counter
            appearances = _Counter()
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
            # Per-use TUNABLE read so a Save in Admin → Portainer takes
            # effect on the next orphan-probe pass. Defensive fallback
            # to legacy 3s on tunable-resolver failure.
            try:
                from logic.tuning import Tunable as _Tunable, tuning_int as _tuning_int
                _orphan_probe_to = float(_tuning_int(_Tunable.GATHER_ORPHAN_PROBE_TIMEOUT_SECONDS))
            except (ImportError, KeyError, ValueError, TypeError):
                _orphan_probe_to = 3.0

            async def _probe_one(probe_cid: str) -> tuple[str, Optional[str]]:
                """Try each Swarm node in turn for `probe_cid`; first 200 wins.

                Returns ``(probe_cid, hostname)`` on hit or ``(probe_cid, None)``
                if no node has the container. Per-attempt exceptions are
                swallowed so one slow daemon doesn't black-hole the probe.
                """
                for hn in hostnames:
                    try:
                        r = await client.get(
                            f"{portainer.PORTAINER_URL}{ep}/containers/{probe_cid}/json",
                            headers=portainer.headers(agent_target=hn),
                            timeout=_orphan_probe_to,
                        )
                        if r.status_code == 200:
                            return probe_cid, hn
                    except (httpx.HTTPError, OSError):
                        continue
                return probe_cid, None

            sem = asyncio.Semaphore(portainer.stats_concurrency())

            async def _probe_bounded(bounded_cid: str):
                """Semaphore-bounded wrapper around `_probe_one`."""
                async with sem:
                    return await _probe_one(bounded_cid)

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
                    container_node_by_id[str(c["Id"])] = str(candidate)
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
                containers_by_service.setdefault(str(sid), []).append(c)

        # Cache image-inspect results within this gather so services sharing an
        # image don't trigger N image-inspect calls.
        image_digest_cache: dict[str, Optional[str]] = {}
        # image_id -> OCI app version label (or "" when none). Populated as a
        # side-effect of _digest_for_image_id (which already inspects the image)
        # so service items can resolve a version without an extra round-trip.
        image_version_cache: dict[str, str] = {}

        async def _digest_for_image_id(image_id: str) -> Optional[str]:
            if not image_id:
                return None
            if image_id in image_digest_cache:
                return image_digest_cache[image_id]
            try:
                img_doc = await portainer.pg(client, f"{ep}/images/{image_id}/json")
                # Cache the image's OCI version label while we have the doc open.
                image_version_cache[image_id] = _extract_app_version(
                    (img_doc.get("Config") or {}).get("Labels") or {})
                for repo_digest in img_doc.get("RepoDigests") or []:
                    if "@" in repo_digest:
                        digest = repo_digest.split("@", 1)[1]
                        image_digest_cache[image_id] = digest
                        return digest
            except (httpx.HTTPError, OSError, ValueError) as digest_err:
                print(f"[digest-fallback] {image_id[:12]}: {digest_err}")
            image_digest_cache[image_id] = None
            return None

        with db_conn() as c:
            ignores = [dict(r) for r in c.execute("SELECT * FROM ignores").fetchall()]

        def is_ignored(image, ig_stack):
            """True if `image` or `ig_stack` matches any row in the ignores table."""
            for ig in ignores:
                p = ig["pattern"]
                if ig["kind"] == "image" and p and p in (image or ""):
                    return True
                if ig["kind"] == "stack" and p and p == (ig_stack or ""):
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
                node_id = t.get("NodeID")
                st = t.get("Status") or {}
                state = (st.get("State") or "").lower()
                # Skip tasks Swarm hasn't actually PLACED on a node yet — no
                # NodeID AND still in a pre-run / unscheduled state
                # (new / pending / allocated / assigned / accepted / preparing).
                # These are transient or unschedulable task records, NOT real
                # placements; without this filter they rendered as phantom
                # "? — pending" rows in the drawer's Placement list alongside
                # the genuinely-running nodes. A placed-but-pending task (has a
                # NodeID) still shows, and a genuinely-failed task
                # (rejected / failed) still shows so the failure is visible.
                if not node_id and state in (
                        "", "new", "pending", "allocated", "assigned",
                        "accepted", "preparing"):
                    continue
                placements.append({
                    "node": node_map.get(node_id, "?"),
                    "state": st.get("State"),
                    "err": st.get("Err"),
                })

            # `task_error` + `task_history` — surface task-level failures
            # so the SPA can render them in the item drawer without making
            # the user SSH into the manager and run `docker service ps
            # --no-trunc`. Common failure modes: VXLAN sandbox-join
            # errors after a network reconfig, image pull failures,
            # placement constraint mismatches, mounted-volume errors.
            #
            # `task_error` is the most-recent non-empty `Status.Err` from
            # any failed/rejected task, picked by `Status.Timestamp` so
            # the freshest error wins. `task_history` captures up to the
            # last 10 failed attempts (timestamp + state + node + err)
            # so the user can see whether the failure is sticky
            # (same error every retry → root-cause is environmental,
            # e.g. missing secret) vs flapping (different errors each
            # time → likely a network / scheduling race).
            failed_tasks = []
            for t in svc_tasks:
                st = t.get("Status") or {}
                err = (st.get("Err") or "").strip()
                state = (st.get("State") or "").lower()
                # Surface tasks in genuinely-failed states OR with a
                # non-empty Err string. State 'rejected' / 'failed' /
                # 'orphaned' are unambiguous; 'shutdown' often carries
                # a benign error (replaced by newer task during a
                # service-update rolling restart — exit 255 from
                # SIGKILL is the canonical pattern). Keep shutdown
                # tasks in the history so the operator can drill into
                # them via "Show recent failed attempts", but flag
                # them so the "Latest task error" headline computation
                # below can prefer a real failure over a benign
                # shutdown-replacement record.
                if not err and state not in ("rejected", "failed", "orphaned"):
                    continue
                ts_raw = st.get("Timestamp") or t.get("CreatedAt") or ""
                failed_tasks.append({
                    "node": node_map.get(t.get("NodeID"), "?"),
                    "state": st.get("State"),
                    "err": err,
                    "ts": ts_raw,
                    "ts_epoch": _parse_docker_ts(ts_raw) or 0,
                    # Mark shutdown-with-err records as benign so the
                    # SPA's headline "Latest task error" panel doesn't
                    # alarm on rolling-update shutdowns when the
                    # current task is running healthy.
                    "benign": state == "shutdown",
                })
            failed_tasks.sort(key=lambda x: x.get("ts_epoch") or 0, reverse=True)
            task_history = failed_tasks[:10]
            # `task_error` is the headline failure surfaced in the
            # drawer. Prefer a NON-benign failure (rejected / failed
            # / orphaned, or shutdown-with-err only when EVERY entry
            # is benign — meaning we have nothing more meaningful).
            # This suppresses the "Latest task error: non-zero exit
            # (255)" headline on healthy services that recently
            # rolled a service-update.
            task_error = next(
                (ft["err"] for ft in failed_tasks if ft.get("err") and not ft.get("benign")),
                "",
            )

            if desired == 0:
                health = "offline"
            elif running == 0:
                health = "offline"
            elif running < desired:
                health = "degraded"
            else:
                health = "healthy"

            # Running app version. The image tag is often uninformative
            # (everything pinned to :latest), so surface the OCI version label:
            # try the service's running container's Labels (Docker merges the
            # image's labels in), then the image-version cache (filled if we
            # inspected the image for a digest above), then the service /
            # ContainerSpec deploy labels. Zero extra round-trips.
            svc_app_version = ""
            for _vc in containers_by_service.get(svc["ID"], []):
                svc_app_version = _extract_app_version(_vc.get("Labels") or {})
                if svc_app_version:
                    break
                _vid = _vc.get("ImageID") or _vc.get("Image")
                if _vid and image_version_cache.get(_vid):
                    svc_app_version = image_version_cache[_vid]
                    break
            if not svc_app_version:
                svc_app_version = (_extract_app_version(cs.get("Labels") or {})
                                   or _extract_app_version(labels))

            items.append({
                "id": f"svc:{svc['ID'][:12]}",
                "raw_id": svc["ID"],
                "name": spec.get("Name", ""),
                "type": "service",
                "image": image_name_tag,
                "app_version": svc_app_version,
                "tag": registry.tag_of(image_name_tag),
                "current_digest": current_digest,
                "stack": stack_name,
                "stack_id": stack.get("Id") if isinstance(stack, dict) else None,
                "replicas": {"desired": desired, "running": running},
                "placements": placements,
                "task_error": task_error,
                "task_history": task_history,
                "health": health,
                "state": "running" if running > 0 else "stopped",
                "removable": False,
                "hub_link": registry.hub_link(image_name_tag),
                "ignored": is_ignored(image_name_tag, stack_name),
                "created": spec.get("CreatedAt") or svc.get("CreatedAt"),
                "updated": spec.get("UpdatedAt") or svc.get("UpdatedAt"),
                "ports": _extract_service_ports(svc),
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

            # Resolve the real node BEFORE the image-inspect so the
            # inspect call can be agent-targeted at the correct
            # Docker daemon. Without this, plain compose containers
            # running on worker nodes (e.g. a sidecar agent on a
            # worker node) had their image-inspect call routed to
            # the manager's Docker daemon — which doesn't have the
            # image, returns 404 — exception caught + swallowed,
            # `current_digest` stayed None, and the row reported
            # status=unknown forever even though the remote_digest
            # probe succeeded. Same `X-PortainerAgent-Target` rule
            # that gates `_do_update_container` / `_do_restart_container`
            # / `_do_remove_container`. Priority order (authoritative
            # first):
            #   1. `com.docker.swarm.node.id` label — Swarm stamps
            #      every managed container with this; authoritative.
            #   2. Swarm task-ID → NodeID via task_node_by_id — older
            #      Swarm versions sometimes lack the node-id label.
            #   3. Per-node agent-targeted container sweep
            #      (`container_node_by_id`) — only signal we have for
            #      plain compose containers on worker nodes.
            #   4. Fallback "local" — single-node / non-agent setups
            #      where we genuinely can't tell.
            node_id_label = labels.get("com.docker.swarm.node.id")
            node_name = node_map.get(str(node_id_label)) if node_id_label else None
            if not node_name:
                swarm_task_id = labels.get("com.docker.swarm.task.id")
                node_name = task_node_by_id.get(str(swarm_task_id)) if swarm_task_id else None
            if not node_name:
                node_name = container_node_by_id.get(str(cont["Id"]))
            if not node_name:
                node_name = "local"

            current_digest = None
            cont_app_version = ""
            try:
                # `headers(agent_target=...)` skips the
                # X-PortainerAgent-Target header for "local" / "" /
                # "?" so passing the resolved node_name is safe in
                # all cases (single-node fallback included).
                img = await portainer.pg(
                    client,
                    f"{ep}/images/{cont['ImageID']}/json",
                    agent_target=node_name,
                )
                # OCI version label from the image (authoritative for the
                # running version when the tag is just :latest).
                cont_app_version = _extract_app_version(
                    (img.get("Config") or {}).get("Labels") or {})
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
            except (httpx.HTTPError, OSError, ValueError, KeyError):
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

            # Fall back to the container's own merged Labels when the image
            # inspect didn't yield a version (404 on a purged orphan image, etc.).
            if not cont_app_version:
                cont_app_version = _extract_app_version(labels)

            items.append({
                "id": f"ctn:{cont['Id'][:12]}",
                "raw_id": cont["Id"],
                "name": name,
                "type": "orphan" if is_swarm_task else "container",
                "app_version": cont_app_version,
                "image": image_ref,
                "tag": registry.tag_of(image_ref),
                "current_digest": current_digest,
                "stack": compose_project,
                "stack_id": stack.get("Id") if isinstance(stack, dict) else None,
                "replicas": {"desired": 1, "running": 1 if state == "running" else 0},
                "placements": [{"node": node_name, "state": state}],
                "node": node_name,
                "health": health,
                "state": state,
                "removable": health == "offline",
                "hub_link": registry.hub_link(image_ref),
                "ignored": is_ignored(image_ref, compose_project),
                "created": cont.get("Created"),
                "ports": _extract_container_ports(cont),
            })

        # --- Enrich with remote digests ---
        sem = asyncio.Semaphore(portainer.registry_concurrency())

        async def enrich(enrich_item):
            """Resolve remote digest for one item + classify update status."""
            async with sem:
                remote = await registry.get_remote_digest(client, enrich_item["image"])
            enrich_item["remote_digest"] = remote
            if enrich_item["ignored"]:
                enrich_item["status"] = "ignored"
            elif not enrich_item["current_digest"]:
                enrich_item["status"] = "unknown"
            elif not remote:
                enrich_item["status"] = "error"
            elif enrich_item["current_digest"] == remote:
                enrich_item["status"] = "up-to-date"
            else:
                enrich_item["status"] = "update"
            return enrich_item

        items = list(await asyncio.gather(*(enrich(i) for i in items)))

        # Stack-grouped view — the per-stack roll-up counts (`updates` =
        # actionable live updates only; `updates_offline` for stale-but-offline
        # orphans; errors / unknowns / uptodate / offline / degraded) are built
        # by the shared `_group_items_into_stacks` helper so the direct-Docker
        # merge re-groups the COMBINED item list with identical semantics.
        items.sort(key=lambda i: (i.get("name") or "").lower())
        # Snapshot fallback — fill missing host_* fields from the
        # previous gather's persisted state so a single provider going
        # down doesn't blank the whole row. The fallback marks each
        # filled field in `_stale_fields` so the UI can dim the
        # corresponding bar / value. Live values from this gather take
        # precedence (only MISSING fields are filled).
        try:
            apply_host_snapshot_fallback(nodes_info)
        except Exception as e:  # noqa: BLE001
            print(f"[gather] snapshot fallback failed: {e}")
        # Persist the just-built nodes_info so the NEXT gather (or a
        # restart) has a fresh fallback target. We snapshot the full
        # merged blob, including any field that was itself a fallback —
        # successive provider failures shouldn't cause the snapshot to
        # decay. Offloaded to a worker thread because the body
        # JSON-encodes ~10-30 KB per host (1.5 MB on a 50-host fleet)
        # + runs an `executemany` UPSERT — both are sync SQLite + CPU
        # work that would stall the event loop in the gather context.
        # Same pattern as host_metrics_sampler's prune offload.
        try:
            n_snap = await asyncio.to_thread(save_host_snapshots, nodes_info)
            if n_snap:
                print(f"[gather] snapshot wrote {n_snap} host rows")
        except (asyncio.CancelledError, KeyboardInterrupt):
            raise
        except Exception as e:  # noqa: BLE001
            print(f"[gather] save_host_snapshots failed: {e}")
        _cache["items"] = items
        _cache["nodes"] = node_map
        _cache["nodes_info"] = nodes_info
        _cache["task_node_by_id"] = task_node_by_id
        _cache["container_node_by_id"] = container_node_by_id
        _cache["stacks"] = _group_items_into_stacks(items)
        _cache["ts"] = time.time()
        # Fresh gather just landed — drop the boot-time stale marker so
        # the cache reads as authoritative. Per-row `_stale` flags on
        # `items` / `stacks` items are NOT carried over since the cache
        # was wholesale-replaced above.
        _cache.pop("_stale", None)
        # Direct-Docker nodes — append any Portainer-less
        # Docker node's containers + Node card, tagged backend="docker:<id>",
        # and re-group the combined item list. Runs BEFORE the items-snapshot
        # write so the instant-paint snapshot includes them. No-op (beyond the
        # backend stamp) when no Docker node is configured.
        await _safe_merge_docker("main path")
        # Persist the just-built cache so a container restart can boot
        # with a fully populated `_cache` and serve the FIRST
        # `/api/items` request instantly. Single-row table — replaces
        # the prior snapshot wholesale, so removed/ignored items
        # auto-clear on the next successful gather. Failures are logged
        # + swallowed inside the helper; the gather must never break on
        # a snapshot write.
        try:
            save_items_snapshot()
        except Exception as e:  # noqa: BLE001
            print(f"[gather] save_items_snapshot failed: {e}")
