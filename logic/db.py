"""SQLite database helpers.

Just the infrastructure: a connection context manager and KV helpers for
the ``settings`` table. Table creation (``init_db()``) stays in main.py
as the boot orchestrator — each logic module that owns tables exposes
its own ``init_schema(conn)`` hook there.

The path is read from ``DB_PATH`` at import time; parent directory is
created on import so callers don't have to. ``DB_PATH`` is REQUIRED —
main.py calls ``load_dotenv`` before importing this module. When the
value is missing we DON'T raise at import time (that would crash-loop
the container and hide the error behind Swarm restart noise) — instead
we expose ``DB_PATH_ERROR`` so main.py can install a config-error
middleware that keeps the app up and shows a diagnostic page to the
operator. Any caller that opens ``db_conn()`` without a configured path
still raises loudly, so silent-default drift is not possible.

``DB_TYPE`` selects the backend; today only ``sqlite`` is supported.
Adding a new adapter means: extend ``_SUPPORTED_DB_TYPES``, branch on
``DB_TYPE`` in ``db_conn``, and (likely) split the table-create
statements in main.py:init_db() to handle dialect differences.
"""
import json
import os
import sqlite3
from contextlib import contextmanager
from typing import Any, Optional

from logic.settings_keys import Settings

_SUPPORTED_DB_TYPES = frozenset({"sqlite"})

DB_TYPE: str = (os.getenv("DB_TYPE") or "sqlite").strip().lower()

DB_PATH: Optional[str] = os.getenv("DB_PATH") or None
DB_PATH_ERROR: Optional[str] = None

if DB_TYPE not in _SUPPORTED_DB_TYPES:
    DB_PATH_ERROR = (
        f"DB_TYPE={DB_TYPE!r} is not supported. "
        f"Set DB_TYPE to one of: {', '.join(sorted(_SUPPORTED_DB_TYPES))}."
    )
elif not DB_PATH:
    DB_PATH_ERROR = (
        "DB_PATH is not set. Define it in /app/.env "
        "(e.g. DB_PATH=/app/data/omnigrid.db) and redeploy."
    )
else:
    # Create the parent dir at import (once per process). Safe on restart —
    # exist_ok. "" dirname falls back to "." so relative paths work in dev.
    _db_path_dir = os.path.dirname(DB_PATH) or "."
    os.makedirs(_db_path_dir, exist_ok=True)


@contextmanager
def db_conn():
    """Context-managed SQLite connection with Row factory.

    Commits on clean exit, closes in finally. Fine for our write volume
    (a few ops per minute); if we ever grow a hot write path we can
    switch to WAL + autocommit, but SQLite's default is enough today.

    Raises ``RuntimeError`` (not ``sqlite3.OperationalError``) if
    ``DB_PATH`` is unset — lets the config-error middleware in main.py
    short-circuit with a readable message instead of surfacing a raw
    SQLite error on every request.
    """
    if DB_PATH_ERROR:
        raise RuntimeError(DB_PATH_ERROR)
    if DB_TYPE != "sqlite":
        # Defensive — _SUPPORTED_DB_TYPES gate at import should have
        # caught this. If it didn't, refuse to silently open SQLite for
        # a caller that asked for something else.
        raise RuntimeError(f"db_conn(): no adapter for DB_TYPE={DB_TYPE!r}")
    if DB_PATH is None:
        # Unreachable in practice — DB_PATH_ERROR is set when DB_PATH is
        # None, and we raised above. Explicit narrowing for the type
        # checker so the sqlite3.connect call below typechecks cleanly.
        raise RuntimeError("DB_PATH is None")
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def get_setting(key: str, default: str = "") -> str:
    """Read one row from the ``settings`` table, returning `default`
    when the key isn't set.
    """
    with db_conn() as c:
        r = c.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    return r["value"] if r else default


def set_setting(key: str, value: str) -> None:
    """Upsert one row into the ``settings`` table.

    Bumps `_settings_version` (a synthetic monotonic int) so the SPA
    can poll a cheap version endpoint to detect cross-tab changes
    without re-fetching the full settings blob. Excluded: the version
    row itself (avoids recursion) AND every key in
    `_SETTINGS_VERSION_EXCLUDED` — high-frequency housekeeping rows
    (Telegram listener offset advances per inbound message, swarm
    autoheal cooldown anchors, etc.) that are NOT cross-tab-relevant
    and would otherwise produce a settings-reload storm on every
    connected SPA tab. Multi-field admin Saves can call this N times
    per request — wrap in `defer_settings_version_bump()` to collapse
    the N bumps into ONE end-of-request bump (the SPA sees one
    version mismatch instead of N reloads).
    """
    with db_conn() as c:
        c.execute(
            "INSERT OR REPLACE INTO settings(key,value) VALUES (?,?)",
            (key, value),
        )
        if key == _SETTINGS_VERSION_KEY or key in _SETTINGS_VERSION_EXCLUDED:
            return
        if _settings_version_deferred_count[0] > 0:
            # A defer-context is active — accumulate one notional bump
            # and let the context exit issue a single `_bump_settings_version_in`
            # at the end. Multi-field admin Saves through `api_set_settings`
            # collapse N bumps into one this way.
            _settings_version_pending[0] = True
            return
        _bump_settings_version_in(c)


_SETTINGS_VERSION_KEY = "_settings_version"
# High-frequency housekeeping keys that should NOT bump the
# settings-version + fire a cross-tab SSE event. These are written
# many-times-per-second under normal load (Telegram listener offset,
# swarm autoheal cooldown anchors) and the value isn't operator-
# relevant — broadcasting `settings:updated` for them would force
# every connected SPA tab to re-fetch `/api/settings` + `/api/me`,
# producing a reload storm on chatty deploys. Adding a key here is
# safe iff: (a) no SPA surface reads it via `/api/settings`, (b) the
# value is timestamp / counter / offset semantics (write-mostly).
_SETTINGS_VERSION_EXCLUDED = frozenset({
    "telegram_last_update_id",
    "swarm_autoheal_last_restart_ts",
    "swarm_autoheal_last_notify_ts",
    "swarm_autoheal_last_notify_set",
    "swarm_autoheal_bootstrap_done",
})
# Single-element lists used as nullable-int / nullable-bool boxes so the
# context manager + nested-defer support work without globals + lock
# ceremony. OmniGrid runs single-process single-replica by deployment
# invariant; an asyncio re-entrant defer would still write the right
# count because the context manager's enter/exit is synchronous.
_settings_version_deferred_count = [0]
_settings_version_pending = [False]


@contextmanager
def defer_settings_version_bump():
    """Context manager that collapses N `set_setting` calls into ONE
    `_settings_version` bump on context exit.

    Usage:

        with defer_settings_version_bump():
            for k, v in fields.items():
                set_setting(k, v)
        # → exactly one version bump after the with-block exits

    Multi-field admin Saves through `api_set_settings` would otherwise
    bump the counter N times and trigger N cross-tab `settings:updated`
    SSE events, each prompting a `/api/settings` reload on every other
    tab. The defer context ensures other tabs see ONE version mismatch
    per logical operation. Nestable — only the outermost exit triggers
    the actual bump.
    """
    _settings_version_deferred_count[0] += 1
    try:
        yield
    finally:
        _settings_version_deferred_count[0] -= 1
        if _settings_version_deferred_count[0] == 0 and _settings_version_pending[0]:
            _settings_version_pending[0] = False
            try:
                with db_conn() as c:
                    _bump_settings_version_in(c)
            except (sqlite3.Error, RuntimeError, OSError):
                # Defence-in-depth: a defer-exit bump failure must NOT
                # propagate out of the context manager and break the
                # caller's request. SPA loses one cross-tab notification
                # at worst — recoverable on next poll.
                pass


def _bump_settings_version_in(c) -> None:
    """Increment `_settings_version` inside an existing connection.
    Caller is responsible for the commit (the surrounding `db_conn()`
    context manager handles it). Treats a missing row as starting from
    0 → 1; a malformed value rolls back to 0 → 1 too so corrupt state
    self-heals."""
    try:
        row = c.execute(
            "SELECT value FROM settings WHERE key=?",
            (_SETTINGS_VERSION_KEY,),
        ).fetchone()
        cur = 0
        if row and row["value"]:
            raw_val = row["value"]
            try:
                cur = int(str(raw_val))
            except (TypeError, ValueError):
                cur = 0
        c.execute(
            "INSERT OR REPLACE INTO settings(key,value) VALUES (?,?)",
            (_SETTINGS_VERSION_KEY, str(cur + 1)),
        )
    except (sqlite3.Error, RuntimeError):
        # Defence-in-depth: a version-bump failure must NOT roll back
        # the operator's actual settings write. Worst case the SPA
        # misses a cross-tab notification — recoverable on next poll.
        pass


def get_settings_version() -> int:
    """Return the current `_settings_version`. 0 when never written.
    Used by `/api/settings/version` so the SPA can detect cross-tab
    changes without re-fetching the full settings blob."""
    raw = get_setting(_SETTINGS_VERSION_KEY)
    if not raw:
        return 0
    try:
        return int(raw)
    except (TypeError, ValueError):
        return 0


# Truthy strings accepted by :func:`get_setting_bool`. The save path
# always normalises to literal "true"/"false", but DB-direct edits or
# restored backups from older schemas may carry "True", "1", "yes"
# etc. — accept those too so a stray edit doesn't silently flip a
# master toggle off.
_TRUTHY_STRINGS = frozenset({"true", "1", "yes", "y", "on"})
_FALSY_STRINGS = frozenset({"false", "0", "no", "n", "off"})


# noinspection SpellCheckingInspection
def active_host_stats_providers() -> set[str]:
    """Parse the ``host_stats_source`` setting → set of active providers.

    Single source of truth for "which host-stats providers should
    OmniGrid probe right now". Returns a set drawn from
    ``{"beszel", "node_exporter", "pulse", "webmin"}``; empty set means
    "host-stats globally off". Legacy installs that only ever set the
    ``node_exporter_enabled`` flag (pre-CSV settings) auto-fall-back to
    ``{"node_exporter"}``.

    Replaces 6 duplicate copies of the same parse block scattered
    across main.py + the gather/host_*_sampler modules. New providers
    only need to update this helper plus the validation list in
    ``api_set_settings``.
    """
    raw = (get_setting(Settings.HOST_STATS_SOURCE) or "").strip()
    if not raw:
        if (get_setting(Settings.NODE_EXPORTER_ENABLED, "false") or "false").lower() == "true":
            return {"node_exporter"}
        return set()
    out: set[str] = set()
    for token in raw.split(","):
        t = token.strip().lower()
        if t and t != "none":
            out.add(t)
    return out


from typing import Iterator


def iter_curated_hosts(*, require_enabled: bool = True) -> Iterator[dict]:
    """Yield validated ``hosts_config`` rows.

    Canonical generator over the ``hosts_config`` settings row, replacing
    the ~25-line `raw = get_setting → strip → json.loads → isinstance(list)
    → iterate → isinstance(dict) → id-empty-skip → enabled-gate` boilerplate
    that was copy-pasted across 10+ sampler / consumer sites (DUP-001).

    Each yielded row is GUARANTEED to:
      - be a ``dict`` (non-dict rows are skipped silently);
      - have a non-empty ``.get('id').strip()`` value (rows without an
        id are skipped — every downstream consumer needs the id as
        primary key anyway);
      - pass the ``enabled`` gate when ``require_enabled=True`` (the
        default — matches the canonical "ignore disabled rows" contract
        every sampler uses). Pass ``require_enabled=False`` to walk
        EVERY row (e.g. when an admin endpoint needs to surface disabled
        hosts in a count or audit context).

    Per-provider field filtering (``beszel_name`` non-empty, ``ne_url``
    set, ``snmp.enabled=True``, etc.) is the CALLER's responsibility —
    different samplers care about different fields, and a single helper
    that tried to express every per-provider variant would be a worse
    abstraction than the per-sampler one-liner that wraps this generator.

    Returns empty iterator on missing / malformed settings — forgiving
    contract matches the previous per-helper behaviour so a stale
    settings blob can't crash a lifespan task.
    """
    import json as _json

    raw = (get_setting(Settings.HOSTS_CONFIG) or "").strip()
    if not raw:
        return
    try:
        parsed = _json.loads(raw)
    except (ValueError, TypeError):
        return
    if not isinstance(parsed, list):
        return
    for row in parsed:
        if not isinstance(row, dict):
            continue
        hid = (row.get("id") or "").strip()
        if not hid:
            continue
        if require_enabled and not row.get("enabled", True):
            continue
        yield row


# noinspection DuplicatedCode
def _walk_hosts_config() -> list[dict]:
    """Walk the ``hosts_config`` JSON settings row → list of enabled
    host dicts.

    Back-compat wrapper around :func:`iter_curated_hosts` (kept because
    the four ``curated_*_hosts`` helpers below still consume a list).
    Net behaviour change vs the pre-DUP-001 shape: rows without a
    non-empty ``id`` are now filtered out here too (every downstream
    consumer rejected them anyway, so the gate just moved upstream).

    Returns empty list on missing / malformed settings — forgiving
    contract matches what the helpers had before, so a stale settings
    blob can't crash the lifespan tasks.
    """
    return list(iter_curated_hosts(require_enabled=True))


# noinspection DuplicatedCode
def curated_ne_hosts() -> list[dict]:
    """Curated ``hosts_config`` rows that the NE samplers can probe.

    Single source of truth for "which hosts have a usable node-exporter
    URL right now". Walks the JSON ``hosts_config`` setting, returns
    one ``{id, ne_url}`` row per ENABLED entry with a non-empty
    ``ne_url`` and ``id``. Replaces the byte-for-byte duplicate
    ``_load_curated_hosts`` helpers that lived in
    ``logic/host_net_sampler.py`` and ``logic/host_metrics_sampler.py``
   .

    Malformed rows (non-dict, missing id, blank ne_url) are silently
    skipped — same forgiving contract the samplers had before, so a
    stale settings blob can't crash the lifespan task.
    """
    out: list[dict] = []
    # noinspection DuplicatedCode
    for row in _walk_hosts_config():
        ne_url = (row.get("ne_url") or "").strip()
        if not ne_url:
            continue
        hid = (row.get("id") or "").strip()
        if not hid:
            continue
        out.append({"id": hid, "ne_url": ne_url})
    return out


# noinspection SpellCheckingInspection
def curated_ping_hosts() -> list[dict]:
    """Curated ``hosts_config`` rows opted-in for ping probing.

    Mirror of :func:`curated_ne_hosts` but gates on ``ping.enabled``
    rather than ``ne_url``. Returns one ``{id, host, port, transport}``
    row per ENABLED entry whose ``ping.enabled`` flag is true. Defaults
    pulled from the per-row ``ping`` sub-dict; resolution of global
    defaults (``ping_default_port`` / ``ping_use_icmp``) lives in the
    sampler so this helper stays I/O-free beyond the one settings read.

    Single-source-of-truth for "which hosts is OmniGrid ping-probing
    right now" — consumed by the sampler + the gather merge path. New
    consumers (a future debug endpoint, a UI count badge) should use
    this rather than re-walking ``hosts_config``.
    """
    out: list[dict] = []
    for row in _walk_hosts_config():
        _ping_raw = row.get("ping")
        ping_cfg: dict = _ping_raw if isinstance(_ping_raw, dict) else {}
        if not ping_cfg.get("enabled"):
            continue
        hid = (row.get("id") or "").strip()
        if not hid:
            continue
        _ssh_raw = row.get("ssh")
        ssh_cfg: dict = _ssh_raw if isinstance(_ssh_raw, dict) else {}
        host_target = (ssh_cfg.get("fqdn") or ssh_cfg.get("host") or hid).strip() or hid
        port_override = ping_cfg.get("port")
        if port_override in (None, "", 0):
            port = 0
        else:
            try:
                port = int(port_override)  # type: ignore[arg-type]
            except (TypeError, ValueError):
                port = 0
        transport_raw = (ping_cfg.get("transport") or "").strip().lower()
        transport = transport_raw if transport_raw in ("tcp", "icmp") else ""
        out.append({
            "id": hid,
            "host": host_target,
            "port": port,  # 0 = use ping_default_port
            "transport": transport,  # "" = use ping_use_icmp global
        })
    return out


# noinspection DuplicatedCode
def curated_beszel_hosts() -> list[dict]:
    """Curated ``hosts_config`` rows opted-in for Beszel — one row per
    enabled host whose ``beszel_name`` field resolves a target.

    Mirrors :func:`curated_ne_hosts` / :func:`curated_ping_hosts` /
    :func:`curated_snmp_hosts`. Public consumer interface — the
    sampler module also keeps a local `_curated_beszel_hosts()` for
    its own row-shape needs (parallel to how `host_pulse_sampler`
    and `host_webmin_sampler` keep local helpers); both walk the
    same `hosts_config` setting and apply the same gates, so the two
    paths stay in lock-step.

    Returns ``{id, beszel_name}`` per row. Caller resolves global
    Beszel hub credentials so this helper stays I/O-free beyond the
    one settings read.

    Single source of truth for "which hosts is OmniGrid Beszel-
    sampling right now" — consumed by future debug surfaces, count
    badges, and external tooling that wants to enumerate Beszel-
    tracked hosts without round-tripping through the sampler module.
    """
    out: list[dict] = []
    # noinspection DuplicatedCode
    for row in _walk_hosts_config():
        hid = (row.get("id") or "").strip()
        beszel_name = (row.get("beszel_name") or "").strip()
        if not hid or not beszel_name:
            continue
        out.append({"id": hid, "beszel_name": beszel_name})
    return out


# noinspection DuplicatedCode
def curated_snmp_hosts() -> list[dict]:
    """Curated ``hosts_config`` rows opted-in for SNMP probing.

    Mirror of :func:`curated_ne_hosts` / :func:`curated_ping_hosts` but
    gates on ``snmp.enabled === True`` AND a non-empty ``snmp_name`` (or
    a global ``snmp_aliases`` mapping that resolves the host id — caller
    layers the alias lookup on top). Per-host opt-in matches the SPA's
    contract from `enabled is True` is the read-side gate.

    Returns ``{id, snmp_name, ssh}`` per row. The caller resolves global
    SNMP defaults (community / version / port / v3 keys) so this helper
    stays I/O-free beyond the one settings read.

    Single source of truth for "which hosts is OmniGrid SNMP-probing
    right now" — consumed by the per-host probe path and the
    host_metrics_sampler's permanent-fail tracking pass.
    """
    out: list[dict] = []
    for row in _walk_hosts_config():
        _snmp_raw = row.get("snmp")
        snmp_cfg: dict = _snmp_raw if isinstance(_snmp_raw, dict) else {}
        if not snmp_cfg.get("enabled"):
            continue
        hid = (row.get("id") or "").strip()
        if not hid:
            continue
        snmp_name = (row.get("snmp_name") or "").strip()
        # snmp_aliases lookup is the caller's job — this helper stays
        # I/O-free beyond the one settings read. The shared `address`
        # field rides along so the SNMP probe path can fall through
        # to it via the canonical chain aliases → snmp_name → address
        # → SKIP. Address-only SNMP hosts (snmp.enabled=true with
        # snmp_name blank, address populated) must reach the probe
        # path or the sampler returns early and host_snmp_samples
        # never writes.
        out.append({
            "id": hid,
            "snmp_name": snmp_name,
            "address": (row.get("address") or "").strip(),
            "snmp": dict(snmp_cfg),
        })
    return out


def get_setting_bool(key: str, default: bool = False) -> bool:
    """Read a boolean settings row tolerantly.

    Falls back to ``default`` for unrecognised values so a typo
    ("ture") doesn't pretend to be False. Replaces the per-call site
    `get_setting(...).lower() == "true"` pattern that's case-fragile
    and silently treats any non-"true" string as False.
    """
    raw = get_setting(key)
    if not raw:
        return default
    s = str(raw).strip().lower()
    if s in _TRUTHY_STRINGS:
        return True
    if s in _FALSY_STRINGS:
        return False
    return default


def load_settings_json(
    key: str,
    default: Any = None,
    expected_type: Any = (list, dict),
) -> Any:
    """Read a JSON-serialised settings row tolerantly.

    Replaces the recurring four-step idiom across every operator-
    facing JSON setting:

        raw = (get_setting(KEY) or "").strip()
        if not raw: return <empty>
        try: parsed = json.loads(raw)
        except (ValueError, TypeError): return <empty>
        if not isinstance(parsed, <shape>): return <empty>
        return parsed

    Args:
        key: The settings table key to read.
        default: Returned on missing / empty / corrupt / wrong-type row.
            Caller picks the appropriate empty shape (``[]`` for list-
            valued settings, ``{}`` for dict-valued, ``None`` for
            optional).
        expected_type: One or more concrete types the parsed JSON's
            top-level value MUST be an instance of. Default accepts
            either ``list`` OR ``dict`` (the two top-level JSON
            containers). Pass a single type (``list``) to require a
            specific shape.

    Returns the parsed JSON value when valid, else ``default``. NEVER
    raises — callers can rely on the return type matching either
    ``default`` or ``expected_type``.
    """
    raw = (get_setting(key) or "").strip()
    if not raw:
        return default
    try:
        value = json.loads(raw)
    except (ValueError, TypeError):
        return default
    if not isinstance(value, expected_type):
        return default
    return value
