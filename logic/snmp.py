"""SNMP host-stats provider — sixth in the host-stats family (#344).

Read-only consumer of SNMP-speaking devices: managed switches, routers,
print servers, UPS units, network printers, managed APs — anything that
speaks SNMP v2c or v3 but doesn't run a unix-style agent (Beszel /
node-exporter / Webmin can't reach those boxes).

Auth model
----------
SNMP comes in three protocol flavours. OmniGrid supports the two
modern ones; v1 (deprecated, plaintext community, no integrity) is
explicitly NOT wired:

    - **v2c** — community-string auth, plaintext on the wire. The
      common home-lab default for read-only access. ``community``
      defaults to ``"public"``; operators with community-rotated gear
      configure a custom value via Settings → Providers → SNMP OR
      per-host via ``hosts_config[].snmp.community``.
    - **v3 / USM** — secure variant. Three modes:
         * ``noAuthNoPriv`` — username only, no integrity / no
           confidentiality (rarely used).
         * ``authNoPriv`` — HMAC-SHA-256 auth, plaintext payload.
         * ``authPriv`` — HMAC-SHA-256 + AES-128 encryption.
      Selection of HMAC + AES variants is fixed at the modern set
      (SHA-256 / AES-128) — older MD5 / DES variants are not surfaced.

There is no real "lockout-on-failure" surface for SNMP — the agent
just stops responding (UDP). We DO short-circuit retries via the same
`Cooldown` pattern Webmin / SSH / Ping use, so a permanently-
unreachable host doesn't burn timeout budget every gather. Cool-down
arms after a single timeout (UDP timeouts are slower than the auth-
failure case Webmin sees, so two-strikes-then-arm is too forgiving).

Wire model
----------
Data is fetched via a small set of standard OIDs:

    SNMPv2-MIB::sysName.0          1.3.6.1.2.1.1.5.0
    SNMPv2-MIB::sysDescr.0         1.3.6.1.2.1.1.1.0
    SNMPv2-MIB::sysUpTime.0        1.3.6.1.2.1.1.3.0
    HOST-RESOURCES-MIB::hrStorage* 1.3.6.1.2.1.25.2.3.1.* (RAM + disk)
    HOST-RESOURCES-MIB::hrProcessorLoad 1.3.6.1.2.1.25.3.3.1.2 (CPU%)
    IF-MIB::ifDescr / ifOperStatus 1.3.6.1.2.1.2.2.1.{2,8}
    IF-MIB::ifHCInOctets / Out     1.3.6.1.2.1.31.1.1.1.{6,10}  (64-bit)
    IF-MIB::ifInOctets / Out       1.3.6.1.2.1.2.2.1.{10,16}    (32-bit fallback)

Standard MIB-II + Host Resources MIB. Coverage of HRMIB is best on
*nix-style appliances (e.g. UniFi/Synology); printers and small
embedded gear may only respond to MIB-II — in those cases we surface
sysName / sysDescr / sysUpTime + ifTable counters and skip the rest.
Per-OID failures are absorbed into a partial-data response.

Optional dep
------------
``pysnmp`` is the SNMP engine. Lazy-imported (mirroring icmplib in
logic/ping.py) so a missing package doesn't block OmniGrid's import
path or break unrelated providers — ``has_snmp_support()`` returns
False and the Settings tab disables the SNMP master toggle with a
"package missing" hint when the import fails.

Per-host vs. hub
----------------
Each SNMP-speaking device is its own agent — there is no central hub.
The shape mirrors Webmin: ``probe_snmp(host, community=, version=,
port=)`` runs ONE host per call; the gather + per-host-merge paths
fan out via ``asyncio.gather``. Defaults flow from settings; per-host
overrides live in ``hosts_config[].snmp = {community, version, port,
v3_user, v3_auth_key, v3_priv_key}``.

Merge order
-----------
SNMP slots into the merge chain AFTER Pulse but BEFORE Beszel:

    Pulse → SNMP → Beszel → node-exporter → Webmin

Rationale: SNMP's data is COARSER than the unix-style providers
(no per-mount disk detail, no proper memory accounting on most
embedded gear, no kernel/arch reporting); Beszel/NE/Webmin should
override SNMP wherever they have better numbers. SNMP runs AHEAD of
Pulse-in-the-merge-chain ONLY in the sense that Pulse is even coarser
on non-PVE gear (it returns nothing). On dedicated network gear that
ONLY speaks SNMP, the value lands cleanly without contention.
"""
from __future__ import annotations

import asyncio
import time
from typing import Optional

# Cool-down on consecutive timeouts. Different lever than the Webmin /
# SSH 401 cool-down (no auth challenge in SNMP — there's no credential
# lockout to defend against). Pre-#678 we shared
# `tuning_auth_failure_cooldown_seconds` with Webmin / SSH; operators
# debugging "SNMP timing out" reached for the wrong knob. Now uses a
# dedicated `tuning_snmp_unreachable_cooldown_seconds` (default 300s,
# range 30..3600). Per-(host, port) key.
from logic.cooldown import Cooldown as _Cooldown
from logic import tuning as _tuning
_unreachable_cooldown = _Cooldown(
    seconds_fn=lambda: _tuning.tuning_int("tuning_snmp_unreachable_cooldown_seconds")
)

# #671 — module-level SnmpEngine singleton. pysnmp HLAPI engines carry
# per-engine state (USM key cache, message-id state); allocating one
# fresh per probe wasted ~2-3 ms × N concurrent × every gather cadence.
# Lazy-init under an asyncio.Lock so the first concurrent burst doesn't
# race two allocations. Reused across every `probe_snmp` call. We
# don't close the engine explicitly — pysnmp's HLAPI doesn't expose a
# clean shutdown path, and lifespan teardown drops the process anyway.
import asyncio as _asyncio_for_engine_lock
_engine_singleton = None
_engine_lock = _asyncio_for_engine_lock.Lock()


async def _get_snmp_engine():
    global _engine_singleton
    if _engine_singleton is not None:
        return _engine_singleton
    async with _engine_lock:
        if _engine_singleton is None:
            _engine_singleton = SnmpEngine()
    return _engine_singleton


# Standard OIDs we walk. Kept as constants so the extractor branches
# are obvious and a future MIB extension is one line.
_OID_SYS_DESCR     = "1.3.6.1.2.1.1.1.0"
_OID_SYS_UPTIME    = "1.3.6.1.2.1.1.3.0"
_OID_SYS_CONTACT   = "1.3.6.1.2.1.1.4.0"
_OID_SYS_NAME      = "1.3.6.1.2.1.1.5.0"
_OID_SYS_LOCATION  = "1.3.6.1.2.1.1.6.0"
# #681 — ENTITY-MIB physical-component table. Vendor-agnostic surface
# for model name / serial number / firmware version on enterprise gear
# (Cisco / Dell iDRAC / HP / Juniper / etc.) that doesn't necessarily
# implement Host Resources MIB. Walk by sub-tree so we get every
# physical entry (chassis, slot, supply, fan, port). Most agents put
# the chassis-level info at index 1.
_OID_ENT_DESCR        = "1.3.6.1.2.1.47.1.1.1.1.2"
_OID_ENT_NAME         = "1.3.6.1.2.1.47.1.1.1.1.7"
_OID_ENT_SOFTWARE_REV = "1.3.6.1.2.1.47.1.1.1.1.10"
_OID_ENT_SERIAL_NUM   = "1.3.6.1.2.1.47.1.1.1.1.11"
_OID_ENT_MODEL_NAME   = "1.3.6.1.2.1.47.1.1.1.1.13"
_OID_ENT_PHYS_CLASS   = "1.3.6.1.2.1.47.1.1.1.1.5"

# #682 — Vendor-private MIBs for hosts whose SNMP profile blocks the
# standard MIB-II / Host Resources / ENTITY-MIB surfaces. iDRAC v6+ in
# particular returns nothing useful from sysDescr / hrStorage / ifTable /
# ENTITY-MIB on the default community profile, but exposes rich data
# under DELL-RAC-MIB. Cisco SG300 / SG350 / SG500 SMB switches expose
# CPU% + memory pools + product hardware version under
# CISCO-MEMORY-POOL-MIB + CISCO-PROCESS-MIB + CISCO-PRODUCTS-MIB.
#
# These are GETs (not walks) where the OID resolves to a single value;
# walks where the OID resolves to a table (memory pools, CPU per-engine).
# An agent that doesn't speak the vendor's MIB returns "noSuchObject" /
# empty walk and our extractors silently fall through.
#
# Dell DELL-RAC-MIB (iDRAC). Stable since iDRAC6, current on iDRAC9/10.
# Service tag = serial; chassis model = product name; racFirmwareVersion
# = iDRAC firmware string (e.g. "5.10.30.00"). globalSystemStatus uses
# the standard Dell Systems Management Server Health enum:
#   1=other, 2=unknown, 3=ok, 4=non-critical, 5=critical, 6=non-recoverable
_OID_DELL_CHASSIS_SERVICE_TAG = "1.3.6.1.4.1.674.10892.5.1.3.2.0"
_OID_DELL_CHASSIS_MODEL       = "1.3.6.1.4.1.674.10892.5.1.3.3.0"
_OID_DELL_RAC_FIRMWARE        = "1.3.6.1.4.1.674.10892.5.1.1.6.0"
_OID_DELL_GLOBAL_SYS_STATUS   = "1.3.6.1.4.1.674.10892.5.4.200.10.1.4.1"
# Dell host-system info (different sub-tree from chassis): system
# service tag + product short name. Some iDRAC firmware revs only
# answer the host-system OIDs, others only chassis — probe both.
_OID_DELL_SYSTEM_SERVICE_TAG  = "1.3.6.1.4.1.674.10892.5.4.300.10.1.11.1"
_OID_DELL_SYSTEM_MODEL_NAME   = "1.3.6.1.4.1.674.10892.5.4.300.10.1.9.1"
# Cisco — covers SG300 / SG350 / SG500 / Catalyst / Nexus.
# productHardwareVer is SG300-specific (under enterprises.9.6.1.101);
# memory pool + CPU% are common across the Cisco family.
_OID_CISCO_PRODUCT_HW_VER       = "1.3.6.1.4.1.9.6.1.101.1.1.0"
_OID_CISCO_MEM_POOL_NAME        = "1.3.6.1.4.1.9.9.48.1.1.1.2"
_OID_CISCO_MEM_POOL_USED        = "1.3.6.1.4.1.9.9.48.1.1.1.5"
_OID_CISCO_MEM_POOL_FREE        = "1.3.6.1.4.1.9.9.48.1.1.1.6"
_OID_CISCO_CPU_TOTAL_5SEC       = "1.3.6.1.4.1.9.9.109.1.1.1.1.7"

# Dell global-status enum → string label.
_DELL_STATUS_LABELS = {
    1: "other", 2: "unknown", 3: "ok",
    4: "non-critical", 5: "critical", 6: "non-recoverable",
}

# #683 — APC PowerNet-MIB (UPS / PDU). Smart-UPS family answers under
# 1.3.6.1.4.1.318.x. Standard UPS-MIB (RFC 1628) is sometimes also
# present but APC's PowerNet OIDs carry more detail (battery temp,
# runtime in TimeTicks, load% per-phase). One probe pass covers
# Smart-UPS RT, Back-UPS, BR-series, the rack PDU family, etc.
_OID_APC_UPS_MODEL          = "1.3.6.1.4.1.318.1.1.1.1.1.1.0"
_OID_APC_UPS_NAME           = "1.3.6.1.4.1.318.1.1.1.1.1.2.0"
_OID_APC_UPS_FIRMWARE       = "1.3.6.1.4.1.318.1.1.1.1.2.1.0"
_OID_APC_UPS_SERIAL         = "1.3.6.1.4.1.318.1.1.1.1.2.3.0"
_OID_APC_UPS_BATT_STATUS    = "1.3.6.1.4.1.318.1.1.1.2.1.1.0"
_OID_APC_UPS_BATT_CAPACITY  = "1.3.6.1.4.1.318.1.1.1.2.2.1.0"
_OID_APC_UPS_BATT_TEMP_C    = "1.3.6.1.4.1.318.1.1.1.2.2.2.0"
_OID_APC_UPS_BATT_RUNTIME   = "1.3.6.1.4.1.318.1.1.1.2.2.3.0"  # TimeTicks
_OID_APC_UPS_OUTPUT_STATUS  = "1.3.6.1.4.1.318.1.1.1.4.1.1.0"
_OID_APC_UPS_OUTPUT_LOAD    = "1.3.6.1.4.1.318.1.1.1.4.2.3.0"

# APC battery-status enum (from PowerNet-MIB upsBasicBatteryStatus).
_APC_BATT_STATUS_LABELS = {
    1: "unknown", 2: "battery-normal", 3: "battery-low",
    4: "battery-in-fault",
}
# APC output-status enum (from PowerNet-MIB upsBasicOutputStatus).
# Subset that's actually emitted by the Smart-UPS family (full enum
# carries 12 values; the rare ones rarely appear in production).
_APC_OUTPUT_STATUS_LABELS = {
    1: "unknown", 2: "online", 3: "on-battery",
    4: "on-smart-boost", 5: "timed-sleeping", 6: "software-bypass",
    7: "off", 8: "rebooting", 9: "switched-bypass", 10: "hardware-failure-bypass",
    11: "sleeping-until", 12: "on-smart-trim",
}

# #684 — UCD-SNMP-MIB (1.3.6.1.4.1.2021.x). The universal Linux
# net-snmp surface — present on basically every Linux distro running
# net-snmp (DD-WRT / OpenWrt / Synology / generic embedded boxes that
# don't ship Beszel/NE agents). Sometimes the only useful surface on
# routers whose snmpd builds Host-Resources OFF for size.
_OID_UCD_MEM_TOTAL_REAL = "1.3.6.1.4.1.2021.4.5.0"
_OID_UCD_MEM_AVAIL_REAL = "1.3.6.1.4.1.2021.4.6.0"
_OID_UCD_MEM_TOTAL_FREE = "1.3.6.1.4.1.2021.4.11.0"
_OID_UCD_SS_CPU_USER    = "1.3.6.1.4.1.2021.11.9.0"
_OID_UCD_SS_CPU_SYSTEM  = "1.3.6.1.4.1.2021.11.10.0"
_OID_UCD_SS_CPU_IDLE    = "1.3.6.1.4.1.2021.11.11.0"
# laLoadInt walk — three rows: 1m / 5m / 15m × 100 (centi-load).
# Index 1=1m, 2=5m, 3=15m on every snmpd impl I've seen.
_OID_UCD_LA_LOAD_INT    = "1.3.6.1.4.1.2021.10.1.5"
# dskTable walk — per-mount path / total / used / percent.
_OID_UCD_DSK_PATH    = "1.3.6.1.4.1.2021.9.1.2"
_OID_UCD_DSK_TOTAL   = "1.3.6.1.4.1.2021.9.1.6"   # KB
_OID_UCD_DSK_USED    = "1.3.6.1.4.1.2021.9.1.8"   # KB
_OID_UCD_DSK_PERCENT = "1.3.6.1.4.1.2021.9.1.9"

# #685 — SYNOLOGY-MIB. DSM-based NAS (DiskStation, RackStation, etc.).
# DSM 7+ also implements Host Resources MIB; these OIDs add identity +
# DSM-specific health surface (temperature, upgrade-available flag).
_OID_SYNO_MODEL_NAME    = "1.3.6.1.4.1.6574.1.5.5.0"
_OID_SYNO_SERIAL_NUMBER = "1.3.6.1.4.1.6574.1.5.4.0"
_OID_SYNO_DSM_VERSION   = "1.3.6.1.4.1.6574.1.5.3.0"
_OID_SYNO_SYSTEM_STATUS = "1.3.6.1.4.1.6574.1.1.0"
_OID_SYNO_SYSTEM_TEMP   = "1.3.6.1.4.1.6574.1.2.0"
_OID_SYNO_UPGRADE_AVAIL = "1.3.6.1.4.1.6574.1.5.1.0"

# Synology system-status enum (1=normal, 2=failed).
_SYNO_STATUS_LABELS = {
    1: "ok", 2: "failed",
}
# Synology upgrade-available enum (per DSM MIB definition):
# 1=available, 2=unavailable, 3=connecting, 4=disconnected, 5=others.
_SYNO_UPGRADE_LABELS = {
    1: "available", 2: "up-to-date",
    3: "checking", 4: "disconnected", 5: "other",
}

# #702 — Printer-MIB (RFC 1759 / 3805). Universal printer surface —
# HP / Brother / Canon / Epson / Xerox / Konica all implement it.
# `prtMarkerLifeCount` is the lifetime page count (single GET);
# `prtMarkerSupplies*` are per-supply walks (one row per toner /
# ink cartridge / drum / waste container) with description, max
# capacity, and current level.
_OID_PRT_PAGE_COUNT       = "1.3.6.1.2.1.43.10.2.1.4.1.1"
_OID_PRT_SUPPLIES_DESCR   = "1.3.6.1.2.1.43.11.1.1.6"
_OID_PRT_SUPPLIES_MAX_CAP = "1.3.6.1.2.1.43.11.1.1.8"
_OID_PRT_SUPPLIES_LEVEL   = "1.3.6.1.2.1.43.11.1.1.9"
# Console / display message — useful when the printer is in an
# error state ("Replace toner Y" / "Paper jam").
_OID_PRT_CONSOLE_MSG      = "1.3.6.1.2.1.43.16.5.1.2.1.1"
_OID_HR_STORAGE_TYPE  = "1.3.6.1.2.1.25.2.3.1.2"
_OID_HR_STORAGE_DESC  = "1.3.6.1.2.1.25.2.3.1.3"
_OID_HR_STORAGE_UNIT  = "1.3.6.1.2.1.25.2.3.1.4"
_OID_HR_STORAGE_SIZE  = "1.3.6.1.2.1.25.2.3.1.5"
_OID_HR_STORAGE_USED  = "1.3.6.1.2.1.25.2.3.1.6"
_OID_HR_CPU_LOAD      = "1.3.6.1.2.1.25.3.3.1.2"
_OID_IF_DESCR         = "1.3.6.1.2.1.2.2.1.2"
_OID_IF_OPER_STATUS   = "1.3.6.1.2.1.2.2.1.8"
_OID_IF_IN_OCTETS_32  = "1.3.6.1.2.1.2.2.1.10"
_OID_IF_OUT_OCTETS_32 = "1.3.6.1.2.1.2.2.1.16"
_OID_IF_HC_IN_OCTETS  = "1.3.6.1.2.1.31.1.1.1.6"
_OID_IF_HC_OUT_OCTETS = "1.3.6.1.2.1.31.1.1.1.10"

# HOST-RESOURCES-MIB hrStorageType OID prefixes — the value of an
# hrStorageType row points at one of these well-known OIDs.
_OID_HR_TYPE_RAM        = "1.3.6.1.2.1.25.2.1.2"  # hrStorageRam
_OID_HR_TYPE_VIRT_MEM   = "1.3.6.1.2.1.25.2.1.3"  # hrStorageVirtualMemory
_OID_HR_TYPE_FIXED_DISK = "1.3.6.1.2.1.25.2.1.4"  # hrStorageFixedDisk
_OID_HR_TYPE_REMOVABLE  = "1.3.6.1.2.1.25.2.1.5"  # hrStorageRemovableDisk

# Interface descriptions to skip when computing host-wide rx/tx totals.
_LOOPBACK_PREFIXES = ("lo", "loopback", "null", "vlan-internal", "docker", "veth")


# pysnmp is OPTIONAL — same lazy-optional pattern as icmplib in logic/ping.py.
# Importing the asyncio HLAPI surface up front is fine because pysnmp is a
# pure-Python wheel; we do it inside try/except so a missing package
# doesn't trip the whole logic package's import.
#
# pysnmp version compat — multiple breaking changes between 6.x and 7.x:
#  1. Module path: `pysnmp.hlapi.asyncio` (5.x / 6.x) — some 7.x lines
#     ALSO ship a `pysnmp.hlapi.v3arch.asyncio` namespace. Try v3arch
#     first, fall back to the legacy path.
#  2. Function names: `getCmd` / `bulkCmd` (camelCase, ≤6.x) renamed
#     to `get_cmd` / `bulk_cmd` (PEP 8 snake_case, 7.x).
#  3. USM protocol constants: `usmHMACSHA256AuthProtocol` etc. (camelCase,
#     ≤6.x) renamed to `USM_AUTH_HMAC192_SHA256` (UPPER_SNAKE_CASE, 7.x).
#     The full mapping isn't documented anywhere stable; resolve each
#     constant by trying every known historical name and let `_resolve`
#     return None when none match — the v3 USM probe path raises a
#     clear "v3 protocol unavailable" error in that case while v2c
#     probes keep working unaffected.
# Strategy: resolve every symbol by name lookup against the imported
# module rather than `from … import …` — that lets one missing or
# renamed symbol fail JUST that symbol's branch (v3 USM specifically)
# instead of failing the whole package's import. Aliases the modern
# names to the camelCase variables this module already references so
# the rest of the file's call sites stay stable across pysnmp versions.
# Captures the actual error text so future drift surfaces in the
# `[snmp] pysnmp import failed: …` server log + the SPA's inline hint
# (the operator no longer has to grep logs to identify renames).
_SNMP_IMPORT_ERROR = ""
SnmpEngine = CommunityData = UsmUserData = None  # type: ignore[assignment]
UdpTransportTarget = ContextData = ObjectType = ObjectIdentity = None  # type: ignore[assignment]
getCmd = bulkCmd = bulkWalkCmd = walkCmd = None  # type: ignore[assignment]
usmHMACSHAAuthProtocol = usmHMACSHA256AuthProtocol = None  # type: ignore[assignment]
usmAesCfb128Protocol = None  # type: ignore[assignment]
usmNoAuthProtocol = usmNoPrivProtocol = None  # type: ignore[assignment]
try:
    import importlib as _importlib
    _hlapi = None
    _SNMP_HLAPI_NS = ""
    for _ns in ("pysnmp.hlapi.v3arch.asyncio", "pysnmp.hlapi.asyncio"):
        try:
            _hlapi = _importlib.import_module(_ns)
            _SNMP_HLAPI_NS = _ns
            break
        except ImportError:
            continue
    if _hlapi is None:
        raise ImportError("pysnmp not installed (neither v3arch nor legacy hlapi paths importable)")

    def _resolve_required(*candidates: str):
        """First-name-that-exists wins; raise if none of the candidates
        resolve. Use for symbols every SNMP path needs (engine, target,
        OID builders, command functions)."""
        for n in candidates:
            v = getattr(_hlapi, n, None)
            if v is not None:
                return v
        raise ImportError(
            f"none of {candidates!r} exported by {_SNMP_HLAPI_NS} — "
            f"pysnmp may have renamed/removed them again"
        )

    def _resolve_optional(*candidates: str):
        """First-name-that-exists wins; returns None if none match.
        Use for v3-only USM constants — v2c probes don't need them, so
        a missing/renamed v3 protocol shouldn't disable the whole
        SNMP provider. The v3 code path checks for None and surfaces
        a clear "v3 protocol unavailable for SHA256 — pysnmp may have
        renamed it" error per probe instead of disabling everything."""
        for n in candidates:
            v = getattr(_hlapi, n, None)
            if v is not None:
                return v
        return None

    SnmpEngine         = _resolve_required("SnmpEngine")
    CommunityData      = _resolve_required("CommunityData")
    UsmUserData        = _resolve_required("UsmUserData")
    UdpTransportTarget = _resolve_required("UdpTransportTarget")
    ContextData        = _resolve_required("ContextData")
    ObjectType         = _resolve_required("ObjectType")
    ObjectIdentity     = _resolve_required("ObjectIdentity")

    # Cmd functions — snake_case (7.x) preferred, camelCase (≤6.x) fallback.
    getCmd  = _resolve_required("get_cmd",  "getCmd")
    bulkCmd = _resolve_required("bulk_cmd", "bulkCmd")
    # In pysnmp 7.x `bulk_cmd` returns a coroutine (single response —
    # not iterable!), and walks must use `bulk_walk_cmd` (or `walk_cmd`
    # for the slower GETNEXT-based walk). `_snmp_walk` checks for
    # bulkWalkCmd FIRST and falls through to legacy bulkCmd-as-iterator
    # only when the modern function isn't available (pysnmp ≤6.x).
    # The "TypeError: 'async for' requires an object with __aiter__
    # method, got coroutine" runtime error we hit on the live deploy
    # was exactly this: 7.x's bulk_cmd is no longer iterable.
    bulkWalkCmd = _resolve_optional("bulk_walk_cmd", "bulkWalkCmd")
    walkCmd     = _resolve_optional("walk_cmd",      "walkCmd")

    # USM protocols (v3-only — optional, see _resolve_optional docstring).
    # 7.x UPPER_SNAKE_CASE first, ≤6.x camelCase fallback. Variant names
    # cover the bit-length-prefixed forms (HMAC96 / HMAC192 / HMAC384) the
    # 7.x release notes used.
    usmHMACSHAAuthProtocol    = _resolve_optional(
        "USM_AUTH_HMAC96_SHA",
        "USM_AUTH_HMAC_SHA",
        "usmHMACSHAAuthProtocol",
    )
    usmHMACSHA256AuthProtocol = _resolve_optional(
        "USM_AUTH_HMAC192_SHA256",
        "USM_AUTH_HMAC_SHA256",
        "usmHMACSHA256AuthProtocol",
    )
    usmAesCfb128Protocol      = _resolve_optional(
        "USM_PRIV_CFB128_AES",
        "USM_PRIV_AES",
        "usmAesCfb128Protocol",
    )
    usmNoAuthProtocol         = _resolve_optional(
        "USM_AUTH_NONE",
        "usmNoAuthProtocol",
    )
    usmNoPrivProtocol         = _resolve_optional(
        "USM_PRIV_NONE",
        "usmNoPrivProtocol",
    )
    _HAS_SNMP = True
except Exception as _e:
    _HAS_SNMP = False
    _SNMP_IMPORT_ERROR = f"{type(_e).__name__}: {_e}"
    print(f"[snmp] pysnmp import failed — SNMP probes disabled: {_SNMP_IMPORT_ERROR}")


def has_snmp_support() -> bool:
    """Public probe — returns True iff ``pysnmp`` is importable.

    The Settings → Providers → SNMP tab consults this to disable the
    master toggle with a "package missing" hint when the dep isn't
    installed. Mirrors `logic.ping.has_icmp_support` exactly.
    """
    return _HAS_SNMP


def _normalize_arch(s: str) -> str:
    """Light-touch architecture normalisation — same convention the
    other providers use (amd64 → x86_64, etc.)."""
    if not s:
        return ""
    s = s.strip().lower()
    return {"amd64": "x86_64", "arm64": "aarch64"}.get(s, s)


def _in_cooldown(host: str, port: int) -> Optional[float]:
    """Return remaining cool-down seconds, or None if probes can fire."""
    return _unreachable_cooldown.remaining(host, port)


def _arm_cooldown(host: str, port: int) -> None:
    _unreachable_cooldown.arm(host, port)


def _clear_cooldown(host: str, port: int) -> None:
    _unreachable_cooldown.clear(host, port)


def _build_auth_data(
    *,
    version: str,
    community: str,
    v3_user: str,
    v3_auth_key: str,
    v3_priv_key: str,
):
    """Construct the pysnmp auth-data object for the chosen version.

    v2c → CommunityData; v3 → UsmUserData with auth/priv selection
    derived from which keys are non-empty:
      * neither key  → noAuthNoPriv
      * auth only    → authNoPriv (HMAC-SHA-256)
      * auth + priv  → authPriv  (HMAC-SHA-256 + AES-128)

    Returns None when the build fails (e.g. v3 selected without a user)
    so the caller can surface the error before hitting the wire.
    """
    if not _HAS_SNMP:
        return None
    v = (version or "v2c").strip().lower()
    if v == "v2c":
        return CommunityData(community or "public", mpModel=1)
    if v == "v3":
        if not v3_user:
            return None
        if v3_auth_key and v3_priv_key:
            return UsmUserData(
                v3_user, v3_auth_key, v3_priv_key,
                authProtocol=usmHMACSHA256AuthProtocol,
                privProtocol=usmAesCfb128Protocol,
            )
        if v3_auth_key:
            return UsmUserData(
                v3_user, v3_auth_key,
                authProtocol=usmHMACSHA256AuthProtocol,
                privProtocol=usmNoPrivProtocol,
            )
        return UsmUserData(
            v3_user,
            authProtocol=usmNoAuthProtocol,
            privProtocol=usmNoPrivProtocol,
        )
    # Unknown version — fall back to v2c with the supplied community.
    return CommunityData(community or "public", mpModel=1)


async def _snmp_get(engine, auth, target, oids: list[str]) -> dict[str, object]:
    """One SNMP GET with a list of OIDs. Returns ``{oid_str: value}``.

    Per-OID failures inside a successful response are skipped silently
    (some agents `noSuchInstance` the absent ones); a transport-level
    error returns an empty dict so the caller's `if-missing` checks
    naturally fall through.
    """
    if not _HAS_SNMP:
        return {}
    try:
        errorIndication, errorStatus, errorIndex, varBinds = await getCmd(
            engine, auth, target, ContextData(),
            *(ObjectType(ObjectIdentity(o)) for o in oids),
        )
    except (asyncio.CancelledError, KeyboardInterrupt):
        # MED-005 — never swallow cancellation. Lifespan shutdown
        # depends on it propagating up the await chain so the loop
        # task cancels cleanly.
        raise
    except Exception as e:
        # MED-005 — log the exception type so debugging "host returned
        # no data" doesn't have to start with "is this a real network
        # issue or a code bug?". One line per error site is fine —
        # each call site is bounded by the gather's per-OID concurrency.
        print(f"[snmp] WARN GET error against {target} oids={oids}: "
              f"{type(e).__name__}: {e}")
        return {}
    if errorIndication:
        print(f"[snmp] GET errorIndication: {errorIndication}")
        return {}
    if errorStatus:
        print(f"[snmp] GET errorStatus: {errorStatus.prettyPrint()}")
        return {}
    out: dict[str, object] = {}
    for oid, val in varBinds:
        oid_s = str(oid)
        # pysnmp uses sentinel pretty-print strings for absent rows;
        # treat those as "not present" and skip.
        prn = val.prettyPrint() if hasattr(val, "prettyPrint") else str(val)
        if prn in ("noSuchObject", "noSuchInstance", "endOfMibView"):
            continue
        out[oid_s] = val
    return out


async def _snmp_walk(engine, auth, target, base_oid: str,
                     max_rows: int = 256) -> dict[str, object]:
    """Walk a sub-tree under ``base_oid``. Returns ``{full_oid: value}``.

    Bounded by ``max_rows`` so a misbehaving agent can't loop us forever.
    Uses GETBULK (SNMP v2c+) for efficiency; on v1 fallback this would
    need rewriting to GETNEXT but we don't support v1.

    #687 — pysnmp 7.x split the bulk-walk path off `bulk_cmd`. In 7.x:
      - `bulk_cmd(...)` returns a coroutine that yields ONE response —
        NOT iterable. ``async for`` over it raises
        "TypeError: 'async for' requires an object with __aiter__,
        got coroutine".
      - `bulk_walk_cmd(...)` returns an async iterator — the real walk.
    On pysnmp ≤6.x, `bulkCmd` itself returned the iterator and there
    was no separate `bulkWalkCmd`. Resolution: prefer bulkWalkCmd when
    the version exposes it, fall back to bulkCmd (legacy iterator).
    """
    if not _HAS_SNMP:
        return {}
    out: dict[str, object] = {}
    walk_fn = bulkWalkCmd or bulkCmd
    iterator = walk_fn(
        engine, auth, target, ContextData(),
        0, 25,  # nonRepeaters, maxRepetitions
        ObjectType(ObjectIdentity(base_oid)),
        lexicographicMode=False,
    )
    try:
        async for errorIndication, errorStatus, errorIndex, varBinds in iterator:
            if errorIndication or errorStatus:
                print(f"[snmp] WALK error on {base_oid}: "
                      f"{errorIndication or errorStatus}")
                break
            if not varBinds:
                break
            for oid, val in varBinds:
                oid_s = str(oid)
                if not oid_s.startswith(base_oid):
                    return out
                prn = val.prettyPrint() if hasattr(val, "prettyPrint") else str(val)
                if prn in ("noSuchObject", "noSuchInstance", "endOfMibView"):
                    continue
                out[oid_s] = val
                if len(out) >= max_rows:
                    return out
    except (asyncio.CancelledError, KeyboardInterrupt):
        # MED-005 — same cancellation contract as _snmp_get.
        raise
    except Exception as e:
        # MED-005 — log the exception type so a misformatted OID or
        # pysnmp internal bug shows up as a code-bug signal instead of
        # a network-failure signal.
        print(f"[snmp] WARN WALK error on {base_oid} against {target}: "
              f"{type(e).__name__}: {e}")
    return out


def _coerce_int(v) -> int:
    """Best-effort cast — pysnmp returns ASN.1 wrapper objects whose
    str/int conversion is well-defined; defensive for edge cases."""
    if v is None:
        return 0
    try:
        return int(v)
    except (TypeError, ValueError):
        try:
            return int(str(v))
        except (TypeError, ValueError):
            return 0


def _coerce_str(v) -> str:
    if v is None:
        return ""
    try:
        return str(v).strip()
    except Exception:
        return ""


def _last_index(oid_full: str, base: str) -> str:
    """Strip ``base.`` prefix from a full OID and return the trailing
    index portion (one or more dotted segments). Empty when the
    relationship doesn't hold (defensive)."""
    if not oid_full or not oid_full.startswith(base):
        return ""
    suffix = oid_full[len(base):].lstrip(".")
    return suffix


# ---------------------------------------------------------------------
# Pure extractors over raw walk/get dicts. Kept side-effect-free so a
# future pytest suite can add fixture-based regression tests cheaply.
# ---------------------------------------------------------------------
def extract_sys_info(get_result: dict) -> dict:
    """Shape sysName / sysDescr / sysUpTime / sysContact / sysLocation
    into host_* fields. #681 added contact + location alongside the
    pre-existing system info."""
    out: dict = {}
    name = _coerce_str(get_result.get(_OID_SYS_NAME))
    descr = _coerce_str(get_result.get(_OID_SYS_DESCR))
    up_ticks = _coerce_int(get_result.get(_OID_SYS_UPTIME))
    contact = _coerce_str(get_result.get(_OID_SYS_CONTACT))
    location = _coerce_str(get_result.get(_OID_SYS_LOCATION))
    if name:
        out["host_hostname"] = name
    if descr:
        # sysDescr typically reads:
        #   "Linux router 5.15.0 #1 SMP Tue Apr 1 ..."
        #   "RouterOS RB4011iGS+, RouterOS 6.49.7"
        #   "HP ETHERNET MULTI-ENVIRONMENT, Firmware version 12.34"
        # Best-effort split: first token before the first comma OR the
        # first whitespace-separated word.
        out["host_os"] = descr
        first = descr.split(",")[0].strip().split()
        if first:
            out["host_platform"] = first[0]
        # Pull out a kernel hint when one is obvious (Linux X.Y.Z).
        for tok in descr.split():
            if tok.startswith(("3.", "4.", "5.", "6.")) and tok.count(".") >= 1:
                out["host_kernel"] = tok
                break
    if up_ticks:
        # sysUpTime is in centiseconds (TimeTicks). Convert to seconds.
        uptime_s = up_ticks // 100
        out["host_uptime_s"] = uptime_s
        if uptime_s > 0:
            out["host_boot_ts"] = float(time.time() - uptime_s)
    # #681 — sysContact / sysLocation are operator-set on most managed
    # gear; expose them as host_* fields so the drawer can surface
    # "owned by ops@example.com" / "rack 12 / shelf 4" hints.
    if contact:
        out["host_contact"] = contact
    if location:
        out["host_location"] = location
    # #686 — Ubiquiti UniFi switches / APs / routers return ONLY
    # sysDescr ("USW-Enterprise-8-PoE, 7.4.1.16850") and have no
    # useful vendor-private MIB. Detect the comma-then-version-string
    # convention and pull model + firmware out of sysDescr when other
    # extractors haven't populated them. Conservative parser: only
    # fires when sysDescr's second comma-separated token starts with
    # a digit (looks like a version number) AND first token contains
    # no whitespace (model slug like "USW-...", "UAP-...", "U6-LR").
    if descr and "," in descr:
        parts = [p.strip() for p in descr.split(",", 1)]
        if (len(parts) == 2
                and parts[0]
                and " " not in parts[0]
                and parts[1] and parts[1][0].isdigit()
                and "host_model" not in out
                and "host_firmware" not in out):
            out["host_model"] = parts[0]
            out["host_firmware"] = parts[1]
    return out


def extract_vendor_info(walks: dict, existing: Optional[dict] = None) -> dict:
    """#682 — DELL-RAC-MIB + Cisco vendor-MIB extractor.

    Fills `host_model` / `host_serial` / `host_firmware` / `host_health`
    when ENTITY-MIB and the standard MIBs left them empty. Designed to
    NEVER overwrite values the earlier extractors set — the operator's
    Beszel-mapped Linux host stays Beszel-shaped even when SNMP
    incidentally returns a hardware-model string from a USB peripheral.

    Cisco memory pools are summed across all pools; the result lands
    as `host_mem_total` / `host_mem_used` only when Host-Resources MIB
    didn't produce them. Cisco CPU% is the mean across all
    cpmCPUTotal5sec entries (one per CPU engine on stackable switches).

    `walks` keys: 'dell' (GET dict), 'cisco_hw' (GET dict),
    'cisco_mem_used' / 'cisco_mem_free' / 'cisco_mem_name' (walk dicts),
    'cisco_cpu' (walk dict).
    """
    existing = existing or {}
    out: dict = {}
    # ---- Dell DELL-RAC-MIB (iDRAC) ----------------------------------
    dell = walks.get("dell") or {}
    chassis_tag = _coerce_str(dell.get(_OID_DELL_CHASSIS_SERVICE_TAG)).strip()
    chassis_model = _coerce_str(dell.get(_OID_DELL_CHASSIS_MODEL)).strip()
    rac_firmware = _coerce_str(dell.get(_OID_DELL_RAC_FIRMWARE)).strip()
    sys_tag = _coerce_str(dell.get(_OID_DELL_SYSTEM_SERVICE_TAG)).strip()
    sys_model = _coerce_str(dell.get(_OID_DELL_SYSTEM_MODEL_NAME)).strip()
    global_status = _coerce_int(dell.get(_OID_DELL_GLOBAL_SYS_STATUS))
    serial = chassis_tag or sys_tag
    model = chassis_model or sys_model
    if serial and not existing.get("host_serial"):
        out["host_serial"] = serial
    if model and not existing.get("host_model"):
        out["host_model"] = model
    if rac_firmware and not existing.get("host_firmware"):
        out["host_firmware"] = rac_firmware
    if global_status > 0:
        out["host_health"] = _DELL_STATUS_LABELS.get(global_status, f"status={global_status}")
    # ---- Cisco product hardware version -----------------------------
    cisco_hw = walks.get("cisco_hw") or {}
    cisco_model = _coerce_str(cisco_hw.get(_OID_CISCO_PRODUCT_HW_VER)).strip()
    if cisco_model and not existing.get("host_model"):
        out["host_model"] = cisco_model
    # ---- Cisco memory pools (sum across all pools) ------------------
    mem_used_walk = walks.get("cisco_mem_used") or {}
    mem_free_walk = walks.get("cisco_mem_free") or {}
    if mem_used_walk and mem_free_walk:
        used_sum = sum(_coerce_int(v) for v in mem_used_walk.values())
        free_sum = sum(_coerce_int(v) for v in mem_free_walk.values())
        total = used_sum + free_sum
        if total > 0:
            if not existing.get("host_mem_total"):
                out["host_mem_total"] = total
            if not existing.get("host_mem_used"):
                out["host_mem_used"] = used_sum
                out["host_mem_avail"] = free_sum
                out["host_mem_percent"] = (used_sum / total * 100.0) if total else 0.0
    # ---- Cisco CPU% (mean across cpmCPUTotal5sec entries) -----------
    cpu_walk = walks.get("cisco_cpu") or {}
    if cpu_walk and existing.get("host_cpu_percent") is None:
        loads = [_coerce_int(v) for v in cpu_walk.values()]
        loads = [n for n in loads if 0 <= n <= 100]
        if loads:
            out["host_cpu_percent"] = float(sum(loads)) / len(loads)
    # ---- APC PowerNet-MIB (UPS) -------------------------------------
    # #683 — Smart-UPS RT, Back-UPS, BR-series, rack PDUs all live
    # under 1.3.6.1.4.1.318.x. The full surface adds host_ups_status,
    # host_battery_*, host_load_percent — these don't conflict with
    # any existing host_* field and are ONLY emitted by APC gear, so
    # they're additive.
    apc = walks.get("apc") or {}
    apc_model = _coerce_str(apc.get(_OID_APC_UPS_MODEL)).strip()
    apc_firmware = _coerce_str(apc.get(_OID_APC_UPS_FIRMWARE)).strip()
    apc_serial = _coerce_str(apc.get(_OID_APC_UPS_SERIAL)).strip()
    if apc_model and not existing.get("host_model"):
        out["host_model"] = apc_model
    if apc_firmware and not existing.get("host_firmware"):
        out["host_firmware"] = apc_firmware
    if apc_serial and not existing.get("host_serial"):
        out["host_serial"] = apc_serial
    apc_batt_status = _coerce_int(apc.get(_OID_APC_UPS_BATT_STATUS))
    if apc_batt_status > 0:
        out["host_battery_status"] = _APC_BATT_STATUS_LABELS.get(
            apc_batt_status, f"status={apc_batt_status}"
        )
    apc_batt_pct = _coerce_int(apc.get(_OID_APC_UPS_BATT_CAPACITY))
    if 0 <= apc_batt_pct <= 100 and (apc_batt_pct > 0 or apc_batt_status > 0):
        out["host_battery_percent"] = float(apc_batt_pct)
    apc_batt_temp = _coerce_int(apc.get(_OID_APC_UPS_BATT_TEMP_C))
    if apc_batt_temp > 0:
        out["host_battery_temp_c"] = float(apc_batt_temp)
    # upsAdvBatteryRunTimeRemaining is in TimeTicks (centiseconds).
    apc_runtime_ticks = _coerce_int(apc.get(_OID_APC_UPS_BATT_RUNTIME))
    if apc_runtime_ticks > 0:
        out["host_battery_runtime_s"] = apc_runtime_ticks // 100
    apc_output_status = _coerce_int(apc.get(_OID_APC_UPS_OUTPUT_STATUS))
    if apc_output_status > 0:
        out["host_ups_status"] = _APC_OUTPUT_STATUS_LABELS.get(
            apc_output_status, f"status={apc_output_status}"
        )
    apc_load = _coerce_int(apc.get(_OID_APC_UPS_OUTPUT_LOAD))
    if 0 <= apc_load <= 200:  # APC reports up to ~150% before overload
        if apc_load > 0 or apc_output_status > 0:
            out["host_load_percent"] = float(apc_load)
    # ---- UCD-SNMP-MIB (Linux net-snmp) ------------------------------
    # #684 — DD-WRT / OpenWrt / generic Linux without Beszel/NE pick
    # up CPU% (100 - ssCpuIdle), memory (KB → bytes), 1/5/15-min load
    # average (centi-load → float), and per-mount disk via dskTable.
    ucd_mem = walks.get("ucd_mem_cpu") or {}
    if ucd_mem:
        mem_total_kb = _coerce_int(ucd_mem.get(_OID_UCD_MEM_TOTAL_REAL))
        mem_avail_kb = _coerce_int(ucd_mem.get(_OID_UCD_MEM_AVAIL_REAL))
        mem_free_kb  = _coerce_int(ucd_mem.get(_OID_UCD_MEM_TOTAL_FREE))
        if mem_total_kb > 0 and not existing.get("host_mem_total"):
            mem_total = mem_total_kb * 1024
            # Prefer memAvailReal if present (accounts for cache); fall
            # back to memTotalFree (raw free, no cache discount).
            avail_kb = mem_avail_kb if mem_avail_kb > 0 else mem_free_kb
            avail = avail_kb * 1024 if avail_kb > 0 else 0
            mem_used = max(0, mem_total - avail) if avail > 0 else 0
            out["host_mem_total"] = mem_total
            if mem_used > 0:
                out["host_mem_used"] = mem_used
                out["host_mem_avail"] = avail
                out["host_mem_percent"] = (mem_used / mem_total * 100.0) if mem_total else 0.0
        # CPU% via 100 - ssCpuIdle. ssCpuIdle is "% idle since last
        # poll", so the snmpd's accounting interval matters; net-snmp
        # uses 60s by default. Operators wanting tighter granularity
        # configure shorter intervals on the agent side.
        cpu_idle = _coerce_int(ucd_mem.get(_OID_UCD_SS_CPU_IDLE))
        if 0 <= cpu_idle <= 100 and existing.get("host_cpu_percent") is None:
            out["host_cpu_percent"] = float(max(0, min(100, 100 - cpu_idle)))
    # laLoadInt walk — three rows × 100 (centi-load).
    ucd_load = walks.get("ucd_load") or {}
    if ucd_load:
        load_by_idx: dict[int, float] = {}
        for oid, v in ucd_load.items():
            idx_s = oid.rsplit(".", 1)[-1]
            try:
                idx = int(idx_s)
            except ValueError:
                continue
            if idx in (1, 2, 3):
                centi = _coerce_int(v)
                if centi >= 0:
                    load_by_idx[idx] = centi / 100.0
        if 1 in load_by_idx and not existing.get("host_load_1m"):
            out["host_load_1m"] = load_by_idx[1]
        if 2 in load_by_idx and not existing.get("host_load_5m"):
            out["host_load_5m"] = load_by_idx[2]
        if 3 in load_by_idx and not existing.get("host_load_15m"):
            out["host_load_15m"] = load_by_idx[3]
    # ---- SYNOLOGY-MIB (DSM NAS) -------------------------------------
    # #685 — DSM 7+ also implements Host Resources MIB; these OIDs
    # add the NAS-specific identity + the system temperature + an
    # upgrade-available signal. Picks up DiskStation / RackStation
    # gear where the operator hasn't (yet) deployed an OmniGrid agent.
    syno = walks.get("syno") or {}
    syno_model = _coerce_str(syno.get(_OID_SYNO_MODEL_NAME)).strip()
    syno_serial = _coerce_str(syno.get(_OID_SYNO_SERIAL_NUMBER)).strip()
    syno_dsm = _coerce_str(syno.get(_OID_SYNO_DSM_VERSION)).strip()
    if syno_model and not existing.get("host_model") and "host_model" not in out:
        out["host_model"] = syno_model
    if syno_serial and not existing.get("host_serial") and "host_serial" not in out:
        out["host_serial"] = syno_serial
    if syno_dsm and not existing.get("host_firmware") and "host_firmware" not in out:
        out["host_firmware"] = syno_dsm
    syno_status = _coerce_int(syno.get(_OID_SYNO_SYSTEM_STATUS))
    if syno_status > 0 and "host_health" not in out:
        out["host_health"] = _SYNO_STATUS_LABELS.get(syno_status, f"status={syno_status}")
    syno_temp = _coerce_int(syno.get(_OID_SYNO_SYSTEM_TEMP))
    if syno_temp > 0:
        out["host_temp_c"] = float(syno_temp)
    syno_upgrade = _coerce_int(syno.get(_OID_SYNO_UPGRADE_AVAIL))
    if syno_upgrade > 0:
        out["host_upgrade_status"] = _SYNO_UPGRADE_LABELS.get(
            syno_upgrade, f"status={syno_upgrade}"
        )
    # ---- Printer-MIB (HP / Brother / Canon / Epson / Xerox / etc.) ---
    # #702 — universal printer surface. Picks up any device whose SNMP
    # agent implements RFC 1759 / 3805 — basically every networked
    # office / home-office printer. Emits `printer_page_count` (int),
    # `printer_console_msg` (str — "Replace toner Y" / "Paper jam" /
    # etc.), and `printer_supplies[]` (per-supply rows aligned across
    # the three walks).
    prt_basic = walks.get("prt_basic") or {}
    page_count = _coerce_int(prt_basic.get(_OID_PRT_PAGE_COUNT))
    console_msg = _coerce_str(prt_basic.get(_OID_PRT_CONSOLE_MSG)).strip()
    if page_count > 0:
        out["printer_page_count"] = page_count
    if console_msg:
        out["printer_console_msg"] = console_msg
    prt_descrs = walks.get("prt_supply_descr") or {}
    prt_maxs = walks.get("prt_supply_max") or {}
    prt_levels = walks.get("prt_supply_level") or {}
    if prt_descrs:
        supplies = []
        for oid in prt_descrs.keys():
            idx = _last_index(oid, _OID_PRT_SUPPLIES_DESCR)
            name = _coerce_str(prt_descrs.get(oid)).strip()
            max_cap = _coerce_int(_pick(prt_maxs, idx))
            level = _coerce_int(_pick(prt_levels, idx))
            # Printer-MIB sentinel values: -1 = "unknown / unbounded";
            # -2 = "value lasts indefinitely" (e.g. drum / fuser).
            # -3 = "value is in some other unit". Skip those rows so the
            # operator's chart doesn't show "-1%" toner.
            pct = None
            if max_cap > 0 and level >= 0:
                pct = float(level) / float(max_cap) * 100.0
            supplies.append({
                "name":    name or f"supply-{idx}",
                "level":   level if level >= 0 else None,
                "max":     max_cap if max_cap > 0 else None,
                "percent": pct,
            })
        if supplies:
            out["printer_supplies"] = supplies
    # dskTable — per-mount disk. Only emit when hrStorage didn't.
    ucd_paths = walks.get("ucd_dsk_path") or {}
    ucd_totals = walks.get("ucd_dsk_total") or {}
    ucd_useds = walks.get("ucd_dsk_used") or {}
    ucd_pcts = walks.get("ucd_dsk_pct") or {}
    if ucd_paths and not existing.get("mounts"):
        gib = 1024 ** 3
        mounts = []
        disk_total_sum = 0
        disk_used_sum = 0
        for oid in ucd_paths.keys():
            idx = oid.rsplit(".", 1)[-1]
            path = _coerce_str(_pick(ucd_paths, idx)).strip()
            total_kb = _coerce_int(_pick(ucd_totals, idx))
            used_kb = _coerce_int(_pick(ucd_useds, idx))
            pct = _coerce_int(_pick(ucd_pcts, idx))
            if not path or total_kb <= 0:
                continue
            total_b = total_kb * 1024
            used_b = max(0, min(used_kb * 1024, total_b))
            disk_total_sum += total_b
            disk_used_sum += used_b
            mounts.append({
                "n":  path,
                "d":  total_b / gib,
                "du": used_b / gib,
                "dp": float(pct) if 0 <= pct <= 100 else (used_b / total_b * 100.0 if total_b else 0.0),
                "dr": 0,
                "dw": 0,
                "fstype": "ucd",
            })
        if mounts:
            mounts.sort(key=lambda m: m.get("dp", 0), reverse=True)
            out["mounts"] = mounts
            if not existing.get("host_disk_total"):
                out["host_disk_total"] = disk_total_sum
                out["host_disk_used"] = disk_used_sum
                out["host_disk_free"] = max(0, disk_total_sum - disk_used_sum)
                out["host_disk_percent"] = (disk_used_sum / disk_total_sum * 100.0) if disk_total_sum else 0.0
    return out


def extract_entity_info(walk_results: dict) -> dict:
    """#681 — Shape ENTITY-MIB walks into host_model / host_serial /
    host_firmware. Picks the FIRST non-empty value across every walked
    physical-entry index (chassis-level info typically lives at
    entPhysicalIndex=1, but the entry can be deeper on stackable
    switches). Caller passes a dict of the parsed walks keyed by
    'descr' / 'name' / 'serial' / 'model' / 'firmware' / 'class'.
    """
    out: dict = {}
    descrs   = walk_results.get("descr") or {}
    names    = walk_results.get("name") or {}
    serials  = walk_results.get("serial") or {}
    models   = walk_results.get("model") or {}
    firmwares = walk_results.get("firmware") or {}

    def _first_nonempty(walk: dict) -> str:
        for _, v in walk.items():
            s = _coerce_str(v).strip()
            if s:
                return s
        return ""
    model = _first_nonempty(models) or _first_nonempty(names) or _first_nonempty(descrs)
    serial = _first_nonempty(serials)
    firmware = _first_nonempty(firmwares)
    if model:
        out["host_model"] = model
    if serial:
        out["host_serial"] = serial
    if firmware:
        out["host_firmware"] = firmware
    return out


def extract_cpu_percent(walk_result: dict) -> dict:
    """Shape hrProcessorLoad walk into host_cpu_percent (mean across cores)."""
    if not walk_result:
        return {}
    loads: list[int] = []
    for _, v in walk_result.items():
        n = _coerce_int(v)
        if 0 <= n <= 100:
            loads.append(n)
    if not loads:
        return {}
    avg = sum(loads) / len(loads)
    return {
        "host_cpu_percent": float(avg),
        "host_cores": len(loads),
    }


def extract_storage(
    type_walk: dict, desc_walk: dict, unit_walk: dict,
    size_walk: dict, used_walk: dict,
) -> dict:
    """Shape hrStorage rows into host_mem_* + host_disk_* + mounts.

    hrStorageTable rows are indexed by hrStorageIndex; each row carries
    a Type OID telling us if the entry is RAM, virtual memory, a fixed
    disk, removable disk, etc. We:
      - sum RAM-type rows (typically only one) into host_mem_total /
        host_mem_used.
      - per fixed-disk row, build a `mounts[]` entry with {n, d, du,
        dp} matching the schema other providers emit.
    """
    if not size_walk:
        return {}
    out: dict = {}
    mounts: list[dict] = []
    mem_total = 0
    mem_used = 0
    disk_total = 0
    disk_used = 0
    gib = 1024 ** 3

    # Re-key each walk by the index suffix so we can join across them.
    def by_idx(walk: dict, base: str) -> dict[str, object]:
        return {_last_index(oid, base): val for oid, val in walk.items()}

    types = by_idx(type_walk, _OID_HR_STORAGE_TYPE)
    descs = by_idx(desc_walk, _OID_HR_STORAGE_DESC)
    units = by_idx(unit_walk, _OID_HR_STORAGE_UNIT)
    sizes = by_idx(size_walk, _OID_HR_STORAGE_SIZE)
    useds = by_idx(used_walk, _OID_HR_STORAGE_USED)

    for idx, raw_size in sizes.items():
        size_units = _coerce_int(raw_size)
        used_units = _coerce_int(useds.get(idx))
        unit_bytes = _coerce_int(units.get(idx))
        if size_units <= 0 or unit_bytes <= 0:
            continue
        total_bytes = size_units * unit_bytes
        used_bytes = max(0, min(used_units * unit_bytes, total_bytes))
        type_oid = _coerce_str(types.get(idx))
        desc = _coerce_str(descs.get(idx))
        if type_oid == _OID_HR_TYPE_RAM:
            # #664 — unit-normalisation heuristic for older Cisco IOS-XE
            # (and similar agents) that report `Units=1, Size=<KB-count>`
            # for RAM. Naive `Units × Size` math stores 2 MB instead of
            # 2 GB. Heuristic: hrStorageType=RAM with total_bytes < 16 MiB
            # is suspicious for any device that runs SNMP — treat
            # `Units` as 1024 instead.
            if unit_bytes == 1 and total_bytes < (16 * 1024 * 1024):
                total_bytes = size_units * 1024
                used_bytes = max(0, min(used_units * 1024, total_bytes))
            mem_total += total_bytes
            mem_used += used_bytes
        elif type_oid in (_OID_HR_TYPE_FIXED_DISK, _OID_HR_TYPE_REMOVABLE):
            disk_total += total_bytes
            disk_used += used_bytes
            pct = (used_bytes / total_bytes * 100) if total_bytes > 0 else 0.0
            mounts.append({
                "n":  desc or f"snmp-{idx}",
                "d":  total_bytes / gib,
                "du": used_bytes / gib,
                "dp": pct,
                "dr": 0,
                "dw": 0,
                "fstype": "snmp",
            })

    if mem_total > 0:
        out["host_mem_total"] = mem_total
        out["host_mem_used"] = mem_used
        out["host_mem_avail"] = max(0, mem_total - mem_used)
        out["host_mem_percent"] = (mem_used / mem_total * 100) if mem_total else 0.0
    if disk_total > 0:
        out["host_disk_total"] = disk_total
        out["host_disk_used"] = disk_used
        out["host_disk_free"] = max(0, disk_total - disk_used)
        out["host_disk_percent"] = (disk_used / disk_total * 100) if disk_total else 0.0
    if mounts:
        # Sort fullest first to match the convention other providers use.
        mounts.sort(key=lambda m: m.get("dp", 0), reverse=True)
        out["mounts"] = mounts
    return out


def extract_interfaces(
    descr_walk: dict, oper_walk: dict,
    in_hc_walk: dict, out_hc_walk: dict,
    in_32_walk: dict, out_32_walk: dict,
) -> dict:
    """Shape ifTable rows into network_ifaces[] + host_net rx/tx totals.

    Prefers ifHCInOctets / ifHCOutOctets (64-bit, IF-MIB extension) when
    available; falls through to the 32-bit ifInOctets/ifOutOctets when
    the agent doesn't expose the HC variants. Excludes loopback / docker /
    veth interfaces from the host-wide totals (per CLAUDE.md's "exclude
    pseudo NICs from net totals" rule).
    """
    out: dict = {}
    if not descr_walk:
        return out
    descs = {_last_index(oid, _OID_IF_DESCR): _coerce_str(v)
             for oid, v in descr_walk.items()}
    opers = {_last_index(oid, _OID_IF_OPER_STATUS): _coerce_int(v)
             for oid, v in oper_walk.items()}
    in_hc = {_last_index(oid, _OID_IF_HC_IN_OCTETS): _coerce_int(v)
             for oid, v in in_hc_walk.items()}
    out_hc = {_last_index(oid, _OID_IF_HC_OUT_OCTETS): _coerce_int(v)
              for oid, v in out_hc_walk.items()}
    in_32 = {_last_index(oid, _OID_IF_IN_OCTETS_32): _coerce_int(v)
             for oid, v in in_32_walk.items()}
    out_32 = {_last_index(oid, _OID_IF_OUT_OCTETS_32): _coerce_int(v)
              for oid, v in out_32_walk.items()}

    ifaces: list[dict] = []
    rx_total = 0
    tx_total = 0
    for idx, name in descs.items():
        if not name:
            continue
        oper = opers.get(idx, 1)  # 1 = up
        rx = in_hc.get(idx) or in_32.get(idx) or 0
        tx = out_hc.get(idx) or out_32.get(idx) or 0
        ifaces.append({
            "name": name,
            "mac": "",
            "addrs": [],
            "oper_status": "up" if oper == 1 else "down",
            "rx_bytes": rx,
            "tx_bytes": tx,
        })
        # Exclude pseudo NICs from the host-wide totals — same rule the
        # node-exporter sampler applies.
        nlc = name.lower()
        if not any(nlc.startswith(p) for p in _LOOPBACK_PREFIXES):
            rx_total += rx
            tx_total += tx
    if ifaces:
        out["network_ifaces"] = ifaces
    if rx_total:
        out["host_net_rx_total_bytes"] = rx_total
    if tx_total:
        out["host_net_tx_total_bytes"] = tx_total
    return out


def extract_stats(
    sys_get: dict,
    cpu_walk: dict,
    storage_walks: dict,
    iface_walks: dict,
    active_sources: Optional[set[str]] = None,
    entity_walks: Optional[dict] = None,
    vendor_walks: Optional[dict] = None,
) -> dict:
    """Compose every per-section extractor into one host_* dict.

    ``storage_walks`` is the named dict of the five storage sub-walks
    (``type``, ``desc``, ``unit``, ``size``, ``used``); ``iface_walks``
    is the six iface walks (``descr``, ``oper``, ``in_hc``, ``out_hc``,
    ``in_32``, ``out_32``). Splitting them this way lets a fixture in
    a future pytest suite feed each subsystem in isolation.

    ``active_sources`` is honoured to suppress fields a richer provider
    would emit better — same pattern as Webmin.

    ``entity_walks`` (#681) — optional dict of ENTITY-MIB sub-walks
    (``descr`` / ``name`` / ``serial`` / ``model`` / ``firmware``) for
    devices that don't expose Host Resources MIB but DO carry vendor
    identification under entPhysicalEntry.
    """
    stats: dict = {}
    stats.update(extract_sys_info(sys_get))
    stats.update(extract_cpu_percent(cpu_walk))
    stats.update(extract_storage(
        storage_walks.get("type") or {},
        storage_walks.get("desc") or {},
        storage_walks.get("unit") or {},
        storage_walks.get("size") or {},
        storage_walks.get("used") or {},
    ))
    stats.update(extract_interfaces(
        iface_walks.get("descr") or {},
        iface_walks.get("oper") or {},
        iface_walks.get("in_hc") or {},
        iface_walks.get("out_hc") or {},
        iface_walks.get("in_32") or {},
        iface_walks.get("out_32") or {},
    ))
    # #681 — ENTITY-MIB pass. Vendor-agnostic; emits host_model /
    # host_serial / host_firmware when the agent answers.
    if entity_walks:
        stats.update(extract_entity_info(entity_walks))
    # #682 — Vendor-private MIB pass. Dell DELL-RAC-MIB + Cisco
    # CISCO-MEMORY-POOL-MIB / CISCO-PROCESS-MIB. Only fills fields the
    # earlier passes left empty (a Beszel/NE-mapped Linux box probed
    # over SNMP keeps the richer kernel/arch/uptime data; an iDRAC
    # gets host_serial / host_model / host_firmware / host_health
    # because every other surface returned nothing).
    if vendor_walks:
        stats.update(extract_vendor_info(vendor_walks, existing=stats))
    # When a richer provider is active for this host AND likely to
    # report a more accurate CPU/memory snapshot, drop SNMP's coarser
    # values. SNMP CPU% in particular is often a 5-second average that
    # spikes wildly compared to Beszel/NE's smoother windows.
    others = (active_sources or set()) - {"snmp"}
    if others & {"beszel", "node_exporter", "pulse"}:
        stats.pop("host_cpu_percent", None)
    stats["exporter_error"] = None
    return stats


async def probe_snmp(
    host: str,
    *,
    community: str = "public",
    version: str = "v2c",
    port: int = 161,
    v3_user: str = "",
    v3_auth_key: str = "",
    v3_priv_key: str = "",
    timeout: float = 5.0,
    active_sources: Optional[set[str]] = None,
    verbose: bool = False,
) -> dict:
    """Probe one SNMP-speaking host. See module docstring for the contract.

    Returns ``{"hosts": {host_key: stats}, "error": None}`` on success or
    ``{"hosts": {}, "error": "..."}`` on any failure. Never raises.

    Like Webmin, this is a per-host probe (no central hub). Each
    successful probe yields ONE entry keyed by the agent-reported
    ``sysName.0`` (falling back to the supplied host string when sysName
    isn't readable — common on appliances that disable SNMP system
    naming).

    ``verbose`` (#675) — when True, the response also carries a ``raw``
    sub-dict with the parsed system / cpu / storage / interface walks
    (string-keyed pretty-print so the operator can see WHICH OIDs the
    agent answered). Used by the host-drawer debug panel — left OFF on
    the hot gather + per-host probe paths so we don't carry the extra
    bytes through the merge / cache layers.
    """
    if not _HAS_SNMP:
        return {
            "hosts": {},
            "error": "snmp: pysnmp not installed (pip install pysnmp)",
        }
    host_clean = (host or "").strip()
    if not host_clean:
        return {"hosts": {}, "error": "snmp: missing host"}
    try:
        port_int = int(port) if port else 161
    except (TypeError, ValueError):
        port_int = 161
    if not (1 <= port_int <= 65535):
        port_int = 161

    cd = _in_cooldown(host_clean, port_int)
    if cd is not None:
        return {
            "hosts": {},
            "error": f"snmp: in cool-down ({int(cd)}s remaining) — "
                     f"host was unreachable on the previous probe",
        }

    auth = _build_auth_data(
        version=version,
        community=community,
        v3_user=v3_user,
        v3_auth_key=v3_auth_key,
        v3_priv_key=v3_priv_key,
    )
    if auth is None:
        return {
            "hosts": {},
            "error": f"snmp: v3 selected without a username — set "
                     f"snmp_v3_user or fall back to v2c",
        }

    # #671 — reuse the module-level SnmpEngine singleton instead of
    # allocating a fresh one per probe.
    engine = await _get_snmp_engine()
    try:
        target = await UdpTransportTarget.create(
            (host_clean, port_int), timeout=timeout, retries=1,
        )
    except (asyncio.CancelledError, KeyboardInterrupt):
        raise
    except Exception as e:
        return {"hosts": {}, "error": f"snmp: transport setup failed: {e}"}

    # ----------------------------------------------------------------
    # Fan out the GET + walks in parallel. asyncio.gather keeps the
    # wall-clock close to the slowest individual SNMP RTT instead of
    # adding them up. pysnmp's session is reentrant — each `getCmd` /
    # `bulkCmd` call carries its own request ID.
    # ----------------------------------------------------------------
    try:
        # #681 — sys GET expanded with sysContact + sysLocation; new
        # ENTITY-MIB walks added so device model / serial / firmware
        # come back even on agents that don't expose Host Resources MIB.
        sys_task = _snmp_get(engine, auth, target, [
            _OID_SYS_NAME, _OID_SYS_DESCR, _OID_SYS_UPTIME,
            _OID_SYS_CONTACT, _OID_SYS_LOCATION,
        ])
        cpu_task = _snmp_walk(engine, auth, target, _OID_HR_CPU_LOAD)
        st_type_task = _snmp_walk(engine, auth, target, _OID_HR_STORAGE_TYPE)
        st_desc_task = _snmp_walk(engine, auth, target, _OID_HR_STORAGE_DESC)
        st_unit_task = _snmp_walk(engine, auth, target, _OID_HR_STORAGE_UNIT)
        st_size_task = _snmp_walk(engine, auth, target, _OID_HR_STORAGE_SIZE)
        st_used_task = _snmp_walk(engine, auth, target, _OID_HR_STORAGE_USED)
        if_descr_task = _snmp_walk(engine, auth, target, _OID_IF_DESCR)
        if_oper_task = _snmp_walk(engine, auth, target, _OID_IF_OPER_STATUS)
        if_hc_in_task = _snmp_walk(engine, auth, target, _OID_IF_HC_IN_OCTETS)
        if_hc_out_task = _snmp_walk(engine, auth, target, _OID_IF_HC_OUT_OCTETS)
        if_in_task = _snmp_walk(engine, auth, target, _OID_IF_IN_OCTETS_32)
        if_out_task = _snmp_walk(engine, auth, target, _OID_IF_OUT_OCTETS_32)
        # #681 — ENTITY-MIB physical-component walks. Vendor-agnostic
        # source for model name / serial / firmware on enterprise gear.
        ent_descr_task = _snmp_walk(engine, auth, target, _OID_ENT_DESCR)
        ent_name_task = _snmp_walk(engine, auth, target, _OID_ENT_NAME)
        ent_serial_task = _snmp_walk(engine, auth, target, _OID_ENT_SERIAL_NUM)
        ent_model_task = _snmp_walk(engine, auth, target, _OID_ENT_MODEL_NAME)
        ent_fw_task = _snmp_walk(engine, auth, target, _OID_ENT_SOFTWARE_REV)
        # #682 — Vendor-private MIB GETs/walks. Each agent silently
        # returns "noSuchObject" when the OID isn't supported, so a
        # non-Dell / non-Cisco device incurs only the round-trip cost
        # of a few extra UDP packets — no error path.
        dell_vendor_task = _snmp_get(engine, auth, target, [
            _OID_DELL_CHASSIS_SERVICE_TAG,
            _OID_DELL_CHASSIS_MODEL,
            _OID_DELL_RAC_FIRMWARE,
            _OID_DELL_GLOBAL_SYS_STATUS,
            _OID_DELL_SYSTEM_SERVICE_TAG,
            _OID_DELL_SYSTEM_MODEL_NAME,
        ])
        cisco_hw_task = _snmp_get(engine, auth, target, [
            _OID_CISCO_PRODUCT_HW_VER,
        ])
        cisco_mem_used_task = _snmp_walk(engine, auth, target, _OID_CISCO_MEM_POOL_USED)
        cisco_mem_free_task = _snmp_walk(engine, auth, target, _OID_CISCO_MEM_POOL_FREE)
        cisco_mem_name_task = _snmp_walk(engine, auth, target, _OID_CISCO_MEM_POOL_NAME)
        cisco_cpu_task = _snmp_walk(engine, auth, target, _OID_CISCO_CPU_TOTAL_5SEC)
        # #683 — APC PowerNet-MIB. One GET covers identity + battery +
        # output. Non-APC devices return noSuchObject; extractor tolerates.
        apc_vendor_task = _snmp_get(engine, auth, target, [
            _OID_APC_UPS_MODEL, _OID_APC_UPS_NAME,
            _OID_APC_UPS_FIRMWARE, _OID_APC_UPS_SERIAL,
            _OID_APC_UPS_BATT_STATUS, _OID_APC_UPS_BATT_CAPACITY,
            _OID_APC_UPS_BATT_TEMP_C, _OID_APC_UPS_BATT_RUNTIME,
            _OID_APC_UPS_OUTPUT_STATUS, _OID_APC_UPS_OUTPUT_LOAD,
        ])
        # #684 — UCD-SNMP-MIB. Memory + CPU% (by mode) GETs; load
        # average + dskTable walks. Non-net-snmp devices return empty.
        ucd_mem_cpu_task = _snmp_get(engine, auth, target, [
            _OID_UCD_MEM_TOTAL_REAL, _OID_UCD_MEM_AVAIL_REAL, _OID_UCD_MEM_TOTAL_FREE,
            _OID_UCD_SS_CPU_USER, _OID_UCD_SS_CPU_SYSTEM, _OID_UCD_SS_CPU_IDLE,
        ])
        ucd_load_task = _snmp_walk(engine, auth, target, _OID_UCD_LA_LOAD_INT)
        ucd_dsk_path_task = _snmp_walk(engine, auth, target, _OID_UCD_DSK_PATH)
        ucd_dsk_total_task = _snmp_walk(engine, auth, target, _OID_UCD_DSK_TOTAL)
        ucd_dsk_used_task = _snmp_walk(engine, auth, target, _OID_UCD_DSK_USED)
        ucd_dsk_pct_task = _snmp_walk(engine, auth, target, _OID_UCD_DSK_PERCENT)
        # #685 — SYNOLOGY-MIB. One GET covers identity + system status.
        syno_vendor_task = _snmp_get(engine, auth, target, [
            _OID_SYNO_MODEL_NAME, _OID_SYNO_SERIAL_NUMBER, _OID_SYNO_DSM_VERSION,
            _OID_SYNO_SYSTEM_STATUS, _OID_SYNO_SYSTEM_TEMP, _OID_SYNO_UPGRADE_AVAIL,
        ])
        # #702 — Printer-MIB. Page count + console message GET; per-
        # supply walks (description / max / level). Non-printer agents
        # return empty for all of these; extractor tolerates.
        prt_basic_task = _snmp_get(engine, auth, target, [
            _OID_PRT_PAGE_COUNT, _OID_PRT_CONSOLE_MSG,
        ])
        prt_supply_descr_task = _snmp_walk(engine, auth, target, _OID_PRT_SUPPLIES_DESCR)
        prt_supply_max_task   = _snmp_walk(engine, auth, target, _OID_PRT_SUPPLIES_MAX_CAP)
        prt_supply_level_task = _snmp_walk(engine, auth, target, _OID_PRT_SUPPLIES_LEVEL)

        # #658 — wrap the gather in wait_for so the TimeoutError catch
        # becomes reachable (asyncio.gather alone can't raise TimeoutError
        # without wait_for) AND the caller earns a wall-clock guarantee.
        # Budget = (timeout + 2s) × 2 — covers UDP retransmits per
        # outstanding GET/walk plus a small overhead margin so a partial
        # responder doesn't run forever past the per-OID timeout.
        wall_clock_budget = max(5.0, (timeout + 2.0) * 2)
        results = await asyncio.wait_for(asyncio.gather(
            sys_task, cpu_task,
            st_type_task, st_desc_task, st_unit_task, st_size_task, st_used_task,
            if_descr_task, if_oper_task,
            if_hc_in_task, if_hc_out_task,
            if_in_task, if_out_task,
            ent_descr_task, ent_name_task, ent_serial_task,
            ent_model_task, ent_fw_task,
            dell_vendor_task, cisco_hw_task,
            cisco_mem_used_task, cisco_mem_free_task, cisco_mem_name_task,
            cisco_cpu_task,
            apc_vendor_task, ucd_mem_cpu_task, ucd_load_task,
            ucd_dsk_path_task, ucd_dsk_total_task, ucd_dsk_used_task,
            ucd_dsk_pct_task, syno_vendor_task,
            prt_basic_task, prt_supply_descr_task,
            prt_supply_max_task, prt_supply_level_task,
            return_exceptions=False,
        ), timeout=wall_clock_budget)
    except asyncio.TimeoutError:
        _arm_cooldown(host_clean, port_int)
        return {"hosts": {}, "error": f"snmp: timeout against {host_clean}:{port_int}"}
    except (asyncio.CancelledError, KeyboardInterrupt):
        raise
    except Exception as e:
        return {"hosts": {}, "error": f"snmp: probe failed: {e}"}

    (sys_get, cpu_walk,
     st_type, st_desc, st_unit, st_size, st_used,
     if_descr, if_oper,
     if_hc_in, if_hc_out, if_in, if_out,
     ent_descr, ent_name, ent_serial, ent_model, ent_fw,
     dell_vendor_get, cisco_hw_get,
     cisco_mem_used_walk, cisco_mem_free_walk, cisco_mem_name_walk,
     cisco_cpu_walk,
     apc_vendor_get, ucd_mem_cpu_get, ucd_load_walk,
     ucd_dsk_path_walk, ucd_dsk_total_walk, ucd_dsk_used_walk,
     ucd_dsk_pct_walk, syno_vendor_get,
     prt_basic_get, prt_supply_descr_walk,
     prt_supply_max_walk, prt_supply_level_walk) = results

    # #681 — entity walks count toward the "any data" gate so a switch
    # that answers ONLY entPhysicalSerialNum (no sysDescr / no ifTable)
    # still passes the cool-down clear. ENTITY-MIB-only is a real
    # config — some agents are locked down to Entity-MIB only.
    # #682 — vendor-private OIDs also count: an iDRAC's whole identity
    # surface lives under DELL-RAC-MIB, so a successful chassis-tag
    # GET is enough signal that the host is alive even when every
    # standard MIB-II / Host-Resources / ENTITY-MIB walk came back empty.
    if not (sys_get or cpu_walk or st_size or if_descr or ent_serial or ent_model
            or dell_vendor_get or cisco_hw_get or cisco_mem_used_walk
            or cisco_cpu_walk
            or apc_vendor_get or ucd_mem_cpu_get or ucd_load_walk
            or ucd_dsk_total_walk or syno_vendor_get
            or prt_basic_get or prt_supply_descr_walk):
        # Every walk came back empty — typically a wrong community or
        # the host doesn't speak SNMP on the expected port.
        _arm_cooldown(host_clean, port_int)
        return {
            "hosts": {},
            "error": f"snmp: no response from {host_clean}:{port_int} "
                     f"— check community / version / port",
        }

    # At least one section succeeded — clear any stale cool-down.
    _clear_cooldown(host_clean, port_int)

    stats = extract_stats(
        sys_get, cpu_walk,
        {"type": st_type, "desc": st_desc, "unit": st_unit,
         "size": st_size, "used": st_used},
        {"descr": if_descr, "oper": if_oper,
         "in_hc": if_hc_in, "out_hc": if_hc_out,
         "in_32": if_in, "out_32": if_out},
        active_sources=active_sources,
        entity_walks={
            "descr": ent_descr, "name": ent_name,
            "serial": ent_serial, "model": ent_model,
            "firmware": ent_fw,
        },
        vendor_walks={
            "dell": dell_vendor_get,
            "cisco_hw": cisco_hw_get,
            "cisco_mem_used": cisco_mem_used_walk,
            "cisco_mem_free": cisco_mem_free_walk,
            "cisco_mem_name": cisco_mem_name_walk,
            "cisco_cpu": cisco_cpu_walk,
            "apc": apc_vendor_get,
            "ucd_mem_cpu": ucd_mem_cpu_get,
            "ucd_load": ucd_load_walk,
            "ucd_dsk_path":  ucd_dsk_path_walk,
            "ucd_dsk_total": ucd_dsk_total_walk,
            "ucd_dsk_used":  ucd_dsk_used_walk,
            "ucd_dsk_pct":   ucd_dsk_pct_walk,
            "syno": syno_vendor_get,
            "prt_basic": prt_basic_get,
            "prt_supply_descr": prt_supply_descr_walk,
            "prt_supply_max":   prt_supply_max_walk,
            "prt_supply_level": prt_supply_level_walk,
        },
    )

    host_key = stats.get("host_hostname") or host_clean
    stats["snmp_name"] = host_key
    print(f"[snmp] probe: host={host_clean!r} port={port_int} "
          f"version={version} key={host_key!r} "
          f"cpu%={stats.get('host_cpu_percent')} "
          f"mem_total={stats.get('host_mem_total')} "
          f"disk_total={stats.get('host_disk_total')} "
          f"ifaces={len(stats.get('network_ifaces') or [])}")

    out = {
        "hosts": {host_key: stats} if host_key else {},
        "error": None,
    }
    if verbose:
        # #675 — surface the parsed walks so the host-drawer debug
        # panel can answer "what SNMP data is actually available?".
        # Pretty-print every value via _coerce_str so the JSON
        # serialiser doesn't choke on pysnmp's ASN.1 wrapper objects.
        # Counts of distinct rows per OID family give a quick "did
        # the agent answer?" signal; the per-row dicts give the
        # full picture.
        def _stringify(d: dict) -> dict:
            return {str(k): _coerce_str(v) for k, v in (d or {}).items()}
        storage_rows = []
        try:
            storage_rows = _summarise_storage_rows(
                {"type": st_type, "desc": st_desc, "unit": st_unit,
                 "size": st_size, "used": st_used},
            )
        except Exception:  # noqa: BLE001
            storage_rows = []
        iface_rows = []
        try:
            iface_rows = _summarise_iface_rows(
                if_descr, if_oper, if_hc_in, if_hc_out, if_in, if_out,
            )
        except Exception:  # noqa: BLE001
            iface_rows = []
        # #681 — entity rows for the verbose surface. Each physical
        # entry is one row keyed by entPhysicalIndex with name / model /
        # serial / firmware aligned.
        entity_rows = []
        try:
            entity_rows = _summarise_entity_rows(
                ent_descr, ent_name, ent_serial, ent_model, ent_fw,
            )
        except Exception:  # noqa: BLE001
            entity_rows = []
        out["raw"] = {
            "system": _stringify(sys_get),
            "cpu_walk": _stringify(cpu_walk),
            "storage_count": len(st_size or {}),
            "storage_rows": storage_rows,
            "iface_count": len(if_descr or {}),
            "iface_rows": iface_rows,
            "entity_count": len(ent_descr or {}),
            "entity_rows": entity_rows,
            # #682 — vendor-private MIB visibility. Dell GETs render as
            # one block of OID → string-pretty-printed values; Cisco
            # walks render per-pool / per-engine. Operators can confirm
            # at a glance whether the iDRAC's chassis service tag came
            # back, whether the SG300 returned its memory pool, etc.
            "vendor_dell": _stringify(dell_vendor_get),
            "vendor_cisco_hw": _stringify(cisco_hw_get),
            "vendor_cisco_mem_used": _stringify(cisco_mem_used_walk),
            "vendor_cisco_mem_free": _stringify(cisco_mem_free_walk),
            "vendor_cisco_mem_name": _stringify(cisco_mem_name_walk),
            "vendor_cisco_cpu": _stringify(cisco_cpu_walk),
            "vendor_apc": _stringify(apc_vendor_get),
            "vendor_ucd_mem_cpu": _stringify(ucd_mem_cpu_get),
            "vendor_ucd_load": _stringify(ucd_load_walk),
            "vendor_ucd_dsk_path": _stringify(ucd_dsk_path_walk),
            "vendor_ucd_dsk_total": _stringify(ucd_dsk_total_walk),
            "vendor_ucd_dsk_used": _stringify(ucd_dsk_used_walk),
            "vendor_ucd_dsk_pct": _stringify(ucd_dsk_pct_walk),
            "vendor_synology": _stringify(syno_vendor_get),
            "vendor_printer_basic": _stringify(prt_basic_get),
            "vendor_printer_supply_descr": _stringify(prt_supply_descr_walk),
            "vendor_printer_supply_max":   _stringify(prt_supply_max_walk),
            "vendor_printer_supply_level": _stringify(prt_supply_level_walk),
            "walk_summary": {
                "sys_keys": len(sys_get or {}),
                "cpu_rows": len(cpu_walk or {}),
                "storage_rows": len(st_size or {}),
                "iface_rows": len(if_descr or {}),
                "if_hc_in_rows": len(if_hc_in or {}),
                "if_hc_out_rows": len(if_hc_out or {}),
                "if_32_in_rows": len(if_in or {}),
                "if_32_out_rows": len(if_out or {}),
                "entity_rows": len(ent_descr or {}),
                "entity_serial_rows": len(ent_serial or {}),
                "entity_model_rows": len(ent_model or {}),
                "vendor_dell_keys": len(dell_vendor_get or {}),
                "vendor_cisco_hw_keys": len(cisco_hw_get or {}),
                "vendor_cisco_mem_pools": len(cisco_mem_used_walk or {}),
                "vendor_cisco_cpu_engines": len(cisco_cpu_walk or {}),
                "vendor_apc_keys": len(apc_vendor_get or {}),
                "vendor_ucd_keys": len(ucd_mem_cpu_get or {}),
                "vendor_ucd_load_rows": len(ucd_load_walk or {}),
                "vendor_ucd_dsk_rows": len(ucd_dsk_path_walk or {}),
                "vendor_synology_keys": len(syno_vendor_get or {}),
                "vendor_printer_basic_keys": len(prt_basic_get or {}),
                "vendor_printer_supply_rows": len(prt_supply_descr_walk or {}),
            },
        }
    return out


def _summarise_entity_rows(descrs: dict, names: dict, serials: dict,
                           models: dict, firmwares: dict) -> list[dict]:
    """#681 — Per-physical-entry row summary from ENTITY-MIB walks.
    Indexed by the trailing entPhysicalIndex so descr / name / serial /
    model / firmware align per row.
    """
    out = []
    seen_idx = set()
    for src in (descrs or {}, names or {}, serials or {}, models or {}, firmwares or {}):
        for oid in src.keys():
            # Trailing dotted index after the .1.x.x.x.x.x prefix.
            idx = oid.rsplit(".", 1)[-1]
            seen_idx.add(idx)
    for idx in sorted(seen_idx, key=lambda s: int(s) if s.isdigit() else 0):
        out.append({
            "idx": idx,
            "descr": _coerce_str(_pick(descrs, idx)),
            "name": _coerce_str(_pick(names, idx)),
            "serial": _coerce_str(_pick(serials, idx)),
            "model": _coerce_str(_pick(models, idx)),
            "firmware": _coerce_str(_pick(firmwares, idx)),
        })
    return out


def _summarise_storage_rows(walks: dict) -> list[dict]:
    """Build a per-row summary of hrStorage walk output for the debug
    surface. Indexed by the trailing OID component so type / desc /
    unit / size / used can be aligned per row."""
    sizes = walks.get("size") or {}
    types = walks.get("type") or {}
    descs = walks.get("desc") or {}
    units = walks.get("unit") or {}
    useds = walks.get("used") or {}
    out = []
    for oid in sizes.keys():
        idx = _last_index(oid, _OID_HR_STORAGE_SIZE)
        out.append({
            "idx": idx,
            "type_oid": _coerce_str(_pick(types, idx)),
            "desc": _coerce_str(_pick(descs, idx)),
            "unit_bytes": _coerce_int(_pick(units, idx)),
            "size_units": _coerce_int(sizes.get(oid)),
            "used_units": _coerce_int(_pick(useds, idx)),
        })
    return out


def _summarise_iface_rows(if_descr: dict, if_oper: dict,
                          if_hc_in: dict, if_hc_out: dict,
                          if_in: dict, if_out: dict) -> list[dict]:
    """Per-interface row summary for the debug surface."""
    out = []
    for oid in (if_descr or {}).keys():
        idx = _last_index(oid, _OID_IF_DESCR)
        out.append({
            "idx": idx,
            "descr": _coerce_str(if_descr.get(oid)),
            "oper": _coerce_int(_pick(if_oper, idx)),
            "in_hc": _coerce_int(_pick(if_hc_in, idx)),
            "out_hc": _coerce_int(_pick(if_hc_out, idx)),
            "in_32": _coerce_int(_pick(if_in, idx)),
            "out_32": _coerce_int(_pick(if_out, idx)),
        })
    return out


def _pick(walk: dict, idx: str):
    """Lookup a walk row by its trailing index suffix (no base prefix)."""
    if not walk or not idx:
        return None
    for oid, val in walk.items():
        if oid.endswith("." + idx):
            return val
    return None


def lookup(snmp_hosts: dict, needle: str) -> Optional[dict]:
    """Case / whitespace-tolerant key lookup matching Webmin / Beszel /
    Pulse. Same call shape so the merge sites stay polymorphic."""
    if not snmp_hosts or not needle:
        return None
    if needle in snmp_hosts:
        return snmp_hosts[needle]
    key = needle.strip().lower()
    if not key:
        return None
    for k, v in snmp_hosts.items():
        if k.strip().lower() == key:
            return v
    return None
