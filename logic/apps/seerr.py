"""Seerr (Overseerr / Jellyseerr) per-app module.

Encapsulates everything Seerr-specific so the route layer
(``main_pkg/apps_routes.py``) stays generic. Public surface mirrors the
``bazarr.py`` shape:

    SLUGS               — catalog slugs this module handles ("seerr").
    requires_api_key()  — True (Seerr authenticates via the X-Api-Key header).
    resolve_base_url(host_row, chip) -> str   (shared helper)
    test_credential(host_row, chip, candidate_key) -> dict
    fetch_data(host_row, chip, *, host_id, service_idx, force) -> dict
    peek_latest(host_id, service_idx) -> dict | None    (AI context)
    SKILLS / run_skill  — status (read-only) + suggest-a-movie (read-only)
                          + request-a-movie (takes a free-form ``arg``).

Seerr (Overseerr or its Jellyfin fork Jellyseerr) is a media-request
manager in front of Sonarr / Radarr. The most impactful, lowest-cost
endpoint is ``GET /api/v1/request/count`` — it returns the request queue
breakdown Seerr shows on its own dashboard:

    pending     — requests awaiting approval
    approved    — approved, handed to Radarr/Sonarr
    processing  — currently downloading
    available   — fulfilled (now in the library)
    declined    — rejected
    total       — all requests

``GET /api/v1/issue/count`` adds the open-issue count (reported problems);
``GET /api/v1/status`` adds the Seerr version (both tolerated-on-failure).

AI / Telegram skills (the headline feature)
-------------------------------------------
* ``seerr_status``         — read-only request-queue summary.
* ``seerr_request_movie``  — request a movie BY TITLE (or by a numeric
  TMDB id). Resolves the title via Seerr's own ``/api/v1/search`` (which
  is TMDB-backed), picks the top movie hit, and POSTs ``/api/v1/request``.
  Takes a free-form ``arg`` (the title / id) threaded from the AI's
  ``ACTION_DATA`` / the Telegram slash command.
* ``seerr_suggest_movie``  — suggest a RANDOM movie the user can then
  request. Pulls from TMDB directly when a TMDB API key is configured on
  the chip (genuine variety via ``/discover/movie`` on a random page),
  else falls back to Seerr's own ``/api/v1/discover/movies``. Returns a
  poster image URL (built from the configured TMDB image base) so the
  chat can preview it.

Auth model: every authenticated Seerr endpoint takes the ``X-Api-Key``
header (Settings → General → API Key in Seerr). ``/api/v1/status`` is
unauthenticated; the credential probe hits the auth-required
``/api/v1/request/count`` so a bad key fails loudly. Single-instance app
(NOT fleet) — one card per pinned chip.

TMDB config is per-chip (so the app stays self-contained — no global
settings surface): ``tmdb_api_key`` (secret), ``tmdb_base_url`` (default
``https://api.themoviedb.org``), ``tmdb_image_base_url`` (default
``https://image.tmdb.org/t/p``). A classic v3 key is sent as the
``api_key`` query param; a v4 read-access token (JWT, starts ``eyJ``) is
sent as ``Authorization: Bearer``.

Upstream API reference: <seerr-host>/api-docs. Endpoints used:
    GET  /api/v1/status                       — version (test + card footnote)
    GET  /api/v1/request/count                — queue breakdown (credential probe + card)
    GET  /api/v1/issue/count                   — open-issue count (tolerated)
    GET  /api/v1/search?query=<t>              — TMDB-backed title search
    GET  /api/v1/discover/movies?page=<n>      — discover fallback for suggestions
    POST /api/v1/request                       — create a media request
    GET  {tmdb}/3/discover/movie               — random-movie suggestions
"""
from __future__ import annotations

import asyncio
import json
import random
import re
import time
from typing import Any, Collection, Optional

import httpx

from logic.apps._common import (
    cache_key, fetch_gate, peek_cache, resolve_cache_ttl,
    resolve_credential_target)
from logic.coerce import safe_float, safe_int

# Catalog template slugs handled by this module.
SLUGS: tuple[str, ...] = ("seerr",)

# Read-only AI / drawer skills + the request-a-movie action skill.
# ``arg``-carrying skills declare ``arg: True`` (+ an ``arg_hint``) so the
# prompt layer tells the model to supply the free-form argument and the
# dispatch surfaces thread it through to ``run_skill``.
SKILLS: tuple[dict, ...] = (
    {
        "id": "seerr_status",
        "name": "Seerr status",
        "ai_phrases": ("seerr status, overseerr status, jellyseerr status, "
                       "media requests, how many requests are pending, "
                       "request queue, what's downloading, pending approvals, "
                       "how many movies are available"),
        "destructive": False,
    },
    {
        "id": "seerr_request_movie",
        "name": "Request a movie",
        "ai_phrases": ("request a movie, request <title>, add <title> to "
                       "the library, can you get <title>, download <title>, "
                       "ask seerr for <title>, request the movie <title>, "
                       "i want to watch <title>"),
        "destructive": False,
        "arg": True,
        "arg_hint": "the movie title (or a numeric TMDB id)",
    },
    {
        "id": "seerr_suggest_movie",
        "name": "Suggest a movie",
        "ai_phrases": ("suggest a movie, recommend a movie, what should i "
                       "watch, random movie, pick a movie for me, suggest "
                       "something to watch, give me a movie recommendation"),
        "destructive": False,
    },
    {
        "id": "seerr_set_filter",
        "name": "Set a suggestion filter",
        "ai_phrases": ("don't suggest movies from <country>, exclude <country> "
                       "movies, no <country> films, only suggest movies rated "
                       "above <n>, minimum rating <n>, don't suggest <genre> "
                       "movies, exclude <genre>, only <genre> movies, allow "
                       "<country> again, remove the <country> filter, clear my "
                       "movie filters, reset suggestion filters, add a filter, "
                       "remove a filter, filter movies by country/rating/genre"),
        "destructive": False,
        "arg": True,
        "arg_hint": ("a filter directive: 'exclude country France' / 'allow "
                     "country France' / 'min rating 7' / 'exclude genre horror' "
                     "/ 'only genre action' / 'allow genre horror' / 'clear'. "
                     "A country directive may list several at once joined by "
                     "'and'/commas, e.g. 'exclude country Spain and Denmark' — "
                     "keep them in ONE directive, don't drop any"),
    },
    {
        "id": "seerr_show_filters",
        "name": "Show suggestion filters",
        "ai_phrases": ("show my movie filters, what are my suggestion filters, "
                       "list my filters, what movies am i excluding, my seerr "
                       "filters"),
        "destructive": False,
    },
)

# Per-(host_id, service_idx) data cache for the expanded card. Default TTL
# overridable per chip via the editor's `cache_ttl` field. 30s default —
# the request counts move slowly.
DEFAULT_CACHE_TTL_S = 30
_data_cache: dict[str, tuple[float, dict]] = {}

# TMDB defaults — used when the chip leaves the field blank. The API base
# conventionally includes the ``/3`` version segment (matching the value
# operators copy from other projects, e.g. `https://api.themoviedb.org/3`).
# `_tmdb_api_url` is tolerant of EITHER form (with or without the trailing
# `/3`) so a host-root base works too. The image base + a width segment
# build a poster URL: `{image_base}/w500{path}`.
_TMDB_BASE_DEFAULT = "https://api.themoviedb.org/3"
_TMDB_IMAGE_BASE_DEFAULT = "https://image.tmdb.org/t/p"
_TMDB_POSTER_SIZE = "w500"
# Random-page ceiling for the discover suggestion. Popular movies are sorted
# popularity-descending, so page 1 is the most mainstream (= most likely the
# operator already has it). Drawing from a WIDE page range surfaces titles
# deeper in the catalogue that are far less likely to be already in the
# library — the `vote_count.gte` filter keeps them watchable, not obscure.
_TMDB_DISCOVER_MAX_PAGE = 50

# TMDB movie genre id -> name. These ids are stable (TMDB has used them for
# years) so we map locally instead of an extra /genre/movie/list call. Both
# TMDB discover (`genre_ids`) and Seerr discover (`genreIds`) return ids.
_TMDB_GENRES = {
    28: "Action", 12: "Adventure", 16: "Animation", 35: "Comedy",
    80: "Crime", 99: "Documentary", 18: "Drama", 10751: "Family",
    14: "Fantasy", 36: "History", 27: "Horror", 10402: "Music",
    9648: "Mystery", 10749: "Romance", 878: "Science Fiction",
    10770: "TV Movie", 53: "Thriller", 10752: "War", 37: "Western",
}
# How many genres to show per suggestion (the first N from TMDB's ordered list).
_GENRE_DISPLAY_MAX = 3


def _genre_names(genre_ids: Any) -> list[str]:
    """Map a list of TMDB genre ids to display names (top
    ``_GENRE_DISPLAY_MAX``, in source order, unknown ids skipped). ``Any``
    arg type — callers pass raw ``dict.get(...)`` values (``Any | None``);
    the isinstance guard below handles a non-list at runtime."""
    if not isinstance(genre_ids, list):
        return []
    out = []
    for gid in genre_ids:
        name = _TMDB_GENRES.get(safe_int(gid))
        if name and name not in out:
            out.append(name)
        if len(out) >= _GENRE_DISPLAY_MAX:
            break
    return out


# ---------------------------------------------------------------------------
# Per-user suggestion filters. Stored per user in users.ui_prefs under
# `seerr_suggest_filters` (so it's database-backed + per-user). The AI / the
# user manage them via the seerr_set_filter skill; seerr_suggest_movie applies
# them (min_rating + genres pushed to the TMDB discover query; countries +
# genres also enforced client-side).
# ---------------------------------------------------------------------------
_GENRE_NAME_TO_ID = {name.lower(): gid for gid, name in _TMDB_GENRES.items()}
_GENRE_SYNONYMS = {
    "sci-fi": "science fiction", "scifi": "science fiction",
    "sci fi": "science fiction", "romcom": "romance",
    "rom-com": "romance", "docu": "documentary", "doc": "documentary",
    "kids": "family", "children": "family",
}
# Country synonym → canonical token (for case-insensitive exclude matching;
# the movie's production-country names are shortened for display, so both the
# user's input and the movie's countries normalise through this).
_COUNTRY_SYNONYMS = {
    "usa": "usa", "us": "usa", "u.s.": "usa", "u.s.a.": "usa",
    "america": "usa", "united states": "usa",
    "united states of america": "usa", "the united states": "usa",
    "uk": "uk", "u.k.": "uk", "united kingdom": "uk", "britain": "uk",
    "great britain": "uk", "england": "uk",
    "korea": "south korea", "republic of korea": "south korea",
    "uae": "uae", "united arab emirates": "uae",
}

_DEFAULT_FILTERS: dict = {
    "exclude_countries": [], "min_rating": 0.0,
    "exclude_genres": [], "include_genres": [],
}


def _canonical_genre(name: str) -> Optional[str]:
    """Resolve a user-typed genre to its canonical TMDB name (handles a few
    synonyms), or ``None`` when unknown."""
    n = (name or "").strip().lower()
    n = _GENRE_SYNONYMS.get(n, n)
    gid = _GENRE_NAME_TO_ID.get(n)
    return _TMDB_GENRES.get(gid) if gid else None


def _norm_country(name: str) -> str:
    """Normalise a country name to a canonical lowercase token for
    case-insensitive exclude matching (USA / United States → usa, etc.)."""
    n = (name or "").strip().lower()
    return _COUNTRY_SYNONYMS.get(n, n)


# Conjunction / list separators for MULTI-country directives. A user (or
# the AI) can say "exclude country Spain and Denmark" or "exclude country
# Spain, Denmark & France" — historically only the FIRST country survived
# because the whole tail was taken as ONE country token (operator-
# reported: "exclude movies from Spain and Denmark" excluded Spain only).
# Splitting on these separators lets the country branches add every named
# country in a single directive. Multi-word synonyms that legitimately
# contain "and" (e.g. "Trinidad and Tobago") are protected by stashing
# them before the split — see `_split_country_list`.
_COUNTRY_LIST_SEP = re.compile(r"\s*(?:,|;|/|&|\+|\band\b)\s*", flags=re.IGNORECASE)
# Country names whose canonical form contains a separator word ("and").
# Stashed to a placeholder before the conjunction split so they don't get
# torn apart, then restored. Kept small + explicit (the common cases).
_COUNTRY_PROTECTED = (
    "trinidad and tobago",
    "antigua and barbuda",
    "saint kitts and nevis",
    "bosnia and herzegovina",
    "sao tome and principe",
)


def _split_country_list(val: str) -> list[str]:
    """Split a free-text country phrase into individual, de-duplicated
    country names (Title-cased). Handles "Spain and Denmark",
    "Spain, Denmark & France", etc. Protects the handful of country
    names that legitimately contain "and". Empty tokens are dropped."""
    if not val:
        return []
    work = val
    holders: dict[str, str] = {}
    low = work.lower()
    for i, name in enumerate(_COUNTRY_PROTECTED):
        if name in low:
            token = f"\x00c{i}\x01"
            # Case-insensitive replace of the protected name with a token.
            work = re.sub(re.escape(name), token, work, flags=re.IGNORECASE)
            holders[token] = name.title()
            low = work.lower()
    out: list[str] = []
    seen: set[str] = set()
    for piece in _COUNTRY_LIST_SEP.split(work):
        s = piece.strip()
        if s in holders:
            s = holders[s]
        else:
            s = s.title()
        if s and s.lower() not in seen:
            seen.add(s.lower())
            out.append(s)
    return out


def _join_human(items: list[str]) -> str:
    """Join a list into operator-readable prose: ``[a]`` → "a",
    ``[a, b]`` → "a and b", ``[a, b, c]`` → "a, b, and c"."""
    items = [i for i in items if i]
    if not items:
        return ""
    if len(items) == 1:
        return items[0]
    if len(items) == 2:
        return f"{items[0]} and {items[1]}"
    return ", ".join(items[:-1]) + f", and {items[-1]}"


def _coerce_filters(raw: Any) -> dict:
    """Normalise a stored / partial filters blob into the canonical shape."""
    f = dict(_DEFAULT_FILTERS)
    f["exclude_countries"] = []
    f["exclude_genres"] = []
    f["include_genres"] = []
    if isinstance(raw, dict):
        f["exclude_countries"] = [str(x).strip() for x in (raw.get("exclude_countries") or [])
                                  if isinstance(x, str) and x.strip()]
        f["exclude_genres"] = [str(x).strip() for x in (raw.get("exclude_genres") or [])
                               if isinstance(x, str) and x.strip()]
        f["include_genres"] = [str(x).strip() for x in (raw.get("include_genres") or [])
                               if isinstance(x, str) and x.strip()]
        f["min_rating"] = max(0.0, min(10.0, safe_float(raw.get("min_rating"))))
    return f


def _load_user_filters(username: Optional[str]) -> dict:
    """Load a user's Seerr suggestion filters from their ui_prefs. Returns the
    default (no filters) when no user / nothing stored. Never raises."""
    if not username:
        return _coerce_filters(None)
    # defensive boundary — a filter-load failure must never break a suggestion.
    # noinspection PyBroadException
    try:
        from logic.db import db_conn  # noqa: PLC0415
        from logic import auth as _auth  # noqa: PLC0415
        with db_conn() as c:
            u = _auth.get_user_by_username(c, username)
            if not u or u.id < 0:
                return _coerce_filters(None)
            row = c.execute("SELECT ui_prefs FROM users WHERE id=?", (u.id,)).fetchone()
        prefs = json.loads(row[0]) if (row and row[0]) else {}
        raw = prefs.get("seerr_suggest_filters") if isinstance(prefs, dict) else None
        return _coerce_filters(raw)
    except Exception:  # noqa: BLE001
        return _coerce_filters(None)


def _save_user_filters(username: Optional[str], filters: dict) -> bool:
    """Persist a user's Seerr suggestion filters into their ui_prefs (merge,
    cache-invalidated via auth.update_ui_prefs). Returns True on success."""
    if not username:
        return False
    # noinspection PyBroadException
    try:
        from logic.db import db_conn  # noqa: PLC0415
        from logic import auth as _auth  # noqa: PLC0415
        with db_conn() as c:
            u = _auth.get_user_by_username(c, username)
            if not u or u.id < 0:
                return False
            _auth.update_ui_prefs(c, u.id, {"seerr_suggest_filters": _coerce_filters(filters)})
        return True
    except Exception:  # noqa: BLE001
        return False


# --- Recently-suggested dedupe (per user, ui_prefs-backed) -----------------
# The AI suggests a random movie via `seerr_suggest_movie`; without memory it
# re-draws from the same most-popular pages and the SAME film reappears within
# one browsing session (operator-reported: "cycle again through the same
# movies in the same session"). We remember each suggested TMDB id per user
# (with a ms timestamp) under `users.ui_prefs.seerr_recent_suggestions` and
# skip those ids for a cooldown window (`tuning_seerr_suggest_cooldown_hours`).
_SUGGEST_RECENT_CAP = 200  # max remembered ids per user (FIFO trim)
_SUGGEST_RECENT_CEILING_HOURS = 168  # hard prune ceiling (a week) regardless of cooldown


def _suggest_cooldown_hours() -> int:
    """Operator-tunable dedupe window (hours). 0 disables dedupe."""
    try:
        from logic.tuning import tuning_int, Tunable  # noqa: PLC0415
        return tuning_int(Tunable.SEERR_SUGGEST_COOLDOWN_HOURS)
    except (ImportError, KeyError, ValueError, TypeError):
        return 12


def _load_recent_suggestion_ids(username: Optional[str], cooldown_hours: int) -> "set[int]":
    """Set of TMDB ids the user was suggested within the cooldown window.
    Returns empty on no user / dedupe disabled / any failure (never raises)."""
    if not username or cooldown_hours <= 0:
        return set()
    # noinspection PyBroadException
    try:
        from logic.db import db_conn  # noqa: PLC0415
        from logic import auth as _auth  # noqa: PLC0415
        with db_conn() as c:
            u = _auth.get_user_by_username(c, username)
            if not u or u.id < 0:
                return set()
            row = c.execute("SELECT ui_prefs FROM users WHERE id=?", (u.id,)).fetchone()
        prefs = json.loads(row[0]) if (row and row[0]) else {}
        recents = prefs.get("seerr_recent_suggestions") if isinstance(prefs, dict) else None
        if not isinstance(recents, list):
            return set()
        cutoff_ms = (time.time() - cooldown_hours * 3600) * 1000.0
        out: "set[int]" = set()
        for r in recents:
            if not isinstance(r, dict):
                continue
            try:
                rid = int(r.get("id") or 0)
                ts = float(r.get("ts") or 0)
            except (TypeError, ValueError):
                continue
            if rid and ts >= cutoff_ms:
                out.add(rid)
        return out
    except Exception:  # noqa: BLE001
        return set()


def _record_suggestion(username: Optional[str], tmdb_id: int) -> None:
    """Append a suggested TMDB id (with now-ms) to the user's recent list,
    pruning stale entries (> ceiling) + capping length. Never raises."""
    if not username or not tmdb_id:
        return
    # noinspection PyBroadException
    try:
        from logic.db import db_conn  # noqa: PLC0415
        from logic import auth as _auth  # noqa: PLC0415
        now_ms = time.time() * 1000.0
        keep_after = now_ms - _SUGGEST_RECENT_CEILING_HOURS * 3600 * 1000.0
        with db_conn() as c:
            u = _auth.get_user_by_username(c, username)
            if not u or u.id < 0:
                return
            row = c.execute("SELECT ui_prefs FROM users WHERE id=?", (u.id,)).fetchone()
            prefs = json.loads(row[0]) if (row and row[0]) else {}
            recents = prefs.get("seerr_recent_suggestions") if isinstance(prefs, dict) else None
            if not isinstance(recents, list):
                recents = []
            cleaned = []
            for r in recents:
                if not isinstance(r, dict):
                    continue
                try:
                    rid = int(r.get("id") or 0)
                    ts = float(r.get("ts") or 0)
                except (TypeError, ValueError):
                    continue
                if rid and rid != int(tmdb_id) and ts >= keep_after:
                    cleaned.append({"id": rid, "ts": ts})
            cleaned.append({"id": int(tmdb_id), "ts": now_ms})
            if len(cleaned) > _SUGGEST_RECENT_CAP:
                cleaned = cleaned[-_SUGGEST_RECENT_CAP:]
            _auth.update_ui_prefs(c, u.id, {"seerr_recent_suggestions": cleaned})
    except Exception:  # noqa: BLE001
        return


def _format_filters(filters: dict) -> str:
    """Human-readable summary of the active filters for the show / set skills."""
    f = _coerce_filters(filters)
    lines = ["🎚️ Your movie-suggestion filters:"]
    mr = f["min_rating"]
    lines.append(f"⭐ Min rating: {mr:.1f}/10" if mr > 0 else "⭐ Min rating: any")
    lines.append("🌍 Excluded countries: "
                 + (", ".join(f["exclude_countries"]) if f["exclude_countries"] else "none"))
    lines.append("🎭 Excluded genres: "
                 + (", ".join(f["exclude_genres"]) if f["exclude_genres"] else "none"))
    if f["include_genres"]:
        lines.append("✅ Only these genres: " + ", ".join(f["include_genres"]))
    return "\n".join(lines)


def _apply_filter_directive(filters: dict, directive: str) -> "tuple[dict, str]":
    """Apply one filter directive to ``filters``; return ``(new_filters,
    message)``. Recognised directives (the AI emits these — see the prompt):

      exclude country <name>     allow country <name>
      exclude genre <name>       allow genre <name>
      only genre <name>          (alias: include genre <name>)
      min rating <number>        (0 / any = clear the rating filter)
      clear                      (reset everything)
    """
    f = _coerce_filters(filters)
    d = (directive or "").strip()
    dl = d.lower()
    if not dl:
        return f, "Tell me what to filter — e.g. “exclude country France”, “min rating 7”, or “exclude genre horror”."
    if dl in ("clear", "reset", "clear all", "reset all", "clear filters", "reset filters"):
        return dict(_coerce_filters(None)), "✅ Cleared all movie-suggestion filters."
    # --- country (multi-country aware: "exclude country Spain and Denmark") ---
    for pre in ("exclude country ", "no country ", "without country ", "block country "):
        if dl.startswith(pre):
            names = _split_country_list(d[len(pre):])
            if not names:
                return f, "Tell me which country to exclude — e.g. “exclude country France”."
            for val in names:
                if not any(_norm_country(x) == _norm_country(val) for x in f["exclude_countries"]):
                    f["exclude_countries"].append(val)
            return f, f"✅ Excluding movies from {_join_human(names)}."
    for pre in ("allow country ", "remove country ", "include country ", "un-exclude country "):
        if dl.startswith(pre):
            names = _split_country_list(d[len(pre):])
            if not names:
                return f, "Tell me which country to allow again — e.g. “allow country France”."
            norms = {_norm_country(v) for v in names}
            f["exclude_countries"] = [x for x in f["exclude_countries"]
                                      if _norm_country(x) not in norms]
            return f, f"✅ {_join_human(names)} movies are allowed again."
    # --- genre ---
    for pre in ("exclude genre ", "no genre ", "without genre ", "block genre "):
        if dl.startswith(pre):
            canon = _canonical_genre(d[len(pre):])
            if not canon:
                return f, _unknown_genre_msg(d[len(pre):])
            if canon not in f["exclude_genres"]:
                f["exclude_genres"].append(canon)
            f["include_genres"] = [g for g in f["include_genres"] if g != canon]
            return f, f"✅ Excluding {canon} movies."
    for pre in ("only genre ", "include genre ", "prefer genre "):
        if dl.startswith(pre):
            canon = _canonical_genre(d[len(pre):])
            if not canon:
                return f, _unknown_genre_msg(d[len(pre):])
            if canon not in f["include_genres"]:
                f["include_genres"].append(canon)
            f["exclude_genres"] = [g for g in f["exclude_genres"] if g != canon]
            return f, f"✅ Only suggesting {canon} movies (plus any other 'only' genres)."
    for pre in ("allow genre ", "remove genre ", "un-exclude genre "):
        if dl.startswith(pre):
            canon = _canonical_genre(d[len(pre):]) or d[len(pre):].strip().title()
            f["exclude_genres"] = [g for g in f["exclude_genres"] if g.lower() != canon.lower()]
            f["include_genres"] = [g for g in f["include_genres"] if g.lower() != canon.lower()]
            return f, f"✅ {canon} genre filter cleared."
    # --- rating ---
    for pre in ("min rating ", "minimum rating ", "rating above ", "rating "):
        if dl.startswith(pre):
            rest = dl[len(pre):].replace("/10", "").strip()
            if rest in ("any", "none", "off", "0"):
                f["min_rating"] = 0.0
                return f, "✅ Rating filter cleared (any rating)."
            try:
                f["min_rating"] = max(0.0, min(10.0, float(rest)))
            except (TypeError, ValueError):
                return f, f"I couldn't read a rating from “{d}”. Try e.g. “min rating 7”."
            return (f, (f"✅ Only suggesting movies rated {f['min_rating']:.1f}/10 or higher."
                        if f["min_rating"] > 0 else "✅ Rating filter cleared (any rating)."))
    if dl in ("any rating", "no rating", "no rating filter"):
        f["min_rating"] = 0.0
        return f, "✅ Rating filter cleared (any rating)."
    return f, (f"I didn't understand “{d}”. Try “exclude country France”, “min rating 7”, "
               "“exclude genre horror”, “allow country France”, or “clear”.")


def _unknown_genre_msg(name: str) -> str:
    return (f"“{name.strip()}” isn't a genre I know. Valid genres: "
            + ", ".join(sorted(_TMDB_GENRES.values())) + ".")


def requires_api_key() -> bool:
    """Seerr authenticates every data endpoint via X-Api-Key; the editor
    MUST render the api_key input + Test-connection button."""
    return True


def _headers(key: str) -> dict:
    return {"X-Api-Key": key, "Accept": "application/json"}


def _tmdb_cfg(chip: dict) -> "tuple[str, str, str]":
    """Resolve the chip's TMDB config: ``(api_key, base_url, image_base)``.
    Blank fields fall back to the public TMDB defaults so suggestions work
    out of the box once the operator pastes only the API key."""
    chip = chip if isinstance(chip, dict) else {}
    key = (chip.get("tmdb_api_key") or "").strip()
    base = (chip.get("tmdb_base_url") or "").strip().rstrip("/") or _TMDB_BASE_DEFAULT
    img = (chip.get("tmdb_image_base_url") or "").strip().rstrip("/") or _TMDB_IMAGE_BASE_DEFAULT
    return key, base, img


def _tmdb_auth(key: str) -> "tuple[dict, dict]":
    """Split a TMDB key into ``(headers, query_params)``. A v4 read-access
    token is a JWT (starts ``eyJ``) sent as a Bearer header; a classic v3
    key goes in the ``api_key`` query param."""
    if key.startswith("eyJ"):
        return {"Authorization": f"Bearer {key}", "Accept": "application/json"}, {}
    return {"Accept": "application/json"}, {"api_key": key}


def _tmdb_api_url(tmdb_base: str, path: str) -> str:
    """Build a TMDB v3 API URL from the configured base + an endpoint
    ``path`` (without a leading slash, e.g. ``"discover/movie"``).

    Tolerant of BOTH base conventions: operators paste either the host
    root (``https://api.themoviedb.org``) OR the version-qualified base
    (``https://api.themoviedb.org/3``). We strip a trailing ``/3`` if
    present, then re-append exactly one ``/3/`` so the result is always
    ``…/3/<path>`` — never a doubled ``/3/3/``."""
    b = (tmdb_base or "").strip().rstrip("/")
    if b.endswith("/3"):
        b = b[:-2].rstrip("/")
    return f"{b}/3/{path.lstrip('/')}"


def _poster_url(image_base: str, poster_path: str) -> str:
    """Build a full poster URL from the configured image base + the
    relative ``poster_path`` TMDB / Seerr return. Empty when no path."""
    p = (poster_path or "").strip()
    if not p:
        return ""
    if not p.startswith("/"):
        p = "/" + p
    return f"{image_base.rstrip('/')}/{_TMDB_POSTER_SIZE}{p}"


def _year_of(date_str: str) -> str:
    """Extract the 4-digit year from a ``YYYY-MM-DD`` release date; ''
    when absent / malformed."""
    s = (date_str or "").strip()
    return s[:4] if len(s) >= 4 and s[:4].isdigit() else ""


def _version_from(resp) -> str:
    """Extract ``version`` from a ``/api/v1/status`` response. Returns ''
    on any non-200 / parse failure (version is never load-bearing)."""
    try:
        if getattr(resp, "status_code", 0) != 200:
            return ""
        body = resp.json() or {}
        return str(body.get("version") or "").strip()
    except (ValueError, TypeError, AttributeError):
        return ""


async def _fetch_version(cli: httpx.AsyncClient, base: str, key: str) -> str:
    """Best-effort Seerr version via ``GET /api/v1/status`` on an already-open
    client — shared by the credential probe + the card fetch. ``''`` on any
    failure (version is a nice-to-have, never load-bearing)."""
    try:
        return _version_from(await cli.get(base + "/api/v1/status",
                                           headers=_headers(key)))
    except (httpx.HTTPError, OSError):
        return ""


async def test_credential(host_row: dict, chip: dict, candidate_key: str, **_kw) -> dict:
    """Probe Seerr's auth-required ``/api/v1/request/count`` with the
    supplied X-Api-Key. Returns ``{ok, detail, status}`` for direct SPA
    consumption. Falls back to the chip's stored ``api_key`` when
    ``candidate_key`` is blank so the operator can re-test after first
    save without retyping."""
    key, base, err = resolve_credential_target(host_row, chip, candidate_key)
    if err:
        return err
    url = base + "/api/v1/request/count"
    try:
        async with httpx.AsyncClient(verify=False, timeout=10.0,
                                     follow_redirects=True) as cli:
            r = await cli.get(url, headers=_headers(key))
            ver = await _fetch_version(cli, base, key)
    except (httpx.HTTPError, OSError) as e:  # noqa: BLE001
        return {"ok": False, "detail": f"{type(e).__name__}: {e}", "status": 0}
    if r.status_code == 200:
        return {"ok": True, "detail": f"OK (Seerr {ver})" if ver else "OK",
                "status": 200}
    if r.status_code in (401, 403):
        return {"ok": False, "detail": "auth failed (check api_key)",
                "status": r.status_code}
    return {"ok": False, "detail": f"HTTP {r.status_code}", "status": r.status_code}


# noinspection DuplicatedCode
# The upstream-error guard + JSON-parse block below is structurally shared
# with every other per-app module's fetch_data (bazarr / speedtest / …) — the
# deliberate per-app encapsulation pattern (CLAUDE.md). The content differs
# (app name, endpoint, fields), so it stays inline rather than coupling the
# modules through a parameterised _common helper.
async def fetch_data(host_row: dict, chip: dict, *,
                     host_id: str, service_idx: int,
                     force: bool = False) -> dict:
    """Fetch Seerr's request-queue counts (+ open issues + version) for the
    expanded card.

    Returns ``{available, total, pending, approved, processing,
    available_count, declined, issues_open, version, fetched_at}``. Raises
    ``ValueError`` / ``RuntimeError`` (caller maps to HTTPException) when
    the chip's api_key is unset / the base URL won't resolve / the upstream
    errors."""
    api_key = (chip.get("api_key") or "").strip()
    now = time.time()
    base, hit = fetch_gate(host_row, chip, host_id, service_idx, _data_cache,
                           resolve_cache_ttl(chip, DEFAULT_CACHE_TTL_S), now, force,
                           credential=api_key, log_tag="seerr")
    if hit is not None:
        return hit
    count_url = base + "/api/v1/request/count"
    try:
        async with httpx.AsyncClient(verify=False, timeout=15.0,
                                     follow_redirects=True) as cli:
            r = await cli.get(count_url, headers=_headers(api_key))
            # Open-issue count + version are nice-to-haves; a failure on
            # either must NOT fail the card.
            issues_open = 0
            try:
                ir = await cli.get(base + "/api/v1/issue/count",
                                   headers=_headers(api_key))
                if ir.status_code == 200:
                    issues_open = safe_int((ir.json() or {}).get("open"))
            except (httpx.HTTPError, OSError, ValueError, TypeError):
                issues_open = 0
            ver = await _fetch_version(cli, base, api_key)
    except (httpx.HTTPError, OSError) as e:  # noqa: BLE001
        print(f"[seerr] error: fetch host={host_id} url={count_url} "
              f"failed — {type(e).__name__}: {e}")
        raise RuntimeError(f"upstream fetch failed: {type(e).__name__}: {e}")
    if r.status_code != 200:
        print(f"[seerr] error: fetch host={host_id} url={r.request.url} "
              f"returned HTTP {r.status_code} (check the chip URL points at "
              f"the Seerr root, e.g. https://requests.example.com)")
        if r.status_code in (401, 403):
            raise RuntimeError(f"upstream auth failed: HTTP {r.status_code} "
                               f"(check api_key) — {count_url}")
        raise RuntimeError(f"upstream returned HTTP {r.status_code} for {count_url}")
    try:
        body = r.json()
    except (ValueError, TypeError):  # noqa: BLE001
        raise RuntimeError("upstream returned non-JSON")
    if not isinstance(body, dict):
        body = {}
    out: dict[str, Any] = {
        "available": True,
        "total": safe_int(body.get("total")),
        "pending": safe_int(body.get("pending")),
        "approved": safe_int(body.get("approved")),
        "processing": safe_int(body.get("processing")),
        "available_count": safe_int(body.get("available")),
        "declined": safe_int(body.get("declined")),
        "issues_open": safe_int(issues_open),
        "version": ver,
        "fetched_at": int(now),
    }
    print(f"[seerr] INFO fetched host={host_id} pending={out['pending']} "
          f"approved={out['approved']} processing={out['processing']} "
          f"available={out['available_count']} issues={out['issues_open']}")
    _data_cache[cache_key(host_id, service_idx)] = (now, out)
    return out


def peek_latest(host_id: str, service_idx: int) -> Optional[dict]:
    """Cache-only peek (no upstream call) for the AI context's
    ``app_skills[].last``. Returns the last fetched queue counts or
    ``None`` when nothing is cached yet."""
    data = peek_cache(_data_cache, host_id, service_idx)
    if not isinstance(data, dict) or not data.get("available"):
        return None
    return {
        "pending": safe_int(data.get("pending")),
        "approved": safe_int(data.get("approved")),
        "processing": safe_int(data.get("processing")),
        "available_count": safe_int(data.get("available_count")),
        "total": safe_int(data.get("total")),
        "issues_open": safe_int(data.get("issues_open")),
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
    """Dispatch one of this app's SKILLS. Returns ``{ok, detail, status?}``
    (+ ``image_url`` for the suggest skill). Raises ValueError on an unknown
    skill id (route maps to HTTP 404). ``arg`` carries the free-form argument
    (movie title / TMDB id, or a filter directive); ``actor_username`` is the
    acting OmniGrid user, used to load/save that user's per-user suggestion
    filters."""
    if skill_id == "seerr_status":
        return await _status_skill(host_row, chip, host_id=host_id,
                                   service_idx=service_idx)
    if skill_id == "seerr_request_movie":
        return await _request_skill(host_row, chip, arg=arg, host_id=host_id)
    if skill_id == "seerr_suggest_movie":
        return await _suggest_skill(host_row, chip, host_id=host_id,
                                    actor_username=actor_username)
    if skill_id == "seerr_set_filter":
        return _set_filter_skill(arg, actor_username)
    if skill_id == "seerr_show_filters":
        return _show_filters_skill(actor_username)
    raise ValueError(f"unknown skill: {skill_id!r}")


def _set_filter_skill(arg: Optional[str], actor_username: Optional[str]) -> dict:
    """Apply a filter directive to the acting user's per-user suggestion
    filters (database-backed in their ui_prefs). Returns ``{ok, detail}``."""
    if not actor_username:
        return {"ok": False, "status": 0,
                "detail": "I can't tell which account you are, so I can't save a "
                          "per-user filter. (Link your account / sign in first.)"}
    current = _load_user_filters(actor_username)
    new_filters, message = _apply_filter_directive(current, arg or "")
    if not _save_user_filters(actor_username, new_filters):
        return {"ok": False, "status": 0, "detail": "couldn't save your filter — try again"}
    summary = _format_filters(new_filters)
    print(f"[seerr] INFO seerr_set_filter user={actor_username!r} arg={arg!r}")
    return {"ok": True, "status": 200, "detail": message + "\n\n" + summary}


def _show_filters_skill(actor_username: Optional[str]) -> dict:
    """Show the acting user's current per-user suggestion filters."""
    if not actor_username:
        return {"ok": False, "status": 0,
                "detail": "I can't tell which account you are. (Link your account / sign in first.)"}
    return {"ok": True, "status": 200,
            "detail": _format_filters(_load_user_filters(actor_username))}


# noinspection DuplicatedCode
# The force-fetch-then-format shape is shared with every per-app module's
# status skill (bazarr / adguard / …) — the deliberate per-app encapsulation
# pattern (CLAUDE.md). The formatted output is app-specific, so it stays
# inline rather than being factored into a shared helper.
async def _status_skill(host_row: dict, chip: dict, *,
                        host_id: Optional[str] = None,
                        service_idx: Optional[int] = None) -> dict:
    """Read-only skill: live-fetch the current request-queue counts
    (force-bypasses the cache) and return a formatted ``detail``. Never
    raises — upstream / config failures come back as ``{ok: False, detail}``."""
    print(f"[seerr] INFO seerr_status host={host_id} svc_idx={service_idx} (live fetch)")
    try:
        data = await fetch_data(host_row, chip,
                                host_id=str(host_id or ""),
                                service_idx=int(service_idx or 0),
                                force=True)
    except (ValueError, RuntimeError) as e:
        print(f"[seerr] warning: seerr_status host={host_id} could not fetch — {e}")
        return {"ok": False, "detail": str(e), "status": 0}
    pend = safe_int(data.get("pending"))
    appr = safe_int(data.get("approved"))
    proc = safe_int(data.get("processing"))
    avail = safe_int(data.get("available_count"))
    total = safe_int(data.get("total"))
    issues = safe_int(data.get("issues_open"))
    lines = [
        f"⏳ Pending approval: {pend:,}",
        f"✅ Approved: {appr:,}",
        f"⬇️ Processing: {proc:,}",
        f"🎬 Available: {avail:,}",
        f"📋 Total requests: {total:,}",
    ]
    if issues:
        lines.append(f"⚠️ Open issues: {issues:,}")
    return {
        "ok": True,
        "detail": "\n".join(lines),
        "status": 200,
        "pending": pend, "approved": appr, "processing": proc,
        "available_count": avail, "total": total, "issues_open": issues,
    }


async def _seerr_search_movie(base: str, api_key: str, query: str) -> Optional[dict]:
    """Resolve a movie by title via Seerr's TMDB-backed search. Returns the
    top ``mediaType == 'movie'`` result as ``{id, title, year, overview,
    poster_path, status}`` or ``None`` when nothing matches / the call
    fails."""
    url = base + "/api/v1/search"
    try:
        async with httpx.AsyncClient(verify=False, timeout=15.0,
                                     follow_redirects=True) as cli:
            r = await cli.get(url, headers=_headers(api_key),
                              params={"query": query, "page": 1})
    except (httpx.HTTPError, OSError) as e:  # noqa: BLE001
        print(f"[seerr] warning: search failed for {query!r} — {type(e).__name__}: {e}")
        return None
    if r.status_code != 200:
        print(f"[seerr] warning: search HTTP {r.status_code} for {query!r}")
        return None
    try:
        _raw = (r.json() or {}).get("results")
        results: list = _raw if isinstance(_raw, list) else []
    except (ValueError, TypeError):
        return None
    for item in results:
        if not isinstance(item, dict):
            continue
        if (item.get("mediaType") or "").strip().lower() != "movie":
            continue
        media_info = item.get("mediaInfo") if isinstance(item.get("mediaInfo"), dict) else {}
        return {
            "id": safe_int(item.get("id")),
            "title": str(item.get("title") or item.get("originalTitle") or "").strip(),
            "year": _year_of(str(item.get("releaseDate") or "")),
            "overview": str(item.get("overview") or "").strip(),
            "poster_path": str(item.get("posterPath") or "").strip(),
            "status": safe_int((media_info or {}).get("status")),
        }
    return None


# Seerr `mediaInfo.status` enum (1 unknown · 2 pending · 3 processing ·
# 4 partially available · 5 available). 3+ means "already in the pipeline".
_SEERR_STATUS_LABEL = {
    2: "already requested (pending)",
    3: "already processing",
    4: "partially available",
    5: "already available",
}


async def _request_skill(host_row: dict, chip: dict, *,
                         arg: Optional[str] = None,
                         host_id: Optional[str] = None) -> dict:
    """Action skill: request a movie BY TITLE (or numeric TMDB id). Resolves
    the title via Seerr search, then POSTs ``/api/v1/request``. Never raises
    — every failure comes back as ``{ok: False, detail}``. Treats an
    already-requested / available movie as a friendly (ok=True) outcome."""
    query = (arg or "").strip()
    if not query:
        return {"ok": False, "status": 0,
                "detail": "no movie title given — tell me which movie to request"}
    api_key = (chip.get("api_key") or "").strip()
    if not api_key:
        return {"ok": False, "status": 0, "detail": "Seerr api_key not set"}
    from logic.apps._common import resolve_base_url  # noqa: PLC0415
    base = resolve_base_url(host_row, chip)
    if not base:
        return {"ok": False, "status": 0, "detail": "no upstream URL configured"}
    # Numeric arg → request that TMDB id directly (the suggest skill hands
    # the model a tmdb id it can re-use for an exact request). Otherwise
    # search by title.
    title = query
    year = ""
    if query.isdigit():
        tmdb_id = int(query)
        already_status = 0
    else:
        match = await _seerr_search_movie(base, api_key, query)
        if not match or not match.get("id"):
            return {"ok": False, "status": 404,
                    "detail": f"no movie found matching “{query}”"}
        tmdb_id = safe_int(match.get("id"))
        title = match.get("title") or query
        year = match.get("year") or ""
        already_status = safe_int(match.get("status"))
    label = f"{title}" + (f" ({year})" if year else "")
    # Already in the pipeline → friendly no-op instead of a 409 error.
    if already_status in _SEERR_STATUS_LABEL:
        return {"ok": True, "status": 200,
                "detail": f"🎬 {label} is {_SEERR_STATUS_LABEL[already_status]} on Seerr.",
                "tmdb_id": tmdb_id}
    print(f"[seerr] INFO seerr_request_movie host={host_id} title={label!r} "
          f"tmdb_id={tmdb_id}")
    try:
        async with httpx.AsyncClient(verify=False, timeout=20.0,
                                     follow_redirects=True) as cli:
            r = await cli.post(base + "/api/v1/request",
                               headers=_headers(api_key),
                               json={"mediaType": "movie", "mediaId": tmdb_id})
    except (httpx.HTTPError, OSError) as e:  # noqa: BLE001
        print(f"[seerr] warning: request failed for {label!r} — {type(e).__name__}: {e}")
        return {"ok": False, "status": 0,
                "detail": f"request failed: {type(e).__name__}: {e}"}
    if r.status_code in (200, 201):
        return {"ok": True, "status": r.status_code,
                "detail": f"🎬 Requested {label} on Seerr — it'll start downloading once approved.",
                "tmdb_id": tmdb_id}
    if r.status_code == 409:
        # 409 = the request already exists (race with the status check).
        return {"ok": True, "status": 409,
                "detail": f"🎬 {label} was already requested on Seerr.",
                "tmdb_id": tmdb_id}
    if r.status_code in (401, 403):
        return {"ok": False, "status": r.status_code,
                "detail": "auth failed (check Seerr api_key)"}
    _body = ""
    try:
        _body = (r.text or "")[:160]
    except (ValueError, TypeError):
        _body = ""
    return {"ok": False, "status": r.status_code,
            "detail": f"Seerr returned HTTP {r.status_code} requesting {label}"
                      + (f" — {_body}" if _body else "")}


# How many candidates to library-check per page (one discover page is ~20
# movies — check them all so a single page yields as many not-in-library
# options as possible).
_SUGGEST_CHECK_LIMIT = 20
# How many fresh random pages to try before concluding the operator really
# does have everything. Each page is a different random slice of the
# catalogue, so 4 attempts checks up to ~80 distinct movies across the
# popularity depth — an active library rarely owns all of those.
_SUGGEST_PAGE_ATTEMPTS = 4
# Seerr mediaInfo.status >= this = already requested / processing / partially
# available / available — i.e. already in (or on its way into) the library.
_SEERR_IN_LIBRARY_MIN_STATUS = 2


async def _tmdb_candidate_movies(tmdb_key: str, tmdb_base: str,
                                 image_base: str, *,
                                 min_rating: float = 0.0,
                                 include_genre_ids: "tuple[int, ...]" = (),
                                 exclude_genre_ids: "tuple[int, ...]" = ()) -> list[dict]:
    """A SHUFFLED list of popular-movie candidates from TMDB's
    ``/discover/movie`` (a random page). Each entry is ``{id, title, year,
    rating, genres, overview, poster_url}``. Returns ``[]`` on failure / no
    key. The per-user filters (min rating, include / exclude genres) are
    pushed INTO the TMDB query (``vote_average.gte`` / ``with_genres`` /
    ``without_genres``) so the page is already pre-filtered; the caller then
    library-checks + country-filters the results."""
    if not tmdb_key:
        return []
    headers, params = _tmdb_auth(tmdb_key)
    page = random.randint(1, _TMDB_DISCOVER_MAX_PAGE)
    params = dict(params)
    params.update({"sort_by": "popularity.desc", "vote_count.gte": "150",
                   "include_adult": "false", "page": str(page)})
    if min_rating and min_rating > 0:
        params["vote_average.gte"] = f"{min_rating:.1f}"
    if include_genre_ids:
        # pipe = OR (any of these genres)
        params["with_genres"] = "|".join(str(g) for g in include_genre_ids)
    if exclude_genre_ids:
        params["without_genres"] = ",".join(str(g) for g in exclude_genre_ids)
    url = _tmdb_api_url(tmdb_base, "discover/movie")
    try:
        # TMDB is a public HTTPS endpoint with a real cert — TLS verification
        # stays ON (httpx's default), unlike the Seerr calls which use
        # verify=False for self-signed home-lab certs.
        async with httpx.AsyncClient(timeout=15.0,
                                     follow_redirects=True) as cli:
            r = await cli.get(url, headers=headers, params=params)
    except (httpx.HTTPError, OSError) as e:  # noqa: BLE001
        print(f"[seerr] warning: TMDB discover failed — {type(e).__name__}: {e}")
        return []
    if r.status_code != 200:
        print(f"[seerr] warning: TMDB discover HTTP {r.status_code} (check tmdb_api_key)")
        return []
    try:
        _raw = (r.json() or {}).get("results")
        results: list = _raw if isinstance(_raw, list) else []
    except (ValueError, TypeError):
        return []
    out = [{
        "id": safe_int(m.get("id")),
        "title": str(m.get("title") or m.get("original_title") or "").strip(),
        "year": _year_of(str(m.get("release_date") or "")),
        "rating": round(safe_float(m.get("vote_average")), 1),
        "genres": _genre_names(m.get("genre_ids")),
        "overview": str(m.get("overview") or "").strip(),
        "poster_url": _poster_url(image_base, str(m.get("poster_path") or "")),
    } for m in results if isinstance(m, dict) and m.get("id")]
    random.shuffle(out)
    return out


# A few verbose TMDB country names shortened for the suggestion line.
_COUNTRY_SHORT = {
    "United States of America": "USA",
    "United Kingdom": "UK",
    "United Arab Emirates": "UAE",
    "Korea, Republic of": "South Korea",
    "Russian Federation": "Russia",
}
_COUNTRY_DISPLAY_MAX = 3


def _extract_countries(raw: Any) -> list[str]:
    """Shape a TMDB / Seerr ``productionCountries`` list into a short display
    list (top ``_COUNTRY_DISPLAY_MAX``, verbose names shortened)."""
    if not isinstance(raw, list):
        return []
    out: list[str] = []
    for c in raw:
        if not isinstance(c, dict):
            continue
        name = str(c.get("name") or "").strip()
        if not name:
            continue
        name = _COUNTRY_SHORT.get(name, name)
        if name not in out:
            out.append(name)
        if len(out) >= _COUNTRY_DISPLAY_MAX:
            break
    return out


async def _seerr_movie_detail(cli: httpx.AsyncClient, base: str, api_key: str,
                              tmdb_id: int) -> "tuple[int, list[str]]":
    """Seerr's ``(mediaInfo.status, production-countries)`` for a TMDB movie id
    via ONE ``GET /api/v1/movie/<id>`` — status ``0`` when not in Seerr /
    unknown, ``>=2`` when already requested / processing / available; countries
    is the short display list. Never raises (a lookup failure reads as
    ``(0, [])`` — not-in-library, no country — so a transient blip just means
    we might suggest one that's already there; the request path guards it)."""
    if not tmdb_id:
        return 0, []
    try:
        r = await cli.get(f"{base}/api/v1/movie/{tmdb_id}",
                          headers=_headers(api_key))
    except (httpx.HTTPError, OSError):
        return 0, []
    if getattr(r, "status_code", 0) != 200:
        return 0, []
    try:
        body = r.json() or {}
    except (ValueError, TypeError):
        return 0, []
    if not isinstance(body, dict):
        return 0, []
    mi = body.get("mediaInfo")
    status = safe_int(mi.get("status")) if isinstance(mi, dict) else 0
    return status, _extract_countries(body.get("productionCountries"))


async def _seerr_discover_candidates(base: str, api_key: str,
                                     image_base: str) -> list[dict]:
    """Fallback candidate source when no TMDB key is set — Seerr's own
    ``/api/v1/discover/movies``, whose results ALREADY carry each movie's
    library status via ``mediaInfo`` (so no per-movie lookup needed). Returns
    a SHUFFLED list of ``{id, title, year, overview, poster_url, status}``."""
    page = random.randint(1, _TMDB_DISCOVER_MAX_PAGE)
    try:
        async with httpx.AsyncClient(verify=False, timeout=15.0,
                                     follow_redirects=True) as cli:
            r = await cli.get(base + "/api/v1/discover/movies",
                              headers=_headers(api_key), params={"page": page})
    except (httpx.HTTPError, OSError) as e:  # noqa: BLE001
        print(f"[seerr] warning: Seerr discover failed — {type(e).__name__}: {e}")
        return []
    if r.status_code != 200:
        return []
    try:
        _raw = (r.json() or {}).get("results")
        results: list = _raw if isinstance(_raw, list) else []
    except (ValueError, TypeError):
        return []
    out = []
    for m in results:
        if not isinstance(m, dict) or not m.get("id"):
            continue
        mi = m.get("mediaInfo")
        out.append({
            "id": safe_int(m.get("id")),
            "title": str(m.get("title") or m.get("originalTitle") or "").strip(),
            "year": _year_of(str(m.get("releaseDate") or "")),
            "rating": round(safe_float(m.get("voteAverage")), 1),
            "genres": _genre_names(m.get("genreIds")),
            "overview": str(m.get("overview") or "").strip(),
            "poster_url": _poster_url(image_base, str(m.get("posterPath") or "")),
            "status": safe_int(mi.get("status")) if isinstance(mi, dict) else 0,
        })
    random.shuffle(out)
    return out


async def _pick_not_in_library(base: str, api_key: str,
                               candidates: list[dict], *,
                               exclude_countries: "tuple[str, ...]" = (),
                               exclude_ids: Collection[int] = frozenset()
                               ) -> Optional[dict]:
    """From a candidate list, return the first movie that is NOT already in
    Seerr's library (``mediaInfo.status < 2``), NOT recently suggested
    (``exclude_ids``), AND not from an excluded country. The chosen movie has
    its ``_countries`` attached (from the same detail call) so the caller
    renders the country line without a re-fetch.

    Candidates that already carry a ``status`` key (Seerr-discover source) are
    filtered inline when there's NO country filter (cheap); otherwise each is
    detail-checked against Seerr in PARALLEL (capped at
    ``_SUGGEST_CHECK_LIMIT``) for status + countries. Returns ``None`` when
    every checked candidate is already in the library / excluded."""
    if not candidates:
        return None
    # Drop recently-suggested ids up front so the dedupe applies on BOTH the
    # fast (inline-status) and the detail-check paths.
    if exclude_ids:
        candidates = [c for c in candidates if safe_int(c.get("id")) not in exclude_ids]
        if not candidates:
            return None
    check = candidates[:_SUGGEST_CHECK_LIMIT]
    ex = {_norm_country(c) for c in exclude_countries if c}
    # Fast path: Seerr-discover candidates carry status inline AND there's no
    # country filter → no detail call needed.
    if not ex and all("status" in c for c in check):
        for c in check:
            if safe_int(c.get("status")) < _SEERR_IN_LIBRARY_MIN_STATUS:
                return c
        return None
    # Need Seerr to check status and/or country. If unreachable, suggest the
    # first (the request path still guards already-owned).
    if not (base and api_key):
        return check[0]
    async with httpx.AsyncClient(verify=False, timeout=15.0,
                                 follow_redirects=True) as cli:
        details = await asyncio.gather(
            *[_seerr_movie_detail(cli, base, api_key, safe_int(c.get("id")))
              for c in check],
            return_exceptions=True)
    skipped = 0
    for c, det in zip(check, details):
        status, countries = det if isinstance(det, tuple) else (0, [])
        # Prefer an inline status (Seerr-discover) over the detail status.
        st = safe_int(c.get("status")) if "status" in c else safe_int(status)
        if st >= _SEERR_IN_LIBRARY_MIN_STATUS:
            skipped += 1
            continue
        if ex and any(_norm_country(x) in ex for x in countries):
            skipped += 1
            continue
        c["_countries"] = countries
        return c
    print(f"[seerr] INFO suggest: all {skipped} checked candidates already in "
          f"the library or excluded by filters")
    return None


def _genre_ids_for_names(names: list) -> "tuple[int, ...]":
    """Map a list of (canonical) genre names to TMDB ids, dropping unknowns."""
    out = []
    for n in names:
        gid = _GENRE_NAME_TO_ID.get(_GENRE_SYNONYMS.get(str(n).lower(), str(n).lower()))
        if gid:
            out.append(gid)
    return tuple(out)


def _candidate_passes_filters(c: dict, min_rating: float,
                              exclude_genres_lc: set, include_genres_lc: set) -> bool:
    """Client-side defence for the Seerr-discover fallback path (the TMDB path
    pushes these into the query). Checks rating + genre include/exclude on the
    candidate's own fields."""
    if min_rating and safe_float(c.get("rating")) < min_rating:
        return False
    cg = {str(g).lower() for g in (c.get("genres") or [])}
    if exclude_genres_lc and (cg & exclude_genres_lc):
        return False
    if include_genres_lc and not (cg & include_genres_lc):
        return False
    return True


def _active_filter_summary(filters: dict) -> str:
    """Compact one-line note of the active filters, appended to a suggestion so
    the user knows their filters are being applied. '' when no filters set."""
    bits = []
    if filters["min_rating"] > 0:
        bits.append(f"⭐≥{filters['min_rating']:.1f}")
    if filters["exclude_countries"]:
        bits.append("no " + "/".join(filters["exclude_countries"]))
    if filters["exclude_genres"]:
        bits.append("no " + "/".join(filters["exclude_genres"]))
    if filters["include_genres"]:
        bits.append("only " + "/".join(filters["include_genres"]))
    return ("🎚️ Filters: " + " · ".join(bits)) if bits else ""


async def _suggest_skill(host_row: dict, chip: dict, *,
                         host_id: Optional[str] = None,
                         actor_username: Optional[str] = None) -> dict:
    """Read-only skill: suggest a random movie the user can then request —
    NOT already in their Seerr library AND matching the requesting user's
    saved filters (min rating, excluded countries / genres, only-genres).
    Prefers TMDB (when a TMDB key is configured on the chip) for genuine
    variety; falls back to Seerr's own discover endpoint. Returns
    ``{ok, detail, image_url, tmdb_id, title, followup}``."""
    api_key = (chip.get("api_key") or "").strip()
    from logic.apps._common import resolve_base_url  # noqa: PLC0415
    base = resolve_base_url(host_row, chip)
    tmdb_key, tmdb_base, image_base = _tmdb_cfg(chip)
    # Per-user filters (database-backed in the requesting user's ui_prefs).
    filters = _load_user_filters(actor_username)
    min_rating = filters["min_rating"]
    exclude_countries = tuple(filters["exclude_countries"])
    inc_ids = _genre_ids_for_names(filters["include_genres"])
    exc_ids = _genre_ids_for_names(filters["exclude_genres"])
    exclude_genres_lc = {g.lower() for g in filters["exclude_genres"]}
    include_genres_lc = {g.lower() for g in filters["include_genres"]}
    # Recently-suggested dedupe: skip ids suggested to THIS user inside the
    # cooldown window so the same film doesn't cycle back in one session.
    cooldown_hours = _suggest_cooldown_hours()
    recent_ids = _load_recent_suggestion_ids(actor_username, cooldown_hours)
    print(f"[seerr] INFO seerr_suggest_movie host={host_id} user={actor_username!r} "
          f"source={'tmdb' if tmdb_key else 'seerr-discover'} "
          f"filters(min_rating={min_rating}, ex_countries={exclude_countries}, "
          f"ex_genres={filters['exclude_genres']}, inc_genres={filters['include_genres']}) "
          f"dedupe(cooldown_h={cooldown_hours}, recent={len(recent_ids)})")
    # Try several fresh random pages — an active library can own every movie
    # on the first (most-popular) page, so we keep drawing deeper / different
    # slices of the catalogue until we find one the operator doesn't have.
    pick = None
    got_candidates = False
    pages_checked = 0
    for _attempt in range(_SUGGEST_PAGE_ATTEMPTS):
        if tmdb_key:
            candidates = await _tmdb_candidate_movies(
                tmdb_key, tmdb_base, image_base, min_rating=min_rating,
                include_genre_ids=inc_ids, exclude_genre_ids=exc_ids)
        elif base and api_key:
            candidates = await _seerr_discover_candidates(base, api_key, image_base)
            # The Seerr discover query can't take the rating/genre filters, so
            # enforce them client-side on the candidate fields.
            candidates = [c for c in candidates
                          if _candidate_passes_filters(c, min_rating,
                                                       exclude_genres_lc, include_genres_lc)]
        else:
            candidates = []
        if not candidates:
            break  # fetch failure / nothing to draw from — retrying won't help
        got_candidates = True
        pages_checked += 1
        pick = await _pick_not_in_library(base, api_key, candidates,
                                          exclude_countries=exclude_countries,
                                          exclude_ids=recent_ids)
        if pick and pick.get("title"):
            break
        pick = None
    if pick is None:
        if not got_candidates:
            return {"ok": False, "status": 0,
                    "detail": "couldn't fetch a suggestion (set a TMDB API key on the "
                              "Seerr app for movie suggestions, or check the Seerr URL)"}
        # Checked several whole pages and nothing passed (owned / filtered) —
        # be honest rather than suggesting a dup or an excluded title.
        print(f"[seerr] INFO suggest: {pages_checked} page(s) checked, nothing "
              f"new passed the filters")
        _fs = _active_filter_summary(filters)
        _tail = (f" (your filters: {_fs[len('🎚️ Filters: '):]})" if _fs else "")
        return {"ok": True, "status": 200,
                "detail": "🎬 I couldn't find a fresh movie that's not already in "
                          f"your Seerr library and matches your filters{_tail}. Ask "
                          "again, or loosen a filter (e.g. “min rating 0”, “allow "
                          "country France”)."}
    title = pick.get("title") or ""
    year = pick.get("year") or ""
    rating = safe_float(pick.get("rating"))
    genres = pick.get("genres") if isinstance(pick.get("genres"), list) else []
    label = title + (f" ({year})" if year else "")
    overview = (pick.get("overview") or "").strip()
    if len(overview) > 300:
        overview = overview[:297].rstrip() + "…"
    tmdb_id = safe_int(pick.get("id"))
    # Remember this suggestion so it's skipped for the cooldown window.
    if tmdb_id and cooldown_hours > 0:
        _record_suggestion(actor_username, tmdb_id)
    # Country comes from the pick's detail call (attached by _pick_not_in_library).
    countries = pick.get("_countries") if isinstance(pick.get("_countries"), list) else []
    lines = [f"🎬 How about: {label}"]
    # ⭐ rating + year meta line (rating is TMDB's 0-10 vote average).
    meta_bits = []
    if rating:
        meta_bits.append(f"⭐ {rating:.1f}/10")
    if year:
        meta_bits.append(str(year))
    if meta_bits:
        lines.append(" · ".join(meta_bits))
    # 🎭 genre line.
    if genres:
        lines.append("🎭 " + " · ".join(genres))
    # 🌍 country line.
    if countries:
        lines.append("🌍 " + " · ".join(countries))
    if overview:
        lines.append(overview)
    lines.append(f"Say “request {title}” and I'll add it to Seerr. (TMDB id {tmdb_id})")
    # Footnote: show the active filters so it's clear they were applied.
    _fs = _active_filter_summary(filters)
    if _fs:
        lines.append(_fs)
    poster = pick.get("poster_url") or ""
    if poster:
        lines.append(poster)
    _has_followup = bool(tmdb_id or title)
    # Diagnostic: the drawer/AI render the poster off `image_url` and the
    # Request button off `followup`. When either renders wrong, this line
    # pins whether the backend attached them + WHERE the poster came from
    # (empty image_base / poster_path => no poster URL was built).
    print(f"[seerr] INFO seerr_suggest_movie RESULT host={host_id} title={title!r} "
          f"tmdb_id={tmdb_id} image_base={image_base!r} "
          f"poster_url={poster!r} (len={len(poster)}) followup={_has_followup}")
    return {
        "ok": True,
        "status": 200,
        "detail": "\n".join(lines),
        "image_url": poster,
        "tmdb_id": tmdb_id,
        "title": title,
        "year": year,
        "rating": rating,
        "genres": genres,
        "countries": countries,
        # Follow-up action the UI can offer as a one-click button (the AI
        # sidebar renders it after a suggestion). Requesting by the exact
        # TMDB id avoids a re-search. Generic shape: {skill_id, arg, label}.
        "followup": {
            "skill_id": "seerr_request_movie",
            "arg": str(tmdb_id) if tmdb_id else title,
            "label": f"Request {label} on Seerr",
        } if (tmdb_id or title) else None,
    }
