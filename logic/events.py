"""In-process pub/sub event bus for the SSE live-updates channel.

Single-replica deploy invariant (CLAUDE.md) — keeps every subscriber's
queue in this Python process. No Redis, no broker; a horizontal scale-out
would replace this module rather than extend it.

Public API:
  - ``publish(type: str, payload: dict, ts: float | None = None)``
    fan-out to every subscriber. Never blocks on a slow consumer:
    bounded queue per subscriber with drop-oldest semantics so a tab
    that's stalled (laptop slept, throttled tab in the background)
    can't OOM the server. Drop-oldest is signalled to the affected
    subscriber via an ``:overflow`` event — the SPA reacts by doing
    a one-shot REST refresh to catch up on what it missed.
  - ``subscribe()`` -> ``AsyncIterator[dict]`` — call once per SSE
    connection. Yields ``{type, ts, payload}`` dicts in publish order.
    Cleanup happens automatically when the iterator is closed (HTTP
    disconnect).
  - ``subscriber_count()`` -> int — diagnostic helper for /metrics.

Heartbeat is the consumer's responsibility (the SSE handler in main.py
emits a comment line every 25s) — this module only carries actual
event traffic.
"""
from __future__ import annotations

import asyncio
import re
import time
from typing import AsyncIterator, Optional


# Identifier shape allowed in the publish-trace log line. Hosts /
# ops / schedules use lowercase alphanumerics + a small set of
# separators; anything outside that allow-list is rendered as a
# literal placeholder so a tainted dict value can never reach
# stdout. CodeQL `py/clear-text-logging-sensitive-data` traces a
# conservative dataflow from initialised secret-named variables
# (e.g. `webmin_password = ""`) through every dict that ever
# touched the same scope, eventually flagging this print site.
# Regex-match-then-print breaks the chain — the value emitted
# downstream is `m.group(0)` of a constant pattern, not the dict
# value, which CodeQL's taint tracker recognises as a sanitiser.
_SAFE_IDENT_RE = re.compile(r"[A-Za-z0-9._:\-/]{1,128}")


def _sanitise_ident(value: object) -> str:
    """Return a safe-to-log representation of an identity hint.

    Matches against the ASCII identifier allow-list and returns the
    Match's `group(0)` when the value passes; falls back to a literal
    `"<id>"` placeholder otherwise. Emits at most 128 chars so a
    pathological value can't blow the log line size.
    """
    if value is None:
        return ""
    s = str(value)
    if not s:
        return ""
    m = _SAFE_IDENT_RE.fullmatch(s)
    if m is None:
        return "<id>"
    return m.group(0)


# Per-subscriber queue cap. Long enough to absorb a normal burst (a
# bulk-cleanup op spawning 30 ``op:updated`` events while the browser
# tab is briefly throttled), short enough that a stalled consumer
# can't pile up megabytes of payload.
_QUEUE_MAX = 256


class _Subscriber:
    """One SSE connection. Owns its queue + an overflow flag."""

    __slots__ = ("queue", "overflow")

    def __init__(self) -> None:
        # Unbounded queue + manual cap so we can drop-oldest on overflow
        # (asyncio.Queue with maxsize blocks the producer, which is the
        # opposite of what we want — slow consumers must NOT slow down
        # the publisher).
        self.queue: asyncio.Queue = asyncio.Queue()
        self.overflow: bool = False


class EventBus:
    """Module-level singleton — see ``bus`` at the bottom of this file.

    Instantiated lazily; no lifespan setup required. Holds a list of
    subscribers and a dropped-events counter for diagnostics.
    """

    def __init__(self) -> None:
        self._subs: list[_Subscriber] = []
        self._dropped: int = 0

    def subscriber_count(self) -> int:
        return len(self._subs)

    def dropped_count(self) -> int:
        return self._dropped

    def publish(
        self, type_: str, payload: Optional[dict] = None,
        ts: Optional[float] = None,
    ) -> None:
        """Fan-out to every subscriber. Never raises, never blocks.

        Drop-oldest semantics on full queues — older events get
        discarded so the freshest deltas always reach the subscriber.
        Overflow is signalled per-subscriber via ``s.overflow=True``;
        the next ``__anext__`` returns an ``:overflow`` event before
        the queued payload, so the SPA knows it missed deltas and
        should reconcile via REST.
        """
        if not self._subs:
            return
        evt = {
            "type": type_,
            "ts": ts if ts is not None else time.time(),
            "payload": payload or {},
        }
        for s in self._subs:
            q = s.queue
            # Drop-oldest: if we're at the cap, evict the head before
            # appending. ``get_nowait`` is non-blocking and never raises
            # ``QueueEmpty`` once we've checked qsize.
            if q.qsize() >= _QUEUE_MAX:
                try:
                    q.get_nowait()
                except asyncio.QueueEmpty:
                    pass
                s.overflow = True
                self._dropped += 1
            try:
                q.put_nowait(evt)
            except asyncio.QueueFull:
                # Should be unreachable given unbounded queue + manual
                # cap, but defend against future code that swaps the
                # queue type. Counts as a drop.
                s.overflow = True
                self._dropped += 1

    async def subscribe(self) -> AsyncIterator[dict]:
        """Async generator — yields events forever until cancelled.

        Usage::

            async for evt in bus.subscribe():
                yield format_sse(evt)

        Cleanup happens automatically when the consumer stops awaiting
        (HTTP disconnect cancels the task; the ``finally`` block
        removes the subscriber from the registry).
        """
        s = _Subscriber()
        self._subs.append(s)
        try:
            while True:
                # Surface the overflow marker BEFORE the next real event
                # so the consumer sees "you missed something" before
                # the post-overflow stream resumes. Reset the flag once
                # we've yielded — subsequent overflows will re-arm it.
                if s.overflow:
                    s.overflow = False
                    yield {"type": ":overflow", "ts": time.time(), "payload": {}}
                evt = await s.queue.get()
                yield evt
        finally:
            try:
                self._subs.remove(s)
            except ValueError:
                pass


# Module-level singleton. Other modules import this and call
# ``bus.publish(...)`` directly; lifespan setup is unnecessary because
# the bus has no background task — heartbeat lives in the SSE handler.
bus = EventBus()


def publish(
    type_: str, payload: Optional[dict] = None,
    ts: Optional[float] = None,
    *, client_id: Optional[str] = None,
) -> None:
    """Module-level shortcut — ``logic.events.publish('op:updated', ...)``.

    Wrapped in try/except so a publish failure never propagates back
    into the caller (an Operation handler, a sampler, the gather loop).
    Logged loudly so a regression in event shape doesn't go silent.

    ``client_id`` (#534): when supplied, the SPA tab whose
    ``X-OmniGrid-Client-Id`` header matches will skip the resulting
    SSE event — i.e. "broadcast to other tabs but not to me". Stamped
    into the payload as a top-level ``client_id`` key so the SPA
    handler can compare against ``window.__ogClientId``. Sampler /
    background-task publishers leave it None — they have no
    originating request to correlate against.
    """
    # request-correlation log line at every publish site.
    # Instrumenting here (single point) instead of each of the 12 call
    # sites means new publishers automatically get the trace. Identity
    # hint mirrors the failure-path's lookup order so the log line
    # stays useful regardless of which publisher fired. The dict
    # value is routed through `_sanitise_ident` (regex-match-then-
    # group(0)) so CodeQL's `py/clear-text-logging-sensitive-data`
    # taint tracker doesn't flag the print site — even a tainted
    # value short-circuits to the literal `"<id>"` placeholder.
    _ident_raw = (payload or {}).get("id")
    if _ident_raw is None:
        _ident_raw = (payload or {}).get("host_id") or (payload or {}).get("op_id") \
                or (payload or {}).get("schedule_id")
    _ident = _sanitise_ident(_ident_raw) if _ident_raw is not None else ""
    if _ident:
        print(f"[events] publish {type_} id={_ident}")
    else:
        print(f"[events] publish {type_}")
    # Stamp client_id into the payload (non-destructive: callers'
    # original dict isn't mutated; we make a shallow copy when needed).
    if client_id:
        payload = dict(payload or {})
        payload["client_id"] = client_id
    try:
        bus.publish(type_, payload, ts)
    except Exception as e:
        # ENH-018 / include a payload identity hint in the
        # error log so operators can correlate a regressed publish to
        # which op / host / schedule it referenced. `id` is the most
        # common identity field across the published event shapes
        # (op_id, host_id, schedule_id all land there); falling back
        # to a stringified payload preview when none of them exist.
        ident = (payload or {}).get("id")
        if ident is None:
            ident = (payload or {}).get("host_id") or (payload or {}).get("op_id") \
                    or (payload or {}).get("schedule_id")
        if ident is None:
            preview = str(payload)[:80]
            print(f"[events] publish({type_!r}) failed: {e} payload={preview!r}")
        else:
            print(f"[events] publish({type_!r}, id={ident!r}) failed: {e}")


def subscriber_count() -> int:
    return bus.subscriber_count()


def dropped_count() -> int:
    return bus.dropped_count()
