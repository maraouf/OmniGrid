"""Readarr per-app module.

Encapsulates everything Readarr-specific so the route layer
(``main_pkg/apps_routes.py``) stays generic. Public surface mirrors the
``lidarr.py`` / ``sonarr.py`` shape (Readarr is the BOOK / AUDIOBOOK
companion to the *arr family — same design, ``/api/v1`` API like Lidarr,
and it manages AUTHORS + BOOKS rather than artists / movies / series):

    SLUGS               — catalog slugs this module handles ("readarr").
    requires_api_key()  — True (Readarr authenticates via the X-Api-Key header).
    test_credential(host_row, chip, candidate_key) -> dict
    fetch_data(host_row, chip, *, host_id, service_idx, force) -> dict
    peek_latest(host_id, service_idx) -> dict | None    (AI context)
    SKILLS / run_skill  — status (read) + upcoming + queue + author-info (arg)
                          + add-author (arg) + remove-author (arg, destructive)
                          + search-missing + refresh.

The expanded card answers "how big is the library, how many books are
missing, what's downloading, and is the disk OK" at a glance:

    authors_total  — every author in the library  (GET /api/v1/author)
    monitored      — authors Readarr is actively managing
    missing        — monitored books with no file yet
                     (GET /api/v1/wanted/missing — totalRecords)
    queue          — items currently downloading  (GET /api/v1/queue/status)
    disk_free_gb   — free space on the largest library disk (GET /api/v1/diskspace)
    health_issues  — active health warnings        (GET /api/v1/health)
    version        — Readarr version               (GET /api/v1/system/status)

AI / Telegram skills
--------------------
* ``readarr_status``          — library summary (live fetch).
* ``readarr_upcoming``        — next ~30 days of upcoming book releases.
* ``readarr_queue``           — what's downloading + progress.
* ``readarr_author_info``     — (arg) "do I have <author>?" — library lookup.
* ``readarr_add_author``      — (arg) add an author by name.
* ``readarr_remove_author``   — (arg, DESTRUCTIVE) remove an author; KEEPS files.
* ``readarr_search_missing``  — trigger a search for all monitored missing books.
* ``readarr_refresh``         — refresh + disk-scan the whole library.

Auth model: every authenticated Readarr v1 endpoint takes the ``X-Api-Key``
header (Readarr → Settings → General → API Key). The credential probe hits
the auth-required ``/api/v1/system/status`` so a bad key fails loudly.
Single-instance app (NOT fleet) — one card per pinned chip.

Add-author caveat: Readarr's add REQUIRES a ``metadataProfileId`` (the
book-type / edition filter) ON TOP of the ``qualityProfileId`` — Radarr /
Sonarr have no metadata profile (Lidarr does, same as here). We fetch
``/api/v1/metadataprofile`` and use the first id.

Upstream API reference: <readarr-host>/api/v1 (Swagger at /api). Endpoints:
    GET  /api/v1/system/status   — version (test-credential probe + footnote)
    GET  /api/v1/author          — library list (total / monitored)
    GET  /api/v1/wanted/missing  — missing-book count (totalRecords)
    GET  /api/v1/queue/status    — downloading count
    GET  /api/v1/diskspace       — per-mount free / total bytes
    GET  /api/v1/health          — active health issues
    GET  /api/v1/calendar        — upcoming book releases
    GET  /api/v1/author/lookup   — Goodreads-backed author search (add)
    GET  /api/v1/qualityprofile  — quality profiles (add)
    GET  /api/v1/metadataprofile — metadata profiles (add, Readarr-required)
    GET  /api/v1/rootfolder      — root folders (add)
    POST /api/v1/author          — add an author
    DELETE /api/v1/author/{id}   — remove an author
    POST /api/v1/command         — MissingBookSearch / RefreshAuthor
"""
from __future__ import annotations

import time
from typing import Any, Optional

import httpx

from functools import partial as _partial

from logic.apps import _servarr
from logic.apps._common import cache_key, fetch_gate, peek_cache, resolve_cache_ttl
from logic.coerce import as_dict, as_list, safe_float, safe_int
from logic.external_urls import ExternalURL


def _book_isbn(book: Any) -> str:
    """First usable ISBN-13/10 from a Readarr book's ``editions`` list, for an
    Open Library cover lookup. ``""`` when none."""
    eds = book.get("editions") if isinstance(book, dict) else None
    if isinstance(eds, list):
        for e in eds:
            if isinstance(e, dict):
                isbn = str(e.get("isbn13") or e.get("isbn") or "").strip()
                if isbn.isdigit() and len(isbn) in (10, 13):
                    return isbn
    return ""


async def _book_poster(cli: "httpx.AsyncClient", base: str, api_key: str,
                       bk: dict, au: dict) -> str:
    """Resolve a Readarr book's poster, MOST-RELIABLE-FIRST. The queue embed
    often TRIMS the book's ``images`` (or carries local-only art that 415s), so
    when the embed has no allowlisted remote cover: re-fetch the full book by id
    (it has the goodreads / amazon remoteUrl), then fall back to a PUBLIC Open
    Library cover by ISBN, then the local ``/MediaCover`` path. Order:
    remote(embed) → remote(full re-fetch) → OpenLibrary(ISBN) → local."""
    remote = _servarr.remote_poster_url(bk) or _servarr.remote_poster_url(au)
    if remote:
        return remote
    full: dict = {}
    bid = safe_int(bk.get("id"))
    if bid:
        try:
            rr = await cli.get(base + f"/api/v1/book/{bid}", headers=_headers(api_key))
            if rr.status_code == 200:
                j = rr.json()
                full = j if isinstance(j, dict) else {}
        except (httpx.HTTPError, OSError, ValueError, TypeError):
            full = {}
    remote = _servarr.remote_poster_url(full)
    if remote:
        return remote
    isbn = _book_isbn(full) or _book_isbn(bk)
    if isbn:
        return f"{ExternalURL.OPENLIBRARY_COVERS}/b/isbn/{isbn}-L.jpg"
    return (_servarr.local_poster_path_only(full)
            or _servarr.local_poster_path_only(bk)
            or _servarr.local_poster_path_only(au))


# Servarr-family shared helpers (logic/apps/_servarr.py) bound to Readarr's
# api version (v1) + brand, aliased to the historical underscore names so the
# skill bodies' call sites stay unchanged. Readarr matches a STRING
# foreignAuthorId + authorName, so it keeps its own _norm_name / _find_in_library.
_headers = _servarr.headers
_version_from = _servarr.version_from
_fmt_size_gib = _servarr.fmt_size_gib
_parse_disks = _servarr.parse_disks
_primary_disk = _servarr.primary_disk
_storage_summary_line = _servarr.storage_summary_line
_GIB = _servarr.GIB
_fetch_version = _partial(_servarr.fetch_version, api_version="v1")
_resolve_skill_target = _partial(_servarr.resolve_skill_target, app_label="Readarr")
_command_skill = _partial(_servarr.command_skill, app_label="Readarr", api_version="v1")

# Per-app image-proxy hook — REQUIRED so the queue's poster_proxy thumbnails
# resolve (the route 400s "no image proxy for this app" without it). Shared
# *arr hook: fetches an allowlisted public-CDN remoteUrl (incl. the OpenLibrary
# / goodreads book-cover hosts) anonymously, or a local /MediaCover path with
# the api_key. (radarr / sonarr / lidarr re-export the same; Readarr was the
# straggler.)
image_proxy_url = _servarr.image_proxy_url
# Cross-host redirect guard for the per-app image proxy (coverartarchive
# -> ia*.archive.org is the load-bearing case; everything off-allowlist is
# rejected). Re-exported from the shared base alongside the image hook.
image_redirect_allowed = _servarr.image_redirect_allowed

# Catalog template slugs handled by this module.
SLUGS: tuple[str, ...] = ("readarr",)

# Read-only skills + free-form-arg author skills + background-command skills.
# No-arg skills surface as one-click drawer buttons AND AI / Telegram actions;
# the ``arg``-carrying author skills are AI / Telegram only (the dispatch
# supplies the name from natural language) — mirrors Lidarr / Sonarr.
SKILLS: tuple[dict, ...] = (
    {
        "id": "readarr_status",
        "name": "Readarr status",
        "ai_phrases": ("readarr status, book library, how many authors, how "
                       "many books are missing, missing books, readarr health, "
                       "book collection size, disk space readarr, ebook library"),
        "destructive": False,
    },
    {
        "id": "readarr_upcoming",
        "name": "Upcoming books",
        "ai_phrases": ("upcoming books, what books are coming out, readarr "
                       "calendar, new book releases, upcoming reads, "
                       "books releasing soon, what's releasing on readarr"),
        "destructive": False,
    },
    {
        "id": "readarr_queue",
        "name": "Download queue",
        "ai_phrases": ("what's downloading on readarr, readarr queue, readarr "
                       "downloads, what books are downloading, "
                       "download progress readarr, queue details"),
        "destructive": False,
    },
    {
        "id": "readarr_queue_delete",
        "name": "Remove from queue",
        "ai_phrases": ("remove from readarr queue, cancel a readarr download, "
                       "delete from download queue, cancel this download, "
                       "remove queued download"),
        "destructive": True,
        "arg": True,
        "arg_hint": ("the queue record id to remove (also removes it from the "
                     "download client); the drawer's per-row trash button supplies it"),
    },
    {
        "id": "readarr_queue_blocklist_search",
        "name": "Blocklist & search a stuck download",
        "ai_phrases": ("blocklist and search readarr, blocklist a stuck download, "
                       "this book download is stuck try another release, "
                       "blocklist and re-search, force a new release readarr"),
        "destructive": True,
        "arg": True,
        "arg_hint": ("the queue record id (the drawer's per-row blocklist button "
                     "supplies it as '<queue_id>:<book_id>')"),
    },
    {
        "id": "readarr_author_info",
        "name": "Look up an author",
        "ai_phrases": ("do i have <author>, is <author> in my library, "
                       "look up <author>, author info <author>, "
                       "status of <author>, do i have books by <author>, "
                       "is <author> monitored, how many books of <author>"),
        "destructive": False,
        "arg": True,
        "arg_hint": "the author name to look up in the Readarr library",
    },
    {
        "id": "readarr_add_author",
        "name": "Add an author",
        "ai_phrases": ("add an author, add <author>, add <author> to readarr, "
                       "add <author> to the library, get <author> on readarr, "
                       "i want books by <author>, put <author> in readarr"),
        "destructive": False,
        "arg": True,
        "arg_hint": "the author name to add",
    },
    {
        "id": "readarr_remove_author",
        "name": "Remove an author",
        "ai_phrases": ("remove an author, remove <author>, delete <author>, "
                       "remove <author> from readarr, take <author> off readarr, "
                       "delete <author> from the library"),
        "destructive": True,
        "arg": True,
        "arg_hint": "the author name to remove from the Readarr library",
    },
    {
        "id": "readarr_search_author",
        "name": "Search for an author",
        "ai_phrases": ("search for <author>, grab books by <author>, "
                       "find <author> now, search readarr for <author>, "
                       "look for books by <author>, download <author> now"),
        "destructive": False,
        "arg": True,
        "arg_hint": "the author name to search for books now (must already "
                    "be in the Readarr library)",
    },
    {
        "id": "readarr_search_missing",
        "name": "Search for missing books",
        "ai_phrases": ("search for missing books, find missing books, "
                       "search readarr for missing, download missing books, "
                       "grab missing books, look for missing books"),
        "destructive": False,
    },
    {
        "id": "readarr_refresh",
        "name": "Refresh book library",
        "ai_phrases": ("refresh readarr, rescan the book library, refresh "
                       "authors, update readarr library, rescan readarr, "
                       "refresh and scan books"),
        "destructive": False,
    },
    # Manual-update skills — only for instances NOT linked to Docker (updates
    # for a native / non-Docker install are applied by hand).
    {
        "id": "readarr_check_update",
        "name": "Check for updates",
        "ai_phrases": ("is readarr up to date, check readarr version, latest readarr "
                       "version, is there a readarr update, readarr update available, "
                       "check for readarr updates, what version of readarr is running"),
        "destructive": False,
        "non_docker_only": True,
    },
    {
        "id": "readarr_update",
        "name": "Update Readarr",
        "ai_phrases": ("update readarr, upgrade readarr, install the readarr update, "
                       "run the readarr updater, update readarr to the latest version, "
                       "apply the readarr update"),
        "destructive": True,
        "non_docker_only": True,
    },
)

# Per-(host_id, service_idx) data cache for the expanded card. 60s default —
# the author list is the heaviest call and changes slowly (matches Lidarr).
DEFAULT_CACHE_TTL_S = 60
_data_cache: dict[str, tuple[float, dict]] = {}


def requires_api_key() -> bool:
    """Readarr authenticates every v1 endpoint via X-Api-Key; the editor MUST
    render the api_key input + Test-connection button."""
    return True


async def test_credential(host_row: dict, chip: dict, candidate_key: str, **_kw) -> dict:
    """Probe Readarr's auth-required ``/api/v1/system/status`` — delegates to the
    shared Servarr probe bound to Readarr's brand + api version."""
    return await _servarr.test_credential(host_row, chip, candidate_key,
                                          app_label="Readarr", api_version="v1")


async def _missing_book_count(cli: httpx.AsyncClient, base: str, key: str) -> int:
    """Total monitored-missing books via ``/api/v1/wanted/missing``
    (``totalRecords`` with ``pageSize=1`` — cheap). 0 on any failure."""
    try:
        r = await cli.get(base + "/api/v1/wanted/missing",
                          headers=_headers(key),
                          params={"page": "1", "pageSize": "1",
                                  "includeAuthor": "false"})
        if r.status_code != 200:
            return 0
        return safe_int((r.json() or {}).get("totalRecords"))
    except (httpx.HTTPError, OSError, ValueError, TypeError):
        return 0


# noinspection DuplicatedCode
# The upstream-error guard + JSON-parse block below is structurally shared
# with every other per-app module's fetch_data (lidarr / sonarr / …) — the
# deliberate per-app encapsulation pattern (CLAUDE.md). Content differs (app
# name, endpoint, fields), so it stays inline rather than coupling modules.
async def fetch_data(host_row: dict, chip: dict, *,
                     host_id: str, service_idx: int,
                     force: bool = False) -> dict:
    """Fetch Readarr's library summary for the expanded card.

    Returns ``{available, authors_total, monitored, missing, queue,
    disk_free_gb, disk_total_gb, disks, health_issues, version,
    fetched_at}``. Raises ``ValueError`` / ``RuntimeError`` when the chip's
    api_key is unset / the base URL won't resolve / the primary upstream call
    errors. The author list is load-bearing; the rest are tolerated."""
    api_key = (chip.get("api_key") or "").strip()
    now = time.time()
    base, hit = fetch_gate(host_row, chip, host_id, service_idx, _data_cache,
                           resolve_cache_ttl(chip, DEFAULT_CACHE_TTL_S), now, force,
                           credential=api_key, log_tag="readarr")
    if hit is not None:
        return hit
    author_url = base + "/api/v1/author"
    try:
        async with httpx.AsyncClient(verify=False, timeout=20.0,
                                     follow_redirects=True) as cli:
            r = await cli.get(author_url, headers=_headers(api_key))
            missing = await _missing_book_count(cli, base, api_key)
            queue = 0
            try:
                qr = await cli.get(base + "/api/v1/queue/status",
                                   headers=_headers(api_key))
                if qr.status_code == 200:
                    queue = safe_int((qr.json() or {}).get("totalCount"))
            except (httpx.HTTPError, OSError, ValueError, TypeError):
                queue = 0
            disks: list[dict] = []
            try:
                dr = await cli.get(base + "/api/v1/diskspace",
                                   headers=_headers(api_key))
                if dr.status_code == 200:
                    disks = _parse_disks(dr.json())
            except (httpx.HTTPError, OSError, ValueError, TypeError):
                disks = []
            health_issues = 0
            health_messages: list[str] = []
            try:
                hr = await cli.get(base + "/api/v1/health",
                                   headers=_headers(api_key))
                if hr.status_code == 200:
                    _hj = hr.json()
                    if isinstance(_hj, list):
                        health_issues = len(_hj)
                        health_messages = [
                            str(h.get("message") or "").strip()
                            for h in _hj[:4] if isinstance(h, dict) and h.get("message")]
            except (httpx.HTTPError, OSError, ValueError, TypeError):
                health_issues = 0
            # Cutoff-unmet — books that HAVE a file but below the quality
            # cutoff (distinct from "missing"). totalRecords from a 1-row page.
            cutoff_unmet = 0
            try:
                cr = await cli.get(base + "/api/v1/wanted/cutoff",
                                   headers=_headers(api_key),
                                   params={"pageSize": "1", "includeAuthor": "false"})
                if cr.status_code == 200:
                    cutoff_unmet = safe_int((cr.json() or {}).get("totalRecords"))
            except (httpx.HTTPError, OSError, ValueError, TypeError):
                cutoff_unmet = 0
            ver = await _fetch_version(cli, base, api_key)
    except (httpx.HTTPError, OSError) as e:  # noqa: BLE001
        print(f"[readarr] error: fetch host={host_id} url={author_url} "
              f"failed — {type(e).__name__}: {e}")
        raise RuntimeError(f"upstream fetch failed: {type(e).__name__}: {e}")
    if r.status_code != 200:
        print(f"[readarr] error: fetch host={host_id} url={r.request.url} "
              f"returned HTTP {r.status_code} (check the chip URL points at "
              f"the Readarr root, e.g. https://readarr.example.com)")
        if r.status_code in (401, 403):
            raise RuntimeError(f"upstream auth failed: HTTP {r.status_code} "
                               f"(check api_key) — {author_url}")
        raise RuntimeError(f"upstream returned HTTP {r.status_code} for {author_url}")
    try:
        authors = r.json()
    except (ValueError, TypeError):  # noqa: BLE001
        raise RuntimeError("upstream returned non-JSON")
    if not isinstance(authors, list):
        authors = []
    total = len(authors)
    monitored = 0
    books_have = 0
    books_total = 0
    size_bytes = 0.0
    for a in authors:
        if not isinstance(a, dict):
            continue
        if a.get("monitored"):
            monitored += 1
        st = as_dict(a.get("statistics"))
        books_have += safe_int(st.get("bookFileCount"))
        books_total += safe_int(st.get("bookCount"))
        size_bytes += safe_float(st.get("sizeOnDisk"))
    books_pct = int(round(books_have / books_total * 100)) if books_total > 0 else 0
    library_size_gb = round(size_bytes / _GIB, 1)
    disk_free_gb, disk_total_gb = _primary_disk(disks)
    # Books releasing TODAY — one cheap calendar call, the card's "Today" chip.
    calendar_today = await _servarr.fetch_today_calendar_count(
        host_row, chip, api_version="v1", app_label="Readarr")
    out: dict[str, Any] = {
        "available": True,
        "authors_total": total,
        "monitored": monitored,
        "missing": safe_int(missing),
        "cutoff_unmet": safe_int(cutoff_unmet),
        "calendar_today": safe_int(calendar_today),
        "books_have": books_have,
        "books_total": books_total,
        "books_pct": books_pct,
        "library_size_gb": library_size_gb,
        "queue": safe_int(queue),
        "disk_free_gb": disk_free_gb,
        "disk_total_gb": disk_total_gb,
        "disks": disks,
        "health_issues": safe_int(health_issues),
        "health_messages": health_messages,
        "version": ver,
        "fetched_at": int(now),
        # Library-growth + missing-backlog + disk-free-runway trend from the
        # shared servarr_samples retention table (drawer-only chart). Tolerated
        # on failure — the card renders fine without it.
        "trend": _safe_trend(host_id, service_idx),
    }
    print(f"[readarr] INFO fetched host={host_id} authors={total} books={books_total} "
          f"monitored={monitored} missing={out['missing']} cutoff_unmet={out['cutoff_unmet']} "
          f"have={books_have} size_gb={library_size_gb} queue={out['queue']} "
          f"mounts={len(disks)} disk_free_gb={disk_free_gb} "
          f"health={out['health_issues']}")
    _data_cache[cache_key(host_id, service_idx)] = (now, out)
    return out


def _safe_trend(host_id: str, service_idx: int) -> dict:
    """Best-effort library / backlog / disk trend from the shared *arr sampler.
    Returns the ``trend_summary`` dict, or ``{}`` on any failure (a missing
    sampler / empty table must never fail the card)."""
    try:
        from logic.apps import servarr_sampler  # noqa: PLC0415
        return servarr_sampler.trend_summary(str(host_id or ""), int(service_idx or 0))
    except Exception as e:  # noqa: BLE001
        print(f"[readarr] warning: trend_summary failed — {type(e).__name__}: {e}")
        return {}


def peek_latest(host_id: str, service_idx: int) -> Optional[dict]:
    """Cache-only peek (no upstream call) for the AI context's
    ``app_skills[].last``."""
    data = peek_cache(_data_cache, host_id, service_idx)
    if not isinstance(data, dict) or not data.get("available"):
        return None
    return {
        "authors_total": safe_int(data.get("authors_total")),
        "monitored": safe_int(data.get("monitored")),
        "missing": safe_int(data.get("missing")),
        "cutoff_unmet": safe_int(data.get("cutoff_unmet")),
        "books_have": safe_int(data.get("books_have")),
        "books_total": safe_int(data.get("books_total")),
        "library_size_gb": safe_float(data.get("library_size_gb")),
        "queue": safe_int(data.get("queue")),
        "disk_free_gb": safe_float(data.get("disk_free_gb")),
        "disks": as_list(data.get("disks")),
        "health_issues": safe_int(data.get("health_issues")),
        "version": data.get("version") or "",
        "fetched_at": safe_int(data.get("fetched_at")),
    }


# ---------------------------------------------------------------------------
# Skills
# ---------------------------------------------------------------------------
async def run_skill(skill_id: str, host_row: dict, chip: dict, *,
                    host_id: Optional[str] = None,
                    service_idx: Optional[int] = None,
                    arg: Optional[str] = None,
                    actor_username: Optional[str] = None, **_kw) -> dict:
    """Dispatch one of this app's SKILLS. Raises ValueError on an unknown
    skill id. ``arg`` carries the free-form author name. ``actor_username`` is
    the invoking user — used to render dates in their Settings -> Profile ->
    Formats date format."""
    if skill_id == "readarr_status":
        return await _status_skill(host_row, chip, host_id=host_id,
                                   service_idx=service_idx)
    if skill_id == "readarr_upcoming":
        return await _upcoming_skill(host_row, chip, host_id=host_id,
                                     actor_username=actor_username)
    if skill_id == "readarr_queue":
        return await _queue_skill(host_row, chip, host_id=host_id)
    if skill_id == "readarr_queue_delete":
        return await _servarr.queue_delete_skill(host_row, chip, arg=arg,
                                                 app_label="Readarr", api_version="v1",
                                                 host_id=host_id)
    if skill_id == "readarr_queue_blocklist_search":
        return await _servarr.queue_blocklist_search_skill(
            host_row, chip, arg=arg, app_label="Readarr", api_version="v1",
            parent_id_field="bookId", search_command="BookSearch",
            search_ids_field="bookIds", host_id=host_id)
    if skill_id == "readarr_author_info":
        return await _author_info_skill(host_row, chip, arg=arg, host_id=host_id)
    if skill_id == "readarr_add_author":
        return await _add_author_skill(host_row, chip, arg=arg, host_id=host_id)
    if skill_id == "readarr_remove_author":
        return await _remove_author_skill(host_row, chip, arg=arg, host_id=host_id)
    if skill_id == "readarr_search_author":
        return await _search_author_skill(host_row, chip, arg=arg, host_id=host_id)
    if skill_id == "readarr_search_missing":
        return await _command_skill(host_row, chip, command="MissingBookSearch",
                                    started_msg="🔍 Started a search for all monitored "
                                                "missing books on Readarr.",
                                    host_id=host_id)
    if skill_id == "readarr_refresh":
        return await _command_skill(host_row, chip, command="RefreshAuthor",
                                    started_msg="🔄 Started a library refresh & disk "
                                                "scan on Readarr.",
                                    host_id=host_id)
    if skill_id == "readarr_check_update":
        return await _servarr.check_update_skill(host_row, chip, app_label="Readarr",
                                                 api_version="v1", host_id=host_id,
                                                 actor_username=actor_username)
    if skill_id == "readarr_update":
        return await _servarr.app_update_skill(host_row, chip, app_label="Readarr",
                                               api_version="v1", host_id=host_id)
    raise ValueError(f"unknown skill: {skill_id!r}")


async def calendar_items(host_row: dict, chip: dict, *,
                         start: str, end: str) -> list[dict]:
    """Normalised upcoming-BOOK rows for the release-calendar widget — one row
    per Readarr ``/api/v1/calendar`` entry (``includeAuthor``) in the window:
    ``{date, title (book), subtitle (author), type, ...}``. Never raises
    (returns [] on any failure)."""
    raw = await _servarr.fetch_calendar(host_row, chip, api_version="v1",
                                        start=start, end=end, app_label="Readarr",
                                        extra_params={"includeAuthor": "true"})
    web = _servarr.resolve_base_url(host_row, chip)
    out: list[dict] = []
    for book in raw:
        if not isinstance(book, dict):
            continue
        when = str(book.get("releaseDate") or "")[:10]
        auth = as_dict(book.get("author"))
        author = str(auth.get("authorName") or "").strip()
        title = str(book.get("title") or "").strip()
        if not when or not (author or title):
            continue
        aslug = str(auth.get("titleSlug") or "").strip()
        app_path = (f"/author/{aslug}" if aslug else "")
        out.append({
            "date": when,
            "title": title or author,
            "subtitle": author if title else "",
            "type": "book",
            "service_slug": "readarr",
            "poster": _servarr.poster_proxy_path(book),
            "poster_proxy": True,
            "overview": _servarr.clamp_overview(book.get("overview") or auth.get("overview")),
            "runtime": 0,
            "time": "",
            # See radarr.calendar_items — app_path lets the widget rebuild the
            # deep link against a friendly reverse-proxy URL override.
            "app_url": ((web + app_path) if (web and app_path) else web),
            "app_path": app_path,
            "imdb_url": "",
            "tmdb_url": "",
        })
    return out


def _norm_name(s: Any) -> str:
    """Normalise an author name / query for matching: lowercase, collapse
    whitespace. (Authors have no year suffix, unlike movies / series.)"""
    import re as _re
    return _re.sub(r"\s+", " ", str(s or "").strip().lower()).strip()


def _find_in_library(authors: Any, query: str) -> Optional[dict]:
    """Find an author in the library list by foreignAuthorId (Goodreads id
    exact), then normalised exact ``authorName``, then BIDIRECTIONAL substring.
    Returns the author dict or ``None``."""
    if not isinstance(authors, list):
        return None
    raw = (query or "").strip()
    q = _norm_name(raw)
    if not q:
        return None
    for a in authors:
        if isinstance(a, dict) and str(a.get("foreignAuthorId") or "").strip().lower() == q:
            return a
    for a in authors:
        if isinstance(a, dict) and _norm_name(a.get("authorName")) == q:
            return a
    for a in authors:
        if not isinstance(a, dict):
            continue
        t = _norm_name(a.get("authorName"))
        if t and (q in t or t in q):
            return a
    return None


# noinspection DuplicatedCode
async def _status_skill(host_row: dict, chip: dict, *,
                        host_id: Optional[str] = None,
                        service_idx: Optional[int] = None) -> dict:
    """Read-only: live-fetch the current library summary (force-bypasses the
    cache). Never raises."""
    print(f"[readarr] INFO readarr_status host={host_id} svc_idx={service_idx} (live fetch)")
    try:
        data = await fetch_data(host_row, chip,
                                host_id=str(host_id or ""),
                                service_idx=int(service_idx or 0),
                                force=True)
    except (ValueError, RuntimeError) as e:
        print(f"[readarr] warning: readarr_status host={host_id} could not fetch — {e}")
        return {"ok": False, "detail": str(e), "status": 0}
    total = safe_int(data.get("authors_total"))
    monitored = safe_int(data.get("monitored"))
    missing = safe_int(data.get("missing"))
    cutoff_unmet = safe_int(data.get("cutoff_unmet"))
    books_have = safe_int(data.get("books_have"))
    books_total = safe_int(data.get("books_total"))
    books_pct = safe_int(data.get("books_pct"))
    library_size_gb = safe_float(data.get("library_size_gb"))
    queue = safe_int(data.get("queue"))
    free_gb = safe_float(data.get("disk_free_gb"))
    health = safe_int(data.get("health_issues"))
    disks = as_list(data.get("disks"))
    health_messages = as_list(data.get("health_messages"))
    lines = [
        f"📚 Authors: {total:,}",
        f"📁 Monitored: {monitored:,}",
    ]
    if books_total:
        lines.append(f"📖 Books: {books_have:,} / {books_total:,} ({books_pct}%)")
    lines.append(f"{'❓' if missing else '✅'} Missing books: {missing:,}")
    if cutoff_unmet:
        lines.append(f"📉 Below quality cutoff: {cutoff_unmet:,}")
    if library_size_gb > 0:
        lines.append(f"📦 Library size: {_fmt_size_gib(library_size_gb)}")
    lines.append(f"⬇️ Downloading: {queue:,}")
    # Compact storage summary for the text surfaces (AI / Telegram); the web
    # drawer renders the per-mount CARDS from the result's `disks` field.
    storage_line = _storage_summary_line(disks, free_gb)
    if storage_line:
        lines.append(storage_line)
    lines.append(f"{'⚠️' if health else '✅'} Health issues: {health:,}")
    for msg in health_messages[:3]:
        if msg:
            lines.append(f"   • {msg}")
    return {
        "ok": True,
        "detail": "\n".join(lines),
        "status": 200,
        "authors_total": total, "monitored": monitored, "missing": missing,
        "cutoff_unmet": cutoff_unmet, "books_have": books_have,
        "books_total": books_total, "library_size_gb": library_size_gb,
        "queue": queue, "disk_free_gb": free_gb, "disks": disks,
        "health_issues": health, "health_messages": health_messages,
    }


# noinspection DuplicatedCode
async def _upcoming_skill(host_row: dict, chip: dict, *,
                          host_id: Optional[str] = None,
                          actor_username: Optional[str] = None) -> dict:
    """Read-only: the next ~30 days of upcoming book releases from
    ``/api/v1/calendar``. Never raises."""
    api_key, base, err = _resolve_skill_target(host_row, chip)
    if err:
        return err
    from datetime import datetime, timedelta, timezone
    now = datetime.now(timezone.utc)
    params = {
        "start": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "end": (now + timedelta(days=30)).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "unmonitored": "false", "includeAuthor": "true",
    }
    print(f"[readarr] INFO readarr_upcoming host={host_id} (live fetch)")
    try:
        async with httpx.AsyncClient(verify=False, timeout=15.0,
                                     follow_redirects=True) as cli:
            r = await cli.get(base + "/api/v1/calendar",
                              headers=_headers(api_key), params=params)
    except (httpx.HTTPError, OSError) as e:  # noqa: BLE001
        return {"ok": False, "status": 0, "detail": f"calendar fetch failed: {type(e).__name__}: {e}"}
    if r.status_code in (401, 403):
        return {"ok": False, "status": r.status_code, "detail": "auth failed (check api_key)"}
    if r.status_code != 200:
        return {"ok": False, "status": r.status_code, "detail": f"HTTP {r.status_code}"}
    try:
        items = r.json()
    except (ValueError, TypeError):
        return {"ok": False, "status": 502, "detail": "non-JSON from upstream"}
    if not isinstance(items, list):
        items = []
    lines = []
    for bk in items[:12]:
        if not isinstance(bk, dict):
            continue
        au = as_dict(bk.get("author"))
        author = str(au.get("authorName") or "?").strip()
        book = str(bk.get("title") or "?").strip()
        when = str(bk.get("releaseDate") or "")[:10]
        when_fmt = _servarr.fmt_release_date(when, actor_username)
        lines.append(f"• {author} — {book}" + (f" ({when_fmt})" if when_fmt else ""))
    if not lines:
        return {"ok": True, "status": 200,
                "detail": "📚 No book releases in the next 30 days."}
    return {"ok": True, "status": 200,
            "detail": "📚 Upcoming books (next 30 days):\n" + "\n".join(lines)}


# noinspection DuplicatedCode
async def _queue_skill(host_row: dict, chip: dict, *,
                       host_id: Optional[str] = None) -> dict:
    """Read-only: what's currently downloading + progress from
    ``/api/v1/queue``. Never raises."""
    api_key, base, err = _resolve_skill_target(host_row, chip)
    if err:
        return err
    print(f"[readarr] INFO readarr_queue host={host_id} (live fetch)")
    try:
        async with httpx.AsyncClient(verify=False, timeout=20.0,
                                     follow_redirects=True) as cli:
            r = await cli.get(base + "/api/v1/queue", headers=_headers(api_key),
                              params={"pageSize": "20", "includeAuthor": "true",
                                      "includeBook": "true"})
            if r.status_code in (401, 403):
                return {"ok": False, "status": r.status_code, "detail": "auth failed (check api_key)"}
            if r.status_code != 200:
                return {"ok": False, "status": r.status_code, "detail": f"HTTP {r.status_code}"}
            try:
                body = r.json()
            except (ValueError, TypeError):
                return {"ok": False, "status": 502, "detail": "non-JSON from upstream"}
            records = body.get("records") if isinstance(body, dict) else None
            records = records if isinstance(records, list) else []
            if not records:
                return {"ok": True, "status": 200, "detail": "⬇️ Nothing is downloading right now."}
            lines = []
            # Structured rows for the SPA's rich skill-result card — SAME
            # {title, subtitle, poster, progress} + per-row delete contract the
            # rest of the *arr family's download queues use. The queue record
            # embeds the `book` + `author` (includeBook / includeAuthor=true);
            # the poster is resolved most-reliable-first (re-fetch + Open Library
            # fallback) because the embed frequently trims the book's images.
            rich: list[dict] = []
            for q in records[:12]:
                if not isinstance(q, dict):
                    continue
                au = as_dict(q.get("author"))
                bk = as_dict(q.get("book"))
                author = str(au.get("authorName") or "?").strip()
                book = str(bk.get("title") or q.get("title") or "").strip()
                total = safe_float(q.get("size"))
                left = safe_float(q.get("sizeleft"))
                pct = int(round((1 - left / total) * 100)) if total > 0 else 0
                st = str(q.get("status") or "").strip().lower()
                label = f"{author}" + (f" — {book}" if book else "")
                lines.append(f"• {label} — {pct}%"
                             + (f" ({st})" if st and st != "downloading" else ""))
                row: "dict[str, Any]" = {
                    "title": label,
                    "subtitle": f"{pct}%" + (f" · {st}" if st and st != "downloading" else ""),
                    "poster": await _book_poster(cli, base, api_key, bk, au),
                    "poster_proxy": True,
                    "progress": pct}
                qid = safe_int(q.get("id"))
                if qid:
                    # Remove-from-queue + blocklist-&-search (the stuck-grab
                    # fix). The blocklist arg carries the parent bookId so the
                    # re-search needs no extra queue lookup.
                    pid = safe_int(q.get("bookId"))
                    row["row_actions"] = [
                        {"skill_id": "readarr_queue_delete", "arg": str(qid),
                         "icon": "trash-2", "destructive": True,
                         "confirm_i18n": "apps.readarr.queue_delete_confirm",
                         "title_i18n": "apps.readarr.queue_delete_title"},
                        {"skill_id": "readarr_queue_blocklist_search",
                         "arg": f"{qid}:{pid}", "icon": "refresh-cw",
                         "destructive": True,
                         "confirm_i18n": "apps.readarr.blocklist_search_confirm",
                         "title_i18n": "apps.readarr.blocklist_search_title"},
                    ]
                rich.append(row)
    except (httpx.HTTPError, OSError) as e:  # noqa: BLE001
        return {"ok": False, "status": 0, "detail": f"queue fetch failed: {type(e).__name__}: {e}"}
    if rich:
        _b0 = as_dict(records[0].get("book"))
        print(f"[readarr] INFO queue posters host={host_id} "
              f"first_poster={rich[0].get('poster') or 'none'!r} "
              f"book_images=[{_servarr.image_debug(_b0)}]")
    return {"ok": True, "status": 200,
            "detail": f"⬇️ Downloading ({len(records)}):\n" + "\n".join(lines),
            "count": len(records), "count_i18n": "apps.skills.downloading_count",
            "items": rich}


# noinspection DuplicatedCode
async def _readarr_lookup(cli: httpx.AsyncClient, base: str, api_key: str,
                          query: str) -> Optional[dict]:
    """Resolve an author via Readarr's Goodreads-backed lookup
    (``/api/v1/author/lookup?term=<name>``). Returns the author dict (which
    carries ``id > 0`` when already in the library) or ``None``."""
    q = (query or "").strip()
    try:
        r = await cli.get(base + "/api/v1/author/lookup",
                          headers=_headers(api_key), params={"term": q})
        if r.status_code != 200:
            return None
        arr = r.json()
    except (httpx.HTTPError, OSError, ValueError, TypeError):
        return None
    if not isinstance(arr, list):
        return None
    for a in arr:
        if isinstance(a, dict) and a.get("foreignAuthorId"):
            return a
    return None


# noinspection DuplicatedCode
async def _author_info_skill(host_row: dict, chip: dict, *,
                             arg: Optional[str] = None,
                             host_id: Optional[str] = None) -> dict:
    """Read-only: is ``<author>`` in the library, monitored, how complete? Looks
    it up in ``/api/v1/author``. Never raises."""
    query = (arg or "").strip()
    if not query:
        return {"ok": False, "status": 0, "detail": "no author name given — which author?"}
    api_key, base, err = _resolve_skill_target(host_row, chip)
    if err:
        return err
    print(f"[readarr] INFO readarr_author_info host={host_id} query={query!r}")
    try:
        async with httpx.AsyncClient(verify=False, timeout=20.0,
                                     follow_redirects=True) as cli:
            r = await cli.get(base + "/api/v1/author", headers=_headers(api_key))
    except (httpx.HTTPError, OSError) as e:  # noqa: BLE001
        return {"ok": False, "status": 0, "detail": f"lookup failed: {type(e).__name__}: {e}"}
    if r.status_code in (401, 403):
        return {"ok": False, "status": r.status_code, "detail": "auth failed (check api_key)"}
    if r.status_code != 200:
        return {"ok": False, "status": r.status_code, "detail": f"HTTP {r.status_code}"}
    try:
        authors = r.json()
    except (ValueError, TypeError):
        return {"ok": False, "status": 502, "detail": "non-JSON from upstream"}
    a = _find_in_library(authors, query)
    if not a:
        return {"ok": True, "status": 200,
                "detail": f"❓ “{query}” is not in your Readarr library. (Ask me to add it.)"}
    label = str(a.get("authorName") or query)
    monitored = bool(a.get("monitored"))
    stats = as_dict(a.get("statistics"))
    have = safe_int(stats.get("bookFileCount"))
    total_books = safe_int(stats.get("bookCount"))
    pct = safe_int(stats.get("percentOfBooks"))
    size_gib = safe_float(stats.get("sizeOnDisk")) / _GIB
    lines = [
        f"📚 {label}",
        "📁 Monitored" if monitored else "🚫 Not monitored",
        f"📖 Books: {have:,} / {total_books:,}" + (f" ({pct}%)" if total_books else ""),
    ]
    if size_gib > 0:
        lines.append(f"💾 {_fmt_size_gib(size_gib)}")
    return {"ok": True, "status": 200, "detail": "\n".join(lines)}


# noinspection DuplicatedCode
async def _add_author_skill(host_row: dict, chip: dict, *,
                            arg: Optional[str] = None,
                            host_id: Optional[str] = None) -> dict:
    """Action skill: add an author BY NAME. Looks it up, resolves a quality
    profile + a metadata profile (Readarr-required) + the most-free root folder,
    then POSTs ``/api/v1/author`` with ``addOptions.searchForMissingBooks``.
    Already-in-library is a friendly ok. Never raises."""
    query = (arg or "").strip()
    if not query:
        return {"ok": False, "status": 0,
                "detail": "no author name given — tell me which author to add"}
    api_key, base, err = _resolve_skill_target(host_row, chip)
    if err:
        return err
    label = query
    try:
        async with httpx.AsyncClient(verify=False, timeout=25.0,
                                     follow_redirects=True) as cli:
            author = await _readarr_lookup(cli, base, api_key, query)
            if not author:
                return {"ok": False, "status": 404,
                        "detail": f"no author found matching “{query}”"}
            label = str(author.get("authorName") or query)
            if safe_int(author.get("id")) > 0:
                return {"ok": True, "status": 200,
                        "detail": f"📚 {label} is already in your Readarr library."}
            qp = await cli.get(base + "/api/v1/qualityprofile", headers=_headers(api_key))
            profiles = qp.json() if qp.status_code == 200 else []
            if not isinstance(profiles, list) or not profiles:
                return {"ok": False, "status": 0,
                        "detail": "no quality profile configured in Readarr"}
            profile_id = safe_int((profiles[0] or {}).get("id"))
            # Readarr REQUIRES a metadata profile on add (Radarr / Sonarr don't).
            mp = await cli.get(base + "/api/v1/metadataprofile", headers=_headers(api_key))
            mprofiles = mp.json() if mp.status_code == 200 else []
            if not isinstance(mprofiles, list) or not mprofiles:
                return {"ok": False, "status": 0,
                        "detail": "no metadata profile configured in Readarr"}
            metadata_id = safe_int((mprofiles[0] or {}).get("id"))
            rf = await cli.get(base + "/api/v1/rootfolder", headers=_headers(api_key))
            folders = rf.json() if rf.status_code == 200 else []
            folders = [f for f in folders if isinstance(f, dict) and f.get("path")] \
                if isinstance(folders, list) else []
            if not folders:
                return {"ok": False, "status": 0,
                        "detail": "no root folder configured in Readarr"}
            best = max(folders, key=lambda f: safe_float(f.get("freeSpace")))
            root_path = str(best.get("path") or "").strip()
            payload = dict(author)
            payload.update({
                "qualityProfileId": profile_id,
                "metadataProfileId": metadata_id,
                "rootFolderPath": root_path,
                "monitored": True,
                "addOptions": {"searchForMissingBooks": True, "monitor": "all"},
            })
            print(f"[readarr] INFO readarr_add_author host={host_id} name={label!r} "
                  f"profile={profile_id} metadata={metadata_id} root={root_path!r}")
            pr = await cli.post(base + "/api/v1/author",
                                headers=_headers(api_key), json=payload)
    except (httpx.HTTPError, OSError) as e:  # noqa: BLE001
        return {"ok": False, "status": 0, "detail": f"add failed: {type(e).__name__}: {e}"}
    if pr.status_code in (200, 201):
        return {"ok": True, "status": pr.status_code,
                "detail": f"📚 Added {label} to Readarr — searching for books now."}
    if pr.status_code in (401, 403):
        return {"ok": False, "status": pr.status_code, "detail": "auth failed (check api_key)"}
    try:
        _body = (pr.text or "")[:200]
    except (ValueError, TypeError):
        _body = ""
    if pr.status_code == 400 and "exist" in _body.lower():
        return {"ok": True, "status": 200,
                "detail": f"📚 {label} is already in your Readarr library."}
    return {"ok": False, "status": pr.status_code,
            "detail": f"Readarr returned HTTP {pr.status_code} adding {label}"
                      + (f" — {_body}" if _body else "")}


# noinspection DuplicatedCode
async def _remove_author_skill(host_row: dict, chip: dict, *,
                               arg: Optional[str] = None,
                               host_id: Optional[str] = None) -> dict:
    """DESTRUCTIVE action skill: remove an author BY NAME from the Readarr
    library. Files on disk are KEPT (``deleteFiles=false``). Never raises."""
    query = (arg or "").strip()
    if not query:
        return {"ok": False, "status": 0,
                "detail": "no author name given — tell me which author to remove"}
    api_key, base, err = _resolve_skill_target(host_row, chip)
    if err:
        return err
    try:
        async with httpx.AsyncClient(verify=False, timeout=20.0,
                                     follow_redirects=True) as cli:
            r = await cli.get(base + "/api/v1/author", headers=_headers(api_key))
            if r.status_code in (401, 403):
                return {"ok": False, "status": r.status_code, "detail": "auth failed (check api_key)"}
            if r.status_code != 200:
                return {"ok": False, "status": r.status_code, "detail": f"HTTP {r.status_code}"}
            try:
                authors = r.json()
            except (ValueError, TypeError):
                return {"ok": False, "status": 502, "detail": "non-JSON from upstream"}
            a = _find_in_library(authors, query)
            if not a:
                return {"ok": False, "status": 404,
                        "detail": f"no author matching “{query}” in your Readarr library"}
            aid = safe_int(a.get("id"))
            label = str(a.get("authorName") or query)
            print(f"[readarr] INFO readarr_remove_author host={host_id} id={aid} name={label!r}")
            dr = await cli.delete(base + f"/api/v1/author/{aid}",
                                  headers=_headers(api_key),
                                  params={"deleteFiles": "false",
                                          "addImportListExclusion": "false"})
    except (httpx.HTTPError, OSError) as e:  # noqa: BLE001
        return {"ok": False, "status": 0, "detail": f"remove failed: {type(e).__name__}: {e}"}
    if dr.status_code in (200, 202, 204):
        return {"ok": True, "status": 200,
                "detail": f"🗑️ Removed {label} from Readarr (files on disk kept)."}
    if dr.status_code in (401, 403):
        return {"ok": False, "status": dr.status_code, "detail": "auth failed (check api_key)"}
    return {"ok": False, "status": dr.status_code,
            "detail": f"Readarr returned HTTP {dr.status_code} removing {label}"}


# noinspection DuplicatedCode
async def _search_author_skill(host_row: dict, chip: dict, *,
                               arg: Optional[str] = None,
                               host_id: Optional[str] = None) -> dict:
    """Action skill: trigger a release search for ONE author already in the
    library (``POST /api/v1/command {name: AuthorSearch, authorId: id}``). Looks
    the author up by name first; not-in-library is a friendly hint to add it.
    Non-destructive (queues a background search). Never raises."""
    query = (arg or "").strip()
    if not query:
        return {"ok": False, "status": 0,
                "detail": "no author name given — which author should I search for?"}
    api_key, base, err = _resolve_skill_target(host_row, chip)
    if err:
        return err
    try:
        async with httpx.AsyncClient(verify=False, timeout=20.0,
                                     follow_redirects=True) as cli:
            r = await cli.get(base + "/api/v1/author", headers=_headers(api_key))
            if r.status_code in (401, 403):
                return {"ok": False, "status": r.status_code, "detail": "auth failed (check api_key)"}
            if r.status_code != 200:
                return {"ok": False, "status": r.status_code, "detail": f"HTTP {r.status_code}"}
            try:
                authors = r.json()
            except (ValueError, TypeError):
                return {"ok": False, "status": 502, "detail": "non-JSON from upstream"}
            a = _find_in_library(authors, query)
            if not a:
                return {"ok": True, "status": 200,
                        "detail": f"❓ “{query}” is not in your Readarr library yet. "
                                  f"(Ask me to add it — that searches automatically.)"}
            aid = safe_int(a.get("id"))
            label = str(a.get("authorName") or query)
            print(f"[readarr] INFO readarr_search_author host={host_id} id={aid} name={label!r}")
            pr = await cli.post(base + "/api/v1/command",
                                headers=_headers(api_key),
                                json={"name": "AuthorSearch", "authorId": aid})
    except (httpx.HTTPError, OSError) as e:  # noqa: BLE001
        return {"ok": False, "status": 0, "detail": f"search failed: {type(e).__name__}: {e}"}
    if pr.status_code in (200, 201):
        return {"ok": True, "status": pr.status_code,
                "detail": f"🔍 Started a book search for {label} on Readarr."}
    if pr.status_code in (401, 403):
        return {"ok": False, "status": pr.status_code, "detail": "auth failed (check api_key)"}
    return {"ok": False, "status": pr.status_code,
            "detail": f"Readarr returned HTTP {pr.status_code} searching for {label}"}
