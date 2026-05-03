"""SNMP host-stats provider — sixth in the host-stats family.

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

from logic import tuning as _tuning
# Cool-down on consecutive timeouts. Different lever than the Webmin /
# SSH 401 cool-down (no auth challenge in SNMP — there's no credential
# lockout to defend against). Pre-#678 we shared
# `tuning_auth_failure_cooldown_seconds` with Webmin / SSH; operators
# debugging "SNMP timing out" reached for the wrong knob. Now uses a
# dedicated `tuning_snmp_unreachable_cooldown_seconds` (default 300s,
# range 30..3600). Per-(host, port) key.
from logic.cooldown import Cooldown as _Cooldown

_unreachable_cooldown = _Cooldown(
    seconds_fn=lambda: _tuning.tuning_int("tuning_snmp_unreachable_cooldown_seconds")
)

# module-level SnmpEngine singleton. pysnmp HLAPI engines carry
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
# ENTITY-MIB physical-component table. Vendor-agnostic surface
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

# Vendor-private MIBs for hosts whose SNMP profile blocks the
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
# Dell systemBIOSTable — BIOS version + release date per BIOS slot
# (almost always one row per server). 5.4.300.50.1.x.
_OID_DELL_BIOS_VERSION        = "1.3.6.1.4.1.674.10892.5.4.300.50.1.8"
_OID_DELL_BIOS_RELEASE_DATE   = "1.3.6.1.4.1.674.10892.5.4.300.50.1.7"
# Dell coolingDeviceTable (fans). One row per fan; 5.4.700.12.1.x:
#   .5 = coolingDeviceStatus (enum)
#   .6 = coolingDeviceReading (RPM)
#   .7 = coolingDeviceType (enum: 3=fan, 4=blower, 10=cooled-air-fan, etc.)
#   .8 = coolingDeviceLocationName (string)
_OID_DELL_FAN_STATUS    = "1.3.6.1.4.1.674.10892.5.4.700.12.1.5"
_OID_DELL_FAN_READING   = "1.3.6.1.4.1.674.10892.5.4.700.12.1.6"
_OID_DELL_FAN_TYPE      = "1.3.6.1.4.1.674.10892.5.4.700.12.1.7"
_OID_DELL_FAN_LOCATION  = "1.3.6.1.4.1.674.10892.5.4.700.12.1.8"
# Dell temperatureProbeTable. 5.4.700.20.1.x:
#   .5 = temperatureProbeStatus (enum)
#   .6 = temperatureProbeReading (deci-degC; 232 = 23.2 °C)
#   .7 = temperatureProbeType (enum)
#   .8 = temperatureProbeLocationName (string — e.g. "CPU1 Temp", "Inlet Temp")
_OID_DELL_TEMP_STATUS   = "1.3.6.1.4.1.674.10892.5.4.700.20.1.5"
_OID_DELL_TEMP_READING  = "1.3.6.1.4.1.674.10892.5.4.700.20.1.6"
_OID_DELL_TEMP_TYPE     = "1.3.6.1.4.1.674.10892.5.4.700.20.1.7"
_OID_DELL_TEMP_LOCATION = "1.3.6.1.4.1.674.10892.5.4.700.20.1.8"
# Dell powerSupplyTable. 5.4.600.12.1.x:
#   .5 = powerSupplyStatus (enum)
#   .6 = powerSupplyOutputWatts (deci-watts; 7500 = 750 W per Dell MIB)
#   .7 = powerSupplyType (enum: AC / DC)
#   .8 = powerSupplyLocationName
#   .12 = powerSupplyConfigurationErrorType (mismatch / not-redundant / etc.)
_OID_DELL_PSU_STATUS    = "1.3.6.1.4.1.674.10892.5.4.600.12.1.5"
_OID_DELL_PSU_WATTS     = "1.3.6.1.4.1.674.10892.5.4.600.12.1.6"
_OID_DELL_PSU_TYPE      = "1.3.6.1.4.1.674.10892.5.4.600.12.1.7"
_OID_DELL_PSU_LOCATION  = "1.3.6.1.4.1.674.10892.5.4.600.12.1.8"
# Dell voltageProbeTable. 5.4.600.20.1.x:
#   .5 = voltageProbeStatus
#   .6 = voltageProbeReading (millivolts)
#   .8 = voltageProbeLocationName
_OID_DELL_VOLT_STATUS   = "1.3.6.1.4.1.674.10892.5.4.600.20.1.5"
_OID_DELL_VOLT_READING  = "1.3.6.1.4.1.674.10892.5.4.600.20.1.6"
_OID_DELL_VOLT_LOCATION = "1.3.6.1.4.1.674.10892.5.4.600.20.1.8"
# Dell amperageProbeTable — also carries PSU input watts on iDRAC9+.
# 5.4.600.30.1.x:
#   .5 = amperageProbeStatus
#   .6 = amperageProbeReading (deci-amperes for current probes; watts
#         direct for type=23 system-power probes)
#   .7 = amperageProbeType (24=power-consumption, 23=watts, 1-3=current)
#   .8 = amperageProbeLocationName
_OID_DELL_AMP_STATUS    = "1.3.6.1.4.1.674.10892.5.4.600.30.1.5"
_OID_DELL_AMP_READING   = "1.3.6.1.4.1.674.10892.5.4.600.30.1.6"
_OID_DELL_AMP_TYPE      = "1.3.6.1.4.1.674.10892.5.4.600.30.1.7"
_OID_DELL_AMP_LOCATION  = "1.3.6.1.4.1.674.10892.5.4.600.30.1.8"
# Dell physicalDiskTable — per-disk identity + state + capacity.
# 5.5.1.20.130.4.1.x:
#   .2 = physicalDiskName ("Physical Disk 0:1:0")
#   .4 = physicalDiskState (enum: 1=ready, 2=failed, 3=online, 4=offline,
#        5=degraded, 6=recovering, 7=removed, 8=rebuild, 11=foreign,
#        13=clear, 14=blocked, 15=non-raid, 16=ready-removed, etc.)
#   .6 = physicalDiskCapacityInMB (megabytes)
#   .10 = physicalDiskSerialNo
#   .11 = physicalDiskRevision
_OID_DELL_PD_NAME       = "1.3.6.1.4.1.674.10892.5.5.1.20.130.4.1.2"
_OID_DELL_PD_STATE      = "1.3.6.1.4.1.674.10892.5.5.1.20.130.4.1.4"
_OID_DELL_PD_CAPACITY   = "1.3.6.1.4.1.674.10892.5.5.1.20.130.4.1.6"
_OID_DELL_PD_SERIAL     = "1.3.6.1.4.1.674.10892.5.5.1.20.130.4.1.10"
_OID_DELL_PD_REVISION   = "1.3.6.1.4.1.674.10892.5.5.1.20.130.4.1.11"
# Dell virtualDiskTable (RAID arrays). 5.5.1.20.140.1.1.x:
#   .2  = virtualDiskName
#   .4  = virtualDiskState (1=ready, 2=failed, 3=online, 4=offline,
#         5=degraded, 6=verifying, etc.)
#   .6  = virtualDiskSizeInMB
#   .13 = virtualDiskLayout (1=concat, 2=raid-0, 3=raid-1, 4=raid-5,
#         5=raid-6, 6=raid-10, 7=raid-50, 8=raid-60)
_OID_DELL_VD_NAME       = "1.3.6.1.4.1.674.10892.5.5.1.20.140.1.1.2"
_OID_DELL_VD_STATE      = "1.3.6.1.4.1.674.10892.5.5.1.20.140.1.1.4"
_OID_DELL_VD_SIZE       = "1.3.6.1.4.1.674.10892.5.5.1.20.140.1.1.6"
_OID_DELL_VD_LAYOUT     = "1.3.6.1.4.1.674.10892.5.5.1.20.140.1.1.13"

# Dell physicalDiskState enum — only the values seen in practice.
_DELL_PD_STATE_LABELS = {
    0: "unknown", 1: "ready", 2: "failed", 3: "online", 4: "offline",
    5: "degraded", 6: "recovering", 7: "removed", 8: "rebuild",
    11: "foreign", 13: "clear", 14: "blocked", 15: "non-raid",
    16: "ready-foreign",
}
# Dell virtualDiskState enum.
_DELL_VD_STATE_LABELS = {
    0: "unknown", 1: "ready", 2: "failed", 3: "online", 4: "offline",
    5: "degraded", 6: "verifying", 7: "resynching",
    8: "regenerating", 9: "failed-redundancy",
}
# Dell virtualDiskLayout enum — the canonical RAID levels.
_DELL_VD_LAYOUT_LABELS = {
    1: "concat", 2: "RAID-0", 3: "RAID-1", 4: "RAID-5",
    5: "RAID-6", 6: "RAID-10", 7: "RAID-50", 8: "RAID-60",
}
# Dell amperageProbeType enum. 23=system-board-power-consumption (watts),
# 24=power-consumption (cumulative). Probes with type 1-3 are pure
# current readings (deci-amperes). Used by the extractor to decide
# whether the .6 reading is a power value vs a current value.
_DELL_AMP_TYPE_WATTS = {23, 24, 25, 26}
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

# APC PowerNet-MIB (UPS / PDU). Smart-UPS family answers under
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

# UCD-SNMP-MIB (1.3.6.1.4.1.2021.x). The universal Linux
# net-snmp surface — present on basically every Linux distro running
# net-snmp (DD-WRT / OpenWrt / Synology / generic embedded boxes that
# don't ship Beszel/NE agents). Sometimes the only useful surface on
# routers whose snmpd builds Host-Resources OFF for size.
_OID_UCD_MEM_TOTAL_REAL = "1.3.6.1.4.1.2021.4.5.0"
_OID_UCD_MEM_AVAIL_REAL = "1.3.6.1.4.1.2021.4.6.0"
_OID_UCD_MEM_TOTAL_FREE = "1.3.6.1.4.1.2021.4.11.0"
# buffers + cached for the stacked-area memory chart.
# memShared (4.13) is rarely populated on modern Linux so skipped.
_OID_UCD_MEM_BUFFER     = "1.3.6.1.4.1.2021.4.14.0"
_OID_UCD_MEM_CACHED     = "1.3.6.1.4.1.2021.4.15.0"
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

# SYNOLOGY-MIB. DSM-based NAS (DiskStation, RackStation, etc.).
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

# Printer-MIB (RFC 1759 / 3805). Universal printer surface —
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
# IF-MIB::ifHighSpeed — link speed in MEGAbits per second (Mbps).
# Derived from the older 32-bit ifSpeed (bps) when the device only
# supports IF-MIB v1; ifHighSpeed handles 10G+ links cleanly. Powers
# the per-port utilization heatmap (#725 slice 4).
_OID_IF_HIGH_SPEED    = "1.3.6.1.2.1.31.1.1.1.15"

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
    # Surface which walk function the resolver picked + which pysnmp
    # namespace we landed in, so operators debugging "walk hangs" /
    # "walk truncates" can confirm the active code path without
    # poking at the import resolver. bulkWalkCmd is the 7.x async-
    # iterator path; bulkCmd is the legacy ≤6.x iterator-style
    # fallback. Module-level ONE-LINE log on import.
    try:
        _walk_fn_for_log = bulkWalkCmd or bulkCmd
        _walk_fn_name    = getattr(_walk_fn_for_log, "__name__", "unknown")
        _ns_used         = locals().get("_SNMP_HLAPI_NS", "unknown")
        print(f"[snmp] walk function resolved: {_walk_fn_name} "
              f"(pysnmp ns={_ns_used})")
    except Exception:
        pass
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

    pysnmp 7.x split the bulk-walk path off `bulk_cmd`. In 7.x:
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
    # `lexicographicMode=True` lets pysnmp continue past sub-tree
    # boundaries; we then filter by `oid_s.startswith(base_oid)` on
    # the way out. Operator-reported case: iDRAC9 returns Dell-RAC-MIB
    # table rows (cooling devices, temps, PSUs, etc.) correctly via
    # both GETBULK + GETNEXT from the CLI, but pysnmp's lex-mode-False
    # was returning 0 rows — likely a strict sub-tree-boundary check
    # tripping on the iDRAC's reply OID format. Lex-mode-True + manual
    # prefix filter recovers the rows; cost is one extra round-trip
    # at the end of each walk (the trailing OID outside the sub-tree
    # is fetched + discarded). Acceptable for the slow vendor-private
    # walks that were broken; legitimate sub-tree boundaries are
    # still respected via the `startswith` filter below.
    iterator = walk_fn(
        engine, auth, target, ContextData(),
        0, 25,  # nonRepeaters, maxRepetitions
        ObjectType(ObjectIdentity(base_oid)),
        lexicographicMode=True,
    )
    try:
        async for errorIndication, errorStatus, errorIndex, varBinds in iterator:
            if errorIndication:
                # Connection-level error (timeout, agent restart mid-walk,
                # transport gone). Keep whatever varBinds we already
                # collected — preserves a partial 149/200 iface walk
                # rather than discarding the whole list.
                print(f"[snmp] WALK errorIndication on {base_oid}: "
                      f"{errorIndication}; returning {len(out)} partial varBinds")
                return out
            if errorStatus:
                # Per-row agent-side error (noSuchObject etc.). Stop the
                # walk but preserve nothing past this point — the agent
                # is rejecting the request shape.
                print(f"[snmp] WALK errorStatus on {base_oid}: "
                      f"{errorStatus.prettyPrint()}")
                break
            if not varBinds:
                break
            # Track whether ANY varBind in this batch was outside the
            # sub-tree — once we see an out-of-tree OID, the walk has
            # crossed the boundary and we should stop. lex-mode-True
            # would otherwise walk us through the entire MIB.
            crossed_boundary = False
            for oid, val in varBinds:
                oid_s = str(oid)
                if not oid_s.startswith(base_oid):
                    crossed_boundary = True
                    break
                prn = val.prettyPrint() if hasattr(val, "prettyPrint") else str(val)
                if prn in ("noSuchObject", "noSuchInstance", "endOfMibView"):
                    continue
                out[oid_s] = val
                if len(out) >= max_rows:
                    return out
            if crossed_boundary:
                # Past the sub-tree — break out of the async-for so
                # we don't walk the entire MIB under lex-mode-True.
                break
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
    # sysContact / sysLocation are operator-set on most managed
    # gear; expose them as host_* fields so the drawer can surface
    # "owned by ops@example.com" / "rack 12 / shelf 4" hints.
    if contact:
        out["host_contact"] = contact
    if location:
        out["host_location"] = location
    # Ubiquiti UniFi switches / APs / routers return ONLY
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
    """DELL-RAC-MIB + Cisco vendor-MIB extractor.

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
    # OID 1.3.6.1.4.1.674.10892.5.1.1.6.0 is `iDRACURL` on iDRAC9/10
    # (the chassis web management URL like `https://<ip>:443`), NOT
    # a firmware version string as older Dell-RAC-MIB references
    # documented. Detect URL-shaped values and route them to a
    # dedicated `host_idrac_url` field instead of `host_firmware` —
    # surfaces the management URL as a click-through link in the
    # host drawer's Hardware card. Real iDRAC firmware versions come
    # from the systemBIOS walk (`_OID_DELL_BIOS_VERSION` →
    # `host_dell_bios_version`) which is the operator-facing
    # firmware string anyway. If the value isn't URL-shaped (older
    # iDRAC firmware that does emit a plain version here) we keep
    # the original behaviour for back-compat.
    if rac_firmware:
        is_urlish = rac_firmware.lower().startswith(("http://", "https://"))
        if is_urlish and not existing.get("host_idrac_url"):
            out["host_idrac_url"] = rac_firmware
        elif not is_urlish and not existing.get("host_firmware"):
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
    # Smart-UPS RT, Back-UPS, BR-series, rack PDUs all live
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
    # DD-WRT / OpenWrt / generic Linux without Beszel/NE pick
    # up CPU% (100 - ssCpuIdle), memory (KB → bytes), 1/5/15-min load
    # average (centi-load → float), and per-mount disk via dskTable.
    ucd_mem = walks.get("ucd_mem_cpu") or {}
    if ucd_mem:
        mem_total_kb = _coerce_int(ucd_mem.get(_OID_UCD_MEM_TOTAL_REAL))
        mem_avail_kb = _coerce_int(ucd_mem.get(_OID_UCD_MEM_AVAIL_REAL))
        mem_free_kb  = _coerce_int(ucd_mem.get(_OID_UCD_MEM_TOTAL_FREE))
        # buffers + cached for the stacked-area memory chart.
        mem_buffer_kb = _coerce_int(ucd_mem.get(_OID_UCD_MEM_BUFFER))
        mem_cached_kb = _coerce_int(ucd_mem.get(_OID_UCD_MEM_CACHED))
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
        # surface raw buffers / cached / free in bytes for the
        # stacked-area chart. Always emit when the OID returned a value,
        # independent of the host_mem_* gates above.
        if mem_buffer_kb > 0:
            out["host_mem_buffers"] = mem_buffer_kb * 1024
        if mem_cached_kb > 0:
            out["host_mem_cached"] = mem_cached_kb * 1024
        if mem_free_kb > 0:
            out["host_mem_free"] = mem_free_kb * 1024
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
    # DSM 7+ also implements Host Resources MIB; these OIDs
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
    # universal printer surface. Picks up any device whose SNMP
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
    # ---- Dell server health tables (iDRAC) --------------------------
    # Walk-paired extractors. Each emits a per-row list keyed by the
    # OID-trailing index. Empty walks → field absent (caller's snapshot
    # / merge layer treats absent as "no data" cleanly).
    def _row_index(oid: str, prefix: str) -> str:
        # Trailing index for an instance OID under `prefix`.
        # `1.3.6.1.4.1.674.10892.5.4.700.20.1.6.1.1` under prefix
        # `...20.1.6` → "1.1".
        if oid.startswith(prefix + "."):
            return oid[len(prefix) + 1:]
        return oid.rsplit(".", 1)[-1]

    # Fans — coolingDeviceTable.
    fan_readings = walks.get("dell_fan_reading") or {}
    if fan_readings:
        fan_status = walks.get("dell_fan_status") or {}
        fan_loc = walks.get("dell_fan_loc") or {}
        rows: list[dict] = []
        for oid, v in fan_readings.items():
            idx = _row_index(oid, _OID_DELL_FAN_READING)
            rpm = _coerce_int(v)
            status_oid = f"{_OID_DELL_FAN_STATUS}.{idx}"
            loc_oid = f"{_OID_DELL_FAN_LOCATION}.{idx}"
            status = _coerce_int(fan_status.get(status_oid))
            location = _coerce_str(fan_loc.get(loc_oid)).strip()
            rows.append({
                "idx":      idx,
                "name":     location or f"fan-{idx}",
                "rpm":      rpm if rpm > 0 else None,
                "status":   _DELL_STATUS_LABELS.get(status, "unknown"),
            })
        if rows:
            out["host_dell_fans"] = rows

    # Temperature probes — temperatureProbeTable.
    temp_readings = walks.get("dell_temp_reading") or {}
    if temp_readings:
        temp_status = walks.get("dell_temp_status") or {}
        temp_loc = walks.get("dell_temp_loc") or {}
        rows: list[dict] = []
        for oid, v in temp_readings.items():
            idx = _row_index(oid, _OID_DELL_TEMP_READING)
            decideg = _coerce_int(v)
            # MIB stores deci-degrees C; -7000 / 65535 are sentinel
            # "no reading" values on the lower / upper rails.
            celsius = decideg / 10.0 if -1000 < decideg < 2000 else None
            status_oid = f"{_OID_DELL_TEMP_STATUS}.{idx}"
            loc_oid = f"{_OID_DELL_TEMP_LOCATION}.{idx}"
            status = _coerce_int(temp_status.get(status_oid))
            location = _coerce_str(temp_loc.get(loc_oid)).strip()
            rows.append({
                "idx":      idx,
                "name":     location or f"temp-{idx}",
                "celsius":  celsius,
                "status":   _DELL_STATUS_LABELS.get(status, "unknown"),
            })
        if rows:
            out["host_dell_temps"] = rows

    # Power supplies — powerSupplyTable.
    psu_status_walk = walks.get("dell_psu_status") or {}
    if psu_status_walk:
        psu_watts_walk = walks.get("dell_psu_watts") or {}
        psu_loc_walk = walks.get("dell_psu_loc") or {}
        rows: list[dict] = []
        for oid, v in psu_status_walk.items():
            idx = _row_index(oid, _OID_DELL_PSU_STATUS)
            status = _coerce_int(v)
            watts_oid = f"{_OID_DELL_PSU_WATTS}.{idx}"
            loc_oid = f"{_OID_DELL_PSU_LOCATION}.{idx}"
            # Per Dell MIB powerSupplyOutputWatts is in deci-watts on
            # iDRAC9; older firmware reports plain watts. Heuristic: a
            # value > 4000 is almost certainly deci-watts (a real PSU
            # never sustains 4 kW); divide by 10. Lower → trust as-is.
            raw_watts = _coerce_int(psu_watts_walk.get(watts_oid))
            watts = (raw_watts / 10.0) if raw_watts > 4000 else float(raw_watts)
            location = _coerce_str(psu_loc_walk.get(loc_oid)).strip()
            rows.append({
                "idx":      idx,
                "name":     location or f"psu-{idx}",
                "watts":    watts if watts > 0 else None,
                "status":   _DELL_STATUS_LABELS.get(status, "unknown"),
            })
        if rows:
            out["host_dell_psus"] = rows

    # Voltage probes — voltageProbeTable.
    volt_readings = walks.get("dell_volt_reading") or {}
    if volt_readings:
        volt_status = walks.get("dell_volt_status") or {}
        volt_loc = walks.get("dell_volt_loc") or {}
        rows: list[dict] = []
        for oid, v in volt_readings.items():
            idx = _row_index(oid, _OID_DELL_VOLT_READING)
            mv = _coerce_int(v)
            status_oid = f"{_OID_DELL_VOLT_STATUS}.{idx}"
            loc_oid = f"{_OID_DELL_VOLT_LOCATION}.{idx}"
            status = _coerce_int(volt_status.get(status_oid))
            location = _coerce_str(volt_loc.get(loc_oid)).strip()
            rows.append({
                "idx":      idx,
                "name":     location or f"volt-{idx}",
                "millivolts": mv if mv > 0 else None,
                "status":   _DELL_STATUS_LABELS.get(status, "unknown"),
            })
        if rows:
            out["host_dell_voltages"] = rows

    # Amperage / power-consumption probes — amperageProbeTable.
    # On iDRAC9+ this also surfaces system-power watts (type=24);
    # the extractor splits that into host_dell_power_watts (chassis
    # total) when available.
    amp_readings = walks.get("dell_amp_reading") or {}
    if amp_readings:
        amp_status = walks.get("dell_amp_status") or {}
        amp_loc = walks.get("dell_amp_loc") or {}
        amp_type = walks.get("dell_amp_type") or {}
        rows: list[dict] = []
        for oid, v in amp_readings.items():
            idx = _row_index(oid, _OID_DELL_AMP_READING)
            reading = _coerce_int(v)
            status_oid = f"{_OID_DELL_AMP_STATUS}.{idx}"
            loc_oid = f"{_OID_DELL_AMP_LOCATION}.{idx}"
            type_oid = f"{_OID_DELL_AMP_TYPE}.{idx}"
            status = _coerce_int(amp_status.get(status_oid))
            location = _coerce_str(amp_loc.get(loc_oid)).strip()
            ptype = _coerce_int(amp_type.get(type_oid))
            is_watts = ptype in _DELL_AMP_TYPE_WATTS
            rows.append({
                "idx":      idx,
                "name":     location or f"amp-{idx}",
                "reading":  reading if reading > 0 else None,
                "unit":     "W" if is_watts else "dA",
                "status":   _DELL_STATUS_LABELS.get(status, "unknown"),
            })
            # First system-power probe (type=24 — pwrConsumption) wins
            # the chassis-total field.
            if is_watts and reading > 0 and "host_dell_power_watts" not in out:
                out["host_dell_power_watts"] = reading
        if rows:
            out["host_dell_amperages"] = rows

    # Physical disks — physicalDiskTable.
    pd_names = walks.get("dell_pd_name") or {}
    if pd_names:
        pd_state_walk = walks.get("dell_pd_state") or {}
        pd_capacity = walks.get("dell_pd_capacity") or {}
        pd_serial = walks.get("dell_pd_serial") or {}
        pd_revision = walks.get("dell_pd_revision") or {}
        rows: list[dict] = []
        for oid, v in pd_names.items():
            idx = _row_index(oid, _OID_DELL_PD_NAME)
            name = _coerce_str(v).strip()
            state_oid = f"{_OID_DELL_PD_STATE}.{idx}"
            cap_oid = f"{_OID_DELL_PD_CAPACITY}.{idx}"
            ser_oid = f"{_OID_DELL_PD_SERIAL}.{idx}"
            rev_oid = f"{_OID_DELL_PD_REVISION}.{idx}"
            state = _coerce_int(pd_state_walk.get(state_oid))
            capacity_mb = _coerce_int(pd_capacity.get(cap_oid))
            serial_no = _coerce_str(pd_serial.get(ser_oid)).strip()
            firmware_rev = _coerce_str(pd_revision.get(rev_oid)).strip()
            rows.append({
                "idx":         idx,
                "name":        name or f"disk-{idx}",
                "state":       _DELL_PD_STATE_LABELS.get(state, f"state={state}"),
                "capacity_mb": capacity_mb if capacity_mb > 0 else None,
                "serial":      serial_no,
                "firmware":    firmware_rev,
            })
        if rows:
            out["host_dell_phys_disks"] = rows

    # Virtual disks — virtualDiskTable.
    vd_names = walks.get("dell_vd_name") or {}
    if vd_names:
        vd_state_walk = walks.get("dell_vd_state") or {}
        vd_size = walks.get("dell_vd_size") or {}
        vd_layout_walk = walks.get("dell_vd_layout") or {}
        rows: list[dict] = []
        for oid, v in vd_names.items():
            idx = _row_index(oid, _OID_DELL_VD_NAME)
            name = _coerce_str(v).strip()
            state_oid = f"{_OID_DELL_VD_STATE}.{idx}"
            size_oid = f"{_OID_DELL_VD_SIZE}.{idx}"
            layout_oid = f"{_OID_DELL_VD_LAYOUT}.{idx}"
            state = _coerce_int(vd_state_walk.get(state_oid))
            size_mb = _coerce_int(vd_size.get(size_oid))
            layout = _coerce_int(vd_layout_walk.get(layout_oid))
            rows.append({
                "idx":     idx,
                "name":    name or f"vd-{idx}",
                "state":   _DELL_VD_STATE_LABELS.get(state, f"state={state}"),
                "size_mb": size_mb if size_mb > 0 else None,
                "layout":  _DELL_VD_LAYOUT_LABELS.get(layout, ""),
            })
        if rows:
            out["host_dell_virt_disks"] = rows

    # System BIOS — systemBIOSTable; pick the first non-empty version.
    bios_versions = walks.get("dell_bios_version") or {}
    if bios_versions:
        for _, v in bios_versions.items():
            ver = _coerce_str(v).strip()
            if ver:
                out["host_bios_version"] = ver
                break
        bios_dates = walks.get("dell_bios_date") or {}
        for _, v in (bios_dates or {}).items():
            d = _coerce_str(v).strip()
            if d:
                out["host_bios_date"] = d
                break
    return out


def extract_entity_info(walk_results: dict) -> dict:
    """Shape ENTITY-MIB walks into host_model / host_serial /
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
    """Shape hrProcessorLoad walk into host_cpu_percent (mean across cores)
    AND host_cpu_per_core (per-index list, sorted by hrProcessorIndex
    so per-core lines render in the same order across ticks). #713 added
    the per-core list so the host drawer can plot one chart line per
    core; pre-#713 we kept only the mean.
    """
    if not walk_result:
        return {}
    indexed: list[tuple[int, int]] = []
    for oid, v in walk_result.items():
        n = _coerce_int(v)
        if 0 <= n <= 100:
            try:
                idx = int(oid.rsplit(".", 1)[-1])
            except ValueError:
                idx = 0
            indexed.append((idx, n))
    if not indexed:
        return {}
    indexed.sort(key=lambda t: t[0])
    loads = [n for _, n in indexed]
    avg = sum(loads) / len(loads)
    return {
        "host_cpu_percent": float(avg),
        "host_cores": len(loads),
        "host_cpu_per_core": loads,
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
            # unit-normalisation heuristic for older Cisco IOS-XE
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
    high_speed_walk: Optional[dict] = None,
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
    speeds = {_last_index(oid, _OID_IF_HIGH_SPEED): _coerce_int(v)
              for oid, v in (high_speed_walk or {}).items()}

    ifaces: list[dict] = []
    rx_total = 0
    tx_total = 0
    for idx, name in descs.items():
        if not name:
            continue
        oper = opers.get(idx, 1)  # 1 = up
        # Counter width tag so the chart helper knows whether to apply
        # 64-bit or 32-bit wrap detection. ifHCInOctets is 64-bit
        # (wraps at 2^64 ≈ 18 EB — never in practice); ifInOctets
        # fallback is 32-bit (wraps at 4.29 GB — possible under
        # sustained ≥ 1 GB/s). The 10 GB chart-side delta cap can't
        # catch wrap-then-stable on a 32-bit-only switch (delta would
        # be ~4 GB, under the cap, looks legit). Marking the source
        # lets the chart cap tighter on 32-bit ifaces.
        hc_present = in_hc.get(idx) is not None or out_hc.get(idx) is not None
        rx = in_hc.get(idx) or in_32.get(idx) or 0
        tx = out_hc.get(idx) or out_32.get(idx) or 0
        speed_mbps = speeds.get(idx) or None  # None when ifHighSpeed not exposed
        iface_row = {
            "name": name,
            "mac": "",
            "addrs": [],
            "oper_status": "up" if oper == 1 else "down",
            "rx_bytes": rx,
            "tx_bytes": tx,
            "counter_width": 64 if hc_present else 32,
        }
        if speed_mbps:
            iface_row["link_speed_mbps"] = speed_mbps
        ifaces.append(iface_row)
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

    ``entity_walks`` — optional dict of ENTITY-MIB sub-walks
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
        iface_walks.get("high_speed") or {},
    ))
    # ENTITY-MIB pass. Vendor-agnostic; emits host_model /
    # host_serial / host_firmware when the agent answers.
    if entity_walks:
        stats.update(extract_entity_info(entity_walks))
    # Vendor-private MIB pass. Dell DELL-RAC-MIB + Cisco
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


# Vendor signatures matched against sysDescr.0 (case-insensitive) for
# auto-detection. Each entry maps a vendor key (used to gate vendor-
# specific walks) to a list of substrings any of which marks a positive
# detection. Order doesn't matter — all keys are evaluated. Adding a
# seventh vendor today requires touching ~80 lines: declare the OID
# constants near the top of this module, branch on
# ``vendor in active_vendors`` inside ``probe_snmp`` (the
# ``_resolved_*`` placeholder pattern keeps the 67-slot result-unpack
# stable for skipped vendors), thread a row into the unpack tuple and
# the ``raw[]`` debug payload, add the vendor's substring set HERE,
# and add the key to ``_VALID_VENDOR_KEYS``. Future ENH would refactor
# to a single ``_VENDOR_WALK_GROUPS`` dict that the dispatch iterates,
# but no such constant exists today.
# Each entry now carries (needle, weight). Vendor-specific tokens
# (idrac / poweredge / smart-ups / ciscosystems / synology / brother)
# score higher than generic OS markers (openwrt / alpine linux), so a
# Linux-running Cisco IOS device that matches both "ucd" (via
# "openwrt") AND "cisco" (via "ciscosystems") can be tie-broken to a
# single primary vendor via ``_detect_primary_vendor`` when an
# operator wants tighter pruning. The legacy
# ``_detect_vendors_from_sysdescr`` set-return contract is preserved
# (still returns every match) — weighting only affects the new
# "primary" helper.
_VENDOR_SIGNATURES: dict[str, tuple[tuple[str, int], ...]] = {
    "dell": (
        ("idrac", 100), ("poweredge", 90), ("openmanage", 80),
        ("dell remote access", 80), ("dell ", 30),
    ),
    "cisco": (
        ("cisco nx-os", 100), ("cisco ios xr", 100), ("cisco ios-xe", 100),
        ("cisco ios", 90),
        ("ciscosystems", 80), ("cisco systems", 80),
    ),
    "apc": (
        ("apc web/snmp", 100), ("apc network management", 100),
        ("smart-ups", 90), ("symmetra", 90), ("back-ups", 80),
        ("powernet", 50),
    ),
    "synology": (
        ("diskstation", 100), ("rackstation", 100), ("synology", 70),
    ),
    "ucd": (
        # Bare "linux " / "freebsd " / "debian " / "ubuntu " were
        # dropped because they over-match: a Cisco IOS XE box, a Dell
        # iDRAC running embedded Linux, and many vendor BMCs all carry
        # "Linux" in sysDescr — auto-detecting them as `ucd` then added
        # 6 wasted UCD-SNMP-MIB walks per probe. Anchor to the actual
        # UCD/net-snmp signatures. ``ucd-snmp`` / ``net-snmp`` score
        # highest because they ARE the daemon being detected; OS
        # markers stay LOW so a Cisco IOS XE box matching both ``cisco
        # ios xe`` (weight 100) and ``openwrt`` (weight 30) tie-breaks
        # to ``cisco`` via ``_detect_primary_vendor``.
        ("ucd-snmp", 100), ("net-snmp", 100),
        ("openwrt", 30), ("alpine linux", 30), ("raspbian ", 30),
    ),
    "printer": (
        # Vendor-keyword + product-line tokens. ``samsung `` alone was
        # over-broad — Samsung NAS / Smart TV / phones all carry
        # "samsung" in sysDescr. Tightened to printer-specific Samsung
        # product-line prefixes. Vendor-specific product lines score
        # high; bare brand tokens (without a product-line discriminator)
        # are excluded.
        ("hp laserjet", 100), ("hp officejet", 100),
        ("brother ", 90), ("epson ", 90), ("canon ", 80),
        ("kyocera", 100), ("ricoh ", 90), ("xerox ", 90),
        ("lexmark", 100), ("konica minolta", 100), ("fuji xerox", 100),
        ("samsung clp", 100), ("samsung clx", 100), ("samsung ml-", 100),
        ("samsung scx", 100), ("samsung xpress", 100),
        ("samsung proxpress", 100), ("samsung multixpress", 100),
    ),
}


def _detect_vendors_from_sysdescr(sys_descr: str) -> set[str]:
    """Return the set of vendor keys matched against ``sys_descr``.

    Case-insensitive substring scan against ``_VENDOR_SIGNATURES``. An
    agent CAN match multiple vendors (e.g. a Linux-based net-snmp host
    that also runs Cisco software) — every match is honoured.

    Returns an empty set when ``sys_descr`` is empty / unrecognised.
    The caller treats an empty result as "fall back to walk-all" so an
    unknown agent stays covered (no regression vs the pre-#901 single-
    phase behaviour).
    """
    if not sys_descr:
        return set()
    haystack = sys_descr.lower()
    matched: set[str] = set()
    for vendor, needles in _VENDOR_SIGNATURES.items():
        for needle, _weight in needles:
            if needle in haystack:
                matched.add(vendor)
                break
    return matched


def _detect_primary_vendor(sys_descr: str) -> Optional[str]:
    """Return the single highest-scoring vendor matched against ``sys_descr``.

    For a fleet that matches multiple vendors against one host (e.g. a
    Linux-running Cisco IOS XE device matches both ``cisco`` via
    ``ciscosystems`` AND ``ucd`` via ``openwrt``), the operator can
    pick a tighter walk set by going through the primary-only path.
    Vendor-specific tokens score higher than generic OS markers so
    ``cisco ios xe`` (weight 100) wins over ``openwrt`` (weight 30).

    Returns the vendor key with the maximum aggregate score across all
    its matching needles, or ``None`` when no signature matches.
    Ties are broken by stable iteration order of ``_VENDOR_SIGNATURES``.
    """
    if not sys_descr:
        return None
    haystack = sys_descr.lower()
    best_vendor: Optional[str] = None
    best_score = 0
    for vendor, needles in _VENDOR_SIGNATURES.items():
        score = sum(weight for needle, weight in needles if needle in haystack)
        if score > best_score:
            best_score = score
            best_vendor = vendor
    return best_vendor


# Valid per-host vendor-override values (passes through to probe_snmp's
# `vendors` kwarg). Mirrors the keys in `_VENDOR_SIGNATURES`. The
# special ``"auto"`` value (operator-facing) is translated to None
# before reaching probe_snmp.
_VALID_VENDOR_KEYS: frozenset[str] = frozenset(_VENDOR_SIGNATURES.keys())


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
    bypass_cooldown: bool = False,
    wall_clock_budget: Optional[float] = None,
    walk_concurrency: Optional[int] = None,
    vendors: Optional[set[str]] = None,
) -> dict:
    """Probe one SNMP-speaking host. See module docstring for the contract.

    Returns ``{"hosts": {host_key: stats}, "error": None}`` on success or
    ``{"hosts": {}, "error": "..."}`` on any failure. Never raises.

    Like Webmin, this is a per-host probe (no central hub). Each
    successful probe yields ONE entry keyed by the agent-reported
    ``sysName.0`` (falling back to the supplied host string when sysName
    isn't readable — common on appliances that disable SNMP system
    naming).

    ``verbose`` — when True, the response also carries a ``raw``
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

    # Cool-down throttles AUTOMATIC background probes (sampler /
    # gather fan-out) when a host is unreachable, so the next 5
    # minutes of routine ticks don't burn UDP timeouts in parallel.
    # Operator-initiated tests (Admin → Hosts → Test connection)
    # MUST bypass — the operator clicked Test specifically to
    # validate connectivity NOW, and gating their click on the
    # cool-down means they can never see whether their fix worked
    # until the cool-down expires.
    if not bypass_cooldown:
        cd = _in_cooldown(host_clean, port_int)
        if cd is not None:
            return {
                "hosts": {},
                "error": f"snmp: in cool-down ({int(cd)}s remaining) — "
                         f"host was unreachable on the previous probe",
                # Structured marker — see logic/webmin.py for the rationale.
                # Auto-pause counters check this to avoid counting a cool-
                # down skip toward the threshold.
                "skipped_cooldown": True,
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

    # reuse the module-level SnmpEngine singleton instead of
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
    # Phase 0 — sysDescr GET. Runs SOLO before any other walk so we can
    # detect the vendor from the response and prune irrelevant vendor
    # walks before they add to the wall-clock budget. The cost is one
    # extra round-trip (~one walk's worth of latency, ~0.5-1s on a
    # typical agent) but the savings on multi-vendor probes are huge:
    # an iDRAC needs only base + Dell walks (~50 OIDs) so skipping
    # Cisco / APC / UCD / Synology / Printer walks (~17 OIDs) drops
    # ~16s of wall-clock at concurrency=1.
    #
    # Per-host ``vendors`` override (operator declared on the curated
    # row) bypasses auto-detection — useful for agents with stripped
    # sysDescr or for forcing a specific vendor's walks even when
    # auto-detect would skip them. ``None`` (the default) means
    # auto-detect from sysDescr.
    # ----------------------------------------------------------------
    try:
        try:
            sys_resp_phase0 = await _snmp_get(engine, auth, target, [
                _OID_SYS_NAME, _OID_SYS_DESCR, _OID_SYS_UPTIME,
                _OID_SYS_CONTACT, _OID_SYS_LOCATION,
            ])
        except (asyncio.CancelledError, KeyboardInterrupt):
            raise
        except Exception:
            # Phase 0 failure → empty response. Phase 1 still runs but
            # vendor detection will return empty → walk-all fallback.
            # Keeps the probe useful on agents where sysDescr fails but
            # other OIDs respond.
            sys_resp_phase0 = {}
        sys_descr_str = ""
        if isinstance(sys_resp_phase0, dict):
            sys_descr_str = str(sys_resp_phase0.get(_OID_SYS_DESCR, "") or "")
        # Resolve active vendor set. Order: per-host override > auto-
        # detection > walk-all fallback.
        if vendors is not None:
            active_vendors = {v for v in vendors if v in _VALID_VENDOR_KEYS}
        else:
            active_vendors = _detect_vendors_from_sysdescr(sys_descr_str)
        # Empty after resolution = fall back to walk-all so unknown
        # agents (sysDescr stripped, novel hardware) stay covered.
        if not active_vendors:
            active_vendors = set(_VALID_VENDOR_KEYS)
        # Dell-only fleets skip ENTITY-MIB walks — Dell-RAC-MIB has the
        # chassis identity (model / serial / firmware) already, so the
        # generic ENTITY-MIB walks are pure overhead on a homogeneous
        # Dell server. Saves 5 walks (~5s at concurrency=1).
        skip_entity_mib = (active_vendors == {"dell"})
        # Resolved-coroutine helpers — instant-return placeholders that
        # plug into the existing 67-slot result list when a vendor is
        # pruned. Keeps the unpacking at the bottom of probe_snmp
        # untouched: skipped walks return [] / {} → downstream
        # extractors emit no fields for that vendor (correct).
        # Uniform helper signatures: each placeholder accepts an
        # optional value with a sensible default. ``_resolved_value``
        # takes no default because its sole call site always passes the
        # pre-fetched Phase 0 response; the kwarg form lets future call
        # sites omit it and get None. ``_resolved_dict`` / `_list`
        # default to empty containers (the skipped-walk semantics).
        async def _resolved_value(v=None):
            return v
        async def _resolved_dict(v=None):
            return v if v is not None else {}
        async def _resolved_list(v=None):
            return v if v is not None else []
        # Re-stamp Phase 0's sys response into the slot the unpack
        # expects so `sys_task` carries the same shape as before.
        sys_task = _resolved_value(sys_resp_phase0)
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
        # IF-MIB::ifHighSpeed (Mbps) for the per-port utilization
        # heatmap. Devices that don't expose ifHighSpeed simply return
        # an empty walk; per-iface link_speed_mbps stays None and the
        # heatmap renders the iface in grey ("unknown speed").
        if_speed_task = _snmp_walk(engine, auth, target, _OID_IF_HIGH_SPEED)
        # ENTITY-MIB physical-component walks. Vendor-agnostic source
        # for model name / serial / firmware on enterprise gear. Pruned
        # on Dell-only agents because Dell-RAC-MIB has the same identity
        # data and ENTITY-MIB is a redundant 5-walk overhead.
        if skip_entity_mib:
            ent_descr_task  = _resolved_list()
            ent_name_task   = _resolved_list()
            ent_serial_task = _resolved_list()
            ent_model_task  = _resolved_list()
            ent_fw_task     = _resolved_list()
        else:
            ent_descr_task  = _snmp_walk(engine, auth, target, _OID_ENT_DESCR)
            ent_name_task   = _snmp_walk(engine, auth, target, _OID_ENT_NAME)
            ent_serial_task = _snmp_walk(engine, auth, target, _OID_ENT_SERIAL_NUM)
            ent_model_task  = _snmp_walk(engine, auth, target, _OID_ENT_MODEL_NAME)
            ent_fw_task     = _snmp_walk(engine, auth, target, _OID_ENT_SOFTWARE_REV)
        # Vendor-private MIB GETs/walks. Each vendor block is gated
        # on whether it's in `active_vendors` (resolved at the top of
        # this try block from per-host override > sysDescr auto-detect
        # > walk-all fallback). Skipped vendors return empty
        # placeholders so the result-unpacking 67 slots below stay
        # intact and the downstream extractors emit no fields for
        # vendors that don't apply.
        if "dell" in active_vendors:
            dell_vendor_task = _snmp_get(engine, auth, target, [
                _OID_DELL_CHASSIS_SERVICE_TAG,
                _OID_DELL_CHASSIS_MODEL,
                _OID_DELL_RAC_FIRMWARE,
                _OID_DELL_GLOBAL_SYS_STATUS,
                _OID_DELL_SYSTEM_SERVICE_TAG,
                _OID_DELL_SYSTEM_MODEL_NAME,
            ])
            # Dell server health tables (iDRAC) — fans, temps, PSUs,
            # voltages, amperage, physical / virtual disks, BIOS.
            dell_fan_status_task   = _snmp_walk(engine, auth, target, _OID_DELL_FAN_STATUS)
            dell_fan_reading_task  = _snmp_walk(engine, auth, target, _OID_DELL_FAN_READING)
            dell_fan_type_task     = _snmp_walk(engine, auth, target, _OID_DELL_FAN_TYPE)
            dell_fan_loc_task      = _snmp_walk(engine, auth, target, _OID_DELL_FAN_LOCATION)
            dell_temp_status_task  = _snmp_walk(engine, auth, target, _OID_DELL_TEMP_STATUS)
            dell_temp_reading_task = _snmp_walk(engine, auth, target, _OID_DELL_TEMP_READING)
            dell_temp_type_task    = _snmp_walk(engine, auth, target, _OID_DELL_TEMP_TYPE)
            dell_temp_loc_task     = _snmp_walk(engine, auth, target, _OID_DELL_TEMP_LOCATION)
            dell_psu_status_task   = _snmp_walk(engine, auth, target, _OID_DELL_PSU_STATUS)
            dell_psu_watts_task    = _snmp_walk(engine, auth, target, _OID_DELL_PSU_WATTS)
            dell_psu_type_task     = _snmp_walk(engine, auth, target, _OID_DELL_PSU_TYPE)
            dell_psu_loc_task      = _snmp_walk(engine, auth, target, _OID_DELL_PSU_LOCATION)
            dell_volt_status_task  = _snmp_walk(engine, auth, target, _OID_DELL_VOLT_STATUS)
            dell_volt_reading_task = _snmp_walk(engine, auth, target, _OID_DELL_VOLT_READING)
            dell_volt_loc_task     = _snmp_walk(engine, auth, target, _OID_DELL_VOLT_LOCATION)
            dell_amp_status_task   = _snmp_walk(engine, auth, target, _OID_DELL_AMP_STATUS)
            dell_amp_reading_task  = _snmp_walk(engine, auth, target, _OID_DELL_AMP_READING)
            dell_amp_type_task     = _snmp_walk(engine, auth, target, _OID_DELL_AMP_TYPE)
            dell_amp_loc_task      = _snmp_walk(engine, auth, target, _OID_DELL_AMP_LOCATION)
            dell_pd_name_task      = _snmp_walk(engine, auth, target, _OID_DELL_PD_NAME)
            dell_pd_state_task     = _snmp_walk(engine, auth, target, _OID_DELL_PD_STATE)
            dell_pd_capacity_task  = _snmp_walk(engine, auth, target, _OID_DELL_PD_CAPACITY)
            dell_pd_serial_task    = _snmp_walk(engine, auth, target, _OID_DELL_PD_SERIAL)
            dell_pd_revision_task  = _snmp_walk(engine, auth, target, _OID_DELL_PD_REVISION)
            dell_vd_name_task      = _snmp_walk(engine, auth, target, _OID_DELL_VD_NAME)
            dell_vd_state_task     = _snmp_walk(engine, auth, target, _OID_DELL_VD_STATE)
            dell_vd_size_task      = _snmp_walk(engine, auth, target, _OID_DELL_VD_SIZE)
            dell_vd_layout_task    = _snmp_walk(engine, auth, target, _OID_DELL_VD_LAYOUT)
            dell_bios_version_task = _snmp_walk(engine, auth, target, _OID_DELL_BIOS_VERSION)
            dell_bios_date_task    = _snmp_walk(engine, auth, target, _OID_DELL_BIOS_RELEASE_DATE)
        else:
            dell_vendor_task       = _resolved_dict()
            dell_fan_status_task   = _resolved_list()
            dell_fan_reading_task  = _resolved_list()
            dell_fan_type_task     = _resolved_list()
            dell_fan_loc_task      = _resolved_list()
            dell_temp_status_task  = _resolved_list()
            dell_temp_reading_task = _resolved_list()
            dell_temp_type_task    = _resolved_list()
            dell_temp_loc_task     = _resolved_list()
            dell_psu_status_task   = _resolved_list()
            dell_psu_watts_task    = _resolved_list()
            dell_psu_type_task     = _resolved_list()
            dell_psu_loc_task      = _resolved_list()
            dell_volt_status_task  = _resolved_list()
            dell_volt_reading_task = _resolved_list()
            dell_volt_loc_task     = _resolved_list()
            dell_amp_status_task   = _resolved_list()
            dell_amp_reading_task  = _resolved_list()
            dell_amp_type_task     = _resolved_list()
            dell_amp_loc_task      = _resolved_list()
            dell_pd_name_task      = _resolved_list()
            dell_pd_state_task     = _resolved_list()
            dell_pd_capacity_task  = _resolved_list()
            dell_pd_serial_task    = _resolved_list()
            dell_pd_revision_task  = _resolved_list()
            dell_vd_name_task      = _resolved_list()
            dell_vd_state_task     = _resolved_list()
            dell_vd_size_task      = _resolved_list()
            dell_vd_layout_task    = _resolved_list()
            dell_bios_version_task = _resolved_list()
            dell_bios_date_task    = _resolved_list()
        if "cisco" in active_vendors:
            cisco_hw_task = _snmp_get(engine, auth, target, [
                _OID_CISCO_PRODUCT_HW_VER,
            ])
            cisco_mem_used_task = _snmp_walk(engine, auth, target, _OID_CISCO_MEM_POOL_USED)
            cisco_mem_free_task = _snmp_walk(engine, auth, target, _OID_CISCO_MEM_POOL_FREE)
            cisco_mem_name_task = _snmp_walk(engine, auth, target, _OID_CISCO_MEM_POOL_NAME)
            cisco_cpu_task      = _snmp_walk(engine, auth, target, _OID_CISCO_CPU_TOTAL_5SEC)
        else:
            cisco_hw_task       = _resolved_dict()
            cisco_mem_used_task = _resolved_list()
            cisco_mem_free_task = _resolved_list()
            cisco_mem_name_task = _resolved_list()
            cisco_cpu_task      = _resolved_list()
        # APC PowerNet-MIB. One GET covers identity + battery + output.
        if "apc" in active_vendors:
            apc_vendor_task = _snmp_get(engine, auth, target, [
                _OID_APC_UPS_MODEL, _OID_APC_UPS_NAME,
                _OID_APC_UPS_FIRMWARE, _OID_APC_UPS_SERIAL,
                _OID_APC_UPS_BATT_STATUS, _OID_APC_UPS_BATT_CAPACITY,
                _OID_APC_UPS_BATT_TEMP_C, _OID_APC_UPS_BATT_RUNTIME,
                _OID_APC_UPS_OUTPUT_STATUS, _OID_APC_UPS_OUTPUT_LOAD,
            ])
        else:
            apc_vendor_task = _resolved_dict()
        # UCD-SNMP-MIB. Memory + CPU% GETs; load + dskTable walks.
        if "ucd" in active_vendors:
            ucd_mem_cpu_task = _snmp_get(engine, auth, target, [
                _OID_UCD_MEM_TOTAL_REAL, _OID_UCD_MEM_AVAIL_REAL, _OID_UCD_MEM_TOTAL_FREE,
                _OID_UCD_MEM_BUFFER, _OID_UCD_MEM_CACHED,
                _OID_UCD_SS_CPU_USER, _OID_UCD_SS_CPU_SYSTEM, _OID_UCD_SS_CPU_IDLE,
            ])
            ucd_load_task      = _snmp_walk(engine, auth, target, _OID_UCD_LA_LOAD_INT)
            ucd_dsk_path_task  = _snmp_walk(engine, auth, target, _OID_UCD_DSK_PATH)
            ucd_dsk_total_task = _snmp_walk(engine, auth, target, _OID_UCD_DSK_TOTAL)
            ucd_dsk_used_task  = _snmp_walk(engine, auth, target, _OID_UCD_DSK_USED)
            ucd_dsk_pct_task   = _snmp_walk(engine, auth, target, _OID_UCD_DSK_PERCENT)
        else:
            ucd_mem_cpu_task   = _resolved_dict()
            ucd_load_task      = _resolved_list()
            ucd_dsk_path_task  = _resolved_list()
            ucd_dsk_total_task = _resolved_list()
            ucd_dsk_used_task  = _resolved_list()
            ucd_dsk_pct_task   = _resolved_list()
        # SYNOLOGY-MIB. One GET covers identity + system status.
        if "synology" in active_vendors:
            syno_vendor_task = _snmp_get(engine, auth, target, [
                _OID_SYNO_MODEL_NAME, _OID_SYNO_SERIAL_NUMBER, _OID_SYNO_DSM_VERSION,
                _OID_SYNO_SYSTEM_STATUS, _OID_SYNO_SYSTEM_TEMP, _OID_SYNO_UPGRADE_AVAIL,
            ])
        else:
            syno_vendor_task = _resolved_dict()
        # Printer-MIB. Page count + console message GET; per-supply
        # walks (description / max / level).
        if "printer" in active_vendors:
            prt_basic_task = _snmp_get(engine, auth, target, [
                _OID_PRT_PAGE_COUNT, _OID_PRT_CONSOLE_MSG,
            ])
            prt_supply_descr_task = _snmp_walk(engine, auth, target, _OID_PRT_SUPPLIES_DESCR)
            prt_supply_max_task   = _snmp_walk(engine, auth, target, _OID_PRT_SUPPLIES_MAX_CAP)
            prt_supply_level_task = _snmp_walk(engine, auth, target, _OID_PRT_SUPPLIES_LEVEL)
        else:
            prt_basic_task        = _resolved_dict()
            prt_supply_descr_task = _resolved_list()
            prt_supply_max_task   = _resolved_list()
            prt_supply_level_task = _resolved_list()

        # wrap the gather in wait_for so the TimeoutError catch
        # becomes reachable (asyncio.gather alone can't raise TimeoutError
        # without wait_for) AND the caller earns a wall-clock guarantee.
        # Budget is operator-tunable via
        # ``tuning_snmp_wall_clock_budget_seconds`` (default 60s) —
        # the previous hardcoded ``(timeout + 2.0) * 2`` formula was
        # too tight for slow embedded snmpd (WD MyCloud / network
        # printers / low-power NAS): the probe fans out ~60 OID
        # operations sequentially-on-the-wire, so a 500ms RTT on a
        # slow device blows past the old 14s budget on every cycle
        # and trips the auto-pause threshold. The floor is still
        # the per-OID timeout + 5s safety margin so a misconfigured
        # tiny budget can't undercut the per-OID retry window.
        # Per-call override (debug panel passes a tighter budget so the
        # operator-facing /api/hosts/debug request returns within the
        # upstream proxy_read_timeout window even when the SNMP probe
        # alone could otherwise take 60s+ on slow BMC-class agents).
        if wall_clock_budget is not None:
            wall_clock_budget_resolved = max(timeout + 5.0, float(wall_clock_budget))
        else:
            wall_clock_budget_resolved = max(
                timeout + 5.0,
                float(_tuning.tuning_int("tuning_snmp_wall_clock_budget_seconds")),
            )
        # Per-host walk concurrency cap. Default 1 (fully serialised
        # — CLI-equivalent wire-level pattern) protects slow embedded
        # snmpd (low-power NAS, network printers, OpenWrt-class gear)
        # from UDP receive-queue overflow when 60+ concurrent bulk
        # requests arrive simultaneously. Server-class BMCs (Dell
        # iDRAC9 / iDRAC10, Cisco IMC, Supermicro IPMI) handle parallel
        # queries fine and benefit dramatically from concurrency > 1
        # because pysnmp v7's per-walk setup cost compounds 67× at
        # concurrency=1 even when the agent itself is fast on the wire.
        # Per-call ``walk_concurrency`` override (passed from
        # ``_merge_one_host`` / ``api_hosts_debug`` / ``/api/snmp/test``
        # via the host's ``snmp.walk_concurrency`` config) wins over
        # the global tunable so a Dell iDRAC can run at 4-8 without
        # affecting a flaky printer's safety floor at 1.
        if walk_concurrency is not None:
            walk_concurrency_resolved = max(1, int(walk_concurrency))
        else:
            # Per-vendor global default. When ``active_vendors``
            # resolved to EXACTLY ONE vendor (auto-detect picked one,
            # no walk-all fallback) AND the vendor's tunable is
            # non-zero, prefer it over the generic
            # ``tuning_snmp_per_host_walk_concurrency``. Lets a
            # homogeneous fleet pin a sensible default per vendor mix
            # (e.g. 4 for Dell iDRAC, 1 for printers) without forcing
            # the operator to set per-host overrides on every row.
            # Single-vendor gate: if auto-detect matched multiple OR
            # fell through to walk-all (== set(_VALID_VENDOR_KEYS)),
            # we use the generic default — vendor-specific defaults
            # only make sense when the agent IS that vendor.
            vendor_default = 0
            if (
                len(active_vendors) == 1
                and active_vendors != set(_VALID_VENDOR_KEYS)
            ):
                only_vendor = next(iter(active_vendors))
                vendor_key = f"tuning_snmp_walk_concurrency_{only_vendor}"
                try:
                    vendor_default = int(_tuning.tuning_int(vendor_key))
                except (TypeError, ValueError, KeyError):
                    vendor_default = 0
            if vendor_default > 0:
                walk_concurrency_resolved = vendor_default
            else:
                walk_concurrency_resolved = max(
                    1, int(_tuning.tuning_int("tuning_snmp_per_host_walk_concurrency"))
                )
        walk_sem = asyncio.Semaphore(walk_concurrency_resolved)
        # Placeholder coroutines (``_resolved_value`` / ``_resolved_dict``
        # / ``_resolved_list``) are instant-return — they do no I/O. With
        # walk_concurrency=1 the semaphore serialises ~30 placeholder
        # awaits behind every real walk for no reason. Identifying them
        # by qualname lets ``_bounded`` skip the semaphore entirely so
        # vendor pruning's "free skip" stays free.
        _PLACEHOLDER_NAMES = frozenset({
            "_resolved_value", "_resolved_dict", "_resolved_list",
        })

        async def _bounded(coro):
            # When an outer ``asyncio.wait_for`` cancels the gather,
            # any wrapper that hadn't yet acquired ``walk_sem`` gets a
            # CancelledError BEFORE entering the body — so the
            # captured ``coro`` is never awaited. Python emits
            # ``coroutine '...' was never awaited`` on GC for each one.
            # With ``Semaphore(1)`` and ~67 contenders, that's up to
            # 66 warnings per timed-out probe. ``coro.close()`` on the
            # cancellation path runs the captured coroutine to
            # completion (synthetic ``GeneratorExit`` at its first
            # suspension point) so the GC sees a "done" coroutine and
            # stays quiet. ``close()`` is a no-op if the coroutine
            # already started, so the body path is unaffected.
            #
            # Explicit acquire/release (instead of ``async with walk_sem:``)
            # makes the placeholder-bypass + cancellation-cleanup paths
            # read uniformly — each branch is one statement, not nested
            # under a context manager. The try/finally guarantees
            # release even if the body raises after a successful
            # acquire, matching the semantics ``async with`` provided.
            code_name = getattr(getattr(coro, "cr_code", None), "co_name", "")
            if code_name in _PLACEHOLDER_NAMES:
                # Placeholder coroutines bypass the semaphore — they
                # complete in one step and would otherwise pin the
                # semaphore behind real walks for no actual concurrency
                # benefit at walk_concurrency=1.
                try:
                    return await coro
                except BaseException:
                    try:
                        coro.close()
                    except (RuntimeError, GeneratorExit):
                        pass
                    raise
            acquired = False
            try:
                await walk_sem.acquire()
                acquired = True
                return await coro
            except BaseException:
                try:
                    coro.close()
                except (RuntimeError, GeneratorExit):
                    pass
                raise
            finally:
                if acquired:
                    walk_sem.release()

        all_tasks = [
            sys_task, cpu_task,
            st_type_task, st_desc_task, st_unit_task, st_size_task, st_used_task,
            if_descr_task, if_oper_task,
            if_hc_in_task, if_hc_out_task,
            if_in_task, if_out_task,
            if_speed_task,
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
            dell_fan_status_task, dell_fan_reading_task,
            dell_fan_type_task, dell_fan_loc_task,
            dell_temp_status_task, dell_temp_reading_task,
            dell_temp_type_task, dell_temp_loc_task,
            dell_psu_status_task, dell_psu_watts_task,
            dell_psu_type_task, dell_psu_loc_task,
            dell_volt_status_task, dell_volt_reading_task, dell_volt_loc_task,
            dell_amp_status_task, dell_amp_reading_task,
            dell_amp_type_task, dell_amp_loc_task,
            dell_pd_name_task, dell_pd_state_task, dell_pd_capacity_task,
            dell_pd_serial_task, dell_pd_revision_task,
            dell_vd_name_task, dell_vd_state_task,
            dell_vd_size_task, dell_vd_layout_task,
            dell_bios_version_task, dell_bios_date_task,
        ]
        # Wrap each pre-constructed coroutine in an asyncio.Task so we
        # can observe per-task completion state on timeout. Tracking
        # tasks (instead of awaiting raw coroutines via gather) lets us
        # report how far the probe got before the wall-clock fired —
        # operators chasing "snmp: timeout" can distinguish "agent is
        # dead" from "agent is fine but we ran ~67 OID walks at default
        # concurrency=1 and didn't finish in time".
        running = [asyncio.create_task(_bounded(t)) for t in all_tasks]
        try:
            results = await asyncio.wait_for(asyncio.gather(
                *running, return_exceptions=False,
            ), timeout=wall_clock_budget_resolved)
        except asyncio.TimeoutError:
            # Cancel still-running tasks + flush their cancellations so
            # nothing leaks past the function return. ``gather`` with
            # ``return_exceptions=True`` swallows the CancelledErrors
            # raised by the explicit cancel() calls below.
            done_count = sum(1 for t in running if t.done() and not t.cancelled())
            for t in running:
                if not t.done():
                    t.cancel()
            await asyncio.gather(*running, return_exceptions=True)
            _arm_cooldown(host_clean, port_int)
            # Error message surfaces the ACTUAL resolved values for both
            # tunables — operator can see at a glance whether their per-
            # host override / global Admin → Config edit is taking
            # effect, vs being silently shadowed. The "raise" recipe
            # references the SAME concrete numbers so they don't have
            # to cross-reference Admin → Config to know what the
            # current value is before deciding what to bump it to.
            tunable_walk_global = int(
                _tuning.tuning_int("tuning_snmp_per_host_walk_concurrency")
            )
            tunable_budget_global = int(
                _tuning.tuning_int("tuning_snmp_wall_clock_budget_seconds")
            )
            if walk_concurrency is not None:
                walk_source = "per-host override"
                walk_qualifier = (
                    f"per-host override; global tunable="
                    f"{tunable_walk_global}"
                )
            else:
                walk_source = "global tunable"
                walk_qualifier = "global tunable; no per-host override set"
            if wall_clock_budget is not None:
                budget_source = "per-call override"
                budget_qualifier = (
                    f"per-call override; global tunable="
                    f"{tunable_budget_global}s"
                )
            else:
                budget_source = "global tunable"
                budget_qualifier = "global tunable; no per-call override"
            return {
                "hosts": {},
                "error": (
                    f"snmp: timeout against {host_clean}:{port_int} "
                    f"({done_count} of {len(running)} OID branches "
                    f"completed within {int(wall_clock_budget_resolved)}s "
                    f"budget) — current walk concurrency="
                    f"{walk_concurrency_resolved} ({walk_qualifier}); "
                    f"current wall-clock budget="
                    f"{int(wall_clock_budget_resolved)}s "
                    f"({budget_qualifier}). Raise the per-host "
                    f"`snmp.walk_concurrency` (Admin → Hosts) for THIS "
                    f"host if the agent handles parallel queries safely "
                    f"(recommended: 4 for Dell iDRAC / Cisco IMC / "
                    f"Supermicro IPMI; 8 for Cisco / Synology / linux "
                    f"net-snmp), OR raise `tuning_snmp_wall_clock_"
                    f"budget_seconds` in Admin → Config to give every "
                    f"probe more time."
                ),
                # Structured fields so the SPA / debug panel can render
                # the diagnostic without parsing the prose.
                "walk_concurrency_resolved": walk_concurrency_resolved,
                "walk_concurrency_source":   walk_source,
                "walk_concurrency_global":   tunable_walk_global,
                "wall_clock_budget_resolved": int(wall_clock_budget_resolved),
                "wall_clock_budget_source":   budget_source,
                "wall_clock_budget_global":   tunable_budget_global,
                "completed_branches":         done_count,
                "total_branches":             len(running),
                # Vendor pruning diagnostics — operator can see which
                # vendor walks were live AND why (per-host override,
                # sysDescr auto-detect, or walk-all fallback).
                "active_vendors":             sorted(active_vendors),
                "active_vendors_source": (
                    "per-host override" if vendors is not None
                    else (
                        "auto-detected from sysDescr"
                        if _detect_vendors_from_sysdescr(sys_descr_str)
                        else "walk-all fallback (sysDescr empty / unrecognised)"
                    )
                ),
                "sys_descr":                  sys_descr_str[:200],
                "skip_entity_mib":            skip_entity_mib,
            }
    except (asyncio.CancelledError, KeyboardInterrupt):
        # Outer cancellation (parent task killed). Flush our running
        # tasks so we don't leak them — same rationale as the
        # TimeoutError branch above, just no error return shape.
        for t in running:
            if not t.done():
                t.cancel()
        try:
            await asyncio.gather(*running, return_exceptions=True)
        except (asyncio.CancelledError, KeyboardInterrupt):
            pass
        raise
    except Exception as e:
        return {"hosts": {}, "error": f"snmp: probe failed: {e}"}

    (sys_get, cpu_walk,
     st_type, st_desc, st_unit, st_size, st_used,
     if_descr, if_oper,
     if_hc_in, if_hc_out, if_in, if_out,
     if_high_speed,
     ent_descr, ent_name, ent_serial, ent_model, ent_fw,
     dell_vendor_get, cisco_hw_get,
     cisco_mem_used_walk, cisco_mem_free_walk, cisco_mem_name_walk,
     cisco_cpu_walk,
     apc_vendor_get, ucd_mem_cpu_get, ucd_load_walk,
     ucd_dsk_path_walk, ucd_dsk_total_walk, ucd_dsk_used_walk,
     ucd_dsk_pct_walk, syno_vendor_get,
     prt_basic_get, prt_supply_descr_walk,
     prt_supply_max_walk, prt_supply_level_walk,
     dell_fan_status_walk, dell_fan_reading_walk,
     dell_fan_type_walk, dell_fan_loc_walk,
     dell_temp_status_walk, dell_temp_reading_walk,
     dell_temp_type_walk, dell_temp_loc_walk,
     dell_psu_status_walk, dell_psu_watts_walk,
     dell_psu_type_walk, dell_psu_loc_walk,
     dell_volt_status_walk, dell_volt_reading_walk, dell_volt_loc_walk,
     dell_amp_status_walk, dell_amp_reading_walk,
     dell_amp_type_walk, dell_amp_loc_walk,
     dell_pd_name_walk, dell_pd_state_walk, dell_pd_capacity_walk,
     dell_pd_serial_walk, dell_pd_revision_walk,
     dell_vd_name_walk, dell_vd_state_walk,
     dell_vd_size_walk, dell_vd_layout_walk,
     dell_bios_version_walk, dell_bios_date_walk) = results

    # entity walks count toward the "any data" gate so a switch
    # that answers ONLY entPhysicalSerialNum (no sysDescr / no ifTable)
    # still passes the cool-down clear. ENTITY-MIB-only is a real
    # config — some agents are locked down to Entity-MIB only.
    # vendor-private OIDs also count: an iDRAC's whole identity
    # surface lives under DELL-RAC-MIB, so a successful chassis-tag
    # GET is enough signal that the host is alive even when every
    # standard MIB-II / Host-Resources / ENTITY-MIB walk came back empty.
    if not (sys_get or cpu_walk or st_size or if_descr or ent_serial or ent_model
            or dell_vendor_get or cisco_hw_get or cisco_mem_used_walk
            or cisco_cpu_walk
            or apc_vendor_get or ucd_mem_cpu_get or ucd_load_walk
            or ucd_dsk_total_walk or syno_vendor_get
            or prt_basic_get or prt_supply_descr_walk
            or dell_fan_reading_walk or dell_temp_reading_walk
            or dell_psu_status_walk or dell_pd_name_walk or dell_vd_name_walk
            or dell_bios_version_walk):
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
         "in_32": if_in, "out_32": if_out,
         "high_speed": if_high_speed},
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
            "dell_fan_status":   dell_fan_status_walk,
            "dell_fan_reading":  dell_fan_reading_walk,
            "dell_fan_type":     dell_fan_type_walk,
            "dell_fan_loc":      dell_fan_loc_walk,
            "dell_temp_status":  dell_temp_status_walk,
            "dell_temp_reading": dell_temp_reading_walk,
            "dell_temp_type":    dell_temp_type_walk,
            "dell_temp_loc":     dell_temp_loc_walk,
            "dell_psu_status":   dell_psu_status_walk,
            "dell_psu_watts":    dell_psu_watts_walk,
            "dell_psu_type":     dell_psu_type_walk,
            "dell_psu_loc":      dell_psu_loc_walk,
            "dell_volt_status":  dell_volt_status_walk,
            "dell_volt_reading": dell_volt_reading_walk,
            "dell_volt_loc":     dell_volt_loc_walk,
            "dell_amp_status":   dell_amp_status_walk,
            "dell_amp_reading":  dell_amp_reading_walk,
            "dell_amp_type":     dell_amp_type_walk,
            "dell_amp_loc":      dell_amp_loc_walk,
            "dell_pd_name":      dell_pd_name_walk,
            "dell_pd_state":     dell_pd_state_walk,
            "dell_pd_capacity":  dell_pd_capacity_walk,
            "dell_pd_serial":    dell_pd_serial_walk,
            "dell_pd_revision":  dell_pd_revision_walk,
            "dell_vd_name":      dell_vd_name_walk,
            "dell_vd_state":     dell_vd_state_walk,
            "dell_vd_size":      dell_vd_size_walk,
            "dell_vd_layout":    dell_vd_layout_walk,
            "dell_bios_version": dell_bios_version_walk,
            "dell_bios_date":    dell_bios_date_walk,
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

    # Vendor-pruning + budget diagnostics surface on the SUCCESS path
    # too — operators verifying a per-host `vendors` override or
    # `walk_concurrency` / `wall_clock_budget` override should see
    # which walks were actually live without having to wait for a
    # timeout. Same shape as the TimeoutError branch above so the
    # debug panel can render diagnostics uniformly.
    out = {
        "hosts": {host_key: stats} if host_key else {},
        "error": None,
        "active_vendors": sorted(active_vendors),
        "active_vendors_source": (
            "per-host override" if vendors is not None
            else (
                "auto-detected from sysDescr"
                if _detect_vendors_from_sysdescr(sys_descr_str)
                else "walk-all fallback (sysDescr empty / unrecognised)"
            )
        ),
        "sys_descr": (sys_descr_str or "")[:200],
        "skip_entity_mib": skip_entity_mib,
        "walk_concurrency_resolved": walk_concurrency_resolved,
        "wall_clock_budget_resolved": int(wall_clock_budget_resolved),
    }
    if verbose:
        # surface the parsed walks so the host-drawer debug
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
        # entity rows for the verbose surface. Each physical
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
            # vendor-private MIB visibility. Dell GETs render as
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
            # Dell server health tables — fans, temps, PSUs, voltages,
            # amperage, physical / virtual disks, BIOS. Empty walks on
            # non-Dell agents so safe to always include.
            "vendor_dell_fan_status":   _stringify(dell_fan_status_walk),
            "vendor_dell_fan_reading":  _stringify(dell_fan_reading_walk),
            "vendor_dell_fan_type":     _stringify(dell_fan_type_walk),
            "vendor_dell_fan_loc":      _stringify(dell_fan_loc_walk),
            "vendor_dell_temp_status":  _stringify(dell_temp_status_walk),
            "vendor_dell_temp_reading": _stringify(dell_temp_reading_walk),
            "vendor_dell_temp_type":    _stringify(dell_temp_type_walk),
            "vendor_dell_temp_loc":     _stringify(dell_temp_loc_walk),
            "vendor_dell_psu_status":   _stringify(dell_psu_status_walk),
            "vendor_dell_psu_watts":    _stringify(dell_psu_watts_walk),
            "vendor_dell_psu_type":     _stringify(dell_psu_type_walk),
            "vendor_dell_psu_loc":      _stringify(dell_psu_loc_walk),
            "vendor_dell_volt_status":  _stringify(dell_volt_status_walk),
            "vendor_dell_volt_reading": _stringify(dell_volt_reading_walk),
            "vendor_dell_volt_loc":     _stringify(dell_volt_loc_walk),
            "vendor_dell_amp_status":   _stringify(dell_amp_status_walk),
            "vendor_dell_amp_reading":  _stringify(dell_amp_reading_walk),
            "vendor_dell_amp_type":     _stringify(dell_amp_type_walk),
            "vendor_dell_amp_loc":      _stringify(dell_amp_loc_walk),
            "vendor_dell_pd_name":      _stringify(dell_pd_name_walk),
            "vendor_dell_pd_state":     _stringify(dell_pd_state_walk),
            "vendor_dell_pd_capacity":  _stringify(dell_pd_capacity_walk),
            "vendor_dell_pd_serial":    _stringify(dell_pd_serial_walk),
            "vendor_dell_pd_revision":  _stringify(dell_pd_revision_walk),
            "vendor_dell_vd_name":      _stringify(dell_vd_name_walk),
            "vendor_dell_vd_state":     _stringify(dell_vd_state_walk),
            "vendor_dell_vd_size":      _stringify(dell_vd_size_walk),
            "vendor_dell_vd_layout":    _stringify(dell_vd_layout_walk),
            "vendor_dell_bios_version": _stringify(dell_bios_version_walk),
            "vendor_dell_bios_date":    _stringify(dell_bios_date_walk),
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
                "vendor_dell_fan_rows":  len(dell_fan_reading_walk or {}),
                "vendor_dell_temp_rows": len(dell_temp_reading_walk or {}),
                "vendor_dell_psu_rows":  len(dell_psu_status_walk or {}),
                "vendor_dell_volt_rows": len(dell_volt_reading_walk or {}),
                "vendor_dell_amp_rows":  len(dell_amp_reading_walk or {}),
                "vendor_dell_pd_rows":   len(dell_pd_name_walk or {}),
                "vendor_dell_vd_rows":   len(dell_vd_name_walk or {}),
                "vendor_dell_bios_rows": len(dell_bios_version_walk or {}),
            },
        }
    return out


def _summarise_entity_rows(descrs: dict, names: dict, serials: dict,
                           models: dict, firmwares: dict) -> list[dict]:
    """Per-physical-entry row summary from ENTITY-MIB walks.
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
