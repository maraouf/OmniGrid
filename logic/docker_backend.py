"""Docker backend abstraction — the formal model behind OmniGrid's two-backend
(and future N-backend) container / service / stack management.

OmniGrid manages containers + services through one of two BACKENDS, recorded on
every gathered item + node as a ``backend`` tag:

  - ``"portainer"``    — the default; via the Portainer REST API
                         (``logic/portainer.py``).
  - ``"docker:<id>"``  — a direct-Docker node (Portainer-less), reached over SSH
                         or TCP+TLS (``logic/docker_direct.py``); ``<id>`` is the
                         ``docker_nodes`` config id.

Write-ops (restart / remove / update container; restart service; update stack)
resolve a target item's backend and dispatch to the matching client. THIS MODULE
IS THE SINGLE SOURCE OF TRUTH for that resolution — the ops delegate here rather
than each re-deriving the backend tag, so a future THIRD backend (e.g. a
Kubernetes adapter) is a one-place extension: add its tag prefix + a resolver
branch here, and a client module alongside ``docker_direct.py``. This is the
lightweight formalization of the "Portainer is just one of N adapters" model —
it centralizes + documents the dispatch WITHOUT rewriting every Portainer call
site behind a heavy interface (the gather / stats / Portainer client stay as-is;
only the backend RESOLUTION is unified here).

Dependency-free leaf at import time — ``gather`` is imported lazily inside the
functions so there's no import cycle (``ops_extras`` → ``docker_backend`` →
``gather``, while ``gather`` never imports this module).
"""
from typing import Optional

# Backend tag values stamped on every gathered item / node.
PORTAINER = "portainer"
DOCKER_PREFIX = "docker:"


def item_backend(item: dict) -> str:
    """The ``backend`` tag on an item / node — ``"portainer"`` when unset (the
    default backend), or ``"docker:<id>"`` for a direct-Docker node."""
    return str((item or {}).get("backend") or PORTAINER)


def is_direct(item: dict) -> bool:
    """True when the item / node is managed by a direct-Docker backend (vs
    Portainer)."""
    return item_backend(item).startswith(DOCKER_PREFIX)


def node_id_of(backend: str) -> Optional[str]:
    """The ``docker_nodes`` config id encoded in a ``"docker:<id>"`` backend tag,
    or ``None`` for the Portainer backend / a malformed tag."""
    b = str(backend or "")
    return b.split(":", 1)[1] if b.startswith(DOCKER_PREFIX) else None


def node_by_id(node_id: str) -> Optional[dict]:
    """The ``docker_nodes`` config dict for ``node_id`` (or ``None``)."""
    from logic import gather  # noqa: PLC0415 — lazy to avoid an import cycle
    nid = str(node_id or "")
    for n in gather.load_docker_nodes_cfg():
        if str(n.get("id") or "") == nid:
            return n
    return None


def resolve_node_for(item_id: str) -> Optional[dict]:
    """Resolve a gathered item id (a CONTAINER or a SERVICE, raw or prefixed) to
    its direct-Docker node config dict, or ``None`` for the Portainer backend /
    an unknown id. The canonical resolver the write-ops dispatch on — a non-None
    result means "use the direct client against this node"; ``None`` means "the
    Portainer path"."""
    from logic import gather  # noqa: PLC0415 — lazy to avoid an import cycle
    cache = gather.get_cache()
    for it in (cache.get("items") or []):
        if not isinstance(it, dict):
            continue
        if it.get("raw_id") == item_id or it.get("id") == item_id:
            nid = node_id_of(item_backend(it))
            return node_by_id(nid) if nid else None
    return None
