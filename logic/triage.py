"""Incident-triage similarity grouping.

When the host drawer's Timeline shows a recent failure, operators need
to know: "is this the same problem we've seen before, or a new one?"
Today they have to scroll-hunt across the Timeline + Admin → History
+ Admin → Logs to triangulate. This module does the work upfront —
walks ``history``, ``notifications``, and ``host_failure_events`` for
the last N hours, groups events by ``(provider, error_pattern)`` where
``error_pattern`` is a small classified token (`timeout` / `auth` /
`refused` / `tls` / etc.), and returns aggregate stats.

The classification is deliberately coarse — every error message goes
through ``_classify_error()``'s ordered keyword scan and lands in one
of ~10 buckets. Coarser classification = more grouping = clearer
"this is the same problem" surface. Fine-grained matching (regex per
provider's exact error shape) buries the signal.

Public surface:

* :func:`triage_host(host_id, hours=720) -> dict` — returns
  ``{ groups: [...], scope: {...}, error: str | None }`` where each
  group has ``{ pattern, provider, count, first_ts, last_ts,
  avg_duration_s, sample_errors: [...], occurrences: [{ts, error}] }``.
  Empty list when nothing classified.
"""
from __future__ import annotations

import time
from typing import Optional

from logic.db import db_conn


# Ordered keyword scan — first match wins. Keys are the pattern label
# returned to the SPA; values are the case-insensitive substrings to
# match in the error / event text. Order MATTERS — more specific
# patterns first so e.g. "TLS handshake timeout" classifies as `tls`
# rather than `timeout`.
_ERROR_PATTERNS = (
    ("tls",          ("tls", "ssl handshake", "x509", "certificate", "self-signed", "unable to verify")),
    ("auth",         ("401", "403", "unauthor", "forbidden", "auth failed",
                       "permission denied", "invalid api key", "invalid credentials",
                       "invalid token", "bad credentials")),
    ("not-found",    ("404", "not found", "no such")),
    ("server-error", ("500", "502", "503", "504", "internal server", "bad gateway",
                       "service unavailable", "gateway timeout")),
    ("dns",          ("dns", "no such host", "name resolution",
                       "could not resolve", "name or service not known")),
    ("refused",      ("connection refused", "refused")),
    ("network",      ("network unreachable", "no route to host",
                       "host unreachable", "connection reset")),
    ("timeout",      ("timeout", "timed out", "deadline exceeded")),
    ("parse",        ("parse error", "decode error", "invalid response",
                       "json", "malformed")),
    ("rate-limit",   ("rate limit", "too many request", "429")),
)


def _classify_error(text: str) -> str:
    """Map an error message to one of ~10 buckets via case-insensitive
    keyword scan. Returns ``other`` for anything that doesn't match.
    Ordered scan — first match wins; more-specific patterns above.
    """
    if not text:
        return "other"
    lc = str(text).lower()
    for label, keywords in _ERROR_PATTERNS:
        for kw in keywords:
            if kw in lc:
                return label
    return "other"


def _parse_provider_from_history(op_type: str) -> str:
    """Best-effort provider extraction from a history row's op_type.

    Some history rows carry the provider in the op_type prefix (e.g.
    ``snmp_resume`` / ``ssh_run``); others are generic. The Timeline
    endpoint already exposes provider on transition events, but
    history rows don't have a provider column — we infer.
    """
    s = (op_type or "").lower()
    if s.startswith("snmp"):     return "snmp"
    if s.startswith("ssh"):      return "ssh"
    if s.startswith("webmin"):   return "webmin"
    if s.startswith("beszel"):   return "beszel"
    if s.startswith("pulse"):    return "pulse"
    if "stack" in s:             return "stack"
    if "container" in s:         return "container"
    if "service" in s:           return "service"
    return ""


def triage_host(host_id: str, hours: int = 720) -> dict:
    """Walk history / notifications / host_failure_events for the host
    over the last ``hours``, classify each error, group by
    ``(provider, pattern)``, return aggregated stats per group.

    ``hours`` clamps to the operator-friendly 1..2160 range (max 90
    days). Default 720 = 30 days, matching the operator's
    "last 30 days" mental model.
    """
    h = max(1, min(2160, int(hours or 720)))
    hid = (host_id or "").strip()
    if not hid:
        return {"groups": [], "scope": {"hours": h, "host_id": ""}, "error": "host_id required"}
    since = time.time() - h * 3600
    groups: dict[tuple[str, str], dict] = {}

    def _bucket(provider: str, pattern: str) -> dict:
        key = (provider or "", pattern or "other")
        b = groups.get(key)
        if b is None:
            b = {
                "provider":       key[0],
                "pattern":        key[1],
                "count":          0,
                "first_ts":       None,
                "last_ts":        None,
                "durations_s":    [],
                "sample_errors":  [],
                "occurrences":    [],
            }
            groups[key] = b
        return b

    def _record(b: dict, ts: float, err: str, duration_s: Optional[float] = None) -> None:
        b["count"] += 1
        ts_int = int(ts or 0)
        if b["first_ts"] is None or ts_int < b["first_ts"]:
            b["first_ts"] = ts_int
        if b["last_ts"] is None or ts_int > b["last_ts"]:
            b["last_ts"] = ts_int
        if duration_s is not None:
            try:
                b["durations_s"].append(float(duration_s))
            except (TypeError, ValueError):
                pass
        # Cap sample errors at 3 — enough for operator pattern-matching,
        # not so many they blow the response size.
        if err and len(b["sample_errors"]) < 3 and err not in b["sample_errors"]:
            b["sample_errors"].append(err[:300])
        # Cap full occurrence list at 50 per group — operators rarely
        # need to scroll past that, and keeps the endpoint response
        # tight on a chatty host.
        if len(b["occurrences"]) < 50:
            b["occurrences"].append({"ts": ts_int, "error": (err or "")[:200]})

    try:
        with db_conn() as c:
            # ---- history rows ------------------------------------------
            # Match either target_id == hid OR target_name == hid (legacy
            # rows) so we catch every op that touched this host.
            try:
                rows = c.execute(
                    "SELECT ts, op_type, status, error, duration "
                    "FROM history "
                    "WHERE ts >= ? AND status = 'error' "
                    "AND (target_id = ? OR target_name = ?)",
                    (since, hid, hid),
                ).fetchall()
            except Exception:
                rows = []
            for r in rows:
                err = r["error"] or ""
                pattern = _classify_error(err)
                provider = _parse_provider_from_history(r["op_type"] or "")
                _record(_bucket(provider, pattern), r["ts"], err, r["duration"])

            # ---- notifications -----------------------------------------
            # Only error / warning severities count as incidents. Skip
            # info / success — those are normal operational signals,
            # not failures to triangulate.
            try:
                rows = c.execute(
                    "SELECT ts, event, severity, body, metadata "
                    "FROM notifications "
                    "WHERE ts >= ? AND target_kind = 'host' AND target_id = ? "
                    "AND severity IN ('error', 'warning')",
                    (since, hid),
                ).fetchall()
            except Exception:
                rows = []
            for r in rows:
                err = r["body"] or ""
                pattern = _classify_error(err)
                # Try to pull provider out of metadata first, fall back
                # to event-prefix inference.
                provider = ""
                try:
                    import json as _json
                    md = _json.loads(r["metadata"]) if r["metadata"] else {}
                    if isinstance(md, dict):
                        provider = (md.get("provider") or "").strip().lower()
                except Exception:
                    pass
                if not provider:
                    provider = _parse_provider_from_history(r["event"] or "")
                _record(_bucket(provider, pattern), r["ts"], err)

            # ---- host_failure_events (transitions) ---------------------
            # `paused` events carry the error string in the `error`
            # column. `recovered` events have NULL error and are
            # interesting only as "previous incident ended" pairs —
            # surface them so the duration math can compute recovery
            # times. We MATCH paused→recovered pairs by walking the
            # rows in ts-ascending order and pairing consecutive same-
            # provider events.
            try:
                rows = c.execute(
                    "SELECT ts, provider, kind, error "
                    "FROM host_failure_events "
                    "WHERE host_id = ? AND ts >= ? "
                    "ORDER BY ts ASC",
                    (hid, since),
                ).fetchall()
            except Exception:
                rows = []
            # Track open paused incidents per provider to compute
            # duration when the matching `recovered` row arrives.
            open_paused: dict[str, dict] = {}
            for r in rows:
                kind = (r["kind"] or "").lower()
                provider = (r["provider"] or "").strip().lower() or "host"
                err = r["error"] or ""
                if kind == "paused":
                    pattern = _classify_error(err)
                    bucket = _bucket(provider, pattern)
                    _record(bucket, r["ts"], err)
                    open_paused[provider] = {"ts": r["ts"], "pattern": pattern}
                elif kind == "recovered":
                    open_inc = open_paused.pop(provider, None)
                    if open_inc:
                        # Append the recovery duration to the matching
                        # paused-bucket so the avg-recovery stat
                        # populates.
                        bucket = _bucket(provider, open_inc["pattern"])
                        try:
                            duration = max(0.0, float(r["ts"]) - float(open_inc["ts"]))
                            bucket["durations_s"].append(duration)
                        except (TypeError, ValueError):
                            pass
    except Exception as e:
        return {
            "groups": [], "scope": {"hours": h, "host_id": hid},
            "error":  f"triage query error: {type(e).__name__}: {e}",
        }

    # Finalise — compute avg_duration_s, sort occurrences newest-first,
    # convert to a stable list.
    out_groups: list[dict] = []
    for (provider, pattern), b in groups.items():
        avg = (sum(b["durations_s"]) / len(b["durations_s"])
               if b["durations_s"] else None)
        b["occurrences"].sort(key=lambda x: x["ts"], reverse=True)
        out_groups.append({
            "provider":       provider,
            "pattern":        pattern,
            "count":          b["count"],
            "first_ts":       b["first_ts"],
            "last_ts":        b["last_ts"],
            "avg_duration_s": int(avg) if avg is not None else None,
            "sample_errors":  b["sample_errors"],
            "occurrences":    b["occurrences"],
        })
    # Sort by last_ts DESC (most recent group first), then count DESC
    # as tiebreaker so a noisier but stale group ranks below a fresh
    # one with fewer hits.
    out_groups.sort(key=lambda g: (-(g["last_ts"] or 0), -g["count"]))
    return {
        "groups": out_groups,
        "scope":  {"hours": h, "host_id": hid, "since_ts": int(since)},
        "error":  None,
    }
