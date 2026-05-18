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
    clobber a provider that did. Booleans (BOTH True AND False) pass
    through as meaningful — a provider explicitly emitting `False`
    for a flag like `host_swap_active` is a real signal, not noise.
    Datetimes and custom objects also pass through.

    Bool short-circuit is BEFORE the int branch because Python's
    `bool ⊂ int` makes `isinstance(False, int)` true, and `False == 0`
    would otherwise route `False` through `v != 0` and report it as
    not-meaningful — exactly the opposite of the intended semantics.
    """
    if v is None:
        return False
    if isinstance(v, bool):
        return True
    if isinstance(v, (int, float)):
        return v != 0
    if isinstance(v, str):
        return v.strip() != ""
    if isinstance(v, (list, dict)):
        return len(v) > 0
    return True


def is_positive_number(v: Any) -> bool:
    """Strict-positive number test for host metrics.

    Used by ``logic.host_metrics_sampler`` to decide whether a CPU /
    memory / disk reading should be persisted. Differs from
    :func:`is_meaningful` in two ways: only numerics pass, and
    negatives are rejected (a negative metric is a buggy provider,
    not "no signal"). Lives here so the sampler doesn't carry its
    own duplicate of the same idea.
    """
    try:
        n = float(v)
    except (TypeError, ValueError):
        return False
    return n > 0


def normalize_arch(arch: str) -> str:
    """Harmonise architecture labels across providers.

    Different sources spell the same physical CPU architecture
    differently — FreeBSD's ``uname -m`` returns ``amd64``, Linux
    distros and most Beszel agents return ``x86_64``. Without
    normalisation, two providers reporting the same host disagree on
    the arch label (the merge order means one wins, but on hosts where
    the winning provider isn't enabled the operator sees the loser's
    spelling). This helper canonicalises every common alias to the
    Linux-style label so every provider extractor's `host_arch` is
    comparable downstream. Empty input passes through.
    """
    if not arch:
        return ""
    a = str(arch).strip().lower()
    if not a:
        return ""
    # FreeBSD-style → Linux-style. Most other arch labels (arm64,
    # aarch64, armv7l, riscv64, ppc64le, s390x) are already consistent
    # across uname / Beszel / NE; only x86_64-vs-amd64 needs unification
    # in practice today. Add more entries here as new arches arrive.
    aliases = {
        "amd64": "x86_64",
        "i386": "x86",
        "i686": "x86",
    }
    return aliases.get(a, a)


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
