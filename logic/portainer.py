"""Portainer API client helpers.

Thin wrappers around httpx for GET / header construction plus the
``node_for_container`` lookup that powers worker-node agent-target
routing. No httpx.AsyncClient is owned here — callers pass one in so
connection reuse stays with the request's scope.

Config (URL / API key / endpoint ID / TLS verify / registry concurrency)
is read from env at import. These values are process-global tunables,
not per-request settings, so a module-level read is fine.
"""
import os
from typing import Optional

import httpx


PORTAINER_URL = os.getenv("PORTAINER_URL", "").rstrip("/")
PORTAINER_API_KEY = os.getenv("PORTAINER_API_KEY", "")
PORTAINER_ENDPOINT_ID = int(os.getenv("PORTAINER_ENDPOINT_ID", "1"))
VERIFY_TLS = os.getenv("VERIFY_TLS", "true").lower() == "true"
# Outbound concurrency caps — named by the thing they limit, not where
# the work happens, because both gather and stats live in main.py today.
REGISTRY_CONCURRENCY = int(os.getenv("REGISTRY_CONCURRENCY", "8"))
STATS_CONCURRENCY = int(os.getenv("STATS_CONCURRENCY", "16"))


def headers(agent_target: Optional[str] = None) -> dict[str, str]:
    """Build the auth-header dict for a Portainer request.

    ``X-PortainerAgent-Target: <hostname>`` routes the request through the
    Portainer agent to a specific Swarm node's Docker daemon. Required for
    container-level actions (delete, restart, recreate) when the container
    lives on a worker node — the manager's daemon would otherwise 404.
    Skips the header for synthetic fallback values ("local", "?").
    """
    h = {"X-API-Key": PORTAINER_API_KEY}
    if agent_target and agent_target not in ("local", "?", ""):
        h["X-PortainerAgent-Target"] = agent_target
    return h


async def pg(client: httpx.AsyncClient, path: str):
    """GET ``PORTAINER_URL + path`` with API-key auth; return parsed JSON.

    Raises ``httpx.HTTPStatusError`` on 4xx/5xx via ``raise_for_status()``.
    Callers typically wrap this with a ``safe()`` helper to swallow one
    sub-API error without failing the whole gather.
    """
    r = await client.get(f"{PORTAINER_URL}{path}", headers=headers())
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
