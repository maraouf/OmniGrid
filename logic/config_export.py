"""Settings-as-Code export / import for OmniGrid admin configuration.

Exports every operator-tunable knob — settings KV (including hosts_config /
host_groups / notify_templates / tuning_* overrides / asset / portainer /
oidc / beszel / pulse / webmin / snmp / ping / scheduler_timezone / ai_*
/ swarm_autoheal / debug_panel_enabled / etc.), schedules table, and
ai_memory table — into a single human-readable JSON document. Operators
commit the file to a private git repo for change tracking; on import the
SAME document overwrites every covered surface in one atomic transaction
so the post-restore deploy matches the snapshot byte-for-byte.

Why JSON not YAML
─────────────────
- stdlib (no new dep — `pyyaml` would be a 200KB wheel for one feature)
- canonical (no formatter ambiguity — `json.dumps(..., indent=2,
  sort_keys=True)` round-trips deterministically across operators)
- operator can `yq -p json` it to YAML in their editor; the on-disk
  format is the round-trip surface, not the editing surface

What's NOT in the snapshot (intentional)
────────────────────────────────────────
- Users / sessions / API tokens / WebAuthn credentials — these belong to
  the SQLite backup zip flow (`logic/backups.py`), not Settings-as-Code.
  Restoring users between deploys is a fundamentally different operation
  (sessions / credentials are local to a deploy). Operators wanting
  full-state backup should use the existing zip backup.
- History / notifications / time-series samples / ai_jobs — same
  rationale: log / event data, not configuration.
- Avatars — operator-uploaded images go through the zip backup path.

Secrets
───────
Every settings key whose name ends with one of the canonical secret
suffixes (`_password`, `_token`, `_api_key`, `_secret`, `_private_key`,
`_passphrase`) is REDACTED to the literal string ``"__OMITTED__"`` in
the export. On import, redacted entries are SKIPPED — the existing
DB value is preserved. This lets operators commit the snapshot to a
private git repo without leaking credentials, AND lets a restore
re-apply non-secret config without disturbing the operator's secret
material.
"""

from __future__ import annotations

import io
import json
import os
import re
import sqlite3
import time
from typing import Optional

from logic.db import DB_PATH, db_conn

# ----- Disk layout ----------------------------------------------------------

_DATA_DIR = os.path.dirname(DB_PATH)
CONFIG_BACKUP_DIR = os.path.join(_DATA_DIR, "config_backups")

_SAFE_NAME = re.compile(r"^[A-Za-z0-9._-]+\.json$")


def ensure_dir() -> None:
    os.makedirs(CONFIG_BACKUP_DIR, exist_ok=True)


def _safe_path(name: str) -> str:
    """Validate + re-anchor a snapshot filename under
    ``CONFIG_BACKUP_DIR``. Mirrors the path-traversal guard pattern in
    ``logic/backups.py`` (regex + realpath confinement).
    """
    if not _SAFE_NAME.match(name or ""):
        raise ValueError(f"invalid config snapshot name: {name!r}")
    full = os.path.realpath(os.path.join(CONFIG_BACKUP_DIR, name))
    root = os.path.realpath(CONFIG_BACKUP_DIR)
    if not (full == root or full.startswith(root + os.sep)):
        raise ValueError("path escapes config backup dir")
    return full


# ----- Secret redaction -----------------------------------------------------

# Suffixes mark settings rows whose value is a credential. The export
# writes the literal sentinel below in place of the value; the import
# treats the sentinel as "preserve existing DB value, don't touch".
_SECRET_SUFFIXES: tuple[str, ...] = (
    "_password", "_token", "_api_key", "_secret",
    "_private_key", "_passphrase",
)
_REDACTION_SENTINEL = "__OMITTED__"


def _is_secret_key(key: str) -> bool:
    if not isinstance(key, str):
        return False
    s = key.lower()
    return any(s.endswith(suffix) for suffix in _SECRET_SUFFIXES)


# ----- Build snapshot -------------------------------------------------------


def build_snapshot() -> dict:
    """Read every export-eligible row from the DB and return a single
    human-readable dict keyed by table / surface.

    Shape::

        {
          "schema_version": 1,
          "exported_at":    "2026-05-10T16:32:00Z",
          "app_version":    "1.4.x",
          "settings":       {key: value, ...},      # secrets redacted
          "schedules":      [{name, kind, ...}, ...],
          "ai_memory":      [{text, source, actor, ts}, ...],
        }
    """
    snap: dict = {
        "schema_version": 1,
        "exported_at":    time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    try:
        from logic.version import APP_VERSION as _v
        snap["app_version"] = str(_v)
    except Exception:
        snap["app_version"] = "unknown"

    with db_conn() as c:
        # ----- settings KV (every operator-tunable knob) -----------------
        rows = c.execute("SELECT key, value FROM settings").fetchall()
        settings_out: dict = {}
        for r in rows:
            key = str(r["key"])
            val = r["value"]
            if _is_secret_key(key):
                settings_out[key] = _REDACTION_SENTINEL
            else:
                settings_out[key] = val
        snap["settings"] = dict(sorted(settings_out.items()))

        # ----- schedules ------------------------------------------------
        sch_rows = c.execute(
            "SELECT name, kind, params, interval_seconds, enabled, "
            "       run_at_hhmm, cadence_mode, days_of_week, day_of_month "
            "FROM schedules ORDER BY name"
        ).fetchall()
        snap["schedules"] = [dict(r) for r in sch_rows]

        # ----- ai_memory ------------------------------------------------
        try:
            mem_rows = c.execute(
                "SELECT text, source, actor, ts "
                "FROM ai_memory ORDER BY ts ASC"
            ).fetchall()
            snap["ai_memory"] = [dict(r) for r in mem_rows]
        except sqlite3.OperationalError:
            # Table may not exist on a very-fresh DB.
            snap["ai_memory"] = []

    return snap


# ----- Apply snapshot -------------------------------------------------------


class SnapshotApplyResult(dict):
    """Plain dict subclass — payload shape: ``{settings_applied: int,
    settings_skipped: int, schedules_replaced: int, ai_memory_replaced:
    int, warnings: [...]}`` for the API to surface to the operator."""


def apply_snapshot(payload: dict) -> SnapshotApplyResult:
    """Atomically apply a previously-exported snapshot to the live DB.

    Semantics:
      - ``settings``: per-key UPSERT. Redacted entries (sentinel
        ``__OMITTED__``) are SKIPPED — the existing value is preserved.
      - ``schedules``: REPLACE-ALL. Drop every row, re-insert from the
        snapshot. Schedules are operator-named and the snapshot is the
        whole truth — partial overlays would leave stale rows the
        operator would have to hunt.
      - ``ai_memory``: REPLACE-ALL — same rationale.

    Raises ``ValueError`` on malformed payload (missing top-level keys,
    wrong types). Wrapped in a single transaction so a mid-apply failure
    leaves the DB unchanged.
    """
    if not isinstance(payload, dict):
        raise ValueError("snapshot must be a JSON object")
    schema_version = payload.get("schema_version")
    if schema_version not in (1,):
        raise ValueError(
            f"unsupported snapshot schema_version: {schema_version!r} (this build expects 1)"
        )
    settings = payload.get("settings") or {}
    schedules = payload.get("schedules") or []
    ai_memory = payload.get("ai_memory") or []
    if not isinstance(settings, dict):
        raise ValueError("snapshot.settings must be a JSON object")
    if not isinstance(schedules, list):
        raise ValueError("snapshot.schedules must be a JSON array")
    if not isinstance(ai_memory, list):
        raise ValueError("snapshot.ai_memory must be a JSON array")

    result = SnapshotApplyResult(
        settings_applied=0,
        settings_skipped=0,
        schedules_replaced=0,
        ai_memory_replaced=0,
        warnings=[],
    )

    with db_conn() as c:
        # ----- settings -------------------------------------------------
        for key, value in settings.items():
            if not isinstance(key, str) or not key:
                result["warnings"].append(f"settings: skipped invalid key {key!r}")
                continue
            if value == _REDACTION_SENTINEL:
                result["settings_skipped"] += 1
                continue
            # Normalise to string (settings table is TEXT-typed).
            if value is None:
                value_str = ""
            elif isinstance(value, (dict, list)):
                value_str = json.dumps(value, separators=(",", ":"))
            else:
                value_str = str(value)
            c.execute(
                "INSERT INTO settings(key, value) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, value_str),
            )
            result["settings_applied"] += 1

        # ----- schedules (replace-all) ---------------------------------
        c.execute("DELETE FROM schedules")
        now = int(time.time())
        for s in schedules:
            if not isinstance(s, dict):
                result["warnings"].append("schedules: skipped non-object row")
                continue
            name = str(s.get("name") or "").strip()
            kind = str(s.get("kind") or "").strip()
            interval = int(s.get("interval_seconds") or 0)
            if not name or not kind or interval <= 0:
                result["warnings"].append(
                    f"schedules: skipped malformed row name={name!r} kind={kind!r} interval={interval}"
                )
                continue
            try:
                c.execute(
                    "INSERT INTO schedules "
                    "(name, kind, params, interval_seconds, enabled, "
                    " run_at_hhmm, cadence_mode, days_of_week, day_of_month, "
                    " created_at, updated_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        name,
                        kind,
                        s.get("params") or None,
                        interval,
                        1 if s.get("enabled", 1) else 0,
                        s.get("run_at_hhmm") or None,
                        s.get("cadence_mode") or None,
                        s.get("days_of_week") or None,
                        s.get("day_of_month"),
                        now,
                        now,
                    ),
                )
                result["schedules_replaced"] += 1
            except sqlite3.IntegrityError as e:
                result["warnings"].append(
                    f"schedules: insert failed for name={name!r}: {e}"
                )

        # ----- ai_memory (replace-all) ---------------------------------
        try:
            c.execute("DELETE FROM ai_memory")
        except sqlite3.OperationalError:
            # Table doesn't exist yet — nothing to clear, the inserts
            # below will likely also fail (caught + reported per-row).
            pass
        for m in ai_memory:
            if not isinstance(m, dict):
                continue
            text = str(m.get("text") or "").strip()
            if not text:
                continue
            try:
                c.execute(
                    "INSERT INTO ai_memory(text, source, actor, ts) "
                    "VALUES (?, ?, ?, ?)",
                    (
                        text,
                        str(m.get("source") or "operator"),
                        str(m.get("actor") or ""),
                        float(m.get("ts") or time.time()),
                    ),
                )
                result["ai_memory_replaced"] += 1
            except sqlite3.OperationalError as e:
                result["warnings"].append(f"ai_memory: insert failed: {e}")
                break  # Table missing — no point continuing.

    return result


# ----- Disk persistence (for the schedule kind + admin "Saved" pane) -------


def save_snapshot_to_disk(*, prefix: str = "config") -> dict:
    """Write a fresh snapshot to ``CONFIG_BACKUP_DIR/<prefix>_<ts>.json``.

    Returns ``{name, size, mtime}`` mirroring ``logic/backups.py``'s
    ``create_backup`` shape so the SPA's saved-files list can render the
    same way.
    """
    ensure_dir()
    snap = build_snapshot()
    ts = time.strftime("%Y.%m.%d_%H.%M.%S", time.localtime())
    name = f"{prefix}_{ts}.json"
    name = re.sub(r"[^A-Za-z0-9._-]", "-", name)
    out = _safe_path(name)
    blob = json.dumps(snap, indent=2, sort_keys=True)
    # Atomic write — temp + os.replace so a partial write never lands.
    tmp = out + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(blob)
    os.replace(tmp, out)
    st = os.stat(out)
    return {
        "name":  os.path.basename(out),
        "size":  st.st_size,
        "mtime": int(st.st_mtime),
    }


def list_snapshots() -> list[dict]:
    """Newest-first list of saved snapshot files. Empty list when the
    directory doesn't exist yet."""
    ensure_dir()
    out: list[dict] = []
    try:
        for entry in os.scandir(CONFIG_BACKUP_DIR):
            if not entry.is_file():
                continue
            if not entry.name.endswith(".json"):
                continue
            try:
                st = entry.stat()
            except OSError:
                continue
            out.append({
                "name":  entry.name,
                "size":  int(st.st_size),
                "mtime": int(st.st_mtime),
            })
    except FileNotFoundError:
        return []
    out.sort(key=lambda e: e["mtime"], reverse=True)
    return out


def read_snapshot(name: str) -> dict:
    """Load one saved snapshot. Raises ``ValueError`` on bad name /
    parse failure."""
    full = _safe_path(name)
    with open(full, "r", encoding="utf-8") as f:
        try:
            return json.load(f)
        except json.JSONDecodeError as e:
            raise ValueError(f"invalid JSON in {name}: {e}")


def delete_snapshot(name: str) -> None:
    """Delete one saved snapshot. Path-traversal-guarded via
    ``_safe_path``."""
    full = _safe_path(name)
    if os.path.exists(full):
        os.remove(full)


def prune_snapshots(keep: int) -> list[str]:
    """Keep the N newest snapshots, delete the rest. Returns the names
    of pruned files. ``keep <= 0`` is a no-op (matches the
    ``backups.prune_backups`` semantics — operator opts in to retention
    explicitly via ``tuning_config_backup_retention_count``).
    """
    if keep <= 0:
        return []
    files = list_snapshots()  # already newest-first
    pruned: list[str] = []
    for f in files[keep:]:
        try:
            delete_snapshot(f["name"])
            pruned.append(f["name"])
        except (ValueError, OSError):
            continue
    return pruned
