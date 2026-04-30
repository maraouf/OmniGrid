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
getCmd = bulkCmd = None  # type: ignore[assignment]
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
     ent_descr, ent_name, ent_serial, ent_model, ent_fw) = results

    # #681 — entity walks count toward the "any data" gate so a switch
    # that answers ONLY entPhysicalSerialNum (no sysDescr / no ifTable)
    # still passes the cool-down clear. ENTITY-MIB-only is a real
    # config — some agents are locked down to Entity-MIB only.
    if not (sys_get or cpu_walk or st_size or if_descr or ent_serial or ent_model):
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
