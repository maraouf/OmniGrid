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
from typing import Optional, Tuple, Hashable


class Cooldown:
    """Per-key auth cooldown timer.

    Each consumer instantiates one with the desired window length:
    ``Cooldown(seconds=300)`` arms expire 5 minutes after :meth:`arm`.

    Keys are arbitrary hashable tuples — the consumer chooses the
    granularity (e.g. SSH uses ``(host_id, user)``; Webmin uses
    ``(base_url, user)``).
    """

    def __init__(self, seconds: float):
        if seconds <= 0:
            raise ValueError("Cooldown window must be > 0 seconds")
        self.seconds = float(seconds)
        # `_armed` maps key → epoch-second timestamp at which the
        # cooldown expires. Lazy expiry: ``remaining()`` pops a key
        # whose expiry is in the past, so the dict can never grow
        # past the number of keys actively in cooldown right now.
        self._armed: dict[Tuple[Hashable, ...], float] = {}

    def arm(self, *key: Hashable) -> None:
        """Start the cooldown window for ``key`` (variadic — any
        hashable values, joined into a tuple).
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
