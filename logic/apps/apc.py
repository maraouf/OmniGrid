"""APC per-app module — UPS (Smart-UPS / NMC / PDU) extras.

The APC catalog template renders a 5-stat panel (battery percent /
output load / runtime remaining / battery temperature / battery
state) on the Apps card. Source data flows from SNMP via the
standard host-stats pipeline (`host_battery_percent` /
`host_load_percent` / `host_battery_runtime_s` /
`host_battery_temp_c` / `host_ups_status` / `host_battery_status`
on the curated host row); the SPA's per-app helper resolves the
fields from `this.hosts` keyed by `inst.host_id` and renders the
panel.

Why this module exists with no helpers
--------------------------------------
APC needs no upstream auth probe (no api_key), no async data
fetch (data is already on the host row), no backend resolver
(extras are read directly from the merged host). The full
custom-rendering logic lives in the SPA + HTML partial. The
module exists ONLY to declare the `SLUGS` tuple so the
`logic.apps.registry` dispatcher can answer "does this slug
have a registered per-app module?" — the answer matters for
the SPA's `apps_module_slugs` list (`/api/me`'s
`client_config.apps_module_slugs`), which other generic helpers
walk to decide UI gates.

Per-app modules that DO need backend logic (Speedtest Tracker
is the reference) expose `requires_api_key()`,
`resolve_base_url()`, `test_credential()`, `fetch_data()`. APC
correctly omits all four — the generic dispatcher endpoints
return HTTP 400 for any caller that lands on
`/api/services/{host_id}/{service_idx}/test-credential` or
`/app-data` for an APC chip, which is the desired contract
(there's nothing to test or fetch upstream).

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

# Catalog template slugs this module handles. Single-element today
# (`apc`); add aliases here for any UPS template that wants to
# reuse the SPA's `apc*` helpers + the APC extras partial.
SLUGS: tuple[str, ...] = ("apc",)
