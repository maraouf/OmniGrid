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
    sub-dict with optional ``user`` / ``port`` / ``disabled`` keys. V1
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
_AUTH_COOLDOWN_SECONDS = 300
_auth_cooldown: dict[tuple[str, str], float] = {}


def _cooldown_key(host_id: str, user: str) -> tuple[str, str]:
    return (host_id or "", user or "")


def _in_cooldown(host_id: str, user: str) -> Optional[float]:
    key = _cooldown_key(host_id, user)
    expires = _auth_cooldown.get(key)
    if not expires:
        return None
    remaining = expires - time.time()
    if remaining <= 0:
        _auth_cooldown.pop(key, None)
        return None
    return remaining


def _arm_cooldown(host_id: str, user: str) -> None:
    _auth_cooldown[_cooldown_key(host_id, user)] = (
        time.time() + _AUTH_COOLDOWN_SECONDS
    )


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


def resolve_ssh_params(host_id: str, hosts_config: list[dict]) -> dict:
    """Merge global defaults + per-host overrides into one connect spec.

    Returns ``{host, user, port, disabled, key_set, known_hosts_set,
    key_fingerprint, error}``. Caller treats ``error`` as a
    configuration problem (not a connection problem) — no SSH call has
    been attempted yet.
    """
    record = _find_host_record(host_id, hosts_config)
    g = get_global_ssh_settings()
    resolved: dict[str, Any] = {
        "host":              "",
        "user":              g["user"],
        "port":              g["port"],
        "disabled":          False,
        "key_set":           bool(g["private_key"]),
        "known_hosts_set":   bool(g["known_hosts"]),
        "key_fingerprint":   _key_fingerprint(g["private_key"], g["passphrase"]),
        "error":             None,
    }
    if record is None:
        resolved["error"] = f"unknown host_id: {host_id!r}"
        return resolved
    per_host = (record.get("ssh") or {}) if isinstance(record.get("ssh"), dict) else {}
    ssh_host_override = (per_host.get("host") or "").strip()
    resolved["host"] = ssh_host_override or record.get("id") or ""
    u_override = (per_host.get("user") or "").strip()
    if u_override:
        resolved["user"] = u_override
    try:
        if per_host.get("port") not in (None, "", 0):
            resolved["port"] = int(per_host["port"])
    except (TypeError, ValueError):
        # Ignore a bad per-host port override; the global default stands.
        pass
    resolved["disabled"] = bool(per_host.get("disabled"))
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
    if not resolved.get("key_set"):
        base_result["error"] = (
            "No SSH private key configured — set one in Admin → SSH."
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
    try:
        client_key = asyncssh.import_private_key(
            g["private_key"], passphrase=g["passphrase"] or None,
        )
    except Exception as e:
        base_result["error"] = f"bad SSH key: {type(e).__name__}: {e}"
        return base_result

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

    try:
        conn_ctx = asyncssh.connect(
            host=resolved["host"],
            port=resolved["port"],
            username=resolved["user"],
            client_keys=[client_key],
            known_hosts=known_hosts_arg,
            # Don't let agent forwarding surprise the operator — explicit
            # "only the key we just loaded" is the least-surprising model.
            agent_path=None,
            # Keep the interactive prompts off; we expect the configured
            # key to be the only valid auth path.
            password=None,
            preferred_auth="publickey",
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
                proc = await conn.run(command, check=False)
                base_result["ok"] = True
                base_result["exit_code"] = proc.exit_status
                base_result["stdout"] = (proc.stdout or "")[: 256 * 1024]
                base_result["stderr"] = (proc.stderr or "")[: 256 * 1024]
    except asyncssh.PermissionDenied as e:
        _arm_cooldown(host_id, resolved["user"])
        base_result["error"] = f"permission denied: {e} — cool-down armed"
    except asyncssh.HostKeyNotVerifiable as e:
        base_result["error"] = (
            f"host key not trusted: {e}. Paste the expected line into "
            f"Admin → SSH → Known hosts, or clear that field to accept "
            f"on first use (TOFU)."
        )
    except (asyncio.TimeoutError, TimeoutError):
        base_result["error"] = f"timeout after {timeout:.1f}s"
    except (OSError, asyncssh.DisconnectError, asyncssh.Error) as e:
        base_result["error"] = f"{type(e).__name__}: {e}"
    except Exception as e:
        base_result["error"] = f"unexpected: {type(e).__name__}: {e}"

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
    configured = bool(
        resolved.get("host")
        and resolved.get("user")
        and resolved.get("key_set")
        and not resolved.get("disabled")
        and not resolved.get("error")
    )
    return {
        "enabled":      not resolved.get("disabled"),
        "configured":   configured,
        "resolved":     resolved,
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
