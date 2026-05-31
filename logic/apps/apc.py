"""APC per-app module — UPS (Smart-UPS / NMC / PDU) extras.

The APC catalog template renders a 5-stat panel (battery percent /
output load / runtime remaining / battery temperature / battery
state) on the Apps card. Source data flows from SNMP via the
standard host-stats pipeline (`host_battery_percent` /
`host_load_percent` / `host_battery_runtime_s` /
`host_battery_temp_c` / `host_ups_status` / `host_battery_status`)
and is persisted every probe into the `host_snmp_samples` table.

Why this module reads samples, not the live host row
----------------------------------------------------
APC needs no upstream auth probe (no api_key), no
`resolve_base_url()`, no `test_credential()`. It DOES expose
`fetch_data()` — but it reads the most-recent `host_snmp_samples`
row for the host (the SNMP sampler persists battery / load /
temperature percentages PLUS the UPS-status / battery-status
label strings + runtime-remaining seconds every probe). The Apps
APC card calls the generic
`/api/services/{host_id}/{service_idx}/app-data` endpoint so it
renders straight from the DB sample — it NEVER triggers a live
host probe from the card (a per-card SNMP round-trip on the hot
path would scale badly across a dashboard of cards). The module
still declares `SLUGS` so the `logic.apps.registry` dispatcher
can answer "does this slug have a registered per-app module?" —
that drives the SPA's `client_config.apps_module_slugs` UI gates.

Adding a sibling stats provider for a different UPS brand
---------------------------------------------------------
The five SNMP fields are already brand-agnostic (the
host-stats extractor stamps them whenever SNMP exposes the
relevant OIDs — Eaton / CyberPower / Tripp Lite etc.). To
support another UPS brand, either:
  (a) Extend this module's `SLUGS` tuple to include the new
      brand's catalog slug, OR
  (b) Drop a separate `logic/apps/<brand>.py` module that
      mirrors this one, and re-register the per-app SPA
      module / HTML partial under that brand's name.
Option (b) keeps the per-brand sprite icon + i18n strings
separate even though the underlying data is the same.
"""

import asyncio

# Catalog template slugs this module handles. Single-element today
# (`apc`); add aliases here for any UPS template that wants to
# reuse the SPA's `apc*` helpers + the APC extras partial.
SLUGS: tuple[str, ...] = ("apc",)


def _latest_ups_sample(host_id: str) -> dict:
    """Read the most-recent persisted UPS row for `host_id` from
    `host_snmp_samples`. Pure-sync (runs inside asyncio.to_thread).
    Returns the SPA-shaped dict; `available` is False when no row
    carries any UPS value (non-UPS host, or sampler hasn't written
    a row yet)."""
    from logic.db import db_conn
    out: dict = {
        "available": False,
        "battery_percent": None,
        "load_percent": None,
        "battery_temp_c": None,
        "battery_runtime_s": None,
        "ups_status": "",
        "battery_status": "",
        "ts": 0,
    }
    try:
        with db_conn() as c:
            row = c.execute(
                "SELECT battery_percent, load_percent, battery_temp_c, "
                "battery_runtime_s, ups_status, battery_status, ts "
                "FROM host_snmp_samples WHERE host_id = ? "
                "ORDER BY ts DESC LIMIT 1",
                (host_id,),
            ).fetchone()
    except Exception:  # noqa: BLE001 — a DB blip renders an empty panel, never fatal
        return out
    if not row:
        return out
    out["battery_percent"] = row[0]
    out["load_percent"] = row[1]
    out["battery_temp_c"] = row[2]
    out["battery_runtime_s"] = row[3]
    out["ups_status"] = row[4] or ""
    out["battery_status"] = row[5] or ""
    out["ts"] = int(row[6] or 0)
    # "available" = at least one UPS field came back non-null. A plain
    # SNMP host (no PowerNet OIDs) writes a row with every UPS column
    # NULL, so the card must distinguish that from a real UPS reading.
    out["available"] = any(
        out[k] is not None
        for k in ("battery_percent", "load_percent", "battery_temp_c", "battery_runtime_s")
    ) or bool(out["ups_status"] or out["battery_status"])
    return out


async def fetch_data(host_row: dict, chip: dict, *,
                     host_id: str, service_idx: int, force: bool = False) -> dict:
    """Return the latest UPS sample for the APC card. Reads the
    `host_snmp_samples` table (offloaded to a worker thread so the
    per-card poll never blocks the event loop) — the card renders
    from the DB, never a live host probe. `host_row` / `chip` /
    `service_idx` / `force` are part of the generic dispatcher
    contract but unused here (the sample row is keyed on host_id and
    the sampler owns refresh cadence; there's no upstream to force)."""
    return await asyncio.to_thread(_latest_ups_sample, host_id)
