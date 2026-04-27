"""Asset inventory consumer — OAuth2 client_credentials against <asset-api-host>.

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
import re
import tempfile
import time
from typing import Any, Optional

import httpx

from logic import errors as _err

# #442 — track the operator's `DB_PATH` data dir convention. Reading
# DB_PATH lazily via os.environ keeps the constant import-time-safe
# even when the variable is set by docker-compose only. Defaults to
# the legacy `/app/data/asset_inventory.json` when DB_PATH is unset
# OR when its dirname is empty (e.g. someone set `DB_PATH=omnigrid.db`
# with no leading directory — uncommon but possible).
def _default_cache_path() -> str:
    db_path = os.environ.get("DB_PATH", "/app/data/omnigrid.db")
    data_dir = os.path.dirname(db_path) or "/app/data"
    return os.path.join(data_dir, "asset_inventory.json")


DEFAULT_CACHE_PATH = _default_cache_path()
DEFAULT_LIST_PATH = "/assets"
# Lifetime-token auth mode (<asset-api-host>'s `services.php` endpoint). POST
# form-encoded with `X-Authorization: Bearer <key>` — no token exchange,
# one request per refresh. Operator pastes the static key into Admin.
DEFAULT_LIFETIME_LIST_PATH = "/services.php"

AUTH_MODE_OAUTH2 = "oauth2"
AUTH_MODE_LIFETIME_TOKEN = "lifetime_token"


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
    # Authentik accept both; Oracle APEX (<asset-api-host>'s flavour) REJECTS
    # Basic with "missing username or password in client_credentials
    # grant Ex3552" and requires body params. So: try Basic first
    # (standards-preferred), fall back to body params on 400/401 so
    # APEX-style servers also work.
    basic = base64.b64encode(f"{client_id}:{client_secret}".encode("utf-8")).decode("ascii")

    async def _post(method: str) -> tuple[int, str, Any]:
        """Returns (status_code, text_preview, parsed_json_or_None).

        Three auth methods supported:
          - "basic"    — RFC 6749 §2.3.1 Basic auth header (default).
          - "body"     — RFC-allowed body params `client_id` +
                         `client_secret`.
          - "userpass" — <asset-api-host> / Oracle APEX flavour: body params
                         named `username` + `password` (token
                         selector goes in username, secret in
                         password). Documented in the upstream's
                         own curl example:
                         ``grant_type=client_credentials&
                         username=...&password=...``.
        """
        if method == "basic":
            headers = {
                "Authorization": f"Basic {basic}",
                "Content-Type": "application/x-www-form-urlencoded",
                "Accept": "application/json",
            }
            body = dict(base_data)
        elif method == "body":
            headers = {
                "Content-Type": "application/x-www-form-urlencoded",
                "Accept": "application/json",
            }
            body = dict(base_data)
            body["client_id"] = client_id
            body["client_secret"] = client_secret
        else:  # "userpass" — <asset-api-host> / APEX style
            headers = {
                "Content-Type": "application/x-www-form-urlencoded",
                "Accept": "application/json",
            }
            body = dict(base_data)
            body["username"] = client_id
            body["password"] = client_secret
        async with httpx.AsyncClient(verify=verify_tls, timeout=timeout) as client:
            resp = await client.post(token_url, data=body, headers=headers)
        # Try to parse JSON eagerly so the retry logic can see a
        # structured error; non-JSON body is fine too.
        try:
            parsed = resp.json()
        except Exception:
            parsed = None
        return resp.status_code, (resp.text or "")[:400], parsed

    def _looks_like_apex_user_error(preview: str) -> bool:
        # APEX returns: {"error":"invalid_request","error_description":
        # "ERROR occurred due to missing username or password in
        # client_credentials grant Ex3552"}. Match on the distinctive
        # phrase so we only try the non-standard userpass fallback
        # when the server specifically requests those field names.
        return "missing username or password" in (preview or "").lower()

    try:
        status, preview, payload = await _post("basic")
        attempts = [("basic", status, preview)]
        # Fallback 1: standard body-param credentials (client_id /
        # client_secret). Triggered by 401 or invalid_client-style
        # errors under Basic auth.
        if status == 401 or (status == 400 and "invalid_client" in (preview or "").lower()):
            print(f"[asset_inventory] token endpoint rejected Basic auth ({status}); "
                  f"retrying with body-param credentials. preview={preview[:160]!r}")
            status, preview, payload = await _post("body")
            attempts.append(("body", status, preview))
        # Fallback 2: <asset-api-host> / APEX non-standard `username`+`password`
        # body fields. Only triggered when the server literally says
        # "missing username or password" — we don't want to leak the
        # secret into an arbitrary server's form-fields blindly.
        if status in (400, 401) and _looks_like_apex_user_error(preview):
            print(f"[asset_inventory] token endpoint wants APEX-style "
                  f"username/password body params ({status}); retrying. "
                  f"preview={preview[:160]!r}")
            status, preview, payload = await _post("userpass")
            attempts.append(("userpass", status, preview))
        if status == 401:
            return {"ok": False, "token_type": "", "expires_in": 0,
                    "access_token": "",
                    "error": f"OAuth2 auth rejected (401) — check credentials. Tried: "
                             + ", ".join(m for m, _, _ in attempts)}
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


_PAGE_SIZE = 50   # ASSET_SERVICES_DATABASE_RECORDS_LIMITS default (see <asset-api-host> API guide §4.1.3)
_ERR_NO_RECORDS = "1686"  # ERROR_1686 "no matching records" — tolerated as empty batch


_CODE_PREFIX_RE = re.compile(r"^(?:Ex|ERR_|Error_)", re.IGNORECASE)


def _normalize_code(raw: Any) -> str:
    """Strip the upstream API's code-prefix family from a response
    ``code`` field.

    Upstream returns codes in any of three shapes depending on the
    action / version: ``"Ex1686"``, ``"ERR_1686"``, ``"Error_1686"``,
    or bare ``"1686"``. ENH-010 (#425) — the prefix charset is now
    `(?i)(?:Ex|ERR_|Error_)` so all three normalise to the bare numeric
    string. Downstream equality checks (``_ERR_NO_RECORDS``, pagination
    tolerance) and error formatters (``[Ex{code}]``) work uniformly
    without double-prefixing into ``[ExEx1686]`` / ``[ExError_1686]``.
    """
    s = str(raw or "").strip()
    m = _CODE_PREFIX_RE.match(s)
    # #432 — Real upstream codes are positive ints. Don't lstrip a
    # leading `-`; that would let `Ex-1686` through (`s[m.end():] = "-1686"`,
    # after lstrip is `"1686"` which would falsely match `_ERR_NO_RECORDS`).
    # `.isdigit()` alone correctly rejects negatives.
    if m and s[m.end():].isdigit():
        return s[m.end():]
    return s


_ASSET_LIST_KEYS = (
    "assets", "result", "records", "data", "items", "results",
    "services", "list", "rows", "range",
)
_ASSET_ENVELOPE_KEYS = {
    "return", "message", "reference_id", "service_name",
    "service", "details", "code",
}


def _extract_assets_from_payload(payload: Any) -> list:
    """Pull a list of assets out of the upstream JSON response.

    <asset-api-host>'s response envelope puts the data under an action-specific
    key. The asset-range action uses ``result``; the single-row
    actions use ``asset`` (see §5 of the API guide — "<payload-key>:
    { … } varies per action"). The extractor handles three shapes
    under any of the known names:

      - direct list of asset dicts
      - dict-wrapper (``{"result": {"assets": [...]}}``) — recurse
      - single asset dict — wrap into a 1-item list

    On total miss, falls back to a shape-based scan and logs the
    payload's keys so the operator can see what the upstream is
    using.
    """
    if isinstance(payload, list):
        return payload
    if not isinstance(payload, dict):
        return []

    # Pass 1: known names → direct list / dict-wrapper / dict.
    for k in _ASSET_LIST_KEYS:
        v = payload.get(k)
        if isinstance(v, list):
            return v
        if isinstance(v, dict):
            # Dict-wrapper — recurse one level. Common when the
            # upstream wraps records under `result.assets` or
            # `result.records` for pagination metadata siblings.
            inner = _extract_assets_from_payload(v)
            if inner:
                print(f"[asset_inventory] extracted assets from "
                      f"{k}.<inner> ({len(inner)} rows)")
                return inner
            # `result` might also be a single asset dict for
            # by-id-style responses returned under `result` instead
            # of `asset`. Wrap it.
            if v and not all(isinstance(val, (dict, list)) for val in v.values()):
                # Looks like a flat asset dict (most values scalar).
                return [v]

    # Pass 2: single-row actions wrap the dict under `asset`.
    single = payload.get("asset")
    if isinstance(single, dict):
        return [single]

    # Pass 3: shape-based fallback. Find any top-level value that's a
    # list of dicts — that's almost certainly the asset list under a
    # name we haven't seen before. Log it so we can name-add it.
    for k, v in payload.items():
        if k in _ASSET_ENVELOPE_KEYS:
            continue
        if isinstance(v, list) and v and isinstance(v[0], dict):
            print(f"[asset_inventory] extracted assets from unknown key "
                  f"{k!r} ({len(v)} rows) — add to _ASSET_LIST_KEYS "
                  f"if this becomes common")
            return v

    # Pass 4: diagnostic — nothing matched. Log the payload shape so
    # the operator can report back with the actual key. Include a
    # value-type sketch so dict-wrappers are visible. If `result`
    # exists, also dump the first 400 chars of its JSON so we can
    # see the actual shape (string? null? object? nested?).
    sketch = {k: type(v).__name__ for k, v in payload.items()}
    print(f"[asset_inventory] no list-valued payload found. "
          f"Top-level keys: {sketch!r}")
    if "result" in payload:
        try:
            preview = json.dumps(payload["result"], default=str)[:400]
        except Exception:
            preview = repr(payload["result"])[:400]
        print(f"[asset_inventory] result sample: {preview}")
    if (str(payload.get("return")) not in ("1", "True")
            and (payload.get("details") or payload.get("message"))):
        print(f"[asset_inventory] envelope says NOT success — "
              f"return={payload.get('return')!r} "
              f"code={payload.get('code')!r} "
              f"details={(payload.get('details') or payload.get('message'))!r}")
    return []


async def _post_asset_api(
    endpoint_url: str,
    token: str,
    body: dict,
    verify_tls: bool,
    timeout: float,
) -> dict:
    """Low-level: one POST to services.php, envelope-aware.

    Returns ``{"ok": bool, "assets": list, "error": str, "code": str,
    "reference_id": str}``. Callers decide what to do with ``code`` —
    ``fetch_assets_lifetime_token`` tolerates ``1686`` (no matching
    records) during pagination, for example.
    """
    headers = {
        "X-Authorization": f"Bearer {token}",
        "Content-Type": "application/x-www-form-urlencoded",
        "Accept": "application/json",
    }
    try:
        async with httpx.AsyncClient(verify=verify_tls, timeout=timeout) as client:
            r = await client.post(endpoint_url, data=body, headers=headers)
    except Exception as e:
        og_err = _err.classify_exception(e)
        return {"ok": False, "assets": [], "error": og_err.message,
                "error_code": og_err.code, "error_params": og_err.params,
                "code": "", "reference_id": ""}

    # Try to parse JSON eagerly so we can pull reference_id / details
    # off a failure response too.
    try:
        parsed = r.json()
    except ValueError:
        parsed = None
    ref_id = str((parsed or {}).get("reference_id") or "") if isinstance(parsed, dict) else ""
    if ref_id:
        print(f"[asset_inventory] reference_id={ref_id} status={r.status_code} "
              f"body={body!r}")

    # HTTP-level failures first — 401 = token dead, 403 = scope denied,
    # 4xx/5xx = everything else. Surface the envelope's details+code
    # when present since it's more actionable than the raw body preview.
    if r.status_code == 401:
        og_err = _err.make_error(_err.AUTH_TOKEN_REJECTED,
                                 params={"reference_id": ref_id})
        code = _normalize_code((parsed or {}).get("code")) if isinstance(parsed, dict) else ""
        return {"ok": False, "assets": [], "error": og_err.message,
                "error_code": og_err.code, "error_params": og_err.params,
                "code": code, "reference_id": ref_id}
    if r.status_code == 403:
        details = str((parsed or {}).get("details") or "").strip() if isinstance(parsed, dict) else ""
        code = _normalize_code((parsed or {}).get("code")) if isinstance(parsed, dict) else ""
        combined = (f"{details or ''}" + (f" [Ex{code}]" if code else "")).strip()
        og_err = _err.make_error(
            _err.AUTH_SCOPE_DENIED,
            params={"upstream_code": code, "reference_id": ref_id},
            override_message=(combined or None),
        )
        return {"ok": False, "assets": [], "error": og_err.message,
                "error_code": og_err.code, "error_params": og_err.params,
                "code": code, "reference_id": ref_id}
    if r.status_code >= 400:
        details = str((parsed or {}).get("details") or "").strip() if isinstance(parsed, dict) else ""
        code = _normalize_code((parsed or {}).get("code")) if isinstance(parsed, dict) else ""
        body_preview = (details or r.text[:200])
        og_err = _err.make_error(
            _err.UPSTREAM_HTTP_ERROR,
            params={"http_status": r.status_code, "upstream_code": code,
                    "reference_id": ref_id},
            override_message=f"HTTP {r.status_code}: {body_preview}"
                             + (f" [Ex{code}]" if code else ""),
        )
        return {"ok": False, "assets": [], "error": og_err.message,
                "error_code": og_err.code, "error_params": og_err.params,
                "code": code, "reference_id": ref_id}

    if parsed is None:
        og_err = _err.make_error(
            _err.UPSTREAM_NON_JSON,
            params={"reference_id": ref_id},
            override_message=f"response not JSON: {r.text[:200]}",
        )
        return {"ok": False, "assets": [], "error": og_err.message,
                "error_code": og_err.code, "error_params": og_err.params,
                "code": "", "reference_id": ref_id}

    # 200 OK but the envelope may still report failure. `return` codes:
    # 0 = Failure, 1 = Success, 2 = Processing, 3 = Stalled. Treat
    # anything other than 1 as not-yet-useful for a cache refresh.
    if isinstance(parsed, dict) and "return" in parsed:
        ret_code = parsed.get("return")
        if ret_code == 0 or ret_code == "0":
            details = str(parsed.get("details") or parsed.get("message") or "").strip()
            code = _normalize_code(parsed.get("code"))
            og_err = _err.make_error(
                _err.UPSTREAM_FAILURE,
                params={"upstream_code": code, "reference_id": ref_id},
                override_message=(details or "upstream reported failure")
                                 + (f" [Ex{code}]" if code else ""),
            )
            return {"ok": False, "assets": [], "error": og_err.message,
                    "error_code": og_err.code, "error_params": og_err.params,
                    "code": code, "reference_id": ref_id}
        if ret_code not in (1, "1"):
            # `return: 2` (Processing) or `return: 3` (Stalled) — no data yet.
            og_err = _err.make_error(
                _err.UPSTREAM_UNEXPECTED,
                params={"return": ret_code, "reference_id": ref_id},
                override_message=f"upstream return={ret_code} — "
                                 f"{parsed.get('message') or '(no message)'}",
            )
            return {"ok": False, "assets": [], "error": og_err.message,
                    "error_code": og_err.code, "error_params": og_err.params,
                    "code": _normalize_code(parsed.get("code")),
                    "reference_id": ref_id}

    return {"ok": True, "assets": _extract_assets_from_payload(parsed),
            "error": "", "code": "", "reference_id": ref_id}


async def fetch_assets_lifetime_token(
    endpoint_url: str,
    token: str,
    service: str = "",
    action: str = "",
    min_value: Optional[int] = None,
    max_value: Optional[int] = None,
    verify_tls: bool = True,
    timeout: float = 15.0,
) -> dict:
    """Fetch assets via <asset-api-host>'s lifetime-token auth flavour.

    POST form-encoded to ``endpoint_url`` (full URL — already includes
    the list path) with ``X-Authorization: Bearer <token>`` plus two
    required routing params (``service`` / ``action``) and, when the
    action is a range query, ``min_value`` / ``max_value``. Operator
    configures all four from Admin → Asset inventory — OmniGrid does
    NOT hardcode any combination.

    Pagination: when ``action == 'get_assets_custom_number_range'``
    AND both bounds are supplied, the fetch is split into windows of
    ``_PAGE_SIZE`` (50, matching the server's documented default cap)
    and the results concatenated. Empty windows — the server returns
    ``ERROR_1686`` ("no matching records") for a gap in the custom-number
    range — are tolerated; other errors bail the whole batch.

    Returns ``{"ok", "assets", "error"}``. Envelope parsing is in
    :func:`_post_asset_api` — see §5 of the <asset-api-host> API guide for the full
    contract: ``return == 1`` is success, 0 is failure (with
    ``details`` + ``code`` + ``reference_id``), 2/3 are the
    Processing/Stalled states.
    """
    if not endpoint_url or not token:
        og_err = _err.make_error(
            _err.AUTH_CREDS_INCOMPLETE,
            override_message="missing endpoint_url / token",
        )
        return {"ok": False, "assets": [], "error": og_err.message,
                "error_code": og_err.code, "error_params": og_err.params}

    # Shared base body — service/action are always forwarded when set so
    # upstream's specific error code (Ex3537 for missing service, etc.)
    # reaches the operator unmasked by a client-side reject.
    base: dict[str, str] = {}
    if service:
        base["service"] = service
    if action:
        base["action"] = action

    do_paginate = (
        action == "get_assets_custom_number_range"
        and min_value is not None
        and max_value is not None
    )
    if not do_paginate:
        body = dict(base)
        if min_value is not None:
            body["min_value"] = str(int(min_value))
        if max_value is not None:
            body["max_value"] = str(int(max_value))
        res = await _post_asset_api(endpoint_url, token, body, verify_tls, timeout)
        out = {"ok": res["ok"], "assets": res["assets"], "error": res["error"]}
        if not res["ok"]:
            out["error_code"] = res.get("error_code", _err.NETWORK_ERROR)
            out["error_params"] = res.get("error_params", {})
        return out

    # Pagination: walk [lo, hi] in _PAGE_SIZE-sized windows. We don't
    # rely on the upstream to advertise its limit — the guide pins it
    # at 50 by default, tenants can tune it server-side, but batching
    # with 50 is always safe (smaller pages are never an error).
    lo = int(min_value)
    hi = int(max_value)
    if lo > hi:
        og_err = _err.make_error(
            _err.ASSET_RANGE_INVALID,
            params={"min": lo, "max": hi},
            override_message=f"min_value ({lo}) > max_value ({hi})",
        )
        return {"ok": False, "assets": [], "error": og_err.message,
                "error_code": og_err.code, "error_params": og_err.params}
    all_assets: list = []
    cursor = lo
    while cursor <= hi:
        win_hi = min(cursor + _PAGE_SIZE - 1, hi)
        body = dict(base)
        body["min_value"] = str(cursor)
        body["max_value"] = str(win_hi)
        res = await _post_asset_api(endpoint_url, token, body, verify_tls, timeout)
        if not res["ok"]:
            # Tolerate "no matching records" in a window — gaps in the
            # CN range are the norm (deleted assets leave holes).
            if str(res.get("code") or "") == _ERR_NO_RECORDS:
                cursor = win_hi + 1
                continue
            return {
                "ok": False, "assets": [],
                "error": (f"batch {cursor}-{win_hi}: " + res["error"]).strip(),
                "error_code":   res.get("error_code", _err.NETWORK_ERROR),
                "error_params": {**res.get("error_params", {}),
                                 "batch_min": cursor, "batch_max": win_hi},
            }
        all_assets.extend(res["assets"])
        cursor = win_hi + 1
    return {"ok": True, "assets": all_assets, "error": ""}


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
    token_url: str = "",
    client_id: str = "",
    client_secret: str = "",
    scope: str = "",
    verify_tls: bool = True,
    list_path: str = DEFAULT_LIST_PATH,
    cache_path: str = DEFAULT_CACHE_PATH,
    auth_mode: str = AUTH_MODE_OAUTH2,
    lifetime_token: str = "",
    service: str = "",
    action: str = "",
    min_value: Optional[int] = None,
    max_value: Optional[int] = None,
) -> dict:
    """Compose auth + fetch + save_cache into a single refresh call.

    Two auth flavours:
      - ``oauth2`` (default): probe_token → fetch_assets.
      - ``lifetime_token``: single POST to ``{base_url}{list_path}``
        with ``X-Authorization: Bearer <lifetime_token>`` — no token
        exchange. ``list_path`` defaults to ``/services.php`` in this
        mode (operators pointing at <asset-api-host>'s flavour) but can be
        overridden.

    Returns a summary dict:
      ``{"ok": bool, "count": int, "ts": int, "error": str,
         "upstream": str}``
    """
    if auth_mode == AUTH_MODE_LIFETIME_TOKEN:
        if not base_url or not lifetime_token:
            og_err = _err.make_error(
                _err.AUTH_CREDS_INCOMPLETE,
                override_message="missing base_url / lifetime_token",
            )
            return {
                "ok": False, "count": 0, "ts": 0,
                "upstream": base_url,
                "error": og_err.message,
                "error_code": og_err.code, "error_params": og_err.params,
            }
        effective_list_path = (
            list_path if list_path and list_path != DEFAULT_LIST_PATH
            else DEFAULT_LIFETIME_LIST_PATH
        )
        endpoint = base_url.rstrip("/") + (
            effective_list_path if effective_list_path.startswith("/")
            else "/" + effective_list_path
        )
        fetch_result = await fetch_assets_lifetime_token(
            endpoint, lifetime_token,
            service=service, action=action,
            min_value=min_value, max_value=max_value,
            verify_tls=verify_tls,
        )
        if not fetch_result.get("ok"):
            out = {
                "ok": False, "count": 0, "ts": 0,
                "upstream": base_url,
                "error": fetch_result.get("error") or "asset fetch failed",
            }
            if "error_code" in fetch_result:
                out["error_code"] = fetch_result["error_code"]
                out["error_params"] = fetch_result.get("error_params", {})
            return out
        assets = fetch_result.get("assets") or []
        try:
            save_cache(assets, cache_path=cache_path, upstream=base_url)
        except Exception as e:
            og_err = _err.make_error(
                _err.ASSET_CACHE_WRITE_FAILED,
                override_message=f"cache write failed: {type(e).__name__}: {e}",
            )
            return {
                "ok": False, "count": len(assets), "ts": 0,
                "upstream": base_url,
                "error": og_err.message,
                "error_code": og_err.code, "error_params": og_err.params,
            }
        return {
            "ok": True, "count": len(assets), "ts": int(time.time()),
            "upstream": base_url, "error": "",
        }

    # OAuth2 client_credentials (default, legacy path).
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


def _pick_named(*candidates) -> str:
    """Return the first non-empty string from a list of candidates that
    are either bare strings or {Name|name|CalculatedName} dicts.
    Mirrors the JS frontend's `pick()` helper inside `assetForHost`."""
    for v in candidates:
        if v is None:
            continue
        if isinstance(v, str) and v.strip():
            return v.strip()
        if isinstance(v, dict):
            for k in ("CalculatedName", "Name", "name"):
                s = v.get(k)
                if isinstance(s, str) and s.strip():
                    return s.strip()
    return ""


def shape_asset(a: dict) -> Optional[dict]:
    """Compact public shape for an asset row, mirrored from the
    frontend's `assetForHost(h)` resolver so the same field names are
    available to backend API consumers (`/api/hosts*` injects this
    onto each curated host row when the custom_number matches).

    Returns ``None`` when the input isn't a dict.
    """
    if not isinstance(a, dict):
        return None

    # Hostname CSV → list of FQDNs.
    hostname_str = str(a.get("Hostname") or a.get("hostname") or "").strip()
    hostnames = [s.strip() for s in hostname_str.split(",")] if hostname_str else []
    hostnames = [h for h in hostnames if h]

    # Interfaces — normalize and order by Number then Name. Same shape
    # the frontend uses (lowercase keys).
    raw_ifaces = a.get("Interfaces") or a.get("interfaces") or []
    ifaces = []
    if isinstance(raw_ifaces, list):
        for i in raw_ifaces:
            if not isinstance(i, dict):
                continue
            ifaces.append({
                "name":       str(i.get("Name") or i.get("name") or "").strip(),
                "ip":         str(i.get("IP") or i.get("ip") or "").strip(),
                "mac":        str(i.get("MacAddress") or i.get("mac_address") or "").strip(),
                "number":     i.get("Number"),
                "comment":    str(i.get("Comment") or "").strip(),
                "enabled":    i.get("IsEnabled") is not False,
                "ip_version": str(i.get("IPVersion") or i.get("ip_version") or "").strip(),
            })
    ifaces.sort(key=lambda x: (
        x["number"] if isinstance(x["number"], int) else 1_000_000_000,
        x["name"],
    ))

    # Primary IP — first enabled interface, fallback to first iface.
    primary_ip = ""
    for f in ifaces:
        if f["enabled"] and f["ip"]:
            primary_ip = f["ip"]
            break
    if not primary_ip:
        for f in ifaces:
            if f["ip"]:
                primary_ip = f["ip"]
                break

    # Ports — flatten the nested {Port: {...}} into a flat shape.
    raw_ports = a.get("Ports") or a.get("ports") or []
    ports = []
    if isinstance(raw_ports, list):
        for p in raw_ports:
            if not isinstance(p, dict):
                continue
            inner = p.get("Port") or p.get("port") or {}
            if not isinstance(inner, dict):
                inner = {}
            name = str(inner.get("Name") or inner.get("name") or "").strip()
            number = inner.get("Port") if isinstance(inner.get("Port"), int) else inner.get("port")
            if not name and number is None:
                continue
            ports.append({
                "id":            p.get("ID") or p.get("id"),
                "name":          name,
                "number":        number,
                "service_name":  str(inner.get("ServiceName") or inner.get("service_name") or "").strip(),
                "protocol":      str(inner.get("Protocol") or inner.get("protocol") or "").strip(),
            })

    # Serial — drop placeholder NONE-prefixed values (VMs without
    # real hardware serials report this).
    raw_serial = _pick_named(a.get("SerialNumber"), a.get("serial"), a.get("serial_number"))
    if raw_serial:
        import re as _re
        if _re.match(r"^NONE\d*$", raw_serial, _re.IGNORECASE):
            raw_serial = ""

    brand_obj = a.get("Brand") if isinstance(a.get("Brand"), dict) else None
    location_obj = a.get("Location") if isinstance(a.get("Location"), dict) else None
    status_obj = a.get("Status") if isinstance(a.get("Status"), dict) else None

    # Type sub-object — walk every plausible "short form" key the
    # team has surfaced on <asset-api-host> payloads. Mirrors the JS frontend's
    # type_short logic so backend-injected /api/hosts* responses carry
    # the abbreviation directly (Virtual Machine → "VM", Physical →
    # "PHY", etc.) without the SPA having to fall back to acronym
    # derivation. First non-blank wins.
    type_obj = a.get("Type") if isinstance(a.get("Type"), dict) else (
        a.get("type") if isinstance(a.get("type"), dict) else None
    )
    type_short = ""
    if type_obj:
        for key in (
            "Shortname", "ShortName", "shortname", "shortName", "short_name",
            "Short", "short", "Code", "code", "Abbr", "abbr",
            "Abbreviation", "abbreviation", "Acronym", "acronym",
            "Symbol", "symbol", "Tag", "tag", "Slug", "slug",
            "Alias", "alias",
        ):
            v = type_obj.get(key)
            if isinstance(v, str) and v.strip():
                type_short = v.strip()
                break

    return {
        "id":                a.get("ID") or a.get("id"),
        "custom_number":     a.get("CustomNumber") or a.get("custom_number"),
        "vendor":            _pick_named(a.get("Brand"), a.get("brand"),
                                         a.get("vendor"), a.get("manufacturer")),
        "brand_link":        (str((brand_obj or {}).get("Link") or "").strip()
                              if brand_obj else ""),
        "model":             _pick_named(a.get("Model"), a.get("model"),
                                         a.get("product"), a.get("product_name")),
        "serial":            raw_serial,
        "location":          _pick_named(a.get("Location"), a.get("location"),
                                         a.get("site"), a.get("room")),
        "location_details":  (str((location_obj or {}).get("Details") or "").strip()
                              if location_obj else ""),
        "type":              _pick_named(a.get("Type"), a.get("type")),
        "type_short":        type_short,
        "name":              _pick_named(a.get("Name"), a.get("name")),
        "hostnames":         hostnames,
        "primary_ip":        primary_ip,
        "ram":               _pick_named(a.get("RAM"), a.get("ram"), a.get("memory")),
        "sku":               _pick_named(a.get("SKU"), a.get("sku")),
        "firmware":          _pick_named(a.get("Firmware"), a.get("firmware")),
        "hardware_version":  _pick_named(a.get("HardwareVersion"), a.get("hardware_version")),
        "barcode":           _pick_named(a.get("Barcode"), a.get("barcode")),
        "comment":           _pick_named(a.get("Comment"), a.get("comment")),
        "status_name":       _pick_named(a.get("Status"), a.get("status")),
        "status_color":      (str((status_obj or {}).get("Color") or "").strip()
                              if status_obj else ""),
        "last_modified":     str(a.get("LastModifiedOn") or "").strip(),
        "created_on":        str(a.get("CreatedOn") or "").strip(),
        "interfaces":        ifaces,
        "ports":             ports,
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
