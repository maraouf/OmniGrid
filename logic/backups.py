"""Backup + restore of the whole PortaUpdate configuration surface.

What goes INTO a backup zip:
  - A consistent snapshot of the SQLite DB — taken via SQLite's online
    .backup() API rather than a raw file copy. That way the snapshot is
    guaranteed to be internally consistent even if a write is in flight.
    The snapshot includes every table: users, sessions, api_tokens,
    ignores, settings (incl. Authentik config), history, stats_samples.
  - The whole avatars/ directory (user-uploaded images).
  - A metadata.json with backup_time, app_version, file_count — useful
    for "what was this from" without extracting.

What's NOT backed up:
  - pip-cache (not data; recreated on restart)
  - .env (ships via CI from the repo; server isn't the source of truth)
  - VERSION.txt (server-owned, hand-managed)

Restore flow:
  1. Auto-snapshot the current state to auto-before-restore-<ts>.zip so
     a bad restore is recoverable.
  2. Extract the incoming zip to a temp dir; validate structure.
  3. Replace DB file + avatars dir atomically (best-effort — SQLite
     handles concurrent read access fine because each db_conn() opens
     a fresh handle; any in-flight write would surface as an exception
     to that caller, which is acceptable for a destructive op).

Path-traversal is guarded at every entry point: backup names accepted
from the API are restricted to a safe charset, and zip extraction
rejects entries whose resolved path escapes the target directory.
"""
import json
import os
import re
import shutil
import sqlite3
import tempfile
import time
import zipfile
from pathlib import Path
from typing import Optional

from logic.db import DB_PATH
from logic.version import APP_VERSION


# Layout of the data volume:
#   <data_dir>/portaupdate.db          SQLite file
#   <data_dir>/avatars/                user-uploaded images
#   <data_dir>/backups/                zip archives (owned by this module)
_DATA_DIR = os.path.dirname(DB_PATH) or "."
BACKUP_DIR = os.path.join(_DATA_DIR, "backups")
AVATAR_DIR = os.path.join(_DATA_DIR, "avatars")

# Max bytes for an uploaded restore zip. Generous enough for thousands of
# avatars + a fat history table; small enough that a runaway upload can't
# wedge the container.
MAX_UPLOAD_BYTES = 200 * 1024 * 1024  # 200 MB

# Strict filename charset for API input. Matches what list_backups() emits.
_SAFE_NAME = re.compile(r"^[A-Za-z0-9._-]{1,128}\.zip$")


def ensure_dirs() -> None:
    """Called once at startup from the lifespan handler."""
    os.makedirs(BACKUP_DIR, exist_ok=True)
    os.makedirs(AVATAR_DIR, exist_ok=True)


def _safe_name(name: str) -> str:
    """Validate + return a backup filename, or raise ValueError."""
    if not _SAFE_NAME.match(name or ""):
        raise ValueError("invalid backup name")
    return name


def _backup_path(name: str) -> str:
    """Resolve and defensively re-anchor a backup path under BACKUP_DIR.

    Path-traversal belt-and-braces: even after _safe_name's regex,
    realpath the result and reject anything that escapes BACKUP_DIR.
    """
    safe = _safe_name(name)
    full = os.path.realpath(os.path.join(BACKUP_DIR, safe))
    if not full.startswith(os.path.realpath(BACKUP_DIR) + os.sep):
        raise ValueError("path escapes backup dir")
    return full


def _snapshot_db_to(path: str) -> None:
    """Consistent DB snapshot via SQLite's online .backup() API."""
    src = sqlite3.connect(DB_PATH)
    try:
        dst = sqlite3.connect(path)
        try:
            src.backup(dst)
        finally:
            dst.close()
    finally:
        src.close()


def create_backup(prefix: str = "portaupdate-backup") -> dict:
    """Write a new backup zip. Returns {name, size, mtime}."""
    ensure_dirs()
    ts = time.strftime("%Y.%m.%d_%H.%M.%S", time.localtime())
    # Keep the version in the filename so ops can see at a glance what
    # schema a given backup was produced under.
    name = f"{prefix}_v{APP_VERSION}_{ts}.zip"
    # Replace any chars that snuck in via APP_VERSION so the name stays safe.
    name = re.sub(r"[^A-Za-z0-9._-]", "-", name)
    out = _backup_path(name)

    # Stage the DB snapshot to a temp file first — we don't want a
    # partially-written DB inside the zip if something fails mid-backup.
    with tempfile.TemporaryDirectory(prefix="pu-bk-") as tmp:
        db_tmp = os.path.join(tmp, "portaupdate.db")
        _snapshot_db_to(db_tmp)

        meta = {
            "backup_time": int(time.time()),
            "app_version": APP_VERSION,
            "schema":      "portaupdate-v1",
        }

        with zipfile.ZipFile(out, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as z:
            z.write(db_tmp, arcname="portaupdate.db")
            # Whole avatars tree — preserves user-uploaded images.
            n_av = 0
            if os.path.isdir(AVATAR_DIR):
                for p in Path(AVATAR_DIR).iterdir():
                    if p.is_file():
                        z.write(p, arcname=f"avatars/{p.name}")
                        n_av += 1
            meta["avatar_count"] = n_av
            z.writestr("metadata.json", json.dumps(meta, indent=2))

    st = os.stat(out)
    return {"name": os.path.basename(out), "size": st.st_size, "mtime": int(st.st_mtime)}


def list_backups() -> list[dict]:
    ensure_dirs()
    out = []
    for entry in os.scandir(BACKUP_DIR):
        if entry.is_file() and entry.name.endswith(".zip"):
            try:
                _safe_name(entry.name)
            except ValueError:
                continue  # skip anything that doesn't match our naming rules
            st = entry.stat()
            out.append({
                "name":  entry.name,
                "size":  st.st_size,
                "mtime": int(st.st_mtime),
                # Try to pull the app_version off the metadata for richer UX.
                # Failures are tolerated — we still list the file.
                "version": _read_metadata_version(os.path.join(BACKUP_DIR, entry.name)),
            })
    out.sort(key=lambda r: r["mtime"], reverse=True)
    return out


def _read_metadata_version(path: str) -> Optional[str]:
    try:
        with zipfile.ZipFile(path, "r") as z:
            raw = z.read("metadata.json").decode("utf-8")
        return json.loads(raw).get("app_version")
    except Exception:
        return None


def delete_backup(name: str) -> None:
    p = _backup_path(name)
    if os.path.exists(p):
        os.remove(p)


def prune_backups(keep: int) -> list[str]:
    """Delete the oldest backups so only ``keep`` newest remain.

    ``keep`` values:
      - 0 or negative → no-op (unlimited retention). Matches the
        default 'retention disabled' setting so an operator who hasn't
        set a value never loses backups unexpectedly.
      - 1+ → keep the N newest by mtime; delete the rest.

    Returns the list of deleted filenames (for logging / history trails).
    File-remove errors for individual entries are caught and logged; we
    don't want one stuck file to block the retention of the others.
    """
    if keep is None or keep <= 0:
        return []
    entries = list_backups()  # already sorted mtime DESC
    if len(entries) <= keep:
        return []
    to_delete = entries[keep:]
    removed: list[str] = []
    for e in to_delete:
        try:
            delete_backup(e["name"])
            removed.append(e["name"])
        except Exception as exc:
            print(f"[backups] prune: failed to delete {e['name']}: {exc}")
    return removed


def _validate_zip_entries(path: str) -> None:
    """Check every archive entry resolves safely under the extraction dir
    and matches the known layout (portaupdate.db + optional avatars/ +
    metadata.json). Reject anything unexpected — refuse to extract a
    backup we don't recognise.
    """
    with zipfile.ZipFile(path, "r") as z:
        names = z.namelist()
    # Path-traversal guard.
    for n in names:
        if n.startswith("/") or ".." in Path(n).parts:
            raise ValueError(f"unsafe entry: {n!r}")
    # Structure guard.
    has_db = any(n == "portaupdate.db" for n in names)
    if not has_db:
        raise ValueError("backup is missing portaupdate.db")
    bad = [n for n in names
           if n not in ("portaupdate.db", "metadata.json")
           and not n.startswith("avatars/")]
    if bad:
        raise ValueError(f"backup contains unexpected entries: {bad[:5]}")


def restore_from_file(path: str) -> dict:
    """Restore the DB + avatars from a zip file on disk.

    Before touching anything, this takes an auto-snapshot of the current
    state so a failed/unwanted restore can be rolled back. The auto-snapshot
    is itself a regular backup file and shows up in list_backups().

    Returns {restored_from: name, safety_snapshot: name_or_none, avatar_count}.
    """
    ensure_dirs()
    _validate_zip_entries(path)

    # 1) Safety snapshot of current state — silently continue if it fails
    #    (e.g. brand-new install with almost nothing to snapshot). A restore
    #    that can't take a safety snapshot is still better than no restore.
    safety = None
    try:
        safety_meta = create_backup(prefix="auto-before-restore")
        safety = safety_meta["name"]
    except Exception as e:
        print(f"[backups] WARN: couldn't take safety snapshot: {e}")

    # 2) Extract to a temp dir.
    with tempfile.TemporaryDirectory(prefix="pu-rst-") as tmp:
        with zipfile.ZipFile(path, "r") as z:
            # Explicit extract + per-entry path guard (ZipFile's .extractall
            # respects our earlier validation, but re-check each target path
            # against the extraction root to be thorough).
            for info in z.infolist():
                dest = os.path.realpath(os.path.join(tmp, info.filename))
                if not dest.startswith(os.path.realpath(tmp) + os.sep) and dest != os.path.realpath(tmp):
                    raise ValueError(f"unsafe extract path: {info.filename}")
                z.extract(info, tmp)

        new_db = os.path.join(tmp, "portaupdate.db")
        new_avatars = os.path.join(tmp, "avatars")

        # 3) Replace DB atomically — rename on the same filesystem is atomic.
        #    Any in-flight db_conn() is an independent sqlite3 handle on the
        #    old inode; it'll see old data until it closes, which is fine
        #    (every db_conn() is a short-lived context manager).
        os.replace(new_db, DB_PATH)

        # 4) Replace avatars dir. Move old one to a tmp name, move new in,
        #    delete old — gives us a rollback window if the new tree has
        #    a permissions issue.
        bak_av = AVATAR_DIR + ".old"
        if os.path.isdir(AVATAR_DIR):
            if os.path.isdir(bak_av):
                shutil.rmtree(bak_av)
            os.replace(AVATAR_DIR, bak_av)
        try:
            if os.path.isdir(new_avatars):
                shutil.move(new_avatars, AVATAR_DIR)
            else:
                # Backup had no avatars; create an empty dir so the app's
                # bind mount expectations still hold.
                os.makedirs(AVATAR_DIR, exist_ok=True)
            # Success — clean up the old tree.
            if os.path.isdir(bak_av):
                shutil.rmtree(bak_av, ignore_errors=True)
        except Exception:
            # Best-effort rollback of the avatars swap. DB was already
            # replaced, so the user still needs to know restore partially
            # landed — we re-raise.
            if os.path.isdir(bak_av) and not os.path.isdir(AVATAR_DIR):
                os.replace(bak_av, AVATAR_DIR)
            raise

    # Count avatars post-restore for the UI response.
    n_av = 0
    if os.path.isdir(AVATAR_DIR):
        n_av = sum(1 for p in Path(AVATAR_DIR).iterdir() if p.is_file())

    return {
        "restored_from":   os.path.basename(path),
        "safety_snapshot": safety,
        "avatar_count":    n_av,
    }


def restore_by_name(name: str) -> dict:
    """Restore from a backup already sitting under BACKUP_DIR."""
    return restore_from_file(_backup_path(name))
