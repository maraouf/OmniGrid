"""Clear auto-pause markers for a curated host's sampling.

Shared by the non-web surfaces — the Telegram ``/resume`` command and the
Telegram-AI ``resume_host_sampling`` action — so both behave identically. The
operator's usual trigger is the bot's own "Host sampling paused: <host>
(<provider>)" alert; replying "resume sampling for that" now actually resumes it
instead of being told to go click a chip in the web UI.

Two scopes, matching the two web endpoints:

* ``provider=""`` — whole-host resume. Clears the bare-id row (``provider=''``)
  AND cascades to every per-provider row for that host, mirroring what the web
  drawer does (``resumeHostSampling`` fans out to ``resumeAllProviders``): one
  "resume this host" request clears every pause layer rather than leaving
  provider chips stuck.
* ``provider="<name>"`` — clears just that provider's row, leaving any
  whole-host pause intact.

SCOPE BOUNDARY (deliberate, documented rather than hidden): the web routes
``/api/hosts/{id}/resume-sampling`` and ``/api/hosts/{id}/provider/{name}/resume``
additionally evict provider-specific in-memory cool-downs and probe caches that
live in route-local module state (SNMP target cool-down + per-host SNMP/Webmin
caches, SSH cool-down). Reaching those from ``logic/`` would invert the layering,
so this module does NOT replicate them. The consequence is bounded and only
affects SNMP / Webmin: the pause itself is fully cleared either way, but the very
next probe for those two providers may wait out an unrelated cool-down (≤ the
auth-failure cool-down window) before hitting the wire. Every other provider —
including the ``http_probe`` case these alerts most often name — behaves
identically to the web path. Folding the route bodies onto this module is the
right follow-up; it needs the cool-down state to move out of the route modules
first.
"""
from __future__ import annotations

import time
from typing import Optional

from logic.db import db_conn

# Providers that can be individually auto-paused. Mirrors
# logic.host_metrics_sampler._PROVIDER_PREFIXES.
RESUMABLE_PROVIDERS: tuple[str, ...] = (
    "node_exporter", "beszel", "pulse", "webmin",
    "ping", "snmp", "http_probe", "service_probe",
)


def normalize_provider(value: Optional[str]) -> str:
    """Return a canonical provider name, or "" when absent / unrecognised.

    Tolerates the spellings an operator (or a model quoting an alert) actually
    types — ``HTTP-Probe`` / ``http probe`` / ``node exporter`` all normalise.
    """
    v = str(value or "").strip().lower().replace("-", "_").replace(" ", "_")
    return v if v in RESUMABLE_PROVIDERS else ""


def resume(host_id: str, provider: str = "", *, actor: str = "") -> dict:
    """Clear the auto-pause marker(s) for ``host_id``.

    ``provider`` empty → whole-host resume (cascades to every per-provider row).
    Returns ``{ok, cleared, provider, scope, error}``; never raises, so callers
    can turn the result straight into a chat reply.
    """
    provider = normalize_provider(provider) if provider else ""
    cleared = 0
    try:
        with db_conn() as c:
            if provider:
                cur = c.execute(
                    "DELETE FROM host_failure_state "
                    "WHERE host_id = ? AND provider = ?",
                    (host_id, provider),
                )
                cleared = cur.rowcount or 0
                _log_event(c, host_id, provider, actor)
            else:
                # Which per-provider rows exist BEFORE the delete, so each one
                # gets its own `recovered` timeline row (the drawer renders the
                # per-provider transitions, not just the host-level one).
                paused_providers = [
                    str(r[0] or "") for r in c.execute(
                        "SELECT provider FROM host_failure_state WHERE host_id = ?",
                        (host_id,),
                    ).fetchall()
                ]
                cur = c.execute(
                    "DELETE FROM host_failure_state WHERE host_id = ?",
                    (host_id,),
                )
                cleared = cur.rowcount or 0
                for p in paused_providers:
                    _log_event(c, host_id, p, actor)
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "cleared": 0, "provider": provider,
                "scope": provider or "host", "error": str(e)}
    _publish(host_id, provider)
    return {"ok": True, "cleared": cleared, "provider": provider,
            "scope": provider or "host", "error": ""}


def _log_event(c, host_id: str, provider: str, actor: str) -> None:
    """Append the manual-resume transition to ``host_failure_events`` so the
    drawer Timeline shows it next to the sampler's automatic transitions.
    Best-effort — a timeline write must never fail the resume itself."""
    try:
        c.execute(
            "INSERT INTO host_failure_events "
            "(ts, host_id, provider, kind, error, actor) "
            "VALUES (?, ?, ?, 'recovered', NULL, ?)",
            (time.time(), host_id, provider or "", actor or "unknown"),
        )
    except Exception as e:  # noqa: BLE001
        print(f"[host_resume] timeline write failed for {host_id}/{provider}: {e}")


def _publish(host_id: str, provider: str) -> None:
    """Tell open SPA tabs the pause cleared. `host_id` is the BARE id and the
    provider rides in its own field — the SPA looks the host up by bare id, so a
    prefixed key would 404 its refresh."""
    try:
        from logic import events as _events  # noqa: PLC0415
        payload = {"host_id": host_id, "paused": False, "cleared": True}
        if provider:
            payload["provider"] = provider
        _events.publish("host:failure_state_changed", payload)
    except Exception as e:  # noqa: BLE001
        print(f"[host_resume] SSE publish failed for {host_id}: {e}")
