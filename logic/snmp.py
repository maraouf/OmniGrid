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
# SSH 401 cool-down (no auth challenge in SNMP) — we share the auth-
# failure cool-down knob anyway so operators have one tunable for all
# "host probably wedged, back off" decisions. Per-(host, port) key.
from logic.cooldown import Cooldown as _Cooldown
from logic import tuning as _tuning
_unreachable_cooldown = _Cooldown(
    seconds_fn=lambda: _tuning.tuning_int("tuning_auth_failure_cooldown_seconds")
)


# Standard OIDs we walk. Kept as constants so the extractor branches
# are obvious and a future MIB extension is one line.
_OID_SYS_DESCR     = "1.3.6.1.2.1.1.1.0"
_OID_SYS_UPTIME    = "1.3.6.1.2.1.1.3.0"
_OID_SYS_NAME      = "1.3.6.1.2.1.1.5.0"
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
# pysnmp 7.x reorganised the module hierarchy: the asyncio HLAPI symbols
# moved from `pysnmp.hlapi.asyncio` (5.x / 6.x path) to
# `pysnmp.hlapi.v3arch.asyncio` (7.x path). #344 originally pinned >=7.0.0
# but used the old import path, which made `has_snmp_support()` return
# False even on a correctly-pinned install. Fix (#642): try the modern
# 7.x path first, then fall back to the 5.x / 6.x path. Capture the
# actual ImportError text so a third-party packaging weirdness surfaces
# in the server logs instead of disappearing silently — the operator's
# first diagnostic now is `grep "[snmp] pysnmp import" <log>` which
# names the exact missing symbol.
_SNMP_IMPORT_ERROR = ""
try:
    try:
        # pysnmp 7.x — current modern path.
        from pysnmp.hlapi.v3arch.asyncio import (  # type: ignore
            SnmpEngine, CommunityData, UsmUserData,
            UdpTransportTarget, ContextData, ObjectType, ObjectIdentity,
            getCmd, bulkCmd,
            usmHMACSHAAuthProtocol, usmHMACSHA256AuthProtocol,
            usmAesCfb128Protocol,
            usmNoAuthProtocol, usmNoPrivProtocol,
        )
    except ImportError:
        # pysnmp 5.x / 6.x — legacy path.
        from pysnmp.hlapi.asyncio import (  # type: ignore
            SnmpEngine, CommunityData, UsmUserData,
            UdpTransportTarget, ContextData, ObjectType, ObjectIdentity,
            getCmd, bulkCmd,
            usmHMACSHAAuthProtocol, usmHMACSHA256AuthProtocol,
            usmAesCfb128Protocol,
            usmNoAuthProtocol, usmNoPrivProtocol,
        )
    _HAS_SNMP = True
except ImportError as _e:
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
    except Exception as e:
        print(f"[snmp] GET error against {target}: {e}")
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
    """
    if not _HAS_SNMP:
        return {}
    out: dict[str, object] = {}
    iterator = bulkCmd(
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
    except Exception as e:
        print(f"[snmp] WALK exception on {base_oid}: {e}")
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
    """Shape sysName / sysDescr / sysUpTime into host_* fields."""
    out: dict = {}
    name = _coerce_str(get_result.get(_OID_SYS_NAME))
    descr = _coerce_str(get_result.get(_OID_SYS_DESCR))
    up_ticks = _coerce_int(get_result.get(_OID_SYS_UPTIME))
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
) -> dict:
    """Compose every per-section extractor into one host_* dict.

    ``storage_walks`` is the named dict of the five storage sub-walks
    (``type``, ``desc``, ``unit``, ``size``, ``used``); ``iface_walks``
    is the six iface walks (``descr``, ``oper``, ``in_hc``, ``out_hc``,
    ``in_32``, ``out_32``). Splitting them this way lets a fixture in
    a future pytest suite feed each subsystem in isolation.

    ``active_sources`` is honoured to suppress fields a richer provider
    would emit better — same pattern as Webmin.
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
) -> dict:
    """Probe one SNMP-speaking host. See module docstring for the contract.

    Returns ``{"hosts": {host_key: stats}, "error": None}`` on success or
    ``{"hosts": {}, "error": "..."}`` on any failure. Never raises.

    Like Webmin, this is a per-host probe (no central hub). Each
    successful probe yields ONE entry keyed by the agent-reported
    ``sysName.0`` (falling back to the supplied host string when sysName
    isn't readable — common on appliances that disable SNMP system
    naming).
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

    engine = SnmpEngine()
    try:
        target = await UdpTransportTarget.create(
            (host_clean, port_int), timeout=timeout, retries=1,
        )
    except Exception as e:
        return {"hosts": {}, "error": f"snmp: transport setup failed: {e}"}

    # ----------------------------------------------------------------
    # Fan out the GET + walks in parallel. asyncio.gather keeps the
    # wall-clock close to the slowest individual SNMP RTT instead of
    # adding them up. pysnmp's session is reentrant — each `getCmd` /
    # `bulkCmd` call carries its own request ID.
    # ----------------------------------------------------------------
    try:
        sys_task = _snmp_get(engine, auth, target, [
            _OID_SYS_NAME, _OID_SYS_DESCR, _OID_SYS_UPTIME,
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

        results = await asyncio.gather(
            sys_task, cpu_task,
            st_type_task, st_desc_task, st_unit_task, st_size_task, st_used_task,
            if_descr_task, if_oper_task,
            if_hc_in_task, if_hc_out_task,
            if_in_task, if_out_task,
            return_exceptions=False,
        )
    except asyncio.TimeoutError:
        _arm_cooldown(host_clean, port_int)
        return {"hosts": {}, "error": f"snmp: timeout against {host_clean}:{port_int}"}
    except Exception as e:
        return {"hosts": {}, "error": f"snmp: probe failed: {e}"}

    (sys_get, cpu_walk,
     st_type, st_desc, st_unit, st_size, st_used,
     if_descr, if_oper,
     if_hc_in, if_hc_out, if_in, if_out) = results

    if not (sys_get or cpu_walk or st_size or if_descr):
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
    )

    host_key = stats.get("host_hostname") or host_clean
    stats["snmp_name"] = host_key
    print(f"[snmp] probe: host={host_clean!r} port={port_int} "
          f"version={version} key={host_key!r} "
          f"cpu%={stats.get('host_cpu_percent')} "
          f"mem_total={stats.get('host_mem_total')} "
          f"disk_total={stats.get('host_disk_total')} "
          f"ifaces={len(stats.get('network_ifaces') or [])}")

    return {
        "hosts": {host_key: stats} if host_key else {},
        "error": None,
    }


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
