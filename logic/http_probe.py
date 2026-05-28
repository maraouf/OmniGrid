"""HTTP / TLS-cert / DNS health probe — seventh host-stats provider.

Probes a single URL and returns a unified dict carrying three
sub-probe outcomes:

  1. **DNS resolution** — does the hostname resolve? Uses
     ``socket.getaddrinfo`` in a thread executor so the event loop
     never blocks on a slow resolver.
  2. **HTTP request** — does the URL respond with an accepted status?
     ``httpx.AsyncClient`` with a per-call timeout. Optional content-
     match check looks for a substring in the response body so the
     operator can guard against "200 OK but landing page replaced
     with login redirect" regressions.
  3. **TLS cert (https only)** — extract the leaf cert via
     ``asyncio.open_connection`` + ``ssl.create_default_context``
     + ``writer.get_extra_info('ssl_object').getpeercert()`` so we
     can read ``notAfter`` and compute days-until-expiry without a
     third-party dep.

Provider auth model — NONE. The probe targets the operator's own
clickable URLs (``hosts_config[].url`` + ``hosts_config[].services[].url``
+ optional per-row ``http_probe.urls`` override list). No
credentials, no per-host cool-down on auth failure (HTTP returns
status codes — no auth-lockout class to defend against). 401 / 403
just count as "status_ok=False" outcomes.

Per-host opt-in via ``hosts_config[].http_probe = {enabled,
urls?, content_match?, accepted_status_codes?}``. Master toggle is
the plain ``http_probe_enabled`` setting (legacy pattern matching
Beszel / Pulse / Webmin). Sampler-side tunables (timeouts,
concurrency, retention) live in TUNABLES under the
``tuning_http_probe_*`` prefix.

Returned dict (see :func:`probe_http_health`):

.. code-block:: python

    {
        "ok": bool,                  # overall pass/fail
        "status_code": Optional[int],
        "status_ok": bool,           # status in accepted set
        "content_match_ok": bool,    # True iff content_match found
                                     # (or content_match was empty)
        "tls_expires_in_days": Optional[int],
        "tls_subject": Optional[str],
        "tls_issuer": Optional[str],
        "dns_resolved": bool,
        "dns_error": Optional[str],
        "latency_ms": Optional[int],
        "error": Optional[str],      # collapses any sub-failure
    }
"""
from __future__ import annotations

import asyncio
import socket
import ssl
import time
from datetime import datetime, timezone
from typing import Any, Optional, Sequence
from urllib.parse import urlparse

import httpx

from logic.url_safety import is_safe_http_url

# Accepted-status-codes default — any code inside the operator-tunable
# `tuning_http_probe_default_accepted_lo_code` / `_hi_code` range
# counts as "status_ok" when no per-row override is set. Default
# 200..399 covers redirect-fronted endpoints (Nextcloud, GitLab,
# Forgejo, common reverse-proxy welcome pages) — the homelab norm.
# Operators on diagnostic deploys may broaden (e.g. 100..599 — "any
# response = alive") OR tighten back to 200..299 from Admin → Config.
# The probe also enables ``follow_redirects=True`` on its httpx client
# so a redirect CHAIN lands on the final status code which then
# counts under the default range without any per-row override.
# Per-row ``accepted_status_codes`` CSV overrides this range exactly —
# operators wanting to gate on a specific code (e.g. some hosts
# intentionally redirect and the operator wants to flag a 200 as
# suspicious, or a strictly-locked-down API requires 401 from
# anonymous probes) set the CSV per-host. Per-use reads so Admin →
# Config edits take effect on the next probe without a restart.
_DEFAULT_ACCEPTED_CODES_FALLBACK_LO = 200
_DEFAULT_ACCEPTED_CODES_FALLBACK_HI = 399


def _default_accepted_codes_range() -> tuple[int, int]:
    """Resolve the (lo, hi) range via TUNABLES with a defensive
    fallback. Lazy-import the tuning module to avoid the
    ``http_probe → tuning → ?`` circular-import risk at module load.
    """
    try:
        from logic.tuning import tuning_int, Tunable
        lo = tuning_int(Tunable.HTTP_PROBE_DEFAULT_ACCEPTED_LO_CODE)
        hi = tuning_int(Tunable.HTTP_PROBE_DEFAULT_ACCEPTED_HI_CODE)
        # Defensive: a misconfigured lo > hi would silently reject
        # every status code. Swap to keep the range non-empty.
        if lo > hi:
            lo, hi = hi, lo
        return lo, hi
    except (KeyError, ValueError, TypeError, ImportError):
        return _DEFAULT_ACCEPTED_CODES_FALLBACK_LO, _DEFAULT_ACCEPTED_CODES_FALLBACK_HI


def _clarify_http_connect_error(raw: str) -> str:
    """Rewrite cryptic TLS-handshake connect errors into actionable text.

    The raw OpenSSL message that bubbles up through httpx.ConnectError
    (e.g. ``[SSL: TLSV1_UNRECOGNIZED_NAME] tlsv1 unrecognized name
    (_ssl.c:1081)``) is opaque to an operator. The most common cause in
    a home-lab is an SNI / vhost mismatch: the probe URL's hostname (or
    a bare IP) doesn't match any virtual-host / certificate the HTTPS
    server is configured for, so the server aborts the handshake with a
    fatal ``unrecognized_name`` alert. There is NO client-side fix —
    retrying without SNI would silently probe the server's DEFAULT
    vhost (a possibly-different service) and report a misleading "up",
    so we surface a clear, honest diagnosis instead and let the operator
    point the probe at the correct hostname. Unknown errors pass through
    truncated, unchanged.
    """
    low = (raw or "").lower()
    if "unrecognized_name" in low or "unrecognized name" in low:
        return ("https TLS: server rejected the SNI (unrecognized name) — "
                "the probe URL host doesn't match a vhost/cert on this "
                "server; probe by the configured hostname, not an IP")
    return f"http connect: {raw[:80]}"


def _hostname_of(url: str) -> str:
    """Best-effort hostname extraction from a URL.

    Returns an empty string when ``url`` is blank, has no scheme, or
    parses to a non-host value. Used both by the DNS sub-probe and
    by the TLS sub-probe (which needs the hostname for SNI).
    """
    try:
        parsed = urlparse(url.strip())
        return (parsed.hostname or "").strip()
    except (ValueError, AttributeError):
        return ""


def _port_of(url: str) -> int:
    """Resolve the URL's port. https → 443, http → 80, explicit
    port in the URL wins. Returns 0 on parse failure — callers gate
    the TLS sub-probe on a truthy return.
    """
    try:
        parsed = urlparse(url.strip())
        port_val: Optional[int] = parsed.port
        if port_val is not None and port_val > 0:
            return port_val
        scheme = (parsed.scheme or "").lower()
        if scheme == "https":
            return 443
        if scheme == "http":
            return 80
    except (ValueError, AttributeError):
        pass
    return 0


async def _first_ip(hostname: str, timeout_seconds: float) -> Optional[str]:
    """Resolve ``hostname`` to its first IP literal (for the SNI-disable
    retry). Python's ssl omits the SNI extension when ``server_hostname``
    is an IP address, so passing the resolved IP as httpx's
    ``sni_hostname`` extension probes the server's DEFAULT vhost without
    SNI — the legitimate fallback when verify is OFF and the named vhost
    rejected the handshake with ``unrecognized_name``. Returns None on any
    resolution failure (caller then keeps the original error)."""
    if not hostname:
        return None
    try:
        infos = await asyncio.wait_for(
            asyncio.to_thread(socket.getaddrinfo, hostname, None),
            timeout=timeout_seconds,
        )
        for info in (infos or []):
            sockaddr = info[4]
            if sockaddr and sockaddr[0]:
                return str(sockaddr[0])
    except (asyncio.TimeoutError, socket.gaierror, OSError, ValueError):
        return None
    except (asyncio.CancelledError, KeyboardInterrupt):
        raise
    except Exception:  # noqa: BLE001
        return None
    return None


async def _tcp_reachable(host: str, port: int, timeout_seconds: float) -> bool:
    """Bare TCP-connect liveness check. Used as the verify-OFF fallback when
    an HTTPS handshake is REFUSED by the server (SNI rejected + no-SNI retry
    also rejected, e.g. nginx ssl_reject_handshake). With verify off TLS
    correctness is explicitly disabled, so a port that ACCEPTS a TCP
    connection (something IS listening, it just refuses the TLS negotiation
    by name) counts as 'reachable'. Returns False on any failure."""
    if not host or port <= 0:
        return False
    writer = None
    try:
        _reader, writer = await asyncio.wait_for(
            asyncio.open_connection(host, port), timeout=timeout_seconds)
        return True
    except (asyncio.TimeoutError, OSError, ConnectionError):
        return False
    except (asyncio.CancelledError, KeyboardInterrupt):
        raise
    except Exception:  # noqa: BLE001
        return False
    finally:
        if writer is not None:
            try:
                writer.close()
            except (OSError, ConnectionError):
                pass


async def _dns_probe(hostname: str, timeout_seconds: float) -> tuple[bool, Optional[str]]:
    """Resolve hostname via ``socket.getaddrinfo`` in a thread executor.

    Returns ``(resolved, error)``. Success: ``(True, None)``. Failure:
    ``(False, "<canonical-short-error>")`` — short strings so callers
    can pattern-match without parsing OS-specific messages.
    """
    if not hostname:
        return False, "no hostname"
    try:
        # asyncio.to_thread is the modern (Py 3.9+) helper for
        # dispatching a blocking call to the default thread executor.
        # Cleaner than the loop.run_in_executor positional-args form
        # whose generic *_Ts signature trips PyCharm's strict mode.
        await asyncio.wait_for(
            asyncio.to_thread(socket.getaddrinfo, hostname, None),
            timeout=timeout_seconds,
        )
        return True, None
    except asyncio.TimeoutError:
        return False, "dns timeout"
    except socket.gaierror as e:
        # gaierror covers NXDOMAIN / no resolver / no A/AAAA record. The
        # operator-readable message lives in ``e.strerror`` on most
        # platforms; ``str(e)`` carries the full ``(errno, message)``
        # tuple shape which is harder to read in logs.
        msg = (getattr(e, "strerror", None) or str(e) or "dns error").strip()
        return False, f"dns: {msg[:60]}"
    except (asyncio.CancelledError, KeyboardInterrupt):
        raise
    except Exception as e:  # noqa: BLE001
        return False, f"dns: {type(e).__name__}: {str(e)[:60]}"


async def _tls_probe(hostname: str, port: int, timeout_seconds: float, verify_tls: bool,
                     _sni_override: Optional[str] = None) -> dict:
    """Extract leaf cert info via a raw asyncio TLS handshake.

    Returns ``{tls_expires_in_days, tls_subject, tls_issuer,
    tls_error}``. On any failure, the three positive fields are None
    and ``tls_error`` carries a short canonical reason. The TCP
    connection is closed immediately after the handshake — we don't
    need the cert chain or any wire-level data beyond the cert.

    ``verify_tls=False`` disables verification (self-signed homelab
    certs) — operators set this on the host's curated row. The cert
    can still be parsed even when verification fails, so the
    expires-in-days warning still surfaces.
    """
    # Annotated permissive so the All-None initial values don't make
    # PyCharm infer dict[str, None] and then warn on every str/int
    # assignment downstream.
    out: dict[str, Any] = {
        "tls_expires_in_days": None,
        "tls_subject": None,
        "tls_issuer": None,
        "tls_error": None,
    }
    if not hostname or port <= 0:
        out["tls_error"] = "tls: no hostname/port"
        return out
    try:
        ctx = ssl.create_default_context()
        if not verify_tls:
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
        # SNI sent in the handshake. Normally the named host; on a no-SNI
        # retry (verify off + the server rejected the named vhost) we pass
        # the resolved IP — Python's ssl omits the SNI extension for an IP
        # server_hostname, so the server answers with its DEFAULT vhost cert.
        _sni = _sni_override or hostname
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(hostname, port, ssl=ctx, server_hostname=_sni),
            timeout=timeout_seconds,
        )
        try:
            ssl_obj = writer.get_extra_info("ssl_object")
            if ssl_obj is None:
                out["tls_error"] = "tls: no ssl_object"
                return out
            cert = ssl_obj.getpeercert()
            # When verify_tls=False (CERT_NONE) Python doesn't parse
            # the cert chain, so getpeercert() returns {} even though
            # the cert IS on the wire. Fall back to the DER bytes via
            # binary_form=True and parse with `cryptography.x509` to
            # extract subject / issuer / expiry — that way self-signed
            # homelab endpoints still surface the metadata operators
            # care about (especially the expiry warning).
            if not cert:
                der_bytes: bytes = b""
                try:
                    raw_der = ssl_obj.getpeercert(binary_form=True)
                    if isinstance(raw_der, bytes):
                        der_bytes = raw_der
                except (ValueError, OSError):
                    der_bytes = b""
                if not der_bytes:
                    out["tls_error"] = "tls: empty peer certificate"
                    return out
                # `cryptography` is a hard-pinned dep (requirements.txt)
                # and also pulled in transitively by PyJWT[crypto] +
                # asyncssh — its import cannot fail at runtime.
                try:
                    from cryptography import x509  # type: ignore
                    from cryptography.hazmat.backends import default_backend  # type: ignore
                    parsed = x509.load_der_x509_certificate(der_bytes, default_backend())
                    try:
                        not_after_dt = parsed.not_valid_after_utc
                    except AttributeError:
                        # cryptography < 42 — fall back to naive aware-stamp
                        not_after_dt = parsed.not_valid_after.replace(tzinfo=timezone.utc)
                    delta = not_after_dt - datetime.now(timezone.utc)
                    out["tls_expires_in_days"] = int(delta.total_seconds() // 86400)
                    subj = parsed.subject.rfc4514_string() if parsed.subject else ""
                    iss = parsed.issuer.rfc4514_string() if parsed.issuer else ""
                    if subj:
                        out["tls_subject"] = subj[:200]
                    if iss:
                        out["tls_issuer"] = iss[:200]
                    return out
                except Exception as cert_err:  # noqa: BLE001
                    out["tls_error"] = f"tls: cert parse failed: {type(cert_err).__name__}: {str(cert_err)[:80]}"
                    return out
            # `notAfter` is a string like "Aug 15 23:59:59 2026 GMT".
            # Python's ssl module uses this exact format universally.
            not_after = cert.get("notAfter")
            if not_after:
                try:
                    expiry_dt = datetime.strptime(not_after, "%b %d %H:%M:%S %Y %Z")
                    # `%Z` parses the literal "GMT" but doesn't set tz
                    # on the resulting datetime — stamp UTC explicitly
                    # so the math against `datetime.now(timezone.utc)`
                    # below stays correct.
                    expiry_dt = expiry_dt.replace(tzinfo=timezone.utc)
                    delta = expiry_dt - datetime.now(timezone.utc)
                    out["tls_expires_in_days"] = int(delta.total_seconds() // 86400)
                except (ValueError, TypeError):
                    pass

            # subject + issuer are tuples-of-tuples of RDN pairs.
            # Flatten the most-common attrs (CN / O) into a readable
            # string. Format: "CN=...,O=...".
            def _flatten_rdn(rdn) -> str:
                pairs = []
                for entry in (rdn or []):
                    for pair in (entry or []):
                        if isinstance(pair, (list, tuple)) and len(pair) >= 2:
                            pairs.append(f"{pair[0]}={pair[1]}")
                return ",".join(pairs)

            subject = _flatten_rdn(cert.get("subject"))
            issuer = _flatten_rdn(cert.get("issuer"))
            if subject:
                out["tls_subject"] = subject[:200]
            if issuer:
                out["tls_issuer"] = issuer[:200]
        finally:
            try:
                writer.close()
                await writer.wait_closed()
            except (OSError, ConnectionError):
                pass
        return out
    except asyncio.TimeoutError:
        out["tls_error"] = "tls timeout"
        return out
    except ssl.SSLCertVerificationError as e:
        out["tls_error"] = f"tls verify: {str(e)[:80]}"
        return out
    except (ssl.SSLError, OSError) as e:
        # No-SNI retry (verify off only): when the server rejected the named
        # vhost with `unrecognized_name`, re-probe its DEFAULT vhost without
        # SNI by passing the resolved IP as server_hostname (ssl omits SNI
        # for an IP). Mirrors the GET-path retry so the expiry pill still
        # resolves for verify-off probes against SNI-strict servers.
        _low = str(e).lower()
        if (not verify_tls) and (_sni_override is None) and (
                "unrecognized_name" in _low or "unrecognized name" in _low):
            _ip = await _first_ip(hostname, timeout_seconds)
            if _ip and _ip != hostname:
                return await _tls_probe(hostname, port, timeout_seconds, verify_tls, _sni_override=_ip)
        out["tls_error"] = f"tls: {type(e).__name__}: {str(e)[:60]}"
        return out
    except (asyncio.CancelledError, KeyboardInterrupt):
        raise
    except Exception as e:  # noqa: BLE001
        out["tls_error"] = f"tls: {type(e).__name__}: {str(e)[:60]}"
        return out


def _status_in_accepted(status_code: int, accepted: Optional[Sequence[int]]) -> bool:
    """Status check — explicit list when supplied, else operator-
    tunable range via the ``HTTP_PROBE_DEFAULT_ACCEPTED_*_CODE``
    TUNABLES (default 200..399).

    Accepted list is a sequence of ints; an empty / None list falls
    through to the default range. Per-use read of the TUNABLES range
    so Admin → Config edits take effect on the next probe without a
    restart.
    """
    if not accepted:
        lo, hi = _default_accepted_codes_range()
        return lo <= status_code <= hi
    try:
        return int(status_code) in {int(x) for x in accepted}
    except (TypeError, ValueError):
        return False


async def probe_http_health(
    url: str,
    *,
    timeout: float,
    dns_timeout: float,
    content_match: Optional[str] = None,
    accepted_status_codes: Optional[Sequence[int]] = None,
    verify_tls: bool = True,
) -> dict:
    """One full probe — DNS + HTTP + (https-only) TLS — for one URL.

    ``timeout`` caps the HTTP request AND (separately) the TLS
    handshake. ``dns_timeout`` caps the resolver. ``content_match``
    is a substring searched in the response body (case-sensitive);
    empty / None means "no content check, content_match_ok=True
    short-circuit". ``accepted_status_codes`` overrides the default
    2xx contract. ``verify_tls=False`` accepts self-signed certs
    (homelab default), still parses the cert for expiry tracking.

    Never raises — every error path collapses into the ``error``
    field with a short canonical string + the matching sub-probe
    booleans staying False.
    """
    url_clean = (url or "").strip()
    if not url_clean:
        return {
            "ok": False,
            "status_code": None,
            "status_ok": False,
            "content_match_ok": False,
            "tls_expires_in_days": None,
            "tls_subject": None,
            "tls_issuer": None,
            "dns_resolved": False,
            "dns_error": "no url",
            "latency_ms": None,
            "error": "no url",
        }
    # SSRF defence in depth — the URL comes from per-host curated
    # config (admin-only via `/api/settings`, write-only secrets
    # contract). Threat model is documented in `logic/url_safety.py`'s
    # module docstring: admins already have direct host-network access,
    # internal probes against RFC1918 / link-local space are LEGITIMATE
    # for the home-lab deploy story, so the gate is intentionally
    # narrow (rejects scheme typos like `file://` / `javascript:` /
    # missing host but accepts every legitimate operator-set value).
    # Reject obviously-broken inputs up-front so the httpx call below
    # never sees an unvalidated URL. CodeQL's `py/full-ssrf` annotates
    # the httpx call site; the per-call inline suppression at the
    # `client.get(url_clean, ...)` line below cites this gate as the
    # validator.
    if not is_safe_http_url(url_clean):
        return {
            "ok": False,
            "status_code": None,
            "status_ok": False,
            "content_match_ok": False,
            "tls_expires_in_days": None,
            "tls_subject": None,
            "tls_issuer": None,
            "dns_resolved": False,
            "dns_error": "invalid url",
            "latency_ms": None,
            "error": "invalid url scheme or missing host",
        }
    hostname = _hostname_of(url_clean)
    port = _port_of(url_clean)
    scheme = (urlparse(url_clean).scheme or "").lower()

    # DNS first — bail before opening sockets when the name can't
    # resolve. The HTTP sub-probe would fail with the same error
    # but DNS-level diagnostic is more actionable for the operator.
    dns_resolved, dns_error = await _dns_probe(hostname, dns_timeout)

    # HTTP probe. Even when DNS failed we still attempt the request
    # so a probe of an IP-literal URL (no name to resolve) works
    # cleanly — httpx accepts IPv4/IPv6 literals directly. The DNS
    # error stays separately reported in `dns_resolved` / `dns_error`.
    status_code: Optional[int] = None
    content_match_ok = not bool((content_match or "").strip())
    latency_ms: Optional[int] = None
    http_error: Optional[str] = None
    # Set True by the verify-off fallback when an HTTPS handshake is refused
    # (SNI + no-SNI both rejected) but a bare TCP connect to the port
    # succeeds — i.e. the server is reachable, just refusing TLS by name.
    tls_refused_reachable = False
    t0 = time.monotonic()
    # TLS verify mode. When verify is OFF (operator opted into an insecure
    # probe for a self-signed homelab device), ALSO relax the TLS floor:
    # `verify=False` alone only skips CERT validation, not version/cipher
    # negotiation, so a LEGACY device (old switches like Cisco SG300, IPMI
    # BMCs, iDRAC) that only speaks TLS 1.0/1.1 + pre-modern ciphers still
    # fails the handshake with modern openssl (SSLV3_ALERT_HANDSHAKE_FAILURE).
    # A permissive context (CERT_NONE + TLS 1.0 floor + SECLEVEL=0) lets the
    # probe reach those boxes. verify=True keeps the strict default.
    _verify: "ssl.SSLContext | bool"
    if verify_tls:
        _verify = True
    else:
        _ctx = ssl.create_default_context()
        _ctx.check_hostname = False
        _ctx.verify_mode = ssl.CERT_NONE
        try:
            _ctx.minimum_version = ssl.TLSVersion.TLSv1
        except (ValueError, AttributeError):
            pass
        try:
            # SECLEVEL=0 re-enables legacy ciphers + small RSA keys the
            # old gear presents; safe here because verification is already
            # off and the probe is read-only.
            _ctx.set_ciphers("DEFAULT@SECLEVEL=0")
        except ssl.SSLError:
            pass
        _verify = _ctx
    try:
        # ``follow_redirects=True`` so 301 / 302 chains land on the
        # final response (typically a 200) — matches the broadened
        # 200..399 default-accepted range and stops Nextcloud / GitLab /
        # Forgejo style WWW redirects from showing as persistent
        # "failing" status. ``status_code`` will still surface the
        # final-hop code; intermediate 3xx chain is followed silently.
        async with httpx.AsyncClient(
            timeout=timeout,
            verify=_verify,
            follow_redirects=True,
        ) as client:
            # codeql[py/full-ssrf] — `url_clean` is gated above by
            # `is_safe_http_url()`; the canonical SSRF threat model
            # (per `logic/url_safety.py` docstring) doesn't apply
            # because the URL is admin-set via `/api/settings`, the
            # admin already has host-network access, and probes
            # against RFC1918 / link-local home-lab gear are
            # LEGITIMATE deployment intent.
            resp = await client.get(url_clean, headers={"User-Agent": "OmniGrid/http-probe"})  # noqa: S310
            status_code = resp.status_code
            latency_ms = int((time.monotonic() - t0) * 1000)
            cm_local = (content_match or "").strip()
            if cm_local:
                try:
                    body_text = resp.text or ""
                    content_match_ok = (cm_local in body_text)
                except (ValueError, UnicodeDecodeError):
                    # Binary body / encoding failure — treat as
                    # "match not found" rather than blowing up.
                    content_match_ok = False
    except httpx.TimeoutException:
        http_error = "http timeout"
    except httpx.ConnectError as e:
        http_error = _clarify_http_connect_error(str(e))
        # SNI-disable retry: when verify is OFF and the server rejected the
        # named vhost with `unrecognized_name`, TLS correctness was explicitly
        # disabled, so re-probe the DEFAULT vhost WITHOUT SNI. Python's
        # ssl omits the SNI extension when server_hostname is an IP, so we
        # pass the resolved IP as httpx's `sni_hostname` extension. A success
        # here is a legitimate default-vhost liveness result for a verify-off
        # probe (NOT done when verify is on — that would mask a real vhost
        # misconfiguration; see _clarify_http_connect_error's docstring).
        _raw = str(e).lower()
        if (not verify_tls) and scheme == "https" and (
                "unrecognized_name" in _raw or "unrecognized name" in _raw):
            _ip = await _first_ip(hostname, dns_timeout)
            if not _ip:
                # WARN, not INFO — a no-SNI retry SKIP means the
                # container's libc resolver couldn't reach the
                # hostname (typically a `.home.lan` zone the Docker
                # DNS doesn't know about). The "warning:" token in
                # the message is what `logic.logs._severity_for`
                # matches to bucket this as WARN (uvicorn-style
                # `WARN:` prefix appears on stdout via the tee).
                print(f"[http_probe] warning: no-SNI retry SKIPPED for {url_clean}: "
                      f"could not resolve {hostname!r} to an IP (container's libc resolver "
                      f"can't reach the name; check `/etc/resolv.conf` + Docker DNS config)")
            else:
                try:
                    async with httpx.AsyncClient(
                        timeout=timeout, verify=_verify, follow_redirects=True,
                    ) as _client2:
                        # codeql[py/full-ssrf] — same gate as the primary GET.
                        _resp2 = await _client2.get(  # noqa: S310
                            url_clean,
                            headers={"User-Agent": "OmniGrid/http-probe"},
                            extensions={"sni_hostname": _ip},
                        )
                    status_code = _resp2.status_code
                    latency_ms = int((time.monotonic() - t0) * 1000)
                    http_error = None  # retry succeeded — clear the SNI error
                    print(f"[http_probe] no-SNI retry OK for {url_clean} "
                          f"(via {_ip}, default vhost) status={status_code}")
                    cm_local = (content_match or "").strip()
                    if cm_local:
                        try:
                            content_match_ok = (cm_local in (_resp2.text or ""))
                        except (ValueError, UnicodeDecodeError):
                            content_match_ok = False
                except (httpx.HTTPError, OSError) as _retry_err:
                    # No-SNI retry ALSO failed. The server refuses a handshake
                    # whose SNI doesn't match a configured vhost EVEN with no
                    # SNI (typically nginx `ssl_reject_handshake on`, or no
                    # default-server on 443). Since verify is OFF the probe
                    # only needs REACHABILITY — fall back to a bare TCP connect
                    # to the port: if it accepts, the host IS reachable (the
                    # server is listening, just refusing the TLS negotiation by
                    # name), so treat it as a soft pass. Only when the TCP
                    # connect ALSO fails do we surface the hard SNI diagnosis.
                    if await _tcp_reachable(hostname, port, timeout):
                        tls_refused_reachable = True
                        http_error = None
                        latency_ms = int((time.monotonic() - t0) * 1000)
                        # WARN (not INFO, not ERROR) — TLS is broken
                        # for this hostname (SNI refused, no-SNI also
                        # refused) but TCP IS reachable so the host is
                        # alive. Operator-visible degraded state worth
                        # surfacing in the WARN bucket — the chip
                        # shows the host up but a real TLS user-agent
                        # would still fail. NEUTRAL wording on the
                        # ERROR side (no `fail`/`error` token) keeps
                        # the classifier off the ERROR bucket; the
                        # `warning:` token bumps it to WARN.
                        print(f"[http_probe] warning: no-SNI retry refused for {url_clean} "
                              f"(via {_ip}); verify-off TCP fallback OK "
                              f"(port {port} reachable; TLS handshake refused by name)")
                    else:
                        # Genuine failure: SNI refused, no-SNI refused, AND the
                        # port is not TCP-reachable. THIS one is a real ERROR.
                        print(f"[http_probe] no-SNI retry FAILED for {url_clean} "
                              f"(via {_ip}): {type(_retry_err).__name__}: {str(_retry_err)[:100]}")
                        http_error = ("https TLS: server rejected the SNI AND a no-SNI "
                                      "retry, and the port is not TCP-reachable — the "
                                      "server refuses any handshake whose SNI isn't a "
                                      "configured vhost (e.g. nginx ssl_reject_handshake / "
                                      "no default 443 server). Add a matching server block "
                                      "or default_server, or probe a hostname the server "
                                      "serves.")
    except httpx.HTTPError as e:
        http_error = f"http: {type(e).__name__}: {str(e)[:60]}"
    except (asyncio.CancelledError, KeyboardInterrupt):
        raise
    except Exception as e:  # noqa: BLE001
        http_error = f"http: {type(e).__name__}: {str(e)[:60]}"

    status_ok = status_code is not None and _status_in_accepted(status_code, accepted_status_codes)

    # TLS probe — only for https URLs. The HTTP sub-probe above ran
    # the actual request through httpx which has its own TLS stack;
    # this dedicated probe extracts the cert separately so we can
    # surface ``notAfter`` even when the HTTP layer failed for
    # unrelated reasons (e.g. 500 from the app behind the proxy).
    tls_info = {
        "tls_expires_in_days": None,
        "tls_subject": None,
        "tls_issuer": None,
        "tls_error": None,
    }
    if scheme == "https" and hostname:
        tls_info = await _tls_probe(hostname, port, timeout, verify_tls)

    # Collapse the sub-probe outcomes into a single ``ok`` bool +
    # ``error`` string. The boolean stays True only when EVERY sub-
    # probe relevant to this URL succeeded — DNS for non-IP-literal
    # URLs, HTTP status in accepted set, content match (when
    # configured), and TLS cert parse for https URLs (verify failure
    # already short-circuited via the SSLCertVerificationError
    # branch above).
    # The TLS-cert sub-probe is a HARD gate only when verify is ON. With
    # verify OFF (TLS correctness explicitly disabled, "just reach the box"),
    # a cert that won't parse (self-signed quirk, SNI-strict vhost that
    # refuses the cert probe) must NOT fail an otherwise-healthy GET — the
    # HTTP status is the source of truth. Cert metadata stays best-effort for
    # the expiry pill.
    # verify-off TCP-reachability fallback: when an HTTPS handshake was
    # refused (SNI + no-SNI) but the port accepts a TCP connection, the host
    # is reachable — for a verify-off "is it up?" probe that's a pass, with
    # no HTTP status / content / cert to check.
    if tls_refused_reachable:
        overall_ok = True
    else:
        overall_ok = (
            status_ok
            and content_match_ok
            and (dns_resolved or not hostname or _is_ip_literal(hostname))
            and (scheme != "https" or not verify_tls or tls_info.get("tls_expires_in_days") is not None)
        )
    error: Optional[str] = None
    if not overall_ok:
        # Priority order: status_ok lives at the top because that's
        # what operators most often want to see. DNS is next (foundational).
        # TLS is last because it's only relevant on https.
        if http_error:
            error = http_error
        elif status_code is not None and not status_ok:
            error = f"status {status_code} not accepted"
        elif not content_match_ok:
            error = "content match not found"
        elif not dns_resolved and not _is_ip_literal(hostname):
            error = dns_error or "dns failed"
        elif tls_info.get("tls_error"):
            error = tls_info["tls_error"]
        else:
            error = "unknown failure"

    return {
        "ok": bool(overall_ok),
        "status_code": status_code,
        "status_ok": bool(status_ok),
        "content_match_ok": bool(content_match_ok),
        "tls_expires_in_days": tls_info.get("tls_expires_in_days"),
        "tls_subject": tls_info.get("tls_subject"),
        "tls_issuer": tls_info.get("tls_issuer"),
        "dns_resolved": bool(dns_resolved),
        "dns_error": dns_error,
        "latency_ms": latency_ms,
        "error": error,
        # True when the pass came from the verify-off TCP-reachability
        # fallback (HTTPS handshake refused but the port is open). Lets a
        # caller distinguish a real HTTP 2xx from a bare reachability pass.
        "tls_refused_reachable": bool(tls_refused_reachable),
    }


def _is_ip_literal(s: str) -> bool:
    """True iff ``s`` parses as an IPv4 or IPv6 literal.

    Used to gate the DNS-must-resolve check off — an IP-literal URL
    doesn't need a DNS lookup, so a DNS failure on that probe path
    is irrelevant to the overall ``ok`` outcome.
    """
    if not s:
        return False
    try:
        socket.inet_pton(socket.AF_INET, s)
        return True
    except (OSError, ValueError):
        pass
    try:
        socket.inet_pton(socket.AF_INET6, s)
        return True
    except (OSError, ValueError):
        pass
    return False


def parse_status_codes_csv(raw) -> list[int]:
    """Parse a CSV / list of HTTP status codes into a sorted unique list.

    Accepts ``"200,301,302"`` or ``[200, 301, 302]`` or single ``200``.
    Returns ``[]`` for empty / unparseable input — caller treats
    empty as "fall back to 2xx default".
    """
    out: set[int] = set()
    if raw is None:
        return []
    if isinstance(raw, (list, tuple)):
        items = [str(x) for x in raw]
    elif isinstance(raw, (int, float)):
        items = [str(int(raw))]
    elif isinstance(raw, str):
        items = [s.strip() for s in raw.split(",")]
    else:
        return []
    for token in items:
        if not token:
            continue
        try:
            n = int(token)
            if 100 <= n <= 599:
                out.add(n)
        except (TypeError, ValueError):
            continue
    return sorted(out)


def parse_urls_textarea(raw) -> list[str]:
    """Parse a multi-line textarea / list into a list of URLs.

    Accepts a list directly OR a newline-separated string. Whitespace-
    trimmed, deduplicated, empty entries dropped. Used by the per-host
    editor to feed the ``http_probe.urls`` override.
    """
    if raw is None:
        return []
    items: list[str]
    if isinstance(raw, (list, tuple)):
        items = [str(x) for x in raw]
    elif isinstance(raw, str):
        items = [s.strip() for s in raw.splitlines()]
    else:
        return []
    seen: set[str] = set()
    out: list[str] = []
    for u in items:
        u = u.strip()
        if not u or u in seen:
            continue
        seen.add(u)
        out.append(u)
    return out
