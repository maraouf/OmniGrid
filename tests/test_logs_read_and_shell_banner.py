"""Tests for log-read access, the pre-commit audit gate, and the shell banner.

Three things checked here, all found by looking at the running system rather
than at the code:

1. `GET /api/logs` refused a read-only token (403) while the three
   `/api/admin/logs/files*` reads beside it accepted one (200). Those serve
   whole log FILES, so the same token could already download a full day's log
   while being denied the much smaller in-memory tail of that same content —
   the protection was inverted, not merely uneven. Reads are now uniform;
   `DELETE /api/logs` stays admin-only.

2. The pre-commit hook's audit stage. A status report claimed the hook skipped
   project-wide audits — quoted from a review note that had gone stale. The
   hook has run them since stage 2 was added, and these pin that so the claim
   cannot quietly become true again.

3. `_run_shell_query` typed its command into the middle of the device's login
   banner (seen live as `Server Room / Rackshow mac address-table ...`). It
   was accepted because that switch echoes and buffers; one that drops input
   while printing would have swallowed the command, and the failure would
   have looked like a device that answered nothing.
"""
from __future__ import annotations

import inspect
import pathlib
import re

from logic import ssh

ROOT = pathlib.Path(__file__).resolve().parents[1]


def _scan_routes() -> str:
    return (ROOT / "main_pkg" / "scan_routes.py").read_text(encoding="utf-8")


def _route_signature(path: str) -> str:
    """The decorator + def + params for one route, up to its docstring."""
    src = _scan_routes()
    i = src.index(f'@app.get("{path}")')
    return src[i:i + 400]


def test_reading_the_live_log_allows_a_read_only_token():
    sig = _route_signature("/api/logs")
    assert "_user: AuthedUser" in sig, (
        "GET /api/logs is admin-gated again — a read-only token can download "
        "whole log files next to it but not this, which is backwards")
    assert "_admin: AdminUser" not in sig


def test_the_log_file_reads_stay_open_to_the_same_role():
    """The three file reads are what made the inconsistency visible; if they
    ever narrow, the pairing above should be revisited deliberately."""
    src = _scan_routes()
    for path in ("/api/admin/logs/files",
                 "/api/admin/logs/files/{name}",
                 "/api/admin/logs/files/{name}/download"):
        i = src.index(f'@app.get("{path}")')
        assert "_user: AuthedUser" in src[i:i + 400], f"{path} changed role"


def test_clearing_the_log_buffer_is_still_admin_only():
    """Widening the READ must not have widened the write beside it."""
    src = _scan_routes()
    i = src.index('@app.delete("/api/logs")')
    assert "_admin: AdminUser" in src[i:i + 300], (
        "DELETE /api/logs is no longer admin-only — the log buffer can be "
        "cleared by a read-only token")


def test_the_precommit_hook_runs_the_project_wide_audits():
    hook = (ROOT / ".githooks" / "pre-commit").read_text(encoding="utf-8")
    assert "--audits" in hook, (
        "the pre-commit hook no longer runs the project-wide audits — "
        "audit-only findings would reach a commit unchecked")
    assert re.search(r"--audits[^\n]*--severity warn", hook), (
        "the audit stage no longer blocks on WARNING, only on ERROR")


def test_the_precommit_hook_blocks_on_warnings_in_every_stage():
    hook = (ROOT / ".githooks" / "pre-commit").read_text(encoding="utf-8")
    assert hook.count("--severity warn") >= 2, (
        "a lint stage stopped treating warnings as blocking")


def test_the_audit_selection_covers_every_registered_audit():
    """`--audits` must select the whole set, not a hand-listed subset that a
    newly-added audit could be forgotten from."""
    lint = (ROOT / "scripts" / "lint.py").read_text(encoding="utf-8")
    assert "rid for rid in RULES if rid in _AUDIT_RULES" in lint, (
        "--audits no longer derives its rule list from _AUDIT_RULES, so a new "
        "audit can be registered without the hook picking it up")


def test_the_banner_is_drained_before_the_command_is_typed():
    src = inspect.getsource(ssh._run_shell_query)
    banner_at = src.index("banner_chunks")
    write_at = src.index("proc.stdin.write")
    assert banner_at < write_at, (
        "the command is written before the login banner is drained again — "
        "it can land spliced into the banner text")
    assert "BANNER_QUIET_S" in src and "BANNER_MAX_S" in src


def test_a_silent_device_is_not_waited_on_for_the_full_budget():
    """A device that says nothing until spoken to is normal, so the drain has
    to give up early rather than charge every query the full cap."""
    src = inspect.getsource(ssh._run_shell_query)
    assert "BANNER_SILENT_S" in src, (
        "the no-banner early-exit is gone — every query against a quiet "
        "device now pays the full settle cap")


def test_ok_is_decided_by_the_reply_not_the_banner():
    """`ok` was `bool(out.strip())` over banner+reply, so any device that
    printed a welcome reported success even when the command returned
    nothing."""
    src = inspect.getsource(ssh._run_shell_query)
    assert 'base_result["stdout"] = out' in src
    assert 'base_result["transcript"] = (banner + out) if banner else out' in src, (
        "the banner is back in stdout, so `ok` is satisfied by the welcome "
        "message rather than by an actual answer")
