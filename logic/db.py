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
import os
import sqlite3
from contextlib import contextmanager
from typing import Optional


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
    os.makedirs(os.path.dirname(DB_PATH) or ".", exist_ok=True)


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
    row itself (avoids recursion). Multi-field admin Saves can call
    this N times per request — wrap in `defer_settings_version_bump()`
    to collapse the N bumps into ONE end-of-request bump (the SPA
    sees one version mismatch instead of N reloads).
    """
    with db_conn() as c:
        c.execute(
            "INSERT OR REPLACE INTO settings(key,value) VALUES (?,?)",
            (key, value),
        )
        if key == _SETTINGS_VERSION_KEY:
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
# Single-element lists used as nullable-int / nullable-bool boxes so the
# context manager + nested-defer support work without globals + lock
# ceremony. The harness is single-process single-replica per CLAUDE.md
# invariant; an asyncio re-entrant defer would still write the right
# count because the context manager's enter/exit is synchronous.
_settings_version_deferred_count = [0]
_settings_version_pending = [False]


from contextlib import contextmanager


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
            except Exception:
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
            try:
                cur = int(row["value"])
            except (TypeError, ValueError):
                cur = 0
        c.execute(
            "INSERT OR REPLACE INTO settings(key,value) VALUES (?,?)",
            (_SETTINGS_VERSION_KEY, str(cur + 1)),
        )
    except Exception:
        # Defence-in-depth: a version-bump failure must NOT roll back
        # the operator's actual settings write. Worst case the SPA
        # misses a cross-tab notification — recoverable on next poll.
        pass


def get_settings_version() -> int:
    """Return the current `_settings_version`. 0 when never written.
    Used by `/api/settings/version` so the SPA can detect cross-tab
    changes without re-fetching the full settings blob."""
    raw = get_setting(_SETTINGS_VERSION_KEY, "")
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
    raw = (get_setting("host_stats_source", "") or "").strip()
    if not raw:
        if (get_setting("node_exporter_enabled", "false") or "false").lower() == "true":
            return {"node_exporter"}
        return set()
    out: set[str] = set()
    for token in raw.split(","):
        t = token.strip().lower()
        if t and t != "none":
            out.add(t)
    return out


def curated_ne_hosts() -> list[dict]:
    """Curated ``hosts_config`` rows that the NE samplers can probe.

    Single source of truth for "which hosts have a usable node-exporter
    URL right now". Walks the JSON ``hosts_config`` setting, returns
    one ``{id, ne_url}`` row per ENABLED entry with a non-empty
    ``ne_url`` and ``id``. Replaces the byte-for-byte duplicate
    ``_load_curated_hosts`` helpers that lived in
    ``logic/host_net_sampler.py`` and ``logic/host_metrics_sampler.py``
    (CONS-001).

    Malformed rows (non-dict, missing id, blank ne_url) are silently
    skipped — same forgiving contract the samplers had before, so a
    stale settings blob can't crash the lifespan task.
    """
    import json as _json

    raw = get_setting("hosts_config", "") or ""
    if not raw.strip():
        return []
    try:
        parsed = _json.loads(raw)
    except ValueError:
        return []
    if not isinstance(parsed, list):
        return []
    out: list[dict] = []
    for row in parsed:
        if not isinstance(row, dict):
            continue
        if not row.get("enabled", True):
            continue
        ne_url = (row.get("ne_url") or "").strip()
        if not ne_url:
            continue
        hid = (row.get("id") or "").strip()
        if not hid:
            continue
        out.append({"id": hid, "ne_url": ne_url})
    return out


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
    import json as _json

    raw = get_setting("hosts_config", "") or ""
    if not raw.strip():
        return []
    try:
        parsed = _json.loads(raw)
    except ValueError:
        return []
    if not isinstance(parsed, list):
        return []
    out: list[dict] = []
    for row in parsed:
        if not isinstance(row, dict):
            continue
        if not row.get("enabled", True):
            continue
        ping_cfg = row.get("ping") if isinstance(row.get("ping"), dict) else {}
        if not ping_cfg.get("enabled"):
            continue
        hid = (row.get("id") or "").strip()
        if not hid:
            continue
        ssh_cfg = row.get("ssh") if isinstance(row.get("ssh"), dict) else {}
        host_target = (ssh_cfg.get("fqdn") or ssh_cfg.get("host") or hid).strip() or hid
        try:
            port_override = ping_cfg.get("port")
            port = int(port_override) if port_override not in (None, "", 0) else 0
        except (TypeError, ValueError):
            port = 0
        transport_raw = (ping_cfg.get("transport") or "").strip().lower()
        transport = transport_raw if transport_raw in ("tcp", "icmp") else ""
        out.append({
            "id":        hid,
            "host":      host_target,
            "port":      port,            # 0 = use ping_default_port
            "transport": transport,        # "" = use ping_use_icmp global
        })
    return out


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
    right now" — consumed by the per-host probe path and (post-fix)
    by the host_metrics_sampler's permanent-fail tracking pass.
    """
    import json as _json

    raw = get_setting("hosts_config", "") or ""
    if not raw.strip():
        return []
    try:
        parsed = _json.loads(raw)
    except ValueError:
        return []
    if not isinstance(parsed, list):
        return []
    out: list[dict] = []
    for row in parsed:
        if not isinstance(row, dict):
            continue
        if not row.get("enabled", True):
            continue
        snmp_cfg = row.get("snmp") if isinstance(row.get("snmp"), dict) else {}
        if snmp_cfg.get("enabled") is not True:
            continue
        hid = (row.get("id") or "").strip()
        if not hid:
            continue
        snmp_name = (row.get("snmp_name") or "").strip()
        # Caller may resolve `snmp_aliases[hid]` to override snmp_name —
        # we leave that lookup to the consumer so this helper stays
        # narrow-scoped (matches CLAUDE.md's "logic/db.py is the
        # I/O-free shape layer" rule).
        out.append({
            "id":        hid,
            "snmp_name": snmp_name,
            "snmp":      dict(snmp_cfg),
        })
    return out


def get_setting_bool(key: str, default: bool = False) -> bool:
    """Read a boolean settings row tolerantly.

    Falls back to ``default`` for unrecognised values so a typo
    ("ture") doesn't pretend to be False. Replaces the per-call site
    `get_setting(...).lower() == "true"` pattern that's case-fragile
    and silently treats any non-"true" string as False.
    """
    raw = get_setting(key, "")
    if not raw:
        return default
    s = str(raw).strip().lower()
    if s in _TRUTHY_STRINGS:
        return True
    if s in _FALSY_STRINGS:
        return False
    return default
