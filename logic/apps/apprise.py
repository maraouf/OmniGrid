"""Apprise per-app module (caronc/apprise-api — the notification fan-out API).

Apprise is the notification gateway OmniGrid itself uses for deploy / op
notifications. This per-app integration surfaces, for a pinned Apprise API
instance:

    SLUGS               — catalog slugs this module handles.
    requires_api_key()  — False. The apprise-api server has NO built-in auth
                          (it's protected by the network / a reverse proxy), so
                          the card + skills work against it directly.
    test_credential     — GET /json/urls/<key> reachability probe.
    fetch_data          — the configured-endpoint summary for the expanded card
                          (how many notification URLs, which services, tags).
    peek_latest         — cache-only peek for the AI context.
    SKILLS / run_skill  — status (read) + send a test notification + send a
                          notification with a message (and an optional tag) so
                          the AI / Telegram bot can fire an Apprise notification.

Config key
    apprise-api stores notification URLs under a stateful KEY (OmniGrid's own
    notifier uses the default key ``apprise``). The operator sets the chip URL
    to the apprise-api root; if they paste the full ``…/notify/<key>`` URL (the
    same value they configured in OmniGrid's Apprise notifier) we lift the
    ``<key>`` out of the path and strip it back to the API root. When no key is
    discoverable we default to ``apprise``.

Endpoints used
    GET  /json/urls/<key>?privacy=1   — the configured notification URLs (the
                                        ``privacy`` flag masks every secret in
                                        the returned URLs; we only ever read the
                                        scheme before ``://`` anyway, never the
                                        credential).
    POST /notify/<key>                — send a notification (body/title/type +
                                        optional tag) to the configured URLs.

No-JSON-secret rule: we extract ONLY the URL scheme (``tgram`` / ``discord`` /
``mailto`` / …) to name the service — never the rest of the URL, which carries
bot tokens / passwords.

Per-app encapsulation pattern (CLAUDE.md): every Apprise-specific helper lives
here; the generic dispatcher + drawer stay app-agnostic.
"""
from __future__ import annotations

import re
import time
from typing import Any, Optional
from urllib.parse import quote, urlsplit

import httpx

from logic.apps._common import (
    cache_key, fetch_preamble, peek_cache, resolve_base_url, resolve_cache_ttl)
from logic.coerce import as_list, safe_int

# Catalog slug(s) this module handles. The chip's catalog_id resolves to the
# "apprise" template (logic/service_catalog.py); a de-linked chip that kept the
# brand still matches via the JS-side name check.
SLUGS: tuple[str, ...] = ("apprise",)

DEFAULT_CACHE_TTL_S = 60
_data_cache: dict[str, tuple[float, dict]] = {}

# The default apprise-api stateful config key. OmniGrid's own notifier posts to
# /notify/apprise, so this is the overwhelmingly common key.
_DEFAULT_KEY = "apprise"

# URL schemes Apprise uses, mapped to an operator-friendly service name. The
# fallback (Title-cased scheme) covers every plugin we don't name explicitly,
# so a new Apprise plugin still reads sensibly without a code change.
_SERVICE_NAMES = {
    "tgram": "Telegram",
    "discord": "Discord",
    "slack": "Slack",
    "mailto": "Email", "mailtos": "Email",
    "matrix": "Matrix", "matrixs": "Matrix",
    "ntfy": "ntfy",
    "gotify": "Gotify",
    "pover": "Pushover",
    "pbul": "Pushbullet",
    "join": "Join",
    "twilio": "Twilio",
    "webex": "Webex",
    "msteams": "Microsoft Teams",
    "rocket": "Rocket.Chat", "rockets": "Rocket.Chat",
    "mmost": "Mattermost", "mmosts": "Mattermost",
    "signal": "Signal", "signals": "Signal",
    "json": "JSON webhook", "jsons": "JSON webhook",
    "form": "Form webhook", "forms": "Form webhook",
    "xml": "XML webhook", "xmls": "XML webhook",
    "telegram": "Telegram",
    "wxteams": "Webex Teams",
    "apprises": "Apprise", "apprise": "Apprise",
}


def requires_api_key() -> bool:
    """False — the apprise-api server has no built-in authentication; the card
    + skills work against it directly (it's gated by the network / a proxy)."""
    return False


def _service_name(scheme: str) -> str:
    """Operator-friendly name for an Apprise URL scheme. Unknown schemes
    Title-case so a new plugin still reads sensibly."""
    s = (scheme or "").strip().lower()
    if not s:
        return ""
    return _SERVICE_NAMES.get(s) or s.replace("_", " ").title()


def _resolve_apprise(host_row: dict, chip: dict) -> "tuple[str, str]":
    """Resolve ``(base_url, config_key)`` for an Apprise instance.

    The base is the apprise-api root. Operators commonly paste the full
    ``…/notify/<key>`` URL they configured in OmniGrid's notifier, so strip a
    trailing ``/notify/<key>`` | ``/cfg/<key>`` | ``/json/urls/<key>`` path
    back to the root and lift the ``<key>`` out of it. Default key ``apprise``.
    Returns ``("", "")`` when nothing resolves."""
    raw = resolve_base_url(host_row, chip)
    if not raw:
        return "", ""
    parts = urlsplit(raw)
    if not parts.scheme or not parts.netloc:
        # resolve_base_url already rstrips slashes; if it isn't a full URL we
        # can't reason about the path — return it verbatim with the default key.
        return raw.rstrip("/"), _DEFAULT_KEY
    segs = [s for s in (parts.path or "").split("/") if s]
    key = ""
    root_path_segs = segs
    for marker in ("notify", "cfg"):
        if marker in segs:
            i = segs.index(marker)
            if i + 1 < len(segs):
                key = segs[i + 1]
            root_path_segs = segs[:i]
            break
    else:
        if "json" in segs and "urls" in segs:
            i = segs.index("urls")
            if i + 1 < len(segs):
                key = segs[i + 1]
            root_path_segs = segs[:segs.index("json")]
    base = f"{parts.scheme}://{parts.netloc}"
    if root_path_segs:
        base += "/" + "/".join(root_path_segs)
    return base.rstrip("/"), (key.strip() or _DEFAULT_KEY)


def _shape_urls(body: Any) -> dict:
    """Shape an ``/json/urls/<key>`` payload into the card's fields. Defensive
    over every key (a malformed body yields an empty summary, never raises).

    Returns ``{endpoints, services: [{scheme, name, count}], tags: [...]}``.
    We read ONLY the URL scheme (before ``://``) to name each service — the
    rest of the URL carries secrets and is never surfaced."""
    body = body if isinstance(body, dict) else {}
    urls = as_list(body.get("urls"))
    counts: dict[str, int] = {}
    for entry in urls:
        if isinstance(entry, dict):
            url_str = str(entry.get("url") or "")
        elif isinstance(entry, str):
            url_str = entry
        else:
            url_str = ""
        scheme = url_str.split("://", 1)[0].strip().lower() if "://" in url_str else ""
        if scheme:
            counts[scheme] = counts.get(scheme, 0) + 1
    services = [{"scheme": s, "name": _service_name(s), "count": counts[s]}
                for s in sorted(counts)]
    raw_tags = as_list(body.get("tags"))
    tags = [str(t).strip() for t in raw_tags if isinstance(t, str) and str(t).strip()]
    # Apprise always reports an implicit "all" tag — keep it but de-noise dupes.
    seen: set = set()
    tags_unique = []
    for t in tags:
        if t.lower() not in seen:
            seen.add(t.lower())
            tags_unique.append(t)
    return {"endpoints": len(urls), "services": services, "tags": tags_unique[:16]}


# noinspection PyUnusedLocal
async def test_credential(host_row: dict, chip: dict, candidate_key: str, *,
                          payload: Optional[dict] = None, **_kw) -> dict:
    """Probe ``GET /json/urls/<key>`` and confirm it parses. No auth —
    ``candidate_key`` / ``payload`` are part of the generic route contract but
    unused here (apprise-api has no credentials). Returns ``{ok, detail,
    status}``."""
    base, key = _resolve_apprise(host_row, chip)
    if not base:
        return {"ok": False, "detail": "no upstream URL configured", "status": 0}
    url = base + "/json/urls/" + quote(key, safe="")
    try:
        async with httpx.AsyncClient(verify=False, timeout=10.0,
                                     follow_redirects=True) as cli:
            r = await cli.get(url, params={"privacy": "1"},
                              headers={"Accept": "application/json"})
    except (httpx.HTTPError, OSError) as e:  # noqa: BLE001
        print(f"[apprise] warning: test-connection {url} failed — {type(e).__name__}: {e}")
        return {"ok": False, "detail": f"{type(e).__name__}: {e}", "status": 0}
    print(f"[apprise] INFO test-connection url={url} -> HTTP {r.status_code} final={r.url}")
    if r.status_code == 204:
        return {"ok": True, "detail": f"OK (key '{key}' has no endpoints yet)", "status": 200}
    if r.status_code == 200:
        try:
            shaped = _shape_urls(r.json())
        except (ValueError, TypeError):
            shaped = {}
        n = safe_int(shaped.get("endpoints"))
        return {"ok": True,
                "detail": f"OK ({n} endpoint{'s' if n != 1 else ''} under '{key}')",
                "status": 200}
    if r.status_code == 404:
        return {"ok": False,
                "detail": f"no config under key '{key}' — set the chip URL to "
                          f".../notify/<key> or the apprise-api root",
                "status": 404}
    return {"ok": False, "detail": f"HTTP {r.status_code}", "status": r.status_code}


async def fetch_data(host_row: dict, chip: dict, *,
                     host_id: str, service_idx: int,
                     force: bool = False) -> dict:
    """Fetch the configured-endpoint summary for the expanded card.

    Returns ``{available, config_key, endpoints, services, tags, fetched_at}``.
    Raises ``ValueError`` (base URL won't resolve) / ``RuntimeError`` (upstream
    error) — the caller maps to an HTTPException; the SPA card shows the
    matching error branch."""
    now = time.time()
    base, hit = fetch_preamble(host_row, chip, host_id, service_idx, _data_cache,
                               resolve_cache_ttl(chip, DEFAULT_CACHE_TTL_S), now, force)
    if hit is not None:
        return hit
    _b2, key = _resolve_apprise(host_row, chip)
    url = base.rstrip("/") + "/json/urls/" + quote(key, safe="")
    print(f"[apprise] INFO fetch host={host_id} svc_idx={service_idx} url={url}")
    try:
        async with httpx.AsyncClient(verify=False, timeout=15.0,
                                     follow_redirects=True) as cli:
            r = await cli.get(url, params={"privacy": "1"},
                              headers={"Accept": "application/json"})
    except (httpx.HTTPError, OSError) as e:  # noqa: BLE001
        print(f"[apprise] error: fetch host={host_id} url={url} failed — {type(e).__name__}: {e}")
        raise RuntimeError(f"upstream fetch failed: {type(e).__name__}: {e}")
    if r.status_code == 204:
        # Key exists but has no stored notification URLs yet — not an error.
        out: dict[str, Any] = {"available": True, "fetched_at": int(now),
                               "config_key": key, "endpoints": 0,
                               "services": [], "tags": []}
        _data_cache[cache_key(host_id, service_idx)] = (now, out)
        return out
    if r.status_code == 404:
        raise RuntimeError(f"no Apprise config under key '{key}' — point the chip "
                           f"URL at .../notify/<key> or the apprise-api root ({url})")
    if r.status_code != 200:
        print(f"[apprise] error: fetch host={host_id} url={r.request.url} returned "
              f"HTTP {r.status_code} (check the chip URL points at the apprise-api root)")
        raise RuntimeError(f"upstream returned HTTP {r.status_code} for {url}")
    try:
        body = r.json()
    except (ValueError, TypeError):  # noqa: BLE001
        raise RuntimeError("upstream returned non-JSON")
    shaped = _shape_urls(body)
    out = {"available": True, "fetched_at": int(now), "config_key": key, **shaped}
    print(f"[apprise] INFO fetched host={host_id} key={key} endpoints={shaped['endpoints']} "
          f"services={[s['scheme'] for s in shaped['services']]} tags={shaped['tags']}")
    _data_cache[cache_key(host_id, service_idx)] = (now, out)
    return out


def peek_latest(host_id: str, service_idx: int) -> Optional[dict]:
    """Cache-only peek (no upstream call) for the AI context's
    ``app_skills[].last``. Returns the last fetched summary or ``None``."""
    data = peek_cache(_data_cache, host_id, service_idx)
    if not isinstance(data, dict) or not data.get("available"):
        return None
    services = as_list(data.get("services"))
    return {
        "config_key": data.get("config_key") or _DEFAULT_KEY,
        "endpoints": safe_int(data.get("endpoints")),
        "services": [str(s.get("name") or s.get("scheme") or "")
                     for s in services if isinstance(s, dict)],
        "tags": as_list(data.get("tags")),
        "fetched_at": safe_int(data.get("fetched_at")),
    }


# ---------------------------------------------------------------------------
# Skills
# ---------------------------------------------------------------------------
SKILLS: tuple[dict, ...] = (
    {
        "id": "apprise_status",
        "name": "Apprise status",
        "ai_phrases": ("apprise status, how many notification endpoints, what "
                       "notification services are configured, apprise config, "
                       "what tags can i notify, list apprise tags"),
        "destructive": False,
    },
    {
        "id": "apprise_test",
        "name": "Send a test notification",
        "ai_phrases": ("send an apprise test, test my notifications, fire a "
                       "test notification, check apprise works, send a test "
                       "notification"),
        "destructive": False,
    },
    {
        "id": "apprise_notify",
        "name": "Send a notification",
        "ai_phrases": ("send a notification, notify me, send an apprise "
                       "notification, push a message, send a notification to a "
                       "tag, notify the <tag> tag, alert <tag>, message <tag>"),
        "destructive": False,
        "arg": True,
        "arg_hint": ("the message to send; to route it to a specific Apprise "
                     "tag, start the message with the tag in square brackets, "
                     "e.g. '[telegram] the backup finished' or '[admins] disk "
                     "is full' — without a tag it goes to every endpoint"),
    },
)

# Notification body the test skill sends.
_TEST_BODY = "Test notification from OmniGrid."
# The test fires to OmniGrid's OWN Apprise tag (matches the APPRISE_TAG default
# used by OmniGrid's deploy/op notifications) rather than every configured
# endpoint — so a "test" doesn't spam every channel (and doesn't 424 just
# because some unrelated endpoint is misconfigured).
_TEST_TAG = "omnigrid"
# Parse a leading "[tag]" or "tag=<tag>" / "tag:<tag>" selector off a notify arg.
_TAG_BRACKET_RE = re.compile(r"^\s*\[(?P<tag>.+?)]\s*(?P<body>.*)$", re.S)
_TAG_PREFIX_RE = re.compile(r"^\s*tag[=:](?P<tag>\S+)\s+(?P<body>.*)$", re.S | re.I)


def _resolve_target(host_row: dict, chip: dict) -> "tuple[str, str, Optional[dict]]":
    """Resolve ``(base_url, config_key)`` for a skill, or a ready
    ``{ok: False, detail}`` when the URL won't resolve."""
    base, key = _resolve_apprise(host_row, chip)
    if not base:
        return "", "", {"ok": False, "status": 0, "detail": "no upstream URL configured"}
    return base, key, None


def _parse_notify_arg(arg: Optional[str]) -> "tuple[str, str]":
    """Split a notify arg into ``(tag, body)``. A leading ``[tag]`` or
    ``tag=<tag>`` / ``tag:<tag>`` selector picks the Apprise routing tag; the
    rest is the message body. No selector → ``("", arg)`` (all endpoints)."""
    s = (arg or "").strip()
    if not s:
        return "", ""
    m = _TAG_BRACKET_RE.match(s)
    if m:
        return m.group("tag").strip(), m.group("body").strip()
    m = _TAG_PREFIX_RE.match(s)
    if m:
        return m.group("tag").strip(), m.group("body").strip()
    return "", s


async def run_skill(skill_id: str, host_row: dict, chip: dict, *,
                    arg: Optional[str] = None,
                    host_id: Optional[str] = None,
                    service_idx: Optional[int] = None, **_kw) -> dict:
    """Dispatch one of this app's SKILLS. Returns ``{ok, detail, status?}``.
    Raises ValueError on an unknown skill id (route maps to HTTP 404)."""
    if skill_id == "apprise_status":
        return await _status_skill(host_row, chip, host_id=host_id,
                                   service_idx=service_idx)
    if skill_id == "apprise_test":
        return await _notify_skill(host_row, chip, body=_TEST_BODY, tag=_TEST_TAG,
                                   title="OmniGrid test", host_id=host_id)
    if skill_id == "apprise_notify":
        tag, body = _parse_notify_arg(arg)
        if not body:
            return {"ok": False, "status": 0,
                    "detail": "no message given — say what to send (optionally "
                              "prefix with [tag] to route to a tag)"}
        return await _notify_skill(host_row, chip, body=body, tag=tag,
                                   title="OmniGrid", host_id=host_id)
    raise ValueError(f"unknown skill: {skill_id!r}")


# noinspection DuplicatedCode
async def _status_skill(host_row: dict, chip: dict, *,
                        host_id: Optional[str] = None,
                        service_idx: Optional[int] = None) -> dict:
    """Read-only: live-fetch the configured-endpoint summary + format a detail
    block for the AI / drawer. Never raises."""
    print(f"[apprise] INFO apprise_status host={host_id} svc_idx={service_idx} (live fetch)")
    try:
        data = await fetch_data(host_row, chip, host_id=str(host_id or ""),
                                service_idx=int(service_idx or 0), force=True)
    except (ValueError, RuntimeError) as e:
        print(f"[apprise] warning: apprise_status host={host_id} could not fetch — {e}")
        return {"ok": False, "detail": str(e), "status": 0}
    endpoints = safe_int(data.get("endpoints"))
    services = as_list(data.get("services"))
    tags = as_list(data.get("tags"))
    svc_names = [str(s.get("name") or s.get("scheme") or "") for s in services if isinstance(s, dict)]
    lines = [f"📣 Endpoints: {endpoints}"]
    if svc_names:
        lines.append("🔌 Services: " + ", ".join(svc_names))
    if tags:
        lines.append("🏷️ Tags: " + ", ".join(str(t) for t in tags))
    if not svc_names and endpoints == 0:
        lines.append("ℹ️ No notification URLs configured under key "
                     f"'{data.get('config_key') or _DEFAULT_KEY}' yet.")
    return {
        "ok": True, "status": 200, "detail": "\n".join(lines),
        "endpoints": endpoints, "services": svc_names, "tags": [str(t) for t in tags],
    }


async def _notify_skill(host_row: dict, chip: dict, *, body: str, tag: str,
                        title: str, host_id: Optional[str] = None) -> dict:
    """Action: POST /notify/<key> to fire a notification (optionally routed to
    a tag) to the configured Apprise endpoints. Never raises."""
    base, key, err = _resolve_target(host_row, chip)
    if err:
        return err
    url = base + "/notify/" + quote(key, safe="")
    payload: dict[str, Any] = {"body": body, "title": title, "type": "info"}
    if tag:
        payload["tag"] = tag
    print(f"[apprise] INFO notify host={host_id} url={url} tag={tag or '(all)'} "
          f"len={len(body)}")
    try:
        async with httpx.AsyncClient(verify=False, timeout=20.0,
                                     follow_redirects=True) as cli:
            r = await cli.post(url, json=payload, headers={"Accept": "application/json"})
    except (httpx.HTTPError, OSError) as e:  # noqa: BLE001
        return {"ok": False, "status": 0, "detail": f"send failed: {type(e).__name__}: {e}"}
    if 200 <= r.status_code < 300:
        where = f" to tag '{tag}'" if tag else " to every endpoint"
        return {"ok": True, "status": r.status_code,
                "detail": f"✅ Notification sent{where}."}
    if r.status_code == 404:
        return {"ok": False, "status": 404,
                "detail": f"no Apprise config under key '{key}' (nothing to notify)"}
    # Pull the apprise-api error string out of the JSON body for a clear
    # message (it dumps a verbose {"error":..., "details":[...]} blob otherwise).
    err_text = ""
    try:
        jbody = r.json()
    except (ValueError, TypeError):
        jbody = None
    if isinstance(jbody, dict):
        err_text = str(jbody.get("error") or "").strip()
    if not err_text:
        try:
            err_text = (r.text or "").strip()[:200]
        except (ValueError, TypeError):
            err_text = ""
    # HTTP 424 = apprise ACCEPTED the request but at least one endpoint failed
    # to deliver (the others may have succeeded). When a tag was given AND the
    # error names the tag, it usually means no endpoint carries that tag.
    # Surface a clear partial-delivery message, NOT a scary "send failed".
    if r.status_code == 424:
        if tag:
            return {"ok": False, "status": 424,
                    "detail": f"⚠️ Apprise reported a delivery problem for tag "
                              f"'{tag}' (HTTP 424) — check that an endpoint is "
                              f"tagged '{tag}' and its config is valid."}
        detail = ("⚠️ Apprise accepted the notification but reported that one or "
                  "more endpoints failed to deliver (HTTP 424). The others may "
                  "have been sent — check the failing endpoint's config in "
                  "Apprise (e.g. a bad token or unreachable service).")
        return {"ok": False, "status": 424, "detail": detail}
    suffix = f" — {err_text}" if err_text else ""
    return {"ok": False, "status": r.status_code,
            "detail": f"send failed: HTTP {r.status_code}{suffix}"}
