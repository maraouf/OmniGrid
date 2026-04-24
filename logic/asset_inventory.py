"""Asset inventory consumer — OAuth2 client_credentials against oufa.co.

V1 contract (see notes/research/notes_personal_site_integration.txt):

  - Auth: OAuth2 client_credentials grant at ``{token_url}``.
  - Fetch: ``GET {base_url}/assets`` with ``Authorization: Bearer <token>``.
  - Cache: atomic ``.tmp`` + ``os.replace`` write at
    ``/app/data/asset_inventory.json``. The file is the ONLY source of
    truth the drawer reads from — manual refresh only, no lifespan loop.
  - Secrets: ``client_secret`` is write-only (see ``api_get_settings``
    ``client_secret_set`` flag).

Every routine is a pure async function — no module-level token cache
yet (V1 scope). If refresh cadence grows, layer a cache on top the same
way ``logic/registry.py`` does for registry tokens.
"""
from __future__ import annotations

import base64
import json
import os
import tempfile
import time
from typing import Any, Optional

import httpx

DEFAULT_CACHE_PATH = "/app/data/asset_inventory.json"
DEFAULT_LIST_PATH = "/assets"


async def probe_token(
    token_url: str,
    client_id: str,
    client_secret: str,
    scope: str = "",
    verify_tls: bool = True,
    timeout: float = 10.0,
) -> dict:
    """Run the OAuth2 client_credentials flow once.

    Returns ``{"ok": bool, "token_type": str, "expires_in": int,
    "access_token": str, "error": str}``. The access token is RETURNED
    here because the caller (``refresh_cache``) needs it for the next
    hop; callers that only want auth-validation should ignore it.
    """
    if not token_url or not client_id or not client_secret:
        return {"ok": False, "token_type": "", "expires_in": 0,
                "access_token": "", "error": "missing token_url / client_id / client_secret"}
    base_data: dict[str, str] = {"grant_type": "client_credentials"}
    if scope:
        base_data["scope"] = scope
    # RFC 6749 §2.3.1 allows client authentication via EITHER Basic
    # header OR form body parameters; servers pick one. Keycloak /
    # Authentik accept both; Oracle APEX (oufa.co's flavour) REJECTS
    # Basic with "missing username or password in client_credentials
    # grant Ex3552" and requires body params. So: try Basic first
    # (standards-preferred), fall back to body params on 400/401 so
    # APEX-style servers also work.
    basic = base64.b64encode(f"{client_id}:{client_secret}".encode("utf-8")).decode("ascii")

    async def _post(method: str) -> tuple[int, str, Any]:
        """Returns (status_code, text_preview, parsed_json_or_None)."""
        if method == "basic":
            headers = {
                "Authorization": f"Basic {basic}",
                "Content-Type": "application/x-www-form-urlencoded",
                "Accept": "application/json",
            }
            body = dict(base_data)
        else:  # "body"
            headers = {
                "Content-Type": "application/x-www-form-urlencoded",
                "Accept": "application/json",
            }
            body = dict(base_data)
            body["client_id"] = client_id
            body["client_secret"] = client_secret
        async with httpx.AsyncClient(verify=verify_tls, timeout=timeout) as client:
            resp = await client.post(token_url, data=body, headers=headers)
        # Try to parse JSON eagerly so the retry logic can see a
        # structured error; non-JSON body is fine too.
        try:
            parsed = resp.json()
        except Exception:
            parsed = None
        return resp.status_code, (resp.text or "")[:400], parsed

    try:
        status, preview, payload = await _post("basic")
        # Fallback: APEX-style servers return 400 with
        # "missing username or password" when Basic is used. Retry
        # with body-params credentials. Also retry on 401 in case a
        # server silently rejects Basic.
        needs_body = False
        if status in (400, 401):
            lower = (preview or "").lower()
            if ("missing username or password" in lower
                    or "invalid_client" in lower
                    or status == 401):
                needs_body = True
        if needs_body:
            print(f"[asset_inventory] token endpoint rejected Basic auth ({status}); "
                  f"retrying with body-param credentials. preview={preview[:160]!r}")
            status, preview, payload = await _post("body")
        if status == 401:
            return {"ok": False, "token_type": "", "expires_in": 0,
                    "access_token": "",
                    "error": "OAuth2 auth rejected (401) — check client_id / client_secret"}
        if status >= 400:
            return {"ok": False, "token_type": "", "expires_in": 0,
                    "access_token": "",
                    "error": f"token endpoint HTTP {status}: {preview}"}
        if payload is None:
            return {"ok": False, "token_type": "", "expires_in": 0,
                    "access_token": "",
                    "error": f"token endpoint returned non-JSON: {preview}"}
    except Exception as e:
        return {"ok": False, "token_type": "", "expires_in": 0,
                "access_token": "", "error": f"{type(e).__name__}: {e}"}
    access_token = str(payload.get("access_token") or "")
    if not access_token:
        return {"ok": False, "token_type": "", "expires_in": 0,
                "access_token": "",
                "error": f"token response missing access_token: {json.dumps(payload)[:200]}"}
    return {
        "ok": True,
        "token_type":   str(payload.get("token_type") or "Bearer"),
        "expires_in":   int(payload.get("expires_in") or 0),
        "access_token": access_token,
        "error": "",
    }


async def fetch_assets(
    base_url: str,
    token: str,
    list_path: str = DEFAULT_LIST_PATH,
    verify_tls: bool = True,
    timeout: float = 15.0,
) -> dict:
    """Fetch the asset list. Returns ``{"ok", "assets", "error"}``.

    ``base_url`` + ``list_path`` are concatenated into the full URL; we
    don't hardcode ``/assets`` so operators can point at whatever the
    upstream actually exposes.
    """
    if not base_url or not token:
        return {"ok": False, "assets": [], "error": "missing base_url / token"}
    url = base_url.rstrip("/") + (list_path if list_path.startswith("/") else "/" + list_path)
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/json",
    }
    try:
        async with httpx.AsyncClient(verify=verify_tls, timeout=timeout) as client:
            r = await client.get(url, headers=headers)
        if r.status_code == 401:
            return {"ok": False, "assets": [],
                    "error": "asset list 401 — token rejected"}
        if r.status_code >= 400:
            return {"ok": False, "assets": [],
                    "error": f"asset list HTTP {r.status_code}: {r.text[:200]}"}
        payload = r.json()
    except ValueError as e:
        return {"ok": False, "assets": [],
                "error": f"asset list returned non-JSON: {e}"}
    except Exception as e:
        return {"ok": False, "assets": [], "error": f"{type(e).__name__}: {e}"}
    # Accept a top-level list OR an object with {assets: [...]} / {data: [...]}
    # so this consumer works with a variety of upstream shapes without
    # the operator having to re-package the response.
    assets: list = []
    if isinstance(payload, list):
        assets = payload
    elif isinstance(payload, dict):
        for k in ("assets", "data", "items", "results"):
            if isinstance(payload.get(k), list):
                assets = payload[k]
                break
    return {"ok": True, "assets": assets, "error": ""}


def load_cache(cache_path: str = DEFAULT_CACHE_PATH) -> dict:
    """Read the persisted cache file.

    Returns ``{"ok": True, "ts", "assets", "count", "error": ""}`` on
    success or ``{"ok": False, "error"}`` otherwise. Missing file is
    not an error from the operator's perspective (nothing refreshed
    yet); we return ``ok=False`` with a clear ``error`` so the UI can
    render an empty-state card.
    """
    try:
        with open(cache_path, "r", encoding="utf-8") as f:
            raw = f.read()
    except FileNotFoundError:
        return {"ok": False, "ts": 0, "assets": [], "count": 0,
                "error": "no cache — run Refresh to populate"}
    except Exception as e:
        return {"ok": False, "ts": 0, "assets": [], "count": 0,
                "error": f"{type(e).__name__}: {e}"}
    try:
        data = json.loads(raw or "{}")
    except ValueError as e:
        return {"ok": False, "ts": 0, "assets": [], "count": 0,
                "error": f"cache JSON parse failed: {e}"}
    if not isinstance(data, dict):
        return {"ok": False, "ts": 0, "assets": [], "count": 0,
                "error": "cache is not a JSON object"}
    assets = data.get("assets") if isinstance(data.get("assets"), list) else []
    return {
        "ok":     True,
        "ts":     int(data.get("ts") or 0),
        "assets": assets,
        "count":  len(assets),
        "upstream": str(data.get("upstream") or ""),
        "error":  "",
    }


def save_cache(
    assets: list,
    cache_path: str = DEFAULT_CACHE_PATH,
    upstream: str = "",
) -> None:
    """Atomic write of the asset cache.

    Path is created if the parent dir doesn't exist. The ``.tmp`` +
    ``os.replace`` dance guarantees the file is either the old content
    or the new content — never a partial write.
    """
    os.makedirs(os.path.dirname(cache_path) or ".", exist_ok=True)
    payload = {
        "ts":       int(time.time()),
        "upstream": upstream,
        "count":    len(assets),
        "assets":   assets,
    }
    # tempfile.NamedTemporaryFile + rename gives us atomicity without
    # a manual flush/fsync (OS handles it). ``delete=False`` because
    # we rename it to the final path.
    fd, tmp_path = tempfile.mkstemp(
        prefix=".asset_inventory.",
        suffix=".tmp",
        dir=os.path.dirname(cache_path) or ".",
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(payload, f, separators=(",", ":"))
        os.replace(tmp_path, cache_path)
    except Exception:
        # Clean up the temp file on any failure — stale .tmp files
        # would otherwise accumulate in the data dir.
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


async def refresh_cache(
    base_url: str,
    token_url: str,
    client_id: str,
    client_secret: str,
    scope: str = "",
    verify_tls: bool = True,
    list_path: str = DEFAULT_LIST_PATH,
    cache_path: str = DEFAULT_CACHE_PATH,
) -> dict:
    """Compose probe_token → fetch_assets → save_cache.

    Returns a summary dict:
      ``{"ok": bool, "count": int, "ts": int, "error": str,
         "upstream": str}``
    """
    token_result = await probe_token(
        token_url, client_id, client_secret,
        scope=scope, verify_tls=verify_tls,
    )
    if not token_result.get("ok"):
        return {
            "ok": False, "count": 0, "ts": 0,
            "upstream": base_url,
            "error": token_result.get("error") or "token probe failed",
        }
    fetch_result = await fetch_assets(
        base_url, token_result["access_token"],
        list_path=list_path, verify_tls=verify_tls,
    )
    if not fetch_result.get("ok"):
        return {
            "ok": False, "count": 0, "ts": 0,
            "upstream": base_url,
            "error": fetch_result.get("error") or "asset fetch failed",
        }
    assets = fetch_result.get("assets") or []
    try:
        save_cache(assets, cache_path=cache_path, upstream=base_url)
    except Exception as e:
        return {
            "ok": False, "count": len(assets), "ts": 0,
            "upstream": base_url,
            "error": f"cache write failed: {type(e).__name__}: {e}",
        }
    return {
        "ok": True, "count": len(assets), "ts": int(time.time()),
        "upstream": base_url, "error": "",
    }


def index_by_custom_number(assets: list) -> dict[int, dict]:
    """Build a {custom_number: asset} map for drawer lookups.

    Accepts either snake_case (``custom_number``) or the upstream's
    camelCase (``CustomNumber``) — PersonalSite's JSON schema used
    the latter. Returns only rows whose CN parses as an int.
    """
    out: dict[int, dict] = {}
    if not isinstance(assets, list):
        return out
    for a in assets:
        if not isinstance(a, dict):
            continue
        cn = a.get("custom_number")
        if cn is None:
            cn = a.get("CustomNumber")
        if cn is None:
            cn = a.get("customNumber")
        try:
            ci = int(cn)
        except (TypeError, ValueError):
            continue
        out[ci] = a
    return out
