"""Generic auth-cooldown helper.

A short-lived in-process timer keyed by an arbitrary tuple. After
:meth:`Cooldown.arm` is called for a key, :meth:`Cooldown.remaining`
returns the seconds left until that key is "free" again. Once the
window expires, the key is removed lazily on the next ``remaining``
call.

Used to avoid hammering an upstream after a 401 — both the SSH
runner (per-(host_id, user)) and the Webmin probe (per-(base_url,
user)) hit this pattern. Centralised here in #271 / CONS-004 so the
TTL semantics + lazy-expiry behaviour stay in lockstep.

Single-process state — fine for OmniGrid's single-replica deploy
constraint. If we ever scale horizontally the cooldowns would
need to move to Redis or similar; for now an in-memory dict is
correct and simple.
"""
from __future__ import annotations

import time
from typing import Callable, Optional, Tuple, Hashable, Union


class Cooldown:
    """Per-key auth cooldown timer.

    Each consumer instantiates one with the desired window length:
    ``Cooldown(seconds=300)`` arms expire 5 minutes after :meth:`arm`.

    Keys are arbitrary hashable tuples — the consumer chooses the
    granularity (e.g. SSH uses ``(host_id, user)``; Webmin uses
    ``(base_url, user)``).

    The window length may be a fixed number OR a zero-arg callable
    (#549) — the callable form lets the duration come from a TUNABLES
    knob that the operator can re-tune at runtime. ``arm()`` invokes
    the callable on each call, so a Save in Admin → Config takes
    effect on the next failed-auth event without a restart. Existing
    consumers passing a fixed ``seconds=`` value keep working
    unchanged.
    """

    def __init__(self, seconds: Union[float, Callable[[], float]] = 0,
                 *, seconds_fn: Optional[Callable[[], float]] = None):
        # Normalise the constructor: ``Cooldown(seconds=300)``,
        # ``Cooldown(seconds=lambda: tuning_int(...))``, AND
        # ``Cooldown(seconds_fn=...)`` are all accepted. ``seconds_fn``
        # exists as an explicit keyword so a callable consumer reads
        # naturally at the call site.
        resolver: Optional[Callable[[], float]] = None
        if seconds_fn is not None:
            resolver = seconds_fn
        elif callable(seconds):
            resolver = seconds  # type: ignore[assignment]
        else:
            if seconds <= 0:
                raise ValueError("Cooldown window must be > 0 seconds")
        self._resolver = resolver
        self._fixed = float(seconds) if resolver is None else 0.0
        # `_armed` maps key → epoch-second timestamp at which the
        # cooldown expires. Lazy expiry: ``remaining()`` pops a key
        # whose expiry is in the past, so the dict can never grow
        # past the number of keys actively in cooldown right now.
        self._armed: dict[Tuple[Hashable, ...], float] = {}

    @property
    def seconds(self) -> float:
        """Effective cooldown window for THIS call. Reads the
        operator-tunable value via the resolver if one was given,
        else returns the fixed value.
        """
        if self._resolver is not None:
            try:
                v = float(self._resolver())
            except Exception:
                # Resolver failure (DB unreachable, settings table
                # missing, etc.) — fall back to a safe default of 300s
                # (matches the historical hardcoded value across the
                # original Webmin + SSH consumers).
                v = 300.0
            return v if v > 0 else 300.0
        return self._fixed

    def arm(self, *key: Hashable) -> None:
        """Start the cooldown window for ``key`` (variadic — any
        hashable values, joined into a tuple). The window length is
        resolved per-call when a callable resolver is configured, so
        a Save in Admin → Config takes effect on the next ``arm()``.
        """
        self._armed[tuple(key)] = time.time() + self.seconds

    def remaining(self, *key: Hashable) -> Optional[float]:
        """Seconds left in the cooldown for ``key`` — ``None`` when
        the key has never been armed OR when its window has expired.
        On expiry, the key is dropped from the dict (lazy GC).
        """
        k = tuple(key)
        expires = self._armed.get(k)
        if expires is None:
            return None
        now = time.time()
        if expires <= now:
            self._armed.pop(k, None)
            return None
        return expires - now

    def clear(self, *key: Hashable) -> None:
        """Drop the cooldown for ``key`` early (e.g. after a successful
        auth probe — no need to keep the timer running). Idempotent.
        """
        self._armed.pop(tuple(key), None)
