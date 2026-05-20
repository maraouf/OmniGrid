"""Portainer API client helpers.

Thin wrappers around httpx for GET / header construction plus the
``node_for_container`` lookup that powers worker-node agent-target
routing. No httpx.AsyncClient is owned here — callers pass one in so
connection reuse stays with the request's scope.

Configuration (URL / API key / endpoint ID / TLS verify) is DB-backed
and UI-managed via ``get_portainer_settings()``. Env vars
(``PORTAINER_URL`` / ``PORTAINER_API_KEY`` / ``PORTAINER_ENDPOINT_ID``
/ ``VERIFY_TLS``) are consulted ONLY as a transitional bootstrap — on
first boot with an empty settings row, they're seeded in once. After
that the DB wins and env is ignored. See ``docs/guidelines/env_example.md``.

Concurrency caps (``REGISTRY_CONCURRENCY`` / ``STATS_CONCURRENCY``)
resolve via :mod:`logic.tuning` — DB setting > env var > code default.
Call sites use ``portainer.registry_concurrency()`` /
``portainer.stats_concurrency()`` so each gather sees the current value
without restart.
"""
import sqlite3
from contextlib import asynccontextmanager
from typing import AsyncIterator, Optional

from logic.env_keys import EnvKey, env_get

import httpx

from logic import tuning
from logic.tuning import Tunable


# ----------------------------------------------------------------------------
# Tunables — three-tier (DB > env > default) resolution. Functions, not
# constants, so the live UI override takes effect on the next call.
# ----------------------------------------------------------------------------
def registry_concurrency() -> int:
    """Per-use read of the registry-fetch concurrency cap (DB > env > default)."""
    return tuning.tuning_int(Tunable.REGISTRY_CONCURRENCY)


def stats_concurrency() -> int:
    """Per-use read of the /containers/*/stats fan-out cap (DB > env > default)."""
    return tuning.tuning_int(Tunable.STATS_CONCURRENCY)


# ----------------------------------------------------------------------------
# Settings cache — same pattern as logic.auth.get_auth_settings: read-through
# in-process cache invalidated on UI writes. Middleware + gather hot-path
# reads hit the dict, not SQLite.
# ----------------------------------------------------------------------------
_PORTAINER_SETTING_KEYS = (
    "portainer_url",
    "portainer_api_key",
    "portainer_endpoint_id",
    "portainer_verify_tls",
)

_PORTAINER_DEFAULTS: dict[str, object] = {
    "portainer_url": "",
    "portainer_api_key": "",
    "portainer_endpoint_id": "1",
    "portainer_verify_tls": True,
}

_BOOL_KEYS = ("portainer_verify_tls",)

_portainer_cache: dict = {}
_portainer_cache_valid = False


def bootstrap_portainer_settings(conn: sqlite3.Connection) -> None:
    """Seed the four Portainer settings on first boot.

    Special case: if the DB has no row yet AND the corresponding env var
    is set, the env value wins for that initial seed. Transitional aid
    for existing deployments that had Portainer config in .env — admins
    can migrate at their own pace, env stays ignored after the DB row
    exists. Documented as "transitional; will be removed in a future
    release" in docs/guidelines/env_example.md.
    """
    env_map = {
        "portainer_url": env_get(EnvKey.PORTAINER_URL).rstrip("/"),
        "portainer_api_key": env_get(EnvKey.PORTAINER_API_KEY),
        "portainer_endpoint_id": env_get(EnvKey.PORTAINER_ENDPOINT_ID, "1"),
        "portainer_verify_tls": env_get(EnvKey.VERIFY_TLS, "true").lower() == "true",
    }
    for key in _PORTAINER_SETTING_KEYS:
        existing = conn.execute(
            "SELECT value FROM settings WHERE key=?", (key,),
        ).fetchone()
        if existing is not None:
            continue
        env_val = env_map[key]
        # Empty strings for URL / API key would produce the same no-op
        # seed as the default, so prefer the default in that case for a
        # tidier DB row.
        if env_val in ("", None):
            default = _PORTAINER_DEFAULTS[key]
            val = _encode_setting(default)
        else:
            val = _encode_setting(env_val)
        conn.execute(
            "INSERT INTO settings(key, value) VALUES (?, ?)",
            (key, val),
        )


def _encode_setting(v) -> str:
    if v is True:
        return "true"
    if v is False:
        return "false"
    return str(v)


def _refresh_portainer_cache(conn: sqlite3.Connection) -> None:
    global _portainer_cache, _portainer_cache_valid
    placeholders = ",".join("?" for _ in _PORTAINER_SETTING_KEYS)
    rows = conn.execute(
        f"SELECT key, value FROM settings WHERE key IN ({placeholders})",
        _PORTAINER_SETTING_KEYS,
    ).fetchall()
    fresh: dict[str, object] = {k: _PORTAINER_DEFAULTS[k] for k in _PORTAINER_SETTING_KEYS}
    for r in rows:
        key = r["key"]
        raw = r["value"] or ""
        if key in _BOOL_KEYS:
            fresh[key] = raw.lower() == "true"
        else:
            fresh[key] = raw
    # Normalise: URL has no trailing slash, endpoint_id is an int.
    _url_raw = fresh.get("portainer_url")
    url = _url_raw if isinstance(_url_raw, str) else ""
    fresh["portainer_url"] = url.rstrip("/")
    _eid_raw = fresh.get("portainer_endpoint_id")
    try:
        if isinstance(_eid_raw, (int, str)) and _eid_raw:
            fresh["portainer_endpoint_id"] = int(_eid_raw)
        else:
            fresh["portainer_endpoint_id"] = 1
    except (ValueError, TypeError):
        fresh["portainer_endpoint_id"] = 1
    _portainer_cache = fresh
    _portainer_cache_valid = True


def get_portainer_settings(conn: Optional[sqlite3.Connection] = None) -> dict:
    """Return the live Portainer settings dict:

      - portainer_url          : str (no trailing slash)
      - portainer_api_key      : str
      - portainer_endpoint_id  : int
      - portainer_verify_tls   : bool

    Read-through cached. Pass an open connection when you already have
    one; otherwise the function opens its own (import kept local to
    sidestep the module import cycle with logic.db).
    """
    if _portainer_cache_valid:
        return _portainer_cache
    if conn is None:
        from logic.db import db_conn
        with db_conn() as c:
            _refresh_portainer_cache(c)
    else:
        _refresh_portainer_cache(conn)
    return _portainer_cache


def invalidate_portainer_cache() -> None:
    """Drop the in-process Portainer settings cache so the next read re-fetches from DB."""
    global _portainer_cache_valid
    _portainer_cache_valid = False


def is_configured() -> bool:
    """True when both URL and API key are non-empty AND the per-service
    master switch is on. Callers use this as a pre-flight check
    so they can short-circuit instead of firing a doomed httpx request
    against an empty URL — and the master-switch path lets an operator
    flip Portainer off (during maintenance, debugging, etc.) without
    erasing the stored URL/key."""
    from logic.db import get_setting as _get
    if (_get("portainer_enabled", "true") or "true").lower() != "true":
        return False
    s = get_portainer_settings()
    return bool(s.get("portainer_url")) and bool(s.get("portainer_api_key"))


# ----------------------------------------------------------------------------
# Module-level shortcuts. Read-through via the settings cache — use the
# accessor functions below in call sites. Kept as properties-ish module
# attributes via __getattr__ so existing `portainer.PORTAINER_URL` reads
# still work after the refactor.
# ----------------------------------------------------------------------------
def _url() -> str:
    return str(get_portainer_settings().get("portainer_url") or "")


def _api_key() -> str:
    return str(get_portainer_settings().get("portainer_api_key") or "")


def _endpoint_id() -> int:
    val = get_portainer_settings().get("portainer_endpoint_id") or 1
    return int(val)


def _verify_tls() -> bool:
    return bool(get_portainer_settings().get("portainer_verify_tls", True))


def __getattr__(name: str):
    # Backwards compatibility for the old module-level constants. Every
    # read goes through the cached settings dict so UI changes are
    # picked up without restart. This is the minimum-diff path described
    # in the refactor plan — callers that still import PORTAINER_URL as
    # a module attribute keep reading the live value.
    if name == "PORTAINER_URL":
        return _url()
    if name == "PORTAINER_API_KEY":
        return _api_key()
    if name == "PORTAINER_ENDPOINT_ID":
        return _endpoint_id()
    if name == "VERIFY_TLS":
        return _verify_tls()
    raise AttributeError(f"module 'logic.portainer' has no attribute {name!r}")


@asynccontextmanager
async def write_client(timeout: float = 60.0) -> AsyncIterator[httpx.AsyncClient]:
    """Yield an `httpx.AsyncClient` pre-configured with Portainer's
    `VERIFY_TLS` setting + the supplied wall-clock cap.

    Centralises the boilerplate every Portainer write op duplicated —
    `async with httpx.AsyncClient(verify=portainer.VERIFY_TLS,
    timeout=X)` × 9 sites in `logic/ops.py`. Per CLAUDE.md "Vendor /
    capability key sets need ONE source of truth" — the verify + ssl
    config should be in one place so future TLS-handling changes
    (CA bundle path, retries, etc.) only need one edit.

    Caller passes the timeout in seconds; typically wired to the
    three Portainer-write-op TUNABLE tiers:
        tuning_portainer_op_timeout_short_seconds   (default 120)
        tuning_portainer_op_timeout_medium_seconds  (default 300)
        tuning_portainer_op_timeout_long_seconds    (default 600)

    Sample usage:
        from logic import portainer
        async with portainer.write_client(timeout=600.0) as client:
            await client.post(...)
    """
    async with httpx.AsyncClient(verify=_verify_tls(), timeout=timeout) as client:
        yield client


def headers(agent_target: Optional[str] = None) -> dict[str, str]:
    """Build the auth-header dict for a Portainer request.

    ``X-PortainerAgent-Target: <hostname>`` routes the request through the
    Portainer agent to a specific Swarm node's Docker daemon. Required for
    container-level actions (delete, restart, recreate) when the container
    lives on a worker node — the manager's daemon would otherwise 404.
    Skips the header for synthetic fallback values ("local", "?").
    """
    h = {"X-API-Key": _api_key()}
    if agent_target and agent_target not in ("local", "?", ""):
        h["X-PortainerAgent-Target"] = agent_target
    return h


async def pg(client: httpx.AsyncClient, path: str, agent_target: Optional[str] = None):
    """GET ``PORTAINER_URL + path`` with API-key auth; return parsed JSON.

    Raises ``httpx.HTTPStatusError`` on 4xx/5xx via ``raise_for_status()``.
    Callers typically wrap this with a ``safe()`` helper to swallow one
    sub-API error without failing the whole gather.

    When ``agent_target`` is set, the request is forwarded to that
    specific Swarm node's Docker daemon via ``X-PortainerAgent-Target``.
    In agent-mode endpoints this makes per-node listings (``/containers/json``)
    disjoint instead of aggregated, which is how we discover which node a
    plain compose container lives on (no Swarm task metadata to key off).
    """
    r = await client.get(f"{_url()}{path}", headers=headers(agent_target=agent_target))
    r.raise_for_status()
    return r.json()


def node_for_container(cache: dict, container_id: str) -> Optional[str]:
    """Return the hostname of the Swarm node hosting ``container_id``,
    if known from the last ``_gather()`` snapshot.

    Accepts either the prefixed id (``ctn:abc...``) or the raw Docker ID.
    Returns None for standalone containers whose node can't be determined
    from Swarm metadata — those stay routed to the manager, same as
    before. The cache dict is passed in rather than imported so this
    module doesn't need a circular dep on gather.
    """
    for it in (cache or {}).get("items", []):
        if it.get("raw_id") == container_id or it.get("id") == container_id:
            node = it.get("node")
            if node and node not in ("local", "?", ""):
                return node
            break
    return None
