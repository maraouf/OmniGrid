"""Nginx Proxy Manager (NPM) per-app module.

Wires a jc21 Nginx-Proxy-Manager console into the OmniGrid Apps surface
following the per-app contract (``adguardhome.py`` multi-field-credential shape
+ ``kavita.py`` re-auth-per-fetch shape):

    SLUGS               — catalog slugs this module handles
                          ("nginx-proxy-manager" / "npm").
    requires_api_key()  — True. NPM authenticates with an EMAIL + PASSWORD
                          (the admin login), exchanged for a short-lived bearer
                          token. The chip stores the password in ``api_key`` and
                          the email in the plain ``username`` chip field (same
                          two-field shape as AdGuard Home).
    test_credential(host_row, chip, candidate_key, *, payload) -> dict
    fetch_data(host_row, chip, *, host_id, service_idx, force) -> dict
    peek_latest(host_id, service_idx) -> dict | None    (AI context)
    SKILLS / run_skill  — status (read) + proxy-hosts (read, rich list) +
                          expiring-certs (read, rich list, per-row Renew button) +
                          disable / enable a proxy host (write; disable is
                          DESTRUCTIVE, arg) + renew one cert (write, arg) +
                          renew all expiring certs (write, no-arg).

Auth model: ``POST /api/tokens {identity: <email>, secret: <password>}`` returns
``{token, expires}``; the token (default ~1 day TTL) is sent as
``Authorization: Bearer <token>`` on every call. We re-authenticate per fetch
(one cheap extra round-trip) rather than caching the token across the process —
stateless + correct on a password change. NPM ships a self-signed cert on the
LAN admin port, so TLS verification defaults OFF (``verify=_verify(chip)``); the
operator can flip the per-chip ``verify_tls`` toggle ON when NPM is fronted by a
real cert. Single-instance app (NOT fleet). No image proxy (no thumbnails).

The expanded card answers "is my reverse proxy healthy + are any certs about to
expire":

    proxy_hosts / enabled / disabled  — proxy-host counts
    certs / certs_expiring            — SSL certs + those expiring within 30d
    redirections / streams / dead     — redirection hosts / TCP-UDP streams / 404 hosts
    version                           — NPM application version

Upstream API reference: NPM REST API — base ``<admin-url>/api``,
``POST /api/tokens`` · ``GET /api/`` (version) · ``GET /api/nginx/proxy-hosts``
· ``GET /api/nginx/certificates`` · ``GET /api/nginx/redirection-hosts`` ·
``GET /api/nginx/streams`` · ``GET /api/nginx/dead-hosts`` ·
``POST /api/nginx/proxy-hosts/{id}/{enable|disable}``.
"""
from __future__ import annotations

import asyncio
import time
from datetime import datetime, timezone
from typing import Any, Optional

import httpx

from logic.apps._common import (
    cache_key, fetch_gate, peek_cache, resolve_base_url, resolve_cache_ttl,
    resolve_userpass)
from logic.coerce import as_dict, as_list, safe_int

# Catalog template slugs handled by this module. `nginx-proxy-manager` is the
# canonical built-in template; `npm` covers an operator-renamed chip.
SLUGS: tuple[str, ...] = ("nginx-proxy-manager", "npm")

DEFAULT_CACHE_TTL_S = 60
_data_cache: dict[str, tuple[float, dict]] = {}

# A certificate is "expiring soon" when it has this many days (or fewer) left.
_EXPIRY_SOON_DAYS = 30
# Cap on rich-item rows a list skill returns.
_MAX_ROWS = 25

SKILLS: tuple[dict, ...] = (
    {
        "id": "npm_status",
        "name": "Nginx Proxy Manager status",
        "ai_phrases": ("npm status, nginx proxy manager status, how many proxy "
                       "hosts, reverse proxy status, are any certs expiring, "
                       "npm overview, proxy manager health, ssl certificates"),
        "destructive": False,
    },
    {
        "id": "npm_proxy_hosts",
        "name": "List proxy hosts",
        "ai_phrases": ("list proxy hosts, show my reverse proxies, what domains "
                       "are proxied, which proxy hosts are disabled, npm hosts, "
                       "proxied sites"),
        "destructive": False,
    },
    {
        "id": "npm_expiring_certs",
        "name": "Expiring SSL certificates",
        "ai_phrases": ("which certs are expiring, ssl certificates expiring soon, "
                       "cert expiry, certificates about to expire, npm certs, "
                       "ssl expiry dates"),
        "destructive": False,
    },
    {
        "id": "npm_disable_host",
        "name": "Disable a proxy host",
        "ai_phrases": ("disable the <domain> proxy host, take <domain> offline, "
                       "turn off the proxy for <domain>, disable proxy <domain>"),
        "arg": True,
        "arg_hint": "the proxy-host domain to disable",
        "destructive": True,
    },
    {
        "id": "npm_enable_host",
        "name": "Enable a proxy host",
        "ai_phrases": ("enable the <domain> proxy host, bring <domain> back "
                       "online, turn on the proxy for <domain>, enable proxy <domain>"),
        "arg": True,
        "arg_hint": "the proxy-host domain to enable",
        "destructive": False,
    },
    {
        "id": "npm_renew_cert",
        "name": "Renew a certificate",
        "ai_phrases": ("renew the cert for <domain>, renew the ssl certificate, "
                       "force a let's encrypt renewal, renew <domain>'s cert, "
                       "refresh the certificate for <domain>"),
        # arg = a cert id (the per-row Renew button) OR a domain / name to match.
        # Non-destructive — renewing re-issues a Let's Encrypt cert; nothing is
        # lost if it's already valid.
        "arg": True,
        "arg_hint": "the certificate's domain / name to renew (or its id)",
        "destructive": False,
    },
    {
        "id": "npm_renew_expiring",
        "name": "Renew all expiring certs",
        "ai_phrases": ("renew all expiring certs, renew every certificate about "
                       "to expire, refresh all expiring ssl certs, renew the soon "
                       "to expire certificates, bulk renew certs"),
        # No arg — renews every renewable (Let's Encrypt) cert in the expiry-soon
        # window (or already expired). Non-destructive.
        "destructive": False,
    },
    {
        "id": "npm_access_lists",
        "name": "List access lists",
        "ai_phrases": ("npm access lists, list access lists, which sites have "
                       "access control, what access lists are configured, proxy "
                       "host authentication, which hosts are password protected, "
                       "access control lists"),
        "destructive": False,
    },
)


def requires_api_key() -> bool:
    """NPM authenticates with an email + password (exchanged for a token); the
    editor MUST render the email + password inputs + Test."""
    return True


def _hdr(token: str) -> dict:
    """Bearer-token + JSON-Accept header for an authenticated NPM call."""
    return {"Authorization": f"Bearer {token}", "Accept": "application/json"}


def _verify(chip: dict) -> bool:
    """Whether to verify the upstream TLS certificate. Default False — NPM ships
    a self-signed cert on its LAN admin port, so verification is off unless the
    operator flips the per-chip ``verify_tls`` toggle ON (e.g. when NPM is
    fronted by a real cert)."""
    return bool(chip.get("verify_tls"))


def _totp_now(secret: str) -> str:
    """Current 6-digit TOTP code from a base32 ``secret`` (the NPM user's 2FA
    seed). '' on any failure (no secret / bad seed / pyotp missing)."""
    s = (secret or "").strip().replace(" ", "")
    if not s:
        return ""
    try:
        import pyotp
        return str(pyotp.TOTP(s).now())
    except (ValueError, TypeError, ImportError):
        return ""


async def _get_token(cli: "httpx.AsyncClient", base: str, email: str,
                     password: str, totp_secret: str = "") -> str:
    """Exchange ``email`` + ``password`` for a bearer token via
    ``POST /api/tokens``. When the NPM user has 2FA enabled the first call
    returns ``{requires_2fa, challenge_token}`` instead of a token; complete it
    by generating the current TOTP code from ``totp_secret`` and POSTing
    ``/api/tokens/2fa {challenge_token, code}``. Returns '' on any failure
    (bad creds / unreachable / 2FA required but no secret / wrong code)."""
    try:
        r = await cli.post(base + "/api/tokens",
                           json={"identity": email, "secret": password},
                           headers={"Content-Type": "application/json",
                                    "Accept": "application/json"})
    except (httpx.HTTPError, OSError):
        return ""
    if r.status_code != 200:
        return ""
    try:
        body = as_dict(r.json())
    except (ValueError, TypeError):
        return ""
    token = str(body.get("token") or "")
    if token:
        return token
    # 2FA challenge — complete it with a generated TOTP code.
    if body.get("requires_2fa") and body.get("challenge_token"):
        code = _totp_now(totp_secret)
        if not code:
            return ""
        try:
            r2 = await cli.post(base + "/api/tokens/2fa",
                                json={"challenge_token": str(body.get("challenge_token", "")),
                                      "code": code},
                                headers={"Content-Type": "application/json",
                                         "Accept": "application/json"})
        except (httpx.HTTPError, OSError):
            return ""
        if r2.status_code != 200:
            return ""
        try:
            return str(as_dict(r2.json()).get("token") or "")
        except (ValueError, TypeError):
            return ""
    return ""


async def _get(cli: "httpx.AsyncClient", url: str, token: str) -> Any:
    """GET an authenticated NPM endpoint; parsed JSON or None on non-2xx /
    parse failure."""
    r = await cli.get(url, headers=_hdr(token))
    if not (200 <= r.status_code < 300):
        return None
    try:
        return r.json()
    except (ValueError, TypeError):
        return None


def _version_str(health: Any) -> str:
    """Assemble ``major.minor.revision`` from ``GET /api/``'s version object;
    '' when absent."""
    v = as_dict(as_dict(health).get("version"))
    parts = [v.get("major"), v.get("minor"), v.get("revision")]
    nums = [str(safe_int(x)) for x in parts if x is not None]
    return ".".join(nums) if nums else ""


def _cert_days_left(expires_on: Any) -> "Optional[int]":
    """Whole CALENDAR days until an NPM cert's ``expires_on`` (``YYYY-MM-DD …``).
    None when unparseable. 0 ⇒ expires today; negative ⇒ already expired.

    Compares dates, NOT datetimes: ``expires_on`` is a date (no time), so a cert
    valid until end-of-day today must read 0, not -1. Subtracting a midnight-UTC
    datetime from ``now`` floored the partial day to -1 ("expired 1 day ago")
    while the cert was still valid for the rest of the day — and shifted every
    near-boundary count down by one."""
    s = str(expires_on or "")[:10]
    try:
        exp = datetime.strptime(s, "%Y-%m-%d").date()
    except (ValueError, TypeError):
        return None
    return (exp - datetime.now(timezone.utc).date()).days


def _cert_label(cert: dict) -> str:
    """Display name for a cert — its nice_name, else its first domain."""
    nice = str(cert.get("nice_name") or "").strip()
    if nice:
        return nice
    doms = [str(d) for d in as_list(cert.get("domain_names")) if d]
    return doms[0] if doms else f"cert #{safe_int(cert.get('id'))}"


def _cert_rows(certs_l: list) -> list:
    """Sorted (soonest-expiry first) cert rows for the card / drawer / skills:
    ``[{id, label, days, provider, renewable}]``. Skips certs with an
    unparseable expiry. ``renewable`` is True only for Let's Encrypt certs —
    custom-uploaded certs (``provider == "other"``) have no renew endpoint."""
    rows: list = []
    for c in certs_l:
        if not isinstance(c, dict):
            continue
        days = _cert_days_left(c.get("expires_on"))
        if days is None:
            continue
        provider = str(c.get("provider") or "").strip().lower()
        rows.append({
            "id": safe_int(c.get("id")),
            "label": _cert_label(c),
            "days": days,
            "provider": provider,
            "renewable": provider == "letsencrypt",
        })
    rows.sort(key=lambda r: r["days"])
    return rows


# noinspection DuplicatedCode
async def test_credential(host_row: dict, chip: dict, candidate_key: str, *,
                          payload: Optional[dict] = None, **_kw) -> dict:
    """Probe ``POST /api/tokens`` with the candidate email + password.
    ``candidate_key`` is the password; the email comes from the test payload
    (pre-save) or the stored chip. Returns ``{ok, detail, status}``."""
    pay = payload or {}
    email, password = resolve_userpass(
        chip,
        password=(candidate_key or "").strip() or None,
        username=(pay.get("username") or "").strip() or None,
    )
    if not email:
        return {"ok": False, "detail": "email (admin login) required", "status": 0}
    if not password:
        return {"ok": False, "detail": "password required", "status": 0}
    base = resolve_base_url(host_row, chip)
    if not base:
        return {"ok": False, "detail": "no upstream URL configured", "status": 0}
    # Honour the LIVE editor toggle (test-before-save) when present, else the
    # stored chip value.
    verify = bool(pay.get("verify_tls")) if "verify_tls" in pay else _verify(chip)
    try:
        async with httpx.AsyncClient(verify=verify, timeout=10.0,
                                     follow_redirects=True) as cli:
            r = await cli.post(base + "/api/tokens",
                               json={"identity": email, "secret": password},
                               headers={"Content-Type": "application/json",
                                        "Accept": "application/json"})
            # Auth / transport failures first.
            if r.status_code in (400, 401, 403):
                return {"ok": False,
                        "detail": "auth failed (check the NPM admin email + password)",
                        "status": r.status_code}
            if not (200 <= r.status_code < 300):
                return {"ok": False, "detail": f"HTTP {r.status_code}",
                        "status": r.status_code}
            # 2xx — expect the NPM token shape ``{token, expires}``.
            try:
                body = r.json()
            except (ValueError, TypeError):
                body = None
            token = str(as_dict(body).get("token") or "") if isinstance(body, dict) else ""
            # 2FA challenge — NPM returns {requires_2fa, challenge_token}.
            if not token and isinstance(body, dict) and body.get("requires_2fa") \
                and body.get("challenge_token"):
                totp_secret = ((pay.get("totp_secret") or "").strip()
                               or str(chip.get("totp_secret") or ""))
                if not totp_secret:
                    return {"ok": False, "status": r.status_code,
                            "detail": "this NPM user has 2FA enabled — paste the 2FA "
                                      "secret (the base32 TOTP setup key) below so "
                                      "OmniGrid can generate the codes"}
                code = _totp_now(totp_secret)
                if not code:
                    return {"ok": False, "status": 0,
                            "detail": "the 2FA secret isn't a valid base32 TOTP key"}
                r2 = await cli.post(base + "/api/tokens/2fa",
                                    json={"challenge_token": str(body.get("challenge_token", "")),
                                          "code": code},
                                    headers={"Content-Type": "application/json",
                                             "Accept": "application/json"})
                if r2.status_code != 200:
                    return {"ok": False, "status": r2.status_code,
                            "detail": "2FA verification failed — the generated code "
                                      "was rejected (check the 2FA secret + the "
                                      "server clock)"}
                try:
                    token = str(as_dict(r2.json()).get("token") or "")
                except (ValueError, TypeError):
                    token = ""
            if not token:
                # Connected (2xx) but no token. Almost always the chip URL points
                # at a PROXIED site (the public 80/443) returning that site's page
                # instead of the NPM admin API on port 81.
                ct = (r.headers.get("content-type") or "").lower()
                if "html" in ct or not isinstance(body, dict):
                    detail = ("connected but got a web page, not the NPM API — point "
                              "the chip URL at the NPM ADMIN UI (default port 81), "
                              "not a proxied site")
                else:
                    detail = ("connected but NPM returned no token — check the admin "
                              "email + password")
                return {"ok": False, "detail": detail, "status": r.status_code}
            version = _version_str(await _get(cli, base + "/api/", token))
    except (httpx.HTTPError, OSError) as e:  # noqa: BLE001
        return {"ok": False, "detail": f"{type(e).__name__}: {e}", "status": 0}
    return {"ok": True, "detail": f"OK{(' — NPM ' + version) if version else ''}",
            "status": 200}


async def fetch_data(host_row: dict, chip: dict, *,
                     host_id: str, service_idx: int,
                     force: bool = False) -> dict:
    """Fetch the NPM summary for the card: auth, then fan out proxy-hosts /
    certificates / redirections / streams / dead-hosts + the version, and
    aggregate. Returns the card payload (see the module docstring). Raises
    ``ValueError`` / ``RuntimeError`` (caller maps to HTTPException) when the
    password is unset / the base URL won't resolve / the upstream errors."""
    email, password = resolve_userpass(chip)
    now = time.time()
    base, hit = fetch_gate(host_row, chip, host_id, service_idx, _data_cache,
                           resolve_cache_ttl(chip, DEFAULT_CACHE_TTL_S), now, force,
                           credential=password, log_tag="npm")
    if hit is not None:
        return hit
    totp_secret = str(chip.get("totp_secret") or "")
    try:
        async with httpx.AsyncClient(verify=_verify(chip), timeout=20.0,
                                     follow_redirects=True) as cli:
            token = await _get_token(cli, base, email, password, totp_secret)
            if not token:
                raise RuntimeError(
                    "auth failed (check the NPM admin email + password"
                    + (" / 2FA secret" if totp_secret else "") + ")")
            health, hosts, certs, redirs, streams, dead, acls = await asyncio.gather(
                _get(cli, base + "/api/", token),
                _get(cli, base + "/api/nginx/proxy-hosts", token),
                _get(cli, base + "/api/nginx/certificates", token),
                _get(cli, base + "/api/nginx/redirection-hosts", token),
                _get(cli, base + "/api/nginx/streams", token),
                _get(cli, base + "/api/nginx/dead-hosts", token),
                _get(cli, base + "/api/nginx/access-lists", token),
            )
    except (httpx.HTTPError, OSError) as e:  # noqa: BLE001
        print(f"[npm] error: fetch host={host_id} base={base} "
              f"failed — {type(e).__name__}: {e}")
        raise RuntimeError(f"upstream fetch failed: {type(e).__name__}: {e}")

    hosts_l = [h for h in as_list(hosts) if isinstance(h, dict)]
    certs_l = [c for c in as_list(certs) if isinstance(c, dict)]
    enabled = sum(1 for h in hosts_l if h.get("enabled"))
    cert_rows = _cert_rows(certs_l)
    expiring = sum(1 for r in cert_rows if r["days"] <= _EXPIRY_SOON_DAYS)
    certs_expired = sum(1 for r in cert_rows if r["days"] < 0)
    # Renewal FAILURES — expired Let's Encrypt certs. LE auto-renews ~30d before
    # expiry, so a RENEWABLE cert that's already expired means the auto-renewal
    # failed (actionable: re-run it). Distinct from certs_expired, which also
    # counts custom-uploaded certs that simply lapsed (operator must re-upload).
    certs_renewal_failed = sum(1 for r in cert_rows if r["days"] < 0 and r["renewable"])
    cert_min_days = cert_rows[0]["days"] if cert_rows else None
    # Proxy hosts served over plain HTTP — no SSL cert assigned
    # (``certificate_id == 0``). A "N sites over plain HTTP" security signal;
    # only ENABLED hosts count (a disabled host serves nothing).
    proxy_plain_http = sum(1 for h in hosts_l
                           if h.get("enabled") and not safe_int(h.get("certificate_id")))
    # Access-list usage — how many access lists exist + how many ENABLED proxy
    # hosts are protected by one (access_list_id > 0). A "5 sites have no access
    # control" complement to the plain-HTTP signal.
    acl_l = [a for a in as_list(acls) if isinstance(a, dict)]
    protected_hosts = sum(1 for h in hosts_l
                          if h.get("enabled") and safe_int(h.get("access_list_id")))

    out: dict[str, Any] = {
        "available": True,
        "version": _version_str(health),
        "proxy_hosts": len(hosts_l),
        "proxy_enabled": enabled,
        "proxy_disabled": max(0, len(hosts_l) - enabled),
        "proxy_plain_http": proxy_plain_http,
        "certs": len(certs_l),
        "certs_expiring": expiring,
        "certs_expired": certs_expired,
        # Expired Let's Encrypt certs = auto-renewal failures (actionable).
        "certs_renewal_failed": certs_renewal_failed,
        # Soonest-expiry first (drives the card "next cert" stat + the drawer
        # sorted list + per-cert renew buttons).
        "cert_min_days": cert_min_days,
        "certs_soonest": cert_rows[:_MAX_ROWS],
        "redirections": len([r for r in as_list(redirs) if isinstance(r, dict)]),
        "streams": len([s for s in as_list(streams) if isinstance(s, dict)]),
        "dead_hosts": len([d for d in as_list(dead) if isinstance(d, dict)]),
        # WHICH hosts are 404 / dead — the domain names (NPM doesn't health-check
        # upstreams; this is its 404-host list). Drawer detail, capped.
        "dead_host_names": [
            nm for d in as_list(dead) if isinstance(d, dict)
            for nm in [", ".join(str(x) for x in as_list(d.get("domain_names")) if x)]
            if nm][:_MAX_ROWS],
        "access_lists": len(acl_l),
        "protected_hosts": protected_hosts,
        "fetched_at": int(now),
    }
    # Best-effort config-drift trend from the shared lifespan npm_sampler.
    # Missing sampler / no samples yet leaves the card's instantaneous stats
    # untouched.
    out["trend"] = _safe_trend(host_id, service_idx)
    print(f"[npm] INFO fetched host={host_id} hosts={out['proxy_hosts']} "
          f"(on={out['proxy_enabled']}/off={out['proxy_disabled']}) "
          f"plain_http={out['proxy_plain_http']} "
          f"certs={out['certs']} expiring={out['certs_expiring']} "
          f"expired={out['certs_expired']} renew_failed={out['certs_renewal_failed']} "
          f"min_days={out['cert_min_days']} "
          f"redirs={out['redirections']} streams={out['streams']} "
          f"dead={out['dead_hosts']} acls={out['access_lists']} "
          f"protected={out['protected_hosts']} ver={out['version'] or '-'}")
    _data_cache[cache_key(host_id, service_idx)] = (now, out)
    return out


def _safe_trend(host_id: str, service_idx: int) -> Optional[dict]:
    """Best-effort config-drift trend for the card — the shared npm_sampler's
    per-chip ``trend_summary``. Returns ``None`` (never raises) when the sampler
    isn't importable / errors, so a trend hiccup can't fail the card."""
    try:
        from logic.apps import npm_sampler as _sampler  # noqa: PLC0415
        return _sampler.trend_summary(host_id, int(service_idx))
    except Exception as e:  # noqa: BLE001
        print(f"[npm] trend_summary({host_id}#{service_idx}) skipped: {e}")
        return None


def peek_latest(host_id: str, service_idx: int) -> Optional[dict]:
    """Cache-only peek (no upstream call) for the AI context's
    ``app_skills[].last``."""
    data = peek_cache(_data_cache, host_id, service_idx)
    if not isinstance(data, dict) or not data.get("available"):
        return None
    return {
        "version": data.get("version") or "",
        "proxy_hosts": safe_int(data.get("proxy_hosts")),
        "proxy_enabled": safe_int(data.get("proxy_enabled")),
        "proxy_disabled": safe_int(data.get("proxy_disabled")),
        "proxy_plain_http": safe_int(data.get("proxy_plain_http")),
        "certs": safe_int(data.get("certs")),
        "certs_expiring": safe_int(data.get("certs_expiring")),
        "certs_expired": safe_int(data.get("certs_expired")),
        "certs_renewal_failed": safe_int(data.get("certs_renewal_failed")),
        "cert_min_days": data.get("cert_min_days"),
        "redirections": safe_int(data.get("redirections")),
        "streams": safe_int(data.get("streams")),
        "dead_hosts": safe_int(data.get("dead_hosts")),
        "access_lists": safe_int(data.get("access_lists")),
        "protected_hosts": safe_int(data.get("protected_hosts")),
        "fetched_at": safe_int(data.get("fetched_at")),
    }


# ---------------------------------------------------------------------------
# Skills
# ---------------------------------------------------------------------------
def _resolve_skill_target(host_row: dict, chip: dict) -> "tuple[str, str, str, Optional[dict]]":
    """Resolve ``(email, password, base)`` or a ready ``{ok: False, detail}``
    error dict for an NPM skill."""
    email, password = resolve_userpass(chip)
    if not password:
        return "", "", "", {"ok": False, "status": 0,
                            "detail": "NPM password not set"}
    base = resolve_base_url(host_row, chip)
    if not base:
        return "", "", "", {"ok": False, "status": 0,
                            "detail": "no upstream URL configured"}
    return email, password, base, None


def _attach_items(out: dict, items: list, count_i18n: str) -> dict:
    """Attach the rich-item list + count + count-i18n key to a skill result
    (no-op when empty). Returns ``out`` for one-line use."""
    if items:
        out["items"] = items
        out["count"] = len(items)
        out["count_i18n"] = count_i18n
    return out


async def run_skill(skill_id: str, host_row: dict, chip: dict, *,
                    host_id: Optional[str] = None,
                    service_idx: Optional[int] = None,
                    arg: Optional[str] = None, **_kw) -> dict:
    """Dispatch one of this app's SKILLS. Returns ``{ok, detail, status?}``.
    Raises ValueError on an unknown skill id (route maps to HTTP 404)."""
    if skill_id == "npm_status":
        return await _status_skill(host_row, chip, host_id=host_id,
                                   service_idx=service_idx)
    if skill_id == "npm_proxy_hosts":
        return await _proxy_hosts_skill(host_row, chip, host_id=host_id)
    if skill_id == "npm_expiring_certs":
        return await _expiring_certs_skill(host_row, chip, host_id=host_id)
    if skill_id == "npm_disable_host":
        return await _toggle_host_skill(host_row, chip, arg=arg,
                                        enable=False, host_id=host_id)
    if skill_id == "npm_enable_host":
        return await _toggle_host_skill(host_row, chip, arg=arg,
                                        enable=True, host_id=host_id)
    if skill_id == "npm_renew_cert":
        return await _renew_cert_skill(host_row, chip, arg=arg, host_id=host_id)
    if skill_id == "npm_renew_expiring":
        return await _renew_expiring_skill(host_row, chip, host_id=host_id)
    if skill_id == "npm_access_lists":
        return await _access_lists_skill(host_row, chip, host_id=host_id)
    raise ValueError(f"unknown skill: {skill_id!r}")


# noinspection DuplicatedCode
async def _status_skill(host_row: dict, chip: dict, *,
                        host_id: Optional[str] = None,
                        service_idx: Optional[int] = None) -> dict:
    """Read-only: live-fetch the summary (force-bypasses the cache) and return a
    formatted ``detail``. Never raises."""
    print(f"[npm] INFO npm_status host={host_id} svc_idx={service_idx} (live fetch)")
    try:
        data = await fetch_data(host_row, chip, host_id=str(host_id or ""),
                                service_idx=int(service_idx or 0), force=True)
    except (ValueError, RuntimeError) as e:
        return {"ok": False, "detail": str(e), "status": 0}
    hosts = safe_int(data.get("proxy_hosts"))
    on = safe_int(data.get("proxy_enabled"))
    off = safe_int(data.get("proxy_disabled"))
    plain = safe_int(data.get("proxy_plain_http"))
    expiring = safe_int(data.get("certs_expiring"))
    expired = safe_int(data.get("certs_expired"))
    min_days = data.get("cert_min_days")
    lines = [
        f"🌐 Proxy hosts: {hosts}" + (f" ({on} on · {off} off)" if off else ""),
        f"🔒 Certificates: {safe_int(data.get('certs'))}"
        + (f" · ⚠️ {expiring} expiring ≤{_EXPIRY_SOON_DAYS}d" if expiring else ""),
    ]
    # Soonest-expiry callout — the single most actionable line.
    if isinstance(min_days, int):
        if min_days < 0:
            lines.append(f"🔴 Soonest cert: expired {abs(min_days)}d ago")
        elif min_days <= _EXPIRY_SOON_DAYS:
            lines.append(f"🟠 Soonest cert expires in {min_days}d")
        else:
            lines.append(f"🟢 Soonest cert expires in {min_days}d")
    renewal_failed = safe_int(data.get("certs_renewal_failed"))
    if renewal_failed:
        lines.append(f"🔁 {renewal_failed} Let's Encrypt cert(s) FAILED to auto-renew "
                     f"(expired + renewable — re-run the renewal)")
    elif expired:
        lines.append(f"⛔ {expired} cert(s) already EXPIRED")
    if plain:
        lines.append(f"🔓 {plain} proxy host(s) served over plain HTTP (no SSL)")
    dead_names = [str(n) for n in as_list(data.get("dead_host_names")) if str(n).strip()]
    if dead_names:
        lines.append(f"💀 404 hosts: {', '.join(dead_names[:8])}")
    acls = safe_int(data.get("access_lists"))
    protected = safe_int(data.get("protected_hosts"))
    if acls or protected:
        lines.append(f"🛡️ Access lists: {acls} · {protected} host(s) protected")
    lines.append(
        f"↪️ {safe_int(data.get('redirections'))} redirections · "
        f"🔀 {safe_int(data.get('streams'))} streams · "
        f"💀 {safe_int(data.get('dead_hosts'))} 404 hosts")
    ver = str(data.get("version") or "").strip()
    if ver:
        lines.append(f"· NPM {ver}")
    return {"ok": True, "detail": "\n".join(lines), "status": 200,
            "proxy_hosts": hosts, "certs_expiring": expiring,
            "certs_expired": expired, "cert_min_days": min_days,
            "proxy_plain_http": plain}


# noinspection DuplicatedCode
async def _proxy_hosts_skill(host_row: dict, chip: dict, *,
                             host_id: Optional[str] = None) -> dict:
    """Read-only: list proxy hosts as rich rows (domain + on/off state +
    forward target). Never raises."""
    email, password, base, err = _resolve_skill_target(host_row, chip)
    if err:
        return err
    print(f"[npm] INFO npm_proxy_hosts host={host_id} (live fetch)")
    try:
        async with httpx.AsyncClient(verify=_verify(chip), timeout=20.0,
                                     follow_redirects=True) as cli:
            token = await _get_token(cli, base, email, password, str(chip.get("totp_secret") or ""))
            if not token:
                return {"ok": False, "status": 401,
                        "detail": "auth failed (check the NPM email + password)"}
            hosts = await _get(cli, base + "/api/nginx/proxy-hosts", token)
    except (httpx.HTTPError, OSError) as e:  # noqa: BLE001
        return {"ok": False, "status": 0, "detail": f"fetch failed: {type(e).__name__}: {e}"}
    hosts_l = [h for h in as_list(hosts) if isinstance(h, dict)]
    if not hosts_l:
        return {"ok": True, "status": 200, "detail": "🌐 No proxy hosts configured."}
    # Disabled first, then by primary domain.
    hosts_l.sort(key=lambda _h: (1 if _h.get("enabled") else 0,
                                 str((as_list(_h.get("domain_names")) or [""])[0]).lower()))
    items: list = []
    lines: list = []
    for h in hosts_l[:_MAX_ROWS]:
        doms = [str(d) for d in as_list(h.get("domain_names")) if d]
        title = doms[0] if doms else f"host #{safe_int(h.get('id'))}"
        on = bool(h.get("enabled"))
        scheme = str(h.get("forward_scheme") or "http").strip()
        fwd = f"{scheme}://{h.get('forward_host')}:{safe_int(h.get('forward_port'))}"
        state = "🟢 enabled" if on else "🔴 disabled"
        sub = f"{state} · → {fwd}"
        if len(doms) > 1:
            sub += f" · +{len(doms) - 1} domain(s)"
        items.append({"title": title, "subtitle": sub})
        lines.append(f"• {title}  ({sub})")
    out: dict = {"ok": True, "status": 200,
                 "detail": "🌐 Proxy hosts:\n" + "\n".join(lines)}
    return _attach_items(out, items, "apps.npm.hosts_count")


# noinspection DuplicatedCode
async def _expiring_certs_skill(host_row: dict, chip: dict, *,
                                host_id: Optional[str] = None) -> dict:
    """Read-only: list SSL certs sorted by soonest expiry, flagging those within
    the expiry-soon window. Never raises."""
    email, password, base, err = _resolve_skill_target(host_row, chip)
    if err:
        return err
    print(f"[npm] INFO npm_expiring_certs host={host_id} (live fetch)")
    try:
        async with httpx.AsyncClient(verify=_verify(chip), timeout=20.0,
                                     follow_redirects=True) as cli:
            token = await _get_token(cli, base, email, password, str(chip.get("totp_secret") or ""))
            if not token:
                return {"ok": False, "status": 401,
                        "detail": "auth failed (check the NPM email + password)"}
            certs = await _get(cli, base + "/api/nginx/certificates", token)
    except (httpx.HTTPError, OSError) as e:  # noqa: BLE001
        return {"ok": False, "status": 0, "detail": f"fetch failed: {type(e).__name__}: {e}"}
    certs_l = [c for c in as_list(certs) if isinstance(c, dict)]
    rows = []
    for c in certs_l:
        days = _cert_days_left(c.get("expires_on"))
        if days is None:
            continue
        rows.append((days, c))
    rows.sort(key=lambda r: r[0])
    if not rows:
        return {"ok": True, "status": 200, "detail": "🔒 No certificates found."}
    items: list = []
    lines: list = []
    for days, c in rows[:_MAX_ROWS]:
        label = _cert_label(c)
        if days < 0:
            when = f"expired {abs(days)}d ago"
            dot = "🔴"
        elif days <= _EXPIRY_SOON_DAYS:
            when = f"expires in {days}d"
            dot = "🟠"
        else:
            when = f"expires in {days}d"
            dot = "🟢"
        sub = f"{dot} {when}"
        row: dict = {"title": label, "subtitle": sub}
        # One-click ↻ Renew on Let's Encrypt certs (custom certs have no renew
        # endpoint, so no button). Non-destructive — no confirm gate.
        if str(c.get("provider") or "").strip().lower() == "letsencrypt":
            row["row_action"] = {
                "skill_id": "npm_renew_cert",
                "arg": str(safe_int(c.get("id"))),
                "icon": "refresh-cw",
                "title_i18n": "apps.npm.renew_cert",
            }
        items.append(row)
        lines.append(f"• {label}  ({sub})")
    soon = sum(1 for days, _ in rows if days <= _EXPIRY_SOON_DAYS)
    head = (f"🔒 {soon} cert(s) expiring within {_EXPIRY_SOON_DAYS}d:"
            if soon else "🔒 Certificates (none expiring soon):")
    out: dict = {"ok": True, "status": 200, "detail": head + "\n" + "\n".join(lines)}
    return _attach_items(out, items, "apps.npm.certs_count")


# noinspection DuplicatedCode
async def _toggle_host_skill(host_row: dict, chip: dict, *,
                             arg: Optional[str], enable: bool,
                             host_id: Optional[str] = None) -> dict:
    """Enable / disable ONE proxy host by domain. Resolves the host across the
    console's proxy-hosts, then POSTs the enable / disable action. Disable is
    DESTRUCTIVE (takes a site offline). Never raises."""
    needle = (arg or "").strip()
    if not needle:
        return {"ok": False, "status": 0,
                "detail": "no domain given (say e.g. \"disable app.example.com\")"}
    email, password, base, err = _resolve_skill_target(host_row, chip)
    if err:
        return err
    needle_l = needle.lower()
    verb = "enable" if enable else "disable"
    print(f"[npm] INFO npm_{verb}_host host={host_id} target={needle!r}")
    try:
        async with httpx.AsyncClient(verify=_verify(chip), timeout=20.0,
                                     follow_redirects=True) as cli:
            token = await _get_token(cli, base, email, password, str(chip.get("totp_secret") or ""))
            if not token:
                return {"ok": False, "status": 401,
                        "detail": "auth failed (check the NPM email + password)"}
            hosts = await _get(cli, base + "/api/nginx/proxy-hosts", token)
            match_id = 0
            match_dom = ""
            for h in as_list(hosts):
                if not isinstance(h, dict):
                    continue
                doms = [str(d).strip().lower() for d in as_list(h.get("domain_names")) if d]
                if needle_l in doms:
                    match_id, match_dom = safe_int(h.get("id")), needle
                    break
                if not match_id:
                    hit = next((d for d in doms if needle_l in d), "")
                    if hit:
                        match_id, match_dom = safe_int(h.get("id")), hit
            if not match_id:
                return {"ok": False, "status": 404,
                        "detail": f"no proxy host matched \"{needle}\""}
            ar = await cli.post(
                base + f"/api/nginx/proxy-hosts/{match_id}/{verb}",
                headers=_hdr(token))
    except (httpx.HTTPError, OSError) as e:  # noqa: BLE001
        return {"ok": False, "status": 0, "detail": f"{verb} failed: {type(e).__name__}: {e}"}
    if ar.status_code in (401, 403):
        return {"ok": False, "status": ar.status_code,
                "detail": "auth failed (check the NPM email + password)"}
    if not (200 <= ar.status_code < 300):
        return {"ok": False, "status": ar.status_code, "detail": f"HTTP {ar.status_code}"}
    if enable:
        return {"ok": True, "status": 200,
                "detail": f"🟢 Enabled the proxy host for \"{match_dom}\"."}
    return {"ok": True, "status": 200,
            "detail": f"🔴 Disabled the proxy host for \"{match_dom}\" — it's now "
                      f"offline until re-enabled."}


async def _renew_one(cli: "httpx.AsyncClient", base: str, token: str,
                     cert_id: int) -> "tuple[bool, int]":
    """POST a single cert renew (``/api/nginx/certificates/{id}/renew``).
    Returns ``(ok, status_code)`` — ``(False, 0)`` on a transport error."""
    try:
        r = await cli.post(base + f"/api/nginx/certificates/{cert_id}/renew",
                           headers=_hdr(token))
    except (httpx.HTTPError, OSError):
        return False, 0
    return (200 <= r.status_code < 300), r.status_code


# noinspection DuplicatedCode
async def _renew_cert_skill(host_row: dict, chip: dict, *,
                            arg: Optional[str] = None,
                            host_id: Optional[str] = None) -> dict:
    """Renew ONE Let's Encrypt certificate. Resolves the target from the
    console's certs — an exact cert id (the per-row Renew button) first, else a
    label / domain substring (the AI / Telegram path) — then POSTs the renew.
    Non-destructive. Custom-uploaded certs have no renew endpoint and report a
    clear error. Never raises."""
    needle = (arg or "").strip()
    if not needle:
        return {"ok": False, "status": 0,
                "detail": "no certificate given (say e.g. \"renew the cert for app.example.com\")"}
    email, password, base, err = _resolve_skill_target(host_row, chip)
    if err:
        return err
    nl = needle.lower()
    want_id = safe_int(needle) if needle.isdigit() else 0
    print(f"[npm] INFO npm_renew_cert host={host_id} target={needle!r}")
    try:
        async with httpx.AsyncClient(verify=_verify(chip), timeout=30.0,
                                     follow_redirects=True) as cli:
            token = await _get_token(cli, base, email, password, str(chip.get("totp_secret") or ""))
            if not token:
                return {"ok": False, "status": 401,
                        "detail": "auth failed (check the NPM email + password)"}
            certs = await _get(cli, base + "/api/nginx/certificates", token)
            target = None
            for c in as_list(certs):
                if not isinstance(c, dict):
                    continue
                if want_id and safe_int(c.get("id")) == want_id:
                    target = c
                    break
                if not want_id:
                    label = _cert_label(c).lower()
                    doms = [str(d).strip().lower() for d in as_list(c.get("domain_names")) if d]
                    if nl in label or any(nl in d for d in doms):
                        target = c
                        break
            if target is None:
                return {"ok": False, "status": 404,
                        "detail": f"no certificate matched \"{needle}\""}
            label = _cert_label(target)
            if str(target.get("provider") or "").strip().lower() != "letsencrypt":
                return {"ok": False, "status": 400,
                        "detail": f"\"{label}\" is a custom-uploaded certificate — it "
                                  f"can't be auto-renewed (only Let's Encrypt certs can)."}
            ok, status = await _renew_one(cli, base, token, safe_int(target.get("id")))
    except (httpx.HTTPError, OSError) as e:  # noqa: BLE001
        return {"ok": False, "status": 0, "detail": f"renew failed: {type(e).__name__}: {e}"}
    if not ok:
        return {"ok": False, "status": status,
                "detail": f"NPM didn't accept the renewal for \"{label}\" (HTTP {status})"}
    return {"ok": True, "status": 200,
            "detail": f"🔄 Started a Let's Encrypt renewal for \"{label}\"."}


# noinspection DuplicatedCode
async def _renew_expiring_skill(host_row: dict, chip: dict, *,
                                host_id: Optional[str] = None) -> dict:
    """Renew EVERY renewable (Let's Encrypt) certificate that is expiring within
    the soon-window (or already expired). Non-destructive. Reports the count
    renewed + any custom certs skipped. Never raises."""
    email, password, base, err = _resolve_skill_target(host_row, chip)
    if err:
        return err
    print(f"[npm] INFO npm_renew_expiring host={host_id}")
    try:
        async with httpx.AsyncClient(verify=_verify(chip), timeout=60.0,
                                     follow_redirects=True) as cli:
            token = await _get_token(cli, base, email, password, str(chip.get("totp_secret") or ""))
            if not token:
                return {"ok": False, "status": 401,
                        "detail": "auth failed (check the NPM email + password)"}
            certs = await _get(cli, base + "/api/nginx/certificates", token)
            targets: list = []
            skipped_custom = 0
            for c in as_list(certs):
                if not isinstance(c, dict):
                    continue
                days = _cert_days_left(c.get("expires_on"))
                if days is None or days > _EXPIRY_SOON_DAYS:
                    continue
                if str(c.get("provider") or "").strip().lower() != "letsencrypt":
                    skipped_custom += 1
                    continue
                targets.append(c)
            if not targets:
                msg = "✅ No Let's Encrypt certificates are expiring soon."
                if skipped_custom:
                    msg += f" ({skipped_custom} custom cert(s) can't be auto-renewed.)"
                return {"ok": True, "status": 200, "detail": msg}
            renewed = 0
            failed = 0
            renewed_labels: list = []
            for c in targets:
                ok, _status = await _renew_one(cli, base, token, safe_int(c.get("id")))
                if ok:
                    renewed += 1
                    renewed_labels.append(_cert_label(c))
                else:
                    failed += 1
    except (httpx.HTTPError, OSError) as e:  # noqa: BLE001
        return {"ok": False, "status": 0, "detail": f"renew failed: {type(e).__name__}: {e}"}
    if not renewed:
        return {"ok": False, "status": 502,
                "detail": f"NPM didn't accept any of the {len(targets):,} renewal(s)."}
    tail = (": " + ", ".join(renewed_labels)) if renewed_labels else ""
    extra = ""
    if failed:
        extra += f" {failed} failed."
    if skipped_custom:
        extra += f" {skipped_custom} custom cert(s) skipped."
    return {"ok": True, "status": 200,
            "detail": f"🔄 Started renewal for {renewed:,} cert(s){tail}.{extra}"}


# noinspection DuplicatedCode
async def _access_lists_skill(host_row: dict, chip: dict, *,
                              host_id: Optional[str] = None) -> dict:
    """Read-only: list the configured access lists (HTTP-auth / IP allow-deny
    rules) with how many auth users + client rules each carries AND how many
    proxy hosts each protects (cross-referenced from the proxy-hosts list). Never
    raises."""
    email, password, base, err = _resolve_skill_target(host_row, chip)
    if err:
        return err
    print(f"[npm] INFO npm_access_lists host={host_id} (live fetch)")
    try:
        async with httpx.AsyncClient(verify=_verify(chip), timeout=20.0,
                                     follow_redirects=True) as cli:
            token = await _get_token(cli, base, email, password, str(chip.get("totp_secret") or ""))
            if not token:
                return {"ok": False, "status": 401,
                        "detail": "auth failed (check the NPM email + password)"}
            acls, hosts = await asyncio.gather(
                _get(cli, base + "/api/nginx/access-lists?expand=items,clients", token),
                _get(cli, base + "/api/nginx/proxy-hosts", token))
    except (httpx.HTTPError, OSError) as e:  # noqa: BLE001
        return {"ok": False, "status": 0, "detail": f"fetch failed: {type(e).__name__}: {e}"}
    acl_l = [a for a in as_list(acls) if isinstance(a, dict)]
    if not acl_l:
        return {"ok": True, "status": 200, "detail": "🛡️ No access lists configured."}
    # How many ENABLED proxy hosts each access list protects.
    host_count: dict = {}
    for h in as_list(hosts):
        if isinstance(h, dict) and h.get("enabled"):
            aid = safe_int(h.get("access_list_id"))
            if aid:
                host_count[aid] = host_count.get(aid, 0) + 1
    items: list = []
    lines: list = []
    for a in acl_l[:_MAX_ROWS]:
        name = str(a.get("name") or f"list #{safe_int(a.get('id'))}").strip()
        users = len([u for u in as_list(a.get("items")) if isinstance(u, dict)])
        clients = len([c for c in as_list(a.get("clients")) if isinstance(c, dict)])
        used = host_count.get(safe_int(a.get("id")), 0)
        bits = []
        if users:
            bits.append(f"{users} user(s)")
        if clients:
            bits.append(f"{clients} IP rule(s)")
        bits.append(f"{used} host(s)")
        sub = " · ".join(bits)
        items.append({"title": f"🛡️ {name}", "subtitle": sub})
        lines.append(f"• {name}  ({sub})")
    out: dict = {"ok": True, "status": 200,
                 "detail": "🛡️ Access lists:\n" + "\n".join(lines)}
    return _attach_items(out, items, "apps.npm.access_lists_count")
