"""Per-host TCP/ICMP ping sampler — reachability + RTT time-series.

Lifespan-managed sibling of :mod:`logic.host_metrics_sampler` and
:mod:`logic.host_net_sampler`. One tick per
``tuning_ping_interval_seconds`` (DB > env > default; default 60s,
range 10–3600s).

What it does per tick:

1. Walk the curated hosts list. Filter to rows where
   ``ping.enabled == True`` AND ``"ping"`` is in
   ``active_host_stats_providers()`` — both gates must be true.
2. Run probes through ``asyncio.Semaphore(tuning_ping_concurrency)``
   (default 16, range 1-128).
3. Per result, INSERT a row into ``ping_samples`` (schema in
   ``main.init_db()``) — ``ts``, ``host_id``, ``alive``,
   ``rtt_ms``, ``rtt_min_ms``, ``rtt_max_ms``, ``loss_pct``.
4. Publish a ``host:ping_sampled`` SSE event so any open SPA tab can
   refresh its ping chart without polling.
5. Hourly: prune ``ping_samples`` older than
   ``tuning_stats_history_days`` (reused — no new retention knob).

Skip-don't-synthesize discipline applies but is simpler than the
counter-rate samplers: ``rtt_ms`` is a point-in-time gauge (no delta
math), so there are no rollover / clock-skew corner cases. We DO
write rows when ``alive == False`` — that's the load-bearing signal
the operator wants ("when did it go down?"), not noise.
"""
from __future__ import annotations

import asyncio
import time

from logic import ping as _ping
from logic import tuning
from logic.tuning import Tunable as _Tunable
from logic.db import db_conn, get_setting_bool, active_host_stats_providers, iter_curated_hosts
from logic.settings_keys import Settings


# noinspection DuplicatedCode,PyTypeChecker
def _curated_ping_hosts() -> list[dict]:
    """Curated hosts opted-in for ping probing.

    Walks the JSON ``hosts_config`` setting, returns one row per
    enabled entry whose ``ping.enabled`` flag is true. Each row is
    ``{id, host, port, transport}`` where ``host`` defaults to the
    curated id, ``port`` defaults to ``ping_default_port``,
    ``transport`` defaults to ``ping_use_icmp`` global.

    Lives here rather than ``logic/db.py`` because the row shape is
    sampler-specific (inlined defaults from the global settings) and
    doesn't have other consumers. The JSON-parse + enabled-gate prelude
    is delegated to :func:`logic.db.iter_curated_hosts` (DUP-001).
    """
    default_port = _resolve_default_port()
    use_icmp_global = get_setting_bool(Settings.PING_USE_ICMP, False)
    default_transport = "icmp" if (use_icmp_global and _ping.has_icmp_support()) else "tcp"

    out: list[dict] = []
    for row in iter_curated_hosts():
        # Project-pattern `_raw` intermediate + explicit `: dict`
        # annotation narrows the sub-dict for the type-checker —
        # PyCharm can't carry the `isinstance(..., dict)` narrowing
        # through a conditional-expression assignment so a bare
        # `ping_cfg = ... if isinstance(...) else {}` leaves
        # `ping_cfg` typed as `dict | None | Any` and every `.get()`
        # call below triggers a `Member 'None' of ...` warning.
        _ping_raw = row.get("ping")
        ping_cfg: dict = _ping_raw if isinstance(_ping_raw, dict) else {}
        if not ping_cfg.get("enabled"):
            continue
        hid = (row.get("id") or "").strip()  # iter_curated_hosts already guarantees non-empty
        _ssh_raw = row.get("ssh")
        ssh_cfg: dict = _ssh_raw if isinstance(_ssh_raw, dict) else {}
        # Target resolution chain — MUST mirror the canonical chain
        # documented in CLAUDE.md and used by `_resolve_ping_target`
        # in `main.py`, the on-demand port-scan resolver, the SNMP
        # `_merge_one_host` block, and `logic/ssh.py:resolve_ssh_params`:
        #   address → ping.host → ssh.fqdn → ssh.host → host_id
        # Pre-fix the sampler-side chain (a) didn't include the
        # curated `address` field at all, AND (b) still consulted the
        # row's `url` field as a fallback (parsed via urlparse). The
        # `url` fallback was deliberately removed from every other
        # probe site when the canonical `address` field landed, because
        # `url` carries the operator-facing clickable web-UI link (often a public service relay
        # behind NPM / Cloudflare), not a LAN-reachable probe target —
        # using it for ping samples produced misleading RTTs against
        # the wrong host. The bare `host_id` stays as a last resort
        # for legacy rows that never populated any of the above.
        ping_host_override = (ping_cfg.get("host") or "").strip()
        host_target = (
            (row.get("address") or "").strip()
            or ping_host_override
            or (ssh_cfg.get("fqdn") or "").strip()
            or (ssh_cfg.get("host") or "").strip()
            or hid
        )
        port_override = ping_cfg.get("port")
        try:
            port = int(port_override) if port_override not in (None, "", 0) else default_port
        except (TypeError, ValueError):
            port = default_port
        transport_raw = (ping_cfg.get("transport") or "").strip().lower()
        transport = transport_raw if transport_raw in ("tcp", "icmp") else default_transport
        if transport == "icmp" and not _ping.has_icmp_support():
            transport = "tcp"
        out.append({
            "id": hid,
            "host": host_target,
            "port": port,
            "transport": transport,
        })
    return out


def _resolve_default_port() -> int:
    # `tuning_ping_default_port` clamps to 1..65535 in TUNABLES, so the
    # resolver returns a valid port. Defaults to 443 (HTTPS) when no
    # override is set.
    from logic import tuning as _tuning
    try:
        return _tuning.tuning_int(_Tunable.PING_DEFAULT_PORT) or 443
    except Exception:
        return 443


# noinspection DuplicatedCode,PyTypeChecker
async def _probe_one(host: dict, sem: asyncio.Semaphore) -> None:
    """Probe one host + write a row + publish SSE.

    Probe exceptions (DNS NXDOMAIN, network unreachable, asyncio
    cancellation timeouts that leak past the inner timeout, etc.)
    are caught and synthesised into an alive=False result so we
    STILL write a row to ``ping_samples``. Without this, a host
    with an unresolvable name (e.g. bare-id `ftth` in a container
    whose DNS doesn't have it) produces NO samples at all — the
    `asyncio.gather(return_exceptions=True)` upstream would
    silently swallow the exception and the operator sees an
    empty row in /api/hosts (ping_alive=null forever).
    """
    async with sem:
        # Per-(ping, host) auto-pause short-circuit. Skip the
        # probe entirely when operator has marked this host paused.
        # Done HERE not at the loop level so the failure-state row is
        # checked per-host every tick (cheap SELECT).
        from logic.host_metrics_sampler import (
            record_provider_outcome as _rec_pause_outcome,
        )
        try:
            from logic.db import db_conn as _dbc
            with _dbc() as _c:
                _r = _c.execute(
                    "SELECT paused FROM host_failure_state "
                    "WHERE host_id=? AND provider=?",
                    (host["id"], "ping"),
                ).fetchone()
            if _r and _r[0]:
                return
        except Exception:
            pass  # DB blip — let the probe run
        timeout_s = float(tuning.tuning_int(_Tunable.PING_PROBE_TIMEOUT_SECONDS))
        ping_pause_rounds = tuning.tuning_int(_Tunable.PING_FAILURE_PAUSE_ROUNDS)
        sampler_error: str = ""
        try:
            result = await _ping.probe_ping(
                host["host"], port=host["port"],
                transport=host.get("transport", "tcp"),
                timeout_seconds=timeout_s,
                count=3,
            )
        except asyncio.CancelledError:
            raise
        except Exception as e:
            err = f"{type(e).__name__}: {str(e)[:120]}" if str(e) else type(e).__name__
            print(f"[ping_sampler] {host['id']!r} probe exception: {err}")
            result = {
                "alive": False,
                "rtt_ms": None,
                "rtt_min_ms": None,
                "rtt_max_ms": None,
                "loss_pct": 100.0,
                "error": err,
            }
            # Sampler-level error (DNS failure, ICMP perm-denied,
            # transport setup failure, etc.) — count toward auto-pause.
            # Distinct from the alive=False case below: alive=False is
            # the actual data the operator wants surfaced, not a fault.
            sampler_error = err
        # Auto-pause accounting : only sampler errors count;
        # plain alive=False is the data, not a fault.
        if sampler_error:
            await _rec_pause_outcome(
                host["id"], "ping", False,
                error=sampler_error,
                round_threshold=ping_pause_rounds,
            )
        else:
            await _rec_pause_outcome(host["id"], "ping", True)
        ts = int(time.time())
        try:
            with db_conn() as c:
                c.execute(
                    "INSERT OR REPLACE INTO ping_samples "
                    "(ts, host_id, alive, rtt_ms, rtt_min_ms, "
                    "rtt_max_ms, loss_pct) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (
                        ts, host["id"],
                        1 if result.get("alive") else 0,
                        (float(result["rtt_ms"]) if result.get("rtt_ms") is not None else None),
                        (float(result["rtt_min_ms"]) if result.get("rtt_min_ms") is not None else None),
                        (float(result["rtt_max_ms"]) if result.get("rtt_max_ms") is not None else None),
                        float(result.get("loss_pct") or 0.0),
                    ),
                )
        except Exception as e:
            print(f"[ping_sampler] {host['id']!r} DB insert failed: {e}")
            return
        # SSE — publish once per insert. Payload is intentionally
        # tiny; the SPA refetches the relevant window via
        # /api/hosts/{id}/ping/history when it sees this event AND the
        # host's drawer is open in Live mode. Cheap.
        try:
            from logic import events as _events
            _events.publish("host:ping_sampled", {
                "host_id": host["id"],
                "ts": ts,
                "alive": bool(result.get("alive")),
                "rtt_ms": result.get("rtt_ms"),
                "loss_pct": result.get("loss_pct"),
            })
        except Exception as e:
            print(f"[ping_sampler] SSE publish failed for {host['id']!r}: {e}")
        rtt_blurb = (
            f"rtt={result['rtt_ms']:.1f}ms" if result.get("rtt_ms") is not None
            else f"err={result.get('error') or '—'}"
        )
        # Defensive `or 0` so a future probe_ping that returns
        # `{loss_pct: None}` (distinguishing "sample taken, loss unknown"
        # from "100% loss") doesn't blow this format spec up with
        # TypeError. Today's synthesized error path always populates
        # 100.0; the guard is forward-looking.
        loss_for_log = result.get("loss_pct") or 0
        print(f"[ping_sampler] {host['id']!r} alive={result.get('alive')} "
              f"loss={loss_for_log:.0f}% {rtt_blurb}")


def _prune_old_samples() -> int:
    days = tuning.tuning_int(_Tunable.STATS_HISTORY_DAYS)
    cutoff = int(time.time() - days * 86400)
    try:
        with db_conn() as c:
            cur = c.execute("DELETE FROM ping_samples WHERE ts < ?", (cutoff,))
            return cur.rowcount or 0
    except Exception as e:
        print(f"[ping_sampler] prune failed: {e}")
        return 0


def _resolve_ping_interval() -> int:
    """Ping-specific interval > 0 overrides the global stats interval;
    0 = inherit (parity with Beszel / Pulse / NE / SNMP sample-interval
    knobs). Floors at 10s so a misconfigured "almost zero" override
    can't pin the loop. Default falls back to 300s when both the
    Ping-specific knob AND the global knob are unset.
    """
    ping_iv = tuning.tuning_int(_Tunable.PING_INTERVAL_SECONDS)
    if ping_iv > 0:
        return max(10, ping_iv)
    global_iv = tuning.tuning_int(_Tunable.STATS_SAMPLE_INTERVAL_SECONDS)
    return max(10, global_iv or 300)


async def ping_sampler_loop() -> None:
    """Lifespan-managed sampler. One tick per
    ``tuning_ping_interval_seconds`` (DB > env > default). When set to
    0 (the inherit sentinel — same shape as Beszel / Pulse / NE / SNMP
    sample-interval knobs), falls back to
    ``tuning_stats_sample_interval_seconds`` so Ping ticks on the same
    heartbeat as the data-bearing samplers.

    Dormant when ``"ping"`` isn't in ``host_stats_source`` — keeps
    ticking so the operator can flip ping on without restarting.
    """
    interval = _resolve_ping_interval()
    await asyncio.sleep(min(30, interval))
    tick = 0
    while True:
        try:
            active = active_host_stats_providers()
            if "ping" not in active:
                pass  # globally disabled
            else:
                hosts = _curated_ping_hosts()
                if hosts:
                    sem = asyncio.Semaphore(tuning.tuning_int(_Tunable.PING_CONCURRENCY))
                    await asyncio.gather(
                        *(_probe_one(h, sem) for h in hosts),
                        return_exceptions=True,
                    )
            interval = _resolve_ping_interval()
            days = tuning.tuning_int(_Tunable.STATS_HISTORY_DAYS)
            if tick % max(1, 3600 // interval) == 0:
                n = _prune_old_samples()
                if n:
                    print(f"[ping_sampler] pruned {n} rows older than {days}d")
        except Exception as e:
            print(f"[ping_sampler] tick error: {e}")
        tick += 1
        try:
            await asyncio.sleep(interval)
        except asyncio.CancelledError:
            raise


def recent_samples(host_id: str, since_ts: int, limit: int = 1000) -> list[dict]:
    """Oldest-first rows for one host back to ``since_ts``."""
    if not host_id:
        return []
    try:
        with db_conn() as c:
            rows = c.execute(
                "SELECT ts, alive, rtt_ms, rtt_min_ms, rtt_max_ms, loss_pct "
                "FROM ping_samples "
                "WHERE host_id=? AND ts >= ? "
                "ORDER BY ts ASC LIMIT ?",
                (host_id, int(since_ts), int(limit)),
            ).fetchall()
    except Exception as e:
        print(f"[ping_sampler] recent_samples({host_id!r}) failed: {e}")
        return []
    return [_shape_row(r) for r in rows]


def last_samples(host_id: str, limit: int = 5) -> list[dict]:
    """Newest-first recent rows for the debug endpoint."""
    if not host_id:
        return []
    try:
        with db_conn() as c:
            rows = c.execute(
                "SELECT ts, alive, rtt_ms, rtt_min_ms, rtt_max_ms, loss_pct "
                "FROM ping_samples WHERE host_id=? "
                "ORDER BY ts DESC LIMIT ?",
                (host_id, int(limit)),
            ).fetchall()
    except Exception as e:
        print(f"[ping_sampler] last_samples({host_id!r}) failed: {e}")
        return []
    return [_shape_row(r) for r in rows]


def _shape_row(r) -> dict:
    return {
        "ts": int(r["ts"]),
        "alive": bool(r["alive"]),
        "rtt_ms": (float(r["rtt_ms"]) if r["rtt_ms"] is not None else None),
        "rtt_min_ms": (float(r["rtt_min_ms"]) if r["rtt_min_ms"] is not None else None),
        "rtt_max_ms": (float(r["rtt_max_ms"]) if r["rtt_max_ms"] is not None else None),
        "loss_pct": (float(r["loss_pct"]) if r["loss_pct"] is not None else 0.0),
    }
