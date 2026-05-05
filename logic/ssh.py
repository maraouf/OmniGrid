"""SSH console — admin-only, one-shot remote command runner.

MVP scope is a single fire-and-forget command per request. Interactive
terminals are explicitly out of scope for V1 (see CLAUDE.md "SSH runs are
admin-only, dry-run-by-default, audit-logged" bullet); this module makes
"run one command, show me stdout/stderr/exit" cheap and auditable without
opening a full PTY channel or multiplexed stream API.

Concrete use case that drove the design: edit a Beszel agent's env to set
NICS=eth0 and restart it without leaving the OmniGrid UI. All other
remote-exec workflows should fit the same "preview → confirm → run"
shape.

Why ``asyncssh`` and not ``subprocess`` + openssh:
  - Async-native, so it slots into the existing httpx / asyncio stack
    without a thread pool or blocking I/O.
  - First-class control of known-hosts, key material, ciphers, and the
    host-key check. ``ssh`` CLI would require temp files for the key +
    a ``UserKnownHostsFile`` override, and still leaves the cipher
    negotiation + TOFU confirmation up to the shipped ``ssh_config``.
  - Key fingerprints are reachable programmatically (``get_fingerprint``
    on the server host key) which the UI wants to display.

Credentials storage model:
  - **Global** defaults live in the ``settings`` table under
    ``ssh_default_*``. Secret fields (``ssh_default_private_key``,
    ``ssh_default_private_key_passphrase``) follow the suffix + ``_set``
    flag convention from CLAUDE.md — the browser only ever learns
    "is it set", never the material.
  - **Per-host** overrides live in ``hosts_config[].ssh`` as a JSON
    sub-dict with optional ``user`` / ``port`` / ``enabled`` keys
    (post-fix, opt-in semantics — `enabled=true` is the explicit gate;
    no flag at all means SSH is OFF for this host). V1
    intentionally keeps key material GLOBAL only — per-host user + port
    is enough for the current use case without adding a named-keys
    table. Operators who need multiple keys can revisit this when the
    actual need arises (see CLAUDE.md "solve when needed" ethos).

Safety rails:
  - Every write operation runs **admin-only** at the route layer.
  - Every run is **dry-run-first** by default. The UI always preflights
    with ``dry_run: true`` before executing so the operator can see the
    resolved connection (user@host:port, key fingerprint) + the exact
    command that would run. Only after an explicit confirm does the
    second call with ``dry_run: false`` actually execute.
  - Commands matching ``ssh_destructive_patterns`` (``rm `` / ``mkfs`` /
    ``dd `` / ``>/`` / ``systemctl stop`` / ``reboot`` / ``poweroff`` by
    default) require a typed-hostname confirmation on the client side.
    The list is editable from Admin → SSH.
  - Commands are **length-capped** at 4 KiB to block API callers from
    stuffing a megabyte of shell into a JSON payload.
  - Per-``(host_id, user)`` cool-down mirrors ``logic/webmin.py`` —
    five minutes after any connect-time auth failure, keyed by the
    resolved identity. Stops a broken key + scheduled loop from
    brute-force-signing the target over and over.
  - Every run persists an **audit row** in the ``history`` table via
    :func:`main._ssh_write_audit_row` with ``op_type='ssh_run'`` plus
    the actor / host / command / exit code.

Host-key handling:
  - When ``ssh_default_known_hosts`` is populated, asyncssh's
    ``known_hosts`` parameter is fed the blob directly and any unknown
    host key fails the connection. This is the recommended production
    mode.
  - When blank, the module falls into **TOFU** mode: the first
    connection succeeds but the UI surfaces the returned key fingerprint
    so the operator can confirm it out-of-band. Subsequent connections
    do not re-check — adding a persisted known_hosts file lifecycle is
    a V2 nice-to-have. An operator who wants strict first-use control
    should paste the expected line into ``ssh_default_known_hosts``
    BEFORE the first connect.
"""
from __future__ import annotations

import asyncio
import hashlib
import io
import json
import re
import time
from typing import Any, Optional

import asyncssh


# ---------------------------------------------------------------------------
# Config + defaults
# ---------------------------------------------------------------------------
# Hard ceiling for a single command payload. 4 KiB is generous for one
# shell line (sed/sudo/systemctl calls rarely exceed a few hundred bytes)
# and small enough to reject obvious shell-heredoc dumps at the gate.
MAX_COMMAND_LEN = 4096

# Default destructive-pattern list. Matched case-insensitively against
# the full command string. The operator can override via the
# ``ssh_destructive_patterns`` setting — one pattern per line / CSV.
# Compiled lazily per-call so setting edits take effect immediately
# without a server restart.
DEFAULT_DESTRUCTIVE_PATTERNS = (
    r"\brm\s",             # rm / rm -rf / etc.
    r"\bmkfs\b",           # any mkfs.* invocation
    r"\bdd\s",             # dd if=.. of=..
    r">\s*/",              # truncating redirect to an absolute path
    r"\bsystemctl\s+stop\b",
    r"\breboot\b",
    r"\bpoweroff\b",
    r"\bshutdown\b",
)

# Auth cool-down — keyed by (host_id, user). Mirrors logic/webmin.py.
# Centralised in `logic/cooldown.py` per CONS-004. duration is
# now operator-tunable via `tuning_auth_failure_cooldown_seconds`,
# shared with Webmin so a single Save propagates to both consumers.
from logic.cooldown import Cooldown
from logic import tuning as _tuning
_auth_cooldown_timer = Cooldown(
    seconds_fn=lambda: _tuning.tuning_int("tuning_auth_failure_cooldown_seconds")
)


def _in_cooldown(host_id: str, user: str) -> Optional[float]:
    return _auth_cooldown_timer.remaining(host_id or "", user or "")


def _arm_cooldown(host_id: str, user: str) -> None:
    _auth_cooldown_timer.arm(host_id or "", user or "")


# ---------------------------------------------------------------------------
# Settings helpers — thin wrappers over logic.db.get_setting so the
# module stays self-contained and testable without a running FastAPI app.
# ---------------------------------------------------------------------------
def get_global_ssh_settings() -> dict:
    """Read every ``ssh_default_*`` key into one dict.

    Secret values (the private key + passphrase) are returned in the
    clear — this helper is called from the runner, not the /api/settings
    shaper. The API layer redacts per CLAUDE.md's ``_set`` flag pattern.
    """
    from logic.db import get_setting
    return {
        "user":         (get_setting("ssh_default_user", "") or "").strip(),
        "port":         int(get_setting("ssh_default_port", "22") or "22"),
        "private_key":  get_setting("ssh_default_private_key", "") or "",
        "passphrase":   get_setting("ssh_default_private_key_passphrase", "") or "",
        # Password auth fallback — used when private_key is blank, or
        # when the per-host override specifies a password. Returned in
        # the clear here (consumed by run_command); the /api/settings
        # shaper redacts via the ``password_set`` flag pattern.
        "password":     get_setting("ssh_default_password", "") or "",
        # FQDN suffix appended to bare hostnames during resolve.
        # Normalised on save to include the leading dot.
        "fqdn_suffix":  get_setting("ssh_fqdn_suffix", "") or "",
        "known_hosts":  get_setting("ssh_default_known_hosts", "") or "",
        "destructive_patterns": (
            get_setting("ssh_destructive_patterns", "") or ""
        ).strip(),
    }


def get_destructive_patterns() -> tuple[str, ...]:
    """Return the active destructive-command regexes.

    Operator's ``ssh_destructive_patterns`` setting wins when non-empty;
    otherwise the hard-coded defaults. One pattern per line or CSV.
    Patterns are raw regex — the UI editor shows them verbatim.
    """
    s = get_global_ssh_settings()["destructive_patterns"]
    if not s:
        return DEFAULT_DESTRUCTIVE_PATTERNS
    # Accept newlines or commas as the separator so the textarea UX
    # works either way.
    parts = [p.strip() for p in re.split(r"[\n,]+", s) if p.strip()]
    return tuple(parts) if parts else DEFAULT_DESTRUCTIVE_PATTERNS


def command_is_destructive(command: str) -> list[str]:
    """Return the list of destructive-pattern matches in ``command``.

    Empty list = safe by this heuristic. Multiple hits are preserved so
    the UI can show every reason the command tripped the gate.
    """
    if not command:
        return []
    hits: list[str] = []
    for pat in get_destructive_patterns():
        try:
            if re.search(pat, command, re.IGNORECASE):
                hits.append(pat)
        except re.error:
            # Malformed operator-supplied regex — treat as a no-op so
            # one bad entry doesn't brick the whole gate. The Settings
            # UI should validate up-front, but never trust it.
            continue
    return hits


# ---------------------------------------------------------------------------
# Host resolution + per-host settings
# ---------------------------------------------------------------------------
def _find_host_record(host_id: str, hosts_config: list[dict]) -> Optional[dict]:
    if not host_id:
        return None
    hid = host_id.strip()
    for h in hosts_config or []:
        if (h or {}).get("id") == hid:
            return h
    return None


# Module-level dedupe for the verbose resolve / status diagnostics.
# `resolve_ssh_params` and `ssh_status` are called on every drawer
# status poll — one click into the Hosts view can fire 30+ resolutions,
# each emitting 9+ trace lines. Without dedupe, Admin → Logs fills with
# the same trace over and over. We stash a signature of the inputs per
# host_id; repeat calls with IDENTICAL inputs print nothing (the result
# hasn't changed, so the trace would be identical). Any input change
# (hosts_config edit, group SSH update, global default flip) mints a
# new signature and the full trace re-emits so operators can diagnose
# the new state.
_resolve_input_sig: dict[str, str] = {}
_status_output_sig: dict[str, str] = {}


def _compute_resolve_signature(
    record: Optional[dict],
    g_settings: dict,
    host_groups_raw: str,
) -> str:
    """Stable one-line signature of the inputs that feed
    ``resolve_ssh_params``. Any change ⇒ different signature ⇒
    the resolve trace re-emits on the next call.
    """
    import hashlib, json
    m = hashlib.md5()
    # `json.dumps(..., sort_keys=True, default=str)` produces a
    # stable serialisation regardless of dict key insertion order
    # (`repr(dict)` was sensitive to that — a hosts_config copy
    # with re-inserted keys would blow the cache and re-emit the
    # full verbose trace). BUG-007 in the code review.
    m.update(json.dumps(record, sort_keys=True, default=str).encode("utf-8", "ignore"))
    # Only the fields that actually influence resolution — drops
    # known_hosts + fingerprint etc. which don't change auth path.
    relevant = (
        g_settings.get("user"),
        g_settings.get("port"),
        bool(g_settings.get("private_key")),
        bool(g_settings.get("password")),
        g_settings.get("fqdn_suffix"),
    )
    m.update(json.dumps(relevant, default=str).encode("utf-8", "ignore"))
    m.update(host_groups_raw.encode("utf-8", "ignore"))
    return m.hexdigest()


def _groups_for_host(record: Optional[dict], *, verbose: bool = True) -> tuple[Optional[dict], Optional[dict]]:
    """Match a host_config record against the persisted ``host_groups``
    setting and return ``(main_group, sub_group)`` — either may be
    ``None`` when no match. Sub-group is the most-specific match
    (host's custom_number sits inside the sub-group's range AND that
    sub-group is a child of the matched main_group).

    Loaded directly from settings so this works in contexts that
    don't already have the parsed groups list (the SSH runner is one
    such caller). Tolerant — malformed JSON / missing setting return
    ``(None, None)``.
    """
    def _log(msg: str) -> None:
        if verbose:
            print(msg)
    rid = (record or {}).get("id") if isinstance(record, dict) else None
    if record is None:
        _log(f"[ssh] _groups_for_host: record is None")
        return (None, None)
    cn = record.get("custom_number")
    if cn is None:
        _log(f"[ssh] _groups_for_host id={rid!r}: custom_number is None — no group match possible")
        return (None, None)
    try:
        cn_int = int(cn)
    except (TypeError, ValueError):
        _log(f"[ssh] _groups_for_host id={rid!r}: custom_number={cn!r} not int-parseable")
        return (None, None)
    from logic.db import get_setting
    raw = get_setting("host_groups", "") or ""
    if not raw.strip():
        _log(f"[ssh] _groups_for_host id={rid!r} cn={cn_int}: host_groups setting is empty — no groups defined")
        return (None, None)
    try:
        groups = json.loads(raw)
    except (TypeError, ValueError) as e:
        _log(f"[ssh] _groups_for_host id={rid!r} cn={cn_int}: host_groups JSON parse failed: {e}")
        return (None, None)
    if not isinstance(groups, list):
        _log(f"[ssh] _groups_for_host id={rid!r} cn={cn_int}: host_groups is not a list (got {type(groups).__name__})")
        return (None, None)
    _log(f"[ssh] _groups_for_host id={rid!r} cn={cn_int}: scanning {len(groups)} group(s)")

    main_group: Optional[dict] = None
    for g in groups:
        if not isinstance(g, dict):
            continue
        if g.get("parent_name"):
            continue  # skip sub-groups in pass 1
        try:
            rs = int(g.get("range_start"))
            re_ = int(g.get("range_end"))
        except (TypeError, ValueError):
            continue
        if rs <= cn_int <= re_:
            main_group = g
            _log(f"[ssh] _groups_for_host id={rid!r} cn={cn_int}: matched main_group "
                 f"name={g.get('name')!r} range={rs}-{re_} "
                 f"ssh_keys={list((g.get('ssh') or {}).keys())}")
            break
    if main_group is None:
        _log(f"[ssh] _groups_for_host id={rid!r} cn={cn_int}: NO main_group whose range covers cn — "
             f"check Admin → Host Groups (top-level group's range_start..range_end must include {cn_int})")
    sub_group: Optional[dict] = None
    if main_group is not None:
        for g in groups:
            if not isinstance(g, dict):
                continue
            if g.get("parent_name") != main_group.get("name"):
                continue
            try:
                rs = int(g.get("range_start"))
                re_ = int(g.get("range_end"))
            except (TypeError, ValueError):
                continue
            if rs <= cn_int <= re_:
                sub_group = g
                _log(f"[ssh] _groups_for_host id={rid!r} cn={cn_int}: matched sub_group "
                     f"name={g.get('name')!r} range={rs}-{re_} "
                     f"ssh_keys={list((g.get('ssh') or {}).keys())}")
                break
        if sub_group is None:
            _log(f"[ssh] _groups_for_host id={rid!r} cn={cn_int}: no sub_group matched (main only)")
    return (main_group, sub_group)


def _asset_fqdn_for_record(record: Optional[dict]) -> str:
    """Look up the asset-inventory row matching a host's custom_number
    and return its LAST hostname (most-specific FQDN by upstream
    convention). Returns "" when no match / no hostnames.

    Used by `resolve_ssh_params` as a fallback target when the host
    record's id is bare (no dot) and no `ssh_fqdn_suffix` is set —
    saves the operator from having to manually enter the FQDN in
    each per-host SSH override when the asset already knows it.
    """
    if not isinstance(record, dict):
        return ""
    cn = record.get("custom_number")
    if cn is None:
        return ""
    try:
        cn_int = int(cn)
    except (TypeError, ValueError):
        return ""
    try:
        from logic import asset_inventory as _ai
        cache = _ai.load_cache()
        idx = _ai.index_by_custom_number(cache.get("assets") or [])
        raw = idx.get(cn_int)
        if not isinstance(raw, dict):
            return ""
        hostname_str = str(raw.get("Hostname") or raw.get("hostname") or "").strip()
        if not hostname_str:
            return ""
        # Pick the LAST entry — by <asset-api-host> convention the CSV is
        # ordered least-specific → most-specific (raw IP first,
        # canonical FQDN last). Skip any entry that looks like an
        # IP address — those aren't valid SSH targets.
        parts = [p.strip() for p in hostname_str.split(",") if p.strip()]
        # Walk from the end backwards, pick the first non-IP entry.
        ip_pat = re.compile(r"^\d+\.\d+\.\d+\.\d+$")
        for p in reversed(parts):
            if ip_pat.match(p):
                continue
            return p
        return ""
    except Exception:
        return ""


def _group_ssh(group: Optional[dict]) -> dict:
    """Extract a {user, port, password} dict from a group's ``ssh``
    sub-block. Returns empty dict for missing/malformed inputs so
    callers can iterate uniformly across all four credential layers.
    """
    if not isinstance(group, dict):
        return {}
    s = group.get("ssh")
    return s if isinstance(s, dict) else {}


def resolve_ssh_params(host_id: str, hosts_config: list[dict]) -> dict:
    """Merge global defaults + group + per-host overrides into one
    connect spec.

    Credentials priority ladder (most specific wins):
      1. per-host (``hosts_config[].ssh``)
      2. per-sub-group (``host_groups[].ssh`` on a group with a parent)
      3. per-main-group (``host_groups[].ssh`` on a top-level group)
      4. global default (``ssh_default_*`` settings)

    Returns ``{host, user, port, disabled, key_set, password_set,
    known_hosts_set, key_fingerprint, password_source, error}``.
    ``password_source`` is one of ``"per_host"`` / ``"sub_group"`` /
    ``"main_group"`` / ``"global"`` / ``""`` — used by ``run_command``
    to pick the matching password value, and surfaced to the UI for
    operator transparency.
    """
    record = _find_host_record(host_id, hosts_config)
    g = get_global_ssh_settings()
    # Dedupe the verbose trace — compute a signature from the inputs
    # that actually influence resolution. Matching signature means the
    # result hasn't changed since the last call for this host_id, so
    # re-printing the 9+ trace lines would be pure noise. First call
    # ever (or first after ANY input change) emits the full trace.
    from logic.db import get_setting as _get_setting
    # Per-service master switch. When SSH is globally disabled
    # in Admin → SSH, force every resolution to "disabled" with a
    # clear error. The stored creds + per-host overrides STAY in the
    # settings table — flipping the switch back on resumes service
    # without re-typing. Note this short-circuits BEFORE the heavier
    # per-host walk, so the cost stays at one DB read.
    if (_get_setting("ssh_enabled", "true") or "true").lower() != "true":
        return {
            "host":              "",
            "user":              g["user"],
            "port":              g["port"],
            "disabled":          True,
            "key_set":           bool(g["private_key"]),
            "password_set":      bool(g["password"]),
            "known_hosts_set":   bool(g["known_hosts"]),
            "key_fingerprint":   "",
            "password_source":   "",
            "error":             "SSH disabled in Admin → SSH (master switch off)",
        }
    _groups_raw = _get_setting("host_groups", "") or ""
    _new_sig = _compute_resolve_signature(record, g, _groups_raw)
    verbose = _resolve_input_sig.get(host_id) != _new_sig
    _resolve_input_sig[host_id] = _new_sig

    def _log(msg: str) -> None:
        if verbose:
            print(msg)

    _log(f"[ssh] resolve_ssh_params start id={host_id!r} record_found={record is not None} "
         f"global_user={g.get('user')!r} global_port={g.get('port')} "
         f"global_key_set={bool(g.get('private_key'))} global_password_set={bool(g.get('password'))} "
         f"global_fqdn_suffix={g.get('fqdn_suffix')!r}")
    resolved: dict[str, Any] = {
        "host":              "",
        "user":              g["user"],
        "port":              g["port"],
        "disabled":          False,
        "key_set":           bool(g["private_key"]),
        "password_set":      bool(g["password"]),
        "known_hosts_set":   bool(g["known_hosts"]),
        "key_fingerprint":   _key_fingerprint(g["private_key"], g["passphrase"]),
        "password_source":   "global" if g["password"] else "",
        "error":             None,
    }
    if record is None:
        resolved["error"] = f"unknown host_id: {host_id!r}"
        _log(f"[ssh] resolve_ssh_params id={host_id!r}: NO matching hosts_config record — "
             f"the host must exist in Admin → Hosts before SSH can resolve")
        return resolved

    main_group, sub_group = _groups_for_host(record, verbose=verbose)
    # Diagnostic — operators reported "Not configured" even after
    # saving group SSH creds. Logs which groups matched and what SSH
    # fields were on each, so root cause (custom_number missing /
    # group range mismatch / password not persisted) is visible in
    # Admin → Logs without further code changes.
    _log(
        f"[ssh] groups_for_host id={host_id!r} cn={record.get('custom_number')!r} "
        f"main={(main_group or {}).get('name')!r} "
        f"main_ssh_keys={list((main_group or {}).get('ssh', {}).keys()) if isinstance((main_group or {}).get('ssh'), dict) else None} "
        f"sub={(sub_group or {}).get('name')!r} "
        f"sub_ssh_keys={list((sub_group or {}).get('ssh', {}).keys()) if isinstance((sub_group or {}).get('ssh'), dict) else None}"
    )
    # Walk layers least-specific → most-specific so later overrides win.
    layer_specs = [
        ("main_group", _group_ssh(main_group)),
        ("sub_group",  _group_ssh(sub_group)),
    ]
    for source_name, s in layer_specs:
        if not s:
            _log(f"[ssh] layer {source_name}: empty (no SSH overrides on this group)")
            continue
        applied = []
        u = str(s.get("user") or "").strip()
        if u:
            resolved["user"] = u
            applied.append(f"user={u!r}")
        if s.get("port") not in (None, "", 0):
            try:
                pi = int(s["port"])
                if 1 <= pi <= 65535:
                    resolved["port"] = pi
                    applied.append(f"port={pi}")
            except (TypeError, ValueError):
                pass
        if (s.get("password") or "").strip():
            resolved["password_set"] = True
            resolved["password_source"] = source_name
            applied.append("password=<set>")
        _log(f"[ssh] layer {source_name}: applied {applied or '<nothing — all fields blank>'}")

    per_host = (record.get("ssh") or {}) if isinstance(record.get("ssh"), dict) else {}
    # Target hostname resolution priority:
    #   1. per-host ssh.host override (operator pasted the full FQDN)
    #   2. per-host ssh.fqdn (alias for 1)
    #   3. record.id + ssh_fqdn_suffix (global suffix; ".example.com" →
    #      "webserver" becomes "webserver.example.com")
    #   4. record.id as-is
    # We only append the suffix when the id has no dot — ids that
    # already contain a dot are treated as already-fully-qualified.
    ssh_host_override = (per_host.get("host") or per_host.get("fqdn") or "").strip()
    base_id = record.get("id") or ""
    host_resolution_path: str
    if ssh_host_override:
        resolved["host"] = ssh_host_override
        host_resolution_path = "per_host_override"
    else:
        suffix = (g.get("fqdn_suffix") or "").strip()
        if base_id and "." not in base_id and suffix:
            resolved["host"] = base_id + (suffix if suffix.startswith(".") else "." + suffix)
            host_resolution_path = f"id+suffix({suffix!r})"
        else:
            # Bare id with no fqdn_suffix — try the asset inventory's
            # canonical FQDN before falling back to the bare id.
            asset_fqdn = _asset_fqdn_for_record(record)
            if asset_fqdn and "." not in base_id:
                resolved["host"] = asset_fqdn
                host_resolution_path = f"asset_fqdn({asset_fqdn!r})"
            else:
                resolved["host"] = base_id
                host_resolution_path = "id_as_is"
    _log(f"[ssh] host_resolution id={host_id!r}: target={resolved['host']!r} via {host_resolution_path}")
    per_host_applied = []
    u_override = (per_host.get("user") or "").strip()
    if u_override:
        resolved["user"] = u_override
        per_host_applied.append(f"user={u_override!r}")
    # Per-host password override trumps every layer above.
    if (per_host.get("password") or "").strip():
        resolved["password_set"] = True
        resolved["password_source"] = "per_host"
        resolved["_per_host_password"] = True
        per_host_applied.append("password=<set>")
    try:
        if per_host.get("port") not in (None, "", 0):
            resolved["port"] = int(per_host["port"])
            per_host_applied.append(f"port={resolved['port']}")
    except (TypeError, ValueError):
        pass
    # Per-host SSH is OPT-IN: the operator
    # must explicitly tick "Enable SSH for this host" in Admin → Hosts
    # for the row to inherit the global Admin → SSH master switch.
    # Hosts without the flag stay disabled even when SSH is globally on.
    # Computes `disabled` (the legacy output key consumed by every
    # caller and downstream UI) from the new positive flag so the
    # function's return shape stays stable.
    per_host_enabled = bool(per_host.get("enabled"))
    resolved["disabled"] = not per_host_enabled
    if per_host_enabled:
        per_host_applied.append("enabled=True")
    else:
        per_host_applied.append("enabled=False (default — host opt-in via Admin → Hosts)")
    _log(f"[ssh] layer per_host: applied {per_host_applied or '<nothing — no per-host overrides>'}")
    _log(f"[ssh] resolve_ssh_params done id={host_id!r}: "
         f"host={resolved['host']!r} user={resolved['user']!r} port={resolved['port']} "
         f"key_set={resolved['key_set']} password_set={resolved['password_set']} "
         f"password_source={resolved.get('password_source')!r} "
         f"disabled={resolved['disabled']}")
    return resolved


def _key_fingerprint(private_key_pem: str, passphrase: str) -> str:
    """Return the SHA-256 fingerprint (last 16 hex chars) of the key's
    PUBLIC half, or '' when no key is configured / parsing fails.

    Cheap, cached per-process would be even cheaper; skipped for V1 —
    operators don't test-connect thousands of times per minute.
    """
    if not private_key_pem:
        return ""
    try:
        key = asyncssh.import_private_key(
            private_key_pem,
            passphrase=passphrase or None,
        )
        # AsyncSSH public-key objects expose ``get_fingerprint()`` in
        # openssh format (``SHA256:abc…``). Trim to 16 chars after the
        # prefix so the UI doesn't have to handle a long string.
        fp = key.get_fingerprint("sha256") or ""
        if ":" in fp:
            fp = fp.split(":", 1)[1]
        return fp[:16]
    except Exception as e:
        return f"parse-error: {type(e).__name__}"


# ---------------------------------------------------------------------------
# Core runner
# ---------------------------------------------------------------------------
async def run_command(
    host_id: str,
    command: str,
    hosts_config: list[dict],
    timeout: float = 30.0,
    dry_run: bool = False,
) -> dict:
    """Execute ``command`` over SSH on the host resolved from ``host_id``.

    Returns a dict with ``ok``, ``exit_code``, ``stdout``, ``stderr``,
    ``duration_ms``, ``dry_run``, ``resolved``. On any error (bad
    credentials, unreachable host, timeout) returns ``ok: False`` with
    an ``error`` field populated — the function never raises so the
    caller can turn the result straight into an audit row + JSON
    response without a try/except wrapper at every call site.

    ``dry_run=True`` validates the resolved connection spec without
    actually executing. It exercises enough of the setup to catch the
    common configuration problems (missing key, host disabled, bad
    port, auth cool-down) while guaranteeing nothing runs on the box.
    """
    started = time.time()
    base_result: dict[str, Any] = {
        "ok":          False,
        "exit_code":   None,
        "stdout":      "",
        "stderr":      "",
        "duration_ms": 0,
        "dry_run":     bool(dry_run),
        "resolved":    {},
        "error":       None,
    }
    if not isinstance(command, str):
        base_result["error"] = "command must be a string"
        return base_result
    if len(command) > MAX_COMMAND_LEN:
        base_result["error"] = (
            f"command too long: {len(command)} bytes (max {MAX_COMMAND_LEN})"
        )
        return base_result

    resolved = resolve_ssh_params(host_id, hosts_config)
    base_result["resolved"] = resolved
    if resolved.get("error"):
        base_result["error"] = resolved["error"]
        return base_result
    if resolved.get("disabled"):
        base_result["error"] = "SSH is disabled for this host (per-host override)"
        return base_result
    if not resolved.get("host") or not resolved.get("user"):
        base_result["error"] = "SSH not configured (host + global user both required)"
        return base_result
    # Either a private key OR a password must be available — auth
    # proceeds via whichever is set. Per-host password overrides win
    # over the global one; global key wins over global password.
    if not resolved.get("key_set") and not resolved.get("password_set"):
        base_result["error"] = (
            "No SSH credentials configured — set a private key OR a "
            "password in Admin → SSH (or set ssh.password on this host "
            "row in Admin → Hosts)."
        )
        return base_result

    cd = _in_cooldown(host_id, resolved["user"])
    if cd is not None:
        base_result["error"] = (
            f"auth cool-down ({int(cd)}s remaining) — fix credentials "
            f"and wait before retrying"
        )
        return base_result

    if dry_run:
        # Compute and report as though we'd run the command, but
        # without opening a channel. The resolved block tells the UI
        # exactly where we would have connected.
        base_result["ok"] = True
        base_result["duration_ms"] = int((time.time() - started) * 1000)
        base_result["stdout"] = "(dry-run — no command executed)"
        return base_result

    g = get_global_ssh_settings()
    # Parse the private key ONLY if one is configured. With password-
    # only auth we skip this entirely so a bare-install operator who
    # never pasted a key isn't blocked from running commands.
    client_keys_arg: Any = None
    if g["private_key"]:
        try:
            client_keys_arg = [asyncssh.import_private_key(
                g["private_key"], passphrase=g["passphrase"] or None,
            )]
        except Exception as e:
            # If the key is bad but we also have a password (any
            # layer of the priority ladder), fall through silently
            # to password auth. Otherwise fail.
            if not resolved.get("password_set"):
                base_result["error"] = f"bad SSH key: {type(e).__name__}: {e}"
                return base_result
            client_keys_arg = None

    # Resolve password using the same priority ladder
    # `resolve_ssh_params` recorded via `password_source`. We re-read
    # the source's actual password value here (not stashed on
    # resolved[] — keeps secrets out of audit logs) and fall through
    # to global on a miss. BUG-009 fix : if the recorded source
    # has no password (operator deleted the field but didn't flip the
    # classification), downgrade `password_source` to `"global"` in
    # both `resolved` and `base_result` so the audit row reflects what
    # actually authenticated, not the stale classification.
    ssh_password: Optional[str] = None
    record = _find_host_record(host_id, hosts_config)
    src = resolved.get("password_source") or ""
    if src == "per_host":
        if record and isinstance(record.get("ssh"), dict):
            ssh_password = (record["ssh"].get("password") or "").strip() or None
    elif src in ("sub_group", "main_group"):
        main_group, sub_group = _groups_for_host(record)
        target = sub_group if src == "sub_group" else main_group
        if target and isinstance(target.get("ssh"), dict):
            ssh_password = (target["ssh"].get("password") or "").strip() or None
    if ssh_password is None and g["password"]:
        ssh_password = g["password"]
        # Source classification was per_host / sub_group / main_group but
        # that record's password was empty — the actual auth credential
        # came from the global password. Stamp the audit accordingly so
        # incident review can trust the recorded source.
        if src and src != "global":
            print(f"[ssh] {host_id!r} password_source downgraded "
                  f"from {src!r} to 'global' (no password at recorded source)")
            resolved["password_source"] = "global"
            if isinstance(base_result, dict) and "resolved" in base_result and isinstance(base_result["resolved"], dict):
                base_result["resolved"]["password_source"] = "global"

    # Known-hosts handling — see module docstring's "Host-key handling"
    # section. asyncssh accepts:
    #   - a known_hosts file path
    #   - a tuple of (known_hosts, hashed_known_hosts) buffers
    #   - ``None`` to skip verification (TOFU-ish for V1)
    known_hosts_arg: Any = None
    if g["known_hosts"]:
        try:
            known_hosts_arg = asyncssh.import_known_hosts(g["known_hosts"])
        except Exception as e:
            base_result["error"] = (
                f"bad known_hosts blob: {type(e).__name__}: {e}"
            )
            return base_result

    # Auth preference: key first when both are set; else whichever is
    # available. asyncssh picks the first viable method from the list.
    preferred: list[str] = []
    if client_keys_arg:
        preferred.append("publickey")
    if ssh_password:
        preferred.append("password")
    if not preferred:
        base_result["error"] = "No SSH credentials available at connect time"
        return base_result

    # Entry log — one line at the start of every run so operators can
    # correlate "clicked Run" with "saw output" even when exceptions
    # happen BEFORE the result diagnostic lands. Command is truncated
    # via `sanitize_command_for_audit` so secrets in flags don't land
    # in the log; host/user/port/auth-methods plus dry_run flag are
    # always in the clear.
    sanitized = sanitize_command_for_audit(command)
    print(
        f"[ssh] run START host_id={host_id!r} "
        f"target={resolved.get('user')}@{resolved.get('host')}:{resolved.get('port')} "
        f"preferred_auth={preferred!r} key_set={resolved.get('key_set')} "
        f"password_set={resolved.get('password_set')} dry_run={bool(dry_run)} "
        f"cmd={sanitized[:200]!r}"
    )
    try:
        conn_ctx = asyncssh.connect(
            host=resolved["host"],
            port=resolved["port"],
            username=resolved["user"],
            client_keys=client_keys_arg,
            known_hosts=known_hosts_arg,
            # Don't let agent forwarding surprise the operator — explicit
            # "only the key we just loaded" is the least-surprising model.
            agent_path=None,
            password=ssh_password,
            preferred_auth=",".join(preferred),
            connect_timeout=max(5.0, min(timeout, 30.0)),
            login_timeout=max(5.0, min(timeout, 30.0)),
        )
        async with asyncio.timeout(timeout) if hasattr(asyncio, "timeout") else _noop_timeout(timeout):
            async with conn_ctx as conn:
                # Pull the server host-key fingerprint into the result
                # so the UI can display what we trusted (especially
                # important in the no-known-hosts TOFU path).
                try:
                    server_key = conn.get_server_host_key()
                    fp = server_key.get_fingerprint("sha256") if server_key else ""
                    if fp and ":" in fp:
                        fp = fp.split(":", 1)[1]
                    resolved["server_key_fingerprint"] = fp[:16]
                except Exception:
                    resolved["server_key_fingerprint"] = ""
                # `request_pty='force'` allocates a pseudo-TTY on the
                # remote so interactive-ish tools like sudo can prompt
                # / detect a terminal and behave the way they do over
                # an interactive login. Without a PTY, sudo on some
                # configs silently fails with exit=0 — `tee` in a
                # piped chain echoes its stdin to stdout but writes
                # nothing to disk, which looks like "command ran but
                # didn't apply". With a PTY sudo either runs (NOPASSWD
                # or via cached creds) or fails loudly with "a
                # password is required" on stderr.
                proc = await conn.run(command, check=False, request_pty="force")
                base_result["ok"] = True
                base_result["exit_code"] = proc.exit_status
                base_result["stdout"] = (proc.stdout or "")[: 256 * 1024]
                base_result["stderr"] = (proc.stderr or "")[: 256 * 1024]
                # Verbose diagnostic so Admin → Logs shows exactly
                # what came back for every run. Includes stdout AND
                # stderr previews up to 400 chars each so the usual
                # "sudo: a password is required" / "command not found"
                # / "permission denied" lines are visible inline.
                stdout_preview = (proc.stdout or "")[:400].replace("\n", " | ")
                stderr_preview = (proc.stderr or "")[:400].replace("\n", " | ")
                print(
                    f"[ssh] run DONE host={resolved.get('host')!r} "
                    f"user={resolved.get('user')!r} "
                    f"exit={proc.exit_status} "
                    f"duration_ms={int((time.time() - started) * 1000)} "
                    f"len_out={len(proc.stdout or '')} "
                    f"len_err={len(proc.stderr or '')}"
                )
                if stdout_preview:
                    print(f"[ssh] run stdout: {stdout_preview}")
                if stderr_preview:
                    print(f"[ssh] run stderr: {stderr_preview}")
                # Non-zero exit with empty stderr is a classic silent-
                # sudo signature; flag it so the operator notices even
                # when the UI shows "ok" (exit_code surfaced separately).
                if proc.exit_status and proc.exit_status != 0 and not proc.stderr:
                    print(
                        f"[ssh] run WARN exit={proc.exit_status} but stderr was empty — "
                        f"possible silent-sudo / permission failure"
                    )
    except asyncssh.PermissionDenied as e:
        _arm_cooldown(host_id, resolved["user"])
        base_result["error"] = f"permission denied: {e} — cool-down armed"
        print(f"[ssh] run ERROR PermissionDenied host={resolved.get('host')!r}: {e} — cool-down armed")
    except asyncssh.HostKeyNotVerifiable as e:
        base_result["error"] = (
            f"host key not trusted: {e}. Paste the expected line into "
            f"Admin → SSH → Known hosts, or clear that field to accept "
            f"on first use (TOFU)."
        )
        print(f"[ssh] run ERROR HostKeyNotVerifiable host={resolved.get('host')!r}: {e}")
    except (asyncio.TimeoutError, TimeoutError):
        base_result["error"] = f"timeout after {timeout:.1f}s"
        print(f"[ssh] run ERROR timeout host={resolved.get('host')!r} after {timeout:.1f}s")
    except (OSError, asyncssh.DisconnectError, asyncssh.Error) as e:
        # Classify into the shared error catalog so the frontend can
        # localise it + recognise unreachable hosts distinctly from
        # generic network errors. OSError 113 (EHOSTUNREACH), 101
        # (ENETUNREACH), 111 (ECONNREFUSED) etc. all route through
        # logic.errors.classify_exception → specific OG#### code.
        from logic import errors as _err
        og = _err.classify_exception(e)
        base_result["error"] = og.message
        base_result["error_code"] = og.code
        base_result["error_params"] = og.params
        print(f"[ssh] run ERROR {type(e).__name__} ({og.code}) "
              f"host={resolved.get('host')!r}: {e}")
    except Exception as e:
        base_result["error"] = f"unexpected: {type(e).__name__}: {e}"
        print(f"[ssh] run ERROR unexpected {type(e).__name__} host={resolved.get('host')!r}: {e}")

    base_result["duration_ms"] = int((time.time() - started) * 1000)
    return base_result


class _noop_timeout:
    """Fallback for Python 3.10 which lacks ``asyncio.timeout``.

    We never actually hit this branch on 3.11+, but keeping the
    fallback keeps the runner importable in dev venvs that still run
    3.10. Real enforcement happens via asyncssh's own
    ``connect_timeout`` / ``login_timeout`` in that case.
    """
    def __init__(self, _seconds: float):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False


async def test_connection(host_id: str, hosts_config: list[dict]) -> dict:
    """Short-lived ``whoami`` probe. Thin wrapper around run_command()
    so the UI's Test button shares every safety rail (cool-down, key
    resolution, known-hosts handling) with the real runner.
    """
    r = await run_command(
        host_id, "whoami", hosts_config, timeout=10.0, dry_run=False,
    )
    return r


def ssh_status(host_id: str, hosts_config: list[dict]) -> dict:
    """Lightweight "what would happen if I tried to connect" probe.

    Returns the resolved params + flags the UI needs to render the
    drawer card WITHOUT initiating a TCP connection. Safe to call on
    every drawer open.
    """
    resolved = resolve_ssh_params(host_id, hosts_config)
    # A host is "configured" when we have the bare minimum to attempt a
    # connection: host + user + EITHER a key OR a password. Password-
    # only auth is a valid path (some operators prefer it for ad-hoc
    # targets that don't yet have an OmniGrid public key pushed).
    configured = bool(
        resolved.get("host")
        and resolved.get("user")
        and (resolved.get("key_set") or resolved.get("password_set"))
        and not resolved.get("disabled")
        and not resolved.get("error")
    )
    # Diagnostic print so Admin → Logs shows exactly WHY a host reads
    # as "Not configured" — the status pill doesn't distinguish
    # between "host id unknown", "user blank", "no auth material",
    # and "ssh.enabled missing/false". Operators have reported false negatives
    # here; redacted dump of the resolved dict makes the root cause
    # visible without exposing secrets. Deduped against the last
    # emitted status for this host — if nothing changed, stay silent.
    status_sig = (
        f"{configured}|{resolved.get('host')}|{resolved.get('user')}|"
        f"{resolved.get('port')}|{resolved.get('key_set')}|"
        f"{resolved.get('password_set')}|{resolved.get('password_source')}|"
        f"{bool(resolved.get('_per_host_password'))}|{resolved.get('disabled')}|"
        f"{resolved.get('error')}"
    )
    if _status_output_sig.get(host_id) != status_sig:
        _status_output_sig[host_id] = status_sig
        print(
            f"[ssh] status host_id={host_id!r} configured={configured} "
            f"resolved_host={resolved.get('host')!r} "
            f"user={resolved.get('user')!r} "
            f"port={resolved.get('port')} "
            f"key_set={resolved.get('key_set')} "
            f"password_set={resolved.get('password_set')} "
            f"password_source={resolved.get('password_source')!r} "
            f"per_host_password={bool(resolved.get('_per_host_password'))} "
            f"disabled={resolved.get('disabled')} "
            f"error={resolved.get('error')!r}"
        )
    # Strip every underscore-prefixed key from the resolved dict
    # before handing it to the API — those are internal-only
    # bookkeeping fields (BUG-004 in the code review). Today
    # `_per_host_password` is the only one, but the strip is generic
    # so future internal flags don't leak by default.
    public_resolved = {k: v for k, v in resolved.items() if not str(k).startswith("_")}
    return {
        "enabled":      not resolved.get("disabled"),
        "configured":   configured,
        "resolved":     public_resolved,
    }


def sanitize_command_for_audit(command: str) -> str:
    """Truncate + redact obvious secrets from a command before it lands
    in the history table. We don't try to be clever — just chop long
    strings so an audit row stays legible, and stamp ``***`` over
    anything that looks like ``--password`` / ``--token`` / env-style
    assignments.

    Full sanitisation is a losing game; the aim is "don't make it worse
    than necessary" — raw commands still appear in the sshd logs of the
    target host either way.
    """
    if not command:
        return ""
    out = command.strip()
    # Redact obvious secret-bearing flags. Keep the flag name so the
    # intent is still readable.
    out = re.sub(
        r"(--?(?:password|token|secret|api[-_]?key)[= ]\s*)\S+",
        r"\1***",
        out,
        flags=re.IGNORECASE,
    )
    # Collapse excessive whitespace so one-liner heredocs don't bloat
    # the history view.
    out = re.sub(r"\s+", " ", out)
    if len(out) > 512:
        out = out[:509] + "…"
    return out


# ---------------------------------------------------------------------------
# Interactive shell — TODO #170
# ---------------------------------------------------------------------------
# Live PTY-backed shell for the host-drawer Terminal modal. Bridges a
# WebSocket coming from the SPA (binary frames = shell I/O, JSON text
# frames = control messages) to an asyncssh interactive process.
#
# Reuses every safety rail the one-shot ``run_command`` already enforces:
#   - ``request_pty="force"`` so sudo behaves correctly (CLAUDE.md
#     "SSH runs need ``request_pty='force'``" rule).
#   - per-(host_id, user) ``_arm_cooldown`` on auth failure.
#   - same credential-resolution ladder + ``password_source`` lookup.
#
# Out of scope here: keystroke logging (privacy + audit volume — see
# the route handler in main.py for the start/end-only history rows),
# multi-host fan-out (one shell per WebSocket only), and SFTP.
# ---------------------------------------------------------------------------
class TerminalAuthError(Exception):
    """Raised by :func:`open_shell` when the connection is rejected for
    auth reasons (bad key / bad password / cool-down). The route layer
    surfaces a localised reason via the WS close-frame and still writes
    an audit row before the socket goes away.
    """
    def __init__(self, message: str, *, code: str = "auth_failed", cooldown_armed: bool = False):
        super().__init__(message)
        self.code = code
        self.cooldown_armed = cooldown_armed


class TerminalConfigError(Exception):
    """Raised when the resolved SSH params are insufficient (no host,
    no user, no credentials, host disabled, ...). Distinct from
    :class:`TerminalAuthError` so the route can return a different
    close code + close reason.
    """
    def __init__(self, message: str, *, code: str = "not_configured"):
        super().__init__(message)
        self.code = code


async def open_shell(
    host_id: str,
    hosts_config: list[dict],
    *,
    term_cols: int = 80,
    term_rows: int = 24,
):
    """Open an interactive PTY-backed shell on the host resolved from
    ``host_id``.

    Returns a tuple ``(connection, process, resolved)``:
      - ``connection`` — the live ``asyncssh.SSHClientConnection``. The
        caller MUST close it (``connection.close()`` + ``await
        connection.wait_closed()``) when the WebSocket goes away.
      - ``process`` — an ``asyncssh.SSHClientProcess`` running an
        interactive login shell. ``stdin.write(bytes)`` /
        ``stdout.read(...)`` for I/O,
        ``connection.change_terminal_size(cols, rows, ...)`` is hidden
        behind :func:`resize_shell` for the route layer.
      - ``resolved`` — the same dict :func:`resolve_ssh_params` returns,
        for the caller to stamp on the audit row + status footer.

    Raises :class:`TerminalConfigError` when nothing was tried (missing
    host, missing creds, ssh disabled, cool-down active).
    Raises :class:`TerminalAuthError` when asyncssh rejected the auth
    handshake (cool-down is armed in this case).
    Bubbles every other ``asyncssh`` / ``OSError`` straight through —
    the caller's ``try/finally`` must still close the WS gracefully.
    """
    resolved = resolve_ssh_params(host_id, hosts_config)
    if resolved.get("error"):
        raise TerminalConfigError(resolved["error"], code="resolve_error")
    if resolved.get("disabled"):
        raise TerminalConfigError(
            "SSH is disabled for this host (per-host override)",
            code="disabled",
        )
    if not resolved.get("host") or not resolved.get("user"):
        raise TerminalConfigError(
            "SSH not configured (host + user both required)",
            code="not_configured",
        )
    if not resolved.get("key_set") and not resolved.get("password_set"):
        raise TerminalConfigError(
            "No SSH credentials configured — set a key or password in "
            "Admin → SSH",
            code="no_credentials",
        )

    cd = _in_cooldown(host_id, resolved["user"])
    if cd is not None:
        raise TerminalConfigError(
            f"auth cool-down ({int(cd)}s remaining) — fix credentials "
            f"and wait before retrying",
            code="cooldown",
        )

    g = get_global_ssh_settings()
    client_keys_arg: Any = None
    if g["private_key"]:
        try:
            client_keys_arg = [asyncssh.import_private_key(
                g["private_key"], passphrase=g["passphrase"] or None,
            )]
        except Exception as e:
            if not resolved.get("password_set"):
                raise TerminalConfigError(
                    f"bad SSH key: {type(e).__name__}: {e}",
                    code="bad_key",
                )
            client_keys_arg = None

    # Mirror run_command's password-source lookup so per-host /
    # per-group / global passwords are honoured.
    ssh_password: Optional[str] = None
    record = _find_host_record(host_id, hosts_config)
    src = resolved.get("password_source") or ""
    if src == "per_host":
        if record and isinstance(record.get("ssh"), dict):
            ssh_password = (record["ssh"].get("password") or "").strip() or None
    elif src in ("sub_group", "main_group"):
        main_group, sub_group = _groups_for_host(record)
        target = sub_group if src == "sub_group" else main_group
        if target and isinstance(target.get("ssh"), dict):
            ssh_password = (target["ssh"].get("password") or "").strip() or None
    if ssh_password is None and g["password"]:
        ssh_password = g["password"]

    known_hosts_arg: Any = None
    if g["known_hosts"]:
        try:
            known_hosts_arg = asyncssh.import_known_hosts(g["known_hosts"])
        except Exception as e:
            raise TerminalConfigError(
                f"bad known_hosts blob: {type(e).__name__}: {e}",
                code="bad_known_hosts",
            )

    preferred: list[str] = []
    if client_keys_arg:
        preferred.append("publickey")
    if ssh_password:
        preferred.append("password")
    if not preferred:
        raise TerminalConfigError(
            "No SSH credentials available at connect time",
            code="no_credentials",
        )

    # Bound the requested terminal size — operators have been seen
    # passing wildly large numbers from scripted callers; clamp here so
    # asyncssh's PTY allocation doesn't refuse the request and tear the
    # whole connection down.
    cols = max(1, min(int(term_cols or 80), 500))
    rows = max(1, min(int(term_rows or 24), 200))

    print(
        f"[ssh] terminal OPEN host_id={host_id!r} "
        f"target={resolved.get('user')}@{resolved.get('host')}:{resolved.get('port')} "
        f"preferred_auth={preferred!r} term_size={cols}x{rows}"
    )
    try:
        conn = await asyncssh.connect(
            host=resolved["host"],
            port=resolved["port"],
            username=resolved["user"],
            client_keys=client_keys_arg,
            known_hosts=known_hosts_arg,
            agent_path=None,
            password=ssh_password,
            preferred_auth=",".join(preferred),
            connect_timeout=20.0,
            login_timeout=20.0,
            # Keepalive on the SSH side mirrors the WS-ping cadence in
            # the route handler. Keeps idle proxies + NATs from killing
            # an otherwise-healthy session.
            keepalive_interval=15,
        )
    except asyncssh.PermissionDenied as e:
        _arm_cooldown(host_id, resolved["user"])
        print(f"[ssh] terminal ERROR PermissionDenied host={resolved.get('host')!r}: {e} — cool-down armed")
        raise TerminalAuthError(
            f"permission denied: {e}",
            code="permission_denied",
            cooldown_armed=True,
        )
    except asyncssh.HostKeyNotVerifiable as e:
        print(f"[ssh] terminal ERROR HostKeyNotVerifiable host={resolved.get('host')!r}: {e}")
        raise TerminalConfigError(
            f"host key not trusted: {e}. Paste the expected line into "
            f"Admin → SSH → Known hosts.",
            code="host_key",
        )

    try:
        # Server host-key fingerprint — surface the same way run_command
        # does so the audit row / drawer status footer stays consistent.
        try:
            server_key = conn.get_server_host_key()
            fp = server_key.get_fingerprint("sha256") if server_key else ""
            if fp and ":" in fp:
                fp = fp.split(":", 1)[1]
            resolved["server_key_fingerprint"] = fp[:16]
        except Exception:
            resolved["server_key_fingerprint"] = ""
        # request_pty='force' — same reasoning as run_command. sudo
        # without a TTY behaves badly in piped contexts.
        proc = await conn.create_process(
            request_pty="force",
            term_type="xterm-256color",
            term_size=(cols, rows),
            encoding=None,  # raw bytes both ways — don't transcode
        )
        return conn, proc, resolved
    except Exception:
        # If the shell open failed AFTER auth, drop the connection so
        # we don't leak a TCP socket. Re-raise so the route layer can
        # report it.
        try:
            conn.close()
            await conn.wait_closed()
        except Exception:
            pass
        raise


def resize_shell(proc: Any, cols: int, rows: int) -> None:
    """Forward an xterm.js resize event to the live SSH PTY.

    Wraps asyncssh's ``change_terminal_size``. Bounded the same way
    :func:`open_shell` clamps the initial size so a runaway client
    can't push an unsupported geometry. Silently no-ops on a closed
    process so the route handler doesn't have to special-case the race
    between "client sends resize" and "shell already exited."
    """
    try:
        c = max(1, min(int(cols or 0), 500))
        r = max(1, min(int(rows or 0), 200))
    except (TypeError, ValueError):
        return
    try:
        proc.change_terminal_size(c, r)
    except Exception as e:
        # Don't let a bad resize tear the session down. Log + continue.
        print(f"[ssh] terminal resize ignored ({type(e).__name__}: {e})")
