"""Generic dict-merging helpers for host-stats provider data.

Single source of truth for the "fold provider results into a merged
nodes_info row" pattern. Previously duplicated as private helpers in
``main.py`` and ``logic/gather.py`` because the original concern was
"don't introduce a cross-module dependency on a private helper" — but
moving them here as PUBLIC helpers makes that concern moot.

Both consumers (``logic/gather.py`` during the gather fan-out, and
``main.py`` when shaping the per-host API responses) import these so
the merge semantics stay byte-identical across paths. CLAUDE.md's
provider-merge-order rule (Pulse → Beszel → node-exporter → Webmin)
relies on `_meaningful` matching exactly between the two sites; one
implementation removes that risk.
"""
from typing import Any


def is_meaningful(v: Any) -> bool:
    """True when ``v`` carries information.

    The canonical "this provider returned an actual value" test used
    by ``merge_best`` to decide whether to overwrite the destination.
    Treats zero / empty-string / empty-collection / None as
    not-meaningful so a provider that didn't see a metric can't
    clobber a provider that did. Booleans and other truthy values
    (datetimes, custom objects) pass through.
    """
    if v is None:
        return False
    if isinstance(v, (int, float)):
        return v != 0
    if isinstance(v, str):
        return v.strip() != ""
    if isinstance(v, (list, dict)):
        return len(v) > 0
    return True


def merge_best(dst: dict, src: dict) -> None:
    """Fold ``src`` into ``dst``, overwriting only with meaningful values.

    For each key in ``src``: if the value is meaningful (per
    :func:`is_meaningful`), it overwrites ``dst[k]`` regardless of
    whether ``dst`` already had something. Non-meaningful values
    (None / 0 / "" / [] / {}) are written ONLY if ``dst`` doesn't
    already have a value — this lets the FIRST provider seed empty
    fields that subsequent providers might also leave empty, without
    a later provider blanking out a meaningful value an earlier
    provider set.

    Mutates ``dst`` in place. Returns None.

    Provider merge order matters (CLAUDE.md "Hosts pipeline" rule):
    Pulse → Beszel → node-exporter → Webmin. Each provider calls
    this with ``dst = nodes_info[host]`` and ``src = its_extracted_stats``.
    """
    if not src:
        return
    for k, v in src.items():
        if is_meaningful(v):
            dst[k] = v
        elif k not in dst:
            dst[k] = v
