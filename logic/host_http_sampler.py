"""HTTP probe sampler — lifespan-managed time-series writer.

Per-tick walks the curated hosts list, finds rows where
``http_probe.enabled === True``, derives the target URL list from
``http_probe.urls`` (when set) OR the row's top-level ``url`` field
+ every ``services[].url`` (fallback). Probes each URL in parallel
via ``Semaphore(tuning_http_probe_concurrency)``. Writes ONE row
per (host_id, url, ts) into ``host_http_samples``.

Skip-don't-synthesize discipline applies but is mostly trivial here
because every sub-probe writes back through :func:`probe_http_health`
with its own error string — there are no rate-derivation corner
cases (HTTP status is a point-in-time gauge, not a counter). The
one exception is the ``alive=False`` semantic for ping: HTTP doesn't
have that — a probe failure IS data the operator wants surfaced
(operators monitor TLS-cert expiry on hosts that may be intentionally
down), so we always write a row regardless of outcome.

Per-(http_probe, host) auto-pause: failure threshold is
``tuning_http_probe_failure_pause_rounds`` (default 5). A "round"
is "every URL for this host failed". Mixed success / failure
across URLs doesn't trip the auto-pause — the operator wants
visibility into "one of three URLs is broken" without losing the
host's other probes.
"""
from __future__ import annotations

import asyncio
import sqlite3
import time
from typing import Optional

from logic import http_probe as _http_probe
from logic import tuning
from logic.tuning import Tunable as _Tunable
from logic.db import (
    db_conn,
    get_setting_bool,
    iter_curated_hosts,
)
from logic.settings_keys import Settings


def _curated_http_probe_hosts() -> list[dict]:
    """Curated hosts opted-in for HTTP probing.

    Returns one entry per host (NOT per URL) — the entry carries the
    list of URLs to probe. Lets the sampler decide concurrency per-
    URL OR per-host as it likes. Each entry:

      {
        "id": "<host_id>",
        "urls": [...],            # resolved target URLs
        "content_match": Optional[str],
        "accepted_status_codes": list[int],
        "verify_tls": bool,
      }

    URL resolution chain (FIRST hit wins):
      1. ``http_probe.urls`` (list / textarea content) — explicit
         per-host override list
      2. ``url`` + every ``services[].url`` on the row — the
         operator's curated web-UI links double as health-check
         targets

    Hosts with NO resolvable URLs are dropped (logged once-per-tick
    upstream so the operator can see the empty-list condition).
    """
    out: list[dict] = []
    for row in iter_curated_hosts():
        _raw_cfg = row.get("http_probe")
        cfg: dict = _raw_cfg if isinstance(_raw_cfg, dict) else {}
        if not cfg.get("enabled"):
            continue
        hid = (row.get("id") or "").strip()
        # URL list — operator override first, else top-level url +
        # services[].url. Dedupe within the host's URL set.
        raw_urls = cfg.get("urls")
        urls = _http_probe.parse_urls_textarea(raw_urls)
        if not urls:
            collected: list[str] = []
            top_url = (row.get("url") or "").strip()
            if top_url:
                collected.append(top_url)
            services = row.get("services")
            if isinstance(services, list):
                for svc in services:
                    if not isinstance(svc, dict):
                        continue
                    svc_url = (svc.get("url") or "").strip()
                    if svc_url:
                        collected.append(svc_url)
            urls = _http_probe.parse_urls_textarea(collected)
        if not urls:
            continue
        content_match_raw = cfg.get("content_match")
        content_match = content_match_raw.strip() if isinstance(content_match_raw, str) else ""
        codes = _http_probe.parse_status_codes_csv(cfg.get("accepted_status_codes"))
        # ``verify_tls`` defaults to True; explicit False opts the host
        # into self-signed-cert acceptance. The cert parse still runs
        # so expiry tracking remains useful.
        verify_tls = cfg.get("verify_tls")
        verify_tls = True if verify_tls is None else bool(verify_tls)
        out.append({
            "id": hid,
            "urls": urls,
            "content_match": content_match or None,
            "accepted_status_codes": codes,
            "verify_tls": verify_tls,
        })
    return out


def _dns_skip_synth_result(url: str) -> dict:
    """Synthetic probe-result dict returned when the DNS-skip cache
    short-circuits a probe. Same shape as the real `probe_http_health`
    output so the downstream row-build code in `_one` / `_persist_rows`
    doesn't need a special-case path. Extracted to a module helper so
    the dict shape lives in ONE place — if a new probe-result field
    lands (e.g. `tls_chain_ok`), this helper is the only stop besides
    the real probe."""
    return {
        "url": url,
        "ok": False,
        "status_code": None,
        "status_ok": False,
        "content_match_ok": False,
        "tls_expires_in_days": None,
        "tls_subject": None,
        "tls_issuer": None,
        "dns_resolved": False,
        "dns_error": "skipped — DNS resolution cached as failing",
        "latency_ms": None,
        "error": "dns_skip_cache",
    }


def _build_url_row(r: dict) -> dict:
    """Shape one probe-result dict into the per-URL row stored in
    `host_http_samples` / surfaced via `latest_for_host` /
    `bulk_latest_for_hosts`. Single definition prevents the
    13-line dict-construction block from drifting across the two
    read sites (was duplicated; now shared)."""
    return {
        "url": r["url"],
        "status_code": r["status_code"],
        "status_ok": bool(r["status_ok"]),
        "content_match_ok": bool(r["content_match_ok"]),
        "tls_expires_in_days": r["tls_expires_in_days"],
        "tls_subject": r["tls_subject"],
        "tls_issuer": r["tls_issuer"],
        "tls_error": r["tls_error"],
        "dns_resolved": bool(r["dns_resolved"]),
        "latency_ms": r["latency_ms"],
        "error": r["error"],
    }


def _resolve_http_probe_interval() -> int:
    """Sampler tick cadence — thin wrapper for binary-compat. The
    canonical implementation lives at `tuning.resolve_provider_interval`
    (shared across http_probe / service_probe samplers per CLAUDE.md
    priority L duplicate-code rule). Floors at 30s; falls back to
    `STATS_SAMPLE_INTERVAL_SECONDS` when the per-provider knob is 0.
    """
    return tuning.resolve_provider_interval(_Tunable.HTTP_PROBE_SAMPLE_INTERVAL_SECONDS)


async def _probe_one_host(host: dict, sem: asyncio.Semaphore) -> dict:
    """Probe every URL on one host concurrently; return per-URL outcomes.

    Returns ``{host_id, results: [{url, ok, ...}, ...], any_ok,
    all_ok}`` — the outer tick uses ``all_ok`` for the auto-pause
    threshold accounting (any-URL-success keeps the host out of the
    failed bucket, mirroring the operator's mental model of "one
    URL down doesn't fault the whole host").
    """
    # Per-(http_probe, host) auto-pause short-circuit. Routed through
    # the canonical `_is_provider_paused` helper in main.py (lazy
    # import dodges the circular dependency at module load) so the
    # SELECT shape stays single-sourced — pre-fix this was an inline
    # SELECT here that could drift from the canonical implementation
    # over time. The helper swallows DB errors internally and returns
    # False on any failure, so a transient SQLite blip lets the probe
    # run rather than starving the host.
    import main as _main
    if _main.is_provider_paused(host["id"], "http_probe"):
        return {"host_id": host["id"], "results": [], "any_ok": False, "all_ok": False, "skipped_paused": True}

    timeout_s = float(tuning.tuning_int(_Tunable.HTTP_PROBE_TIMEOUT_SECONDS))
    dns_timeout_s = float(tuning.tuning_int(_Tunable.HTTP_PROBE_DNS_TIMEOUT_SECONDS))
    content_match: Optional[str] = host.get("content_match")
    codes = host.get("accepted_status_codes") or []
    verify_tls = bool(host.get("verify_tls", True))

    async def _one(url: str) -> dict:
        async with sem:
            # DNS-failure short-circuit — synthesize a failure result
            # without paying the full probe path when the URL's
            # hostname is in the shared DNS-skip cache. Latches off
            # on next successful resolution via the cache helper.
            # Local-bind `_host` so the type narrows from `str | None`
            # to `str` for the helper call. Narrow except to the two
            # failure classes that can realistically surface here.
            try:
                from urllib.parse import urlparse as _urlparse
                from logic.dns_skip import should_skip_dns as _should_skip_dns
                _host = _urlparse(url).hostname
                if _host and _should_skip_dns(_host):
                    return _dns_skip_synth_result(url)
            except (ImportError, ValueError):
                # ImportError: dns_skip module unavailable.
                # ValueError: urlparse rejected an unparseable URL.
                # Either way, fall through to the real probe.
                pass
            try:
                probe_result = await _http_probe.probe_http_health(
                    url,
                    timeout=timeout_s,
                    dns_timeout=dns_timeout_s,
                    content_match=content_match,
                    accepted_status_codes=codes,
                    verify_tls=verify_tls,
                )
            except asyncio.CancelledError:
                raise
            except Exception as e:  # noqa: BLE001
                err = f"{type(e).__name__}: {str(e)[:120]}"
                probe_result = {
                    "ok": False,
                    "status_code": None,
                    "status_ok": False,
                    "content_match_ok": False,
                    "tls_expires_in_days": None,
                    "tls_subject": None,
                    "tls_issuer": None,
                    "dns_resolved": False,
                    "dns_error": None,
                    "latency_ms": None,
                    "error": err,
                }
            probe_result["url"] = url
            return probe_result

    results = await asyncio.gather(*(_one(u) for u in host["urls"]))
    any_ok = any(r.get("ok") for r in results)
    all_ok = all(r.get("ok") for r in results) if results else False
    return {
        "host_id": host["id"],
        "results": results,
        "any_ok": any_ok,
        "all_ok": all_ok,
        "skipped_paused": False,
    }


def _clamp200(s) -> Optional[str]:
    """Truncate ``s`` to ≤ 200 chars OR return ``None`` for empty input.

    Used by every TLS / error column that ``host_http_samples`` writes
    (`tls_subject`, `tls_issuer`, `tls_error`, `error`) so the
    truncation contract stays uniform — same cap, same empty-string-
    to-NULL collapse — regardless of which call site produced the
    value. Non-string inputs are coerced via ``str``; ``None`` short-
    circuits to ``None``.
    """
    if s is None:
        return None
    text = str(s) if not isinstance(s, str) else s
    truncated = text[:200]
    return truncated or None


def _persist_rows(host_id: str, results: list[dict], ts: int) -> int:
    """Bulk-insert per-URL results for one host. Returns rows written."""
    if not results:
        return 0
    rows = []
    for r in results:
        url = (r.get("url") or "").strip()
        if not url:
            continue
        # TLS metadata fields persisted alongside the numeric outcome
        # so the drawer card can surface subject / issuer without
        # cross-referencing an external monitor. tls_error carries the
        # exception text when the TLS handshake itself failed (cert
        # chain broken, hostname mismatch, etc.) — distinct from the
        # outer `error` column which covers HTTP-layer failures. Both
        # truncated to 200 chars to bound row size on pathological
        # cert chains.
        rows.append((
            ts, host_id, url,
            r.get("status_code"),
            # Per-URL health signal the drawer card reads. status_ok is
            # "HTTP status accepted"; OR-in tls_refused_reachable so a
            # verify-off URL that's TCP-reachable but refuses the TLS
            # handshake (e.g. nginx ssl_reject_handshake) persists as
            # healthy — matching the live Test (which keys off the overall
            # `ok`). Without this the sampler stored status_ok=0 and the
            # card showed red while the Test showed green.
            1 if (r.get("status_ok") or r.get("tls_refused_reachable")) else 0,
            1 if r.get("content_match_ok") else 0,
            r.get("tls_expires_in_days"),
            _clamp200(r.get("tls_subject")),
            _clamp200(r.get("tls_issuer")),
            _clamp200(r.get("tls_error")),
            1 if r.get("dns_resolved") else 0,
            r.get("latency_ms"),
            _clamp200(r.get("error")),
        ))
    if not rows:
        return 0
    try:
        with db_conn() as c:
            c.executemany(
                "INSERT OR REPLACE INTO host_http_samples "
                "(ts, host_id, url, status_code, status_ok, "
                " content_match_ok, tls_expires_in_days, "
                " tls_subject, tls_issuer, tls_error, "
                " dns_resolved, latency_ms, error) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                rows,
            )
        return len(rows)
    except (sqlite3.Error, OSError) as e:
        print(f"[http_probe_sampler] {host_id!r} DB insert skipped: {e}")
        return 0


def _prune_old_samples() -> int:
    days = tuning.tuning_int(_Tunable.STATS_HISTORY_DAYS)
    cutoff = int(time.time() - days * 86400)
    try:
        with db_conn() as c:
            cur = c.execute("DELETE FROM host_http_samples WHERE ts < ?", (cutoff,))
            return cur.rowcount or 0
    except (sqlite3.Error, OSError) as e:
        print(f"[http_probe_sampler] prune skipped: {e}")
        return 0


async def host_http_sampler_loop() -> None:
    """Lifespan-managed sampler. Dormant when ``"http_probe"`` isn't
    in ``host_stats_source`` OR the master ``http_probe_enabled``
    setting is false — keeps ticking so the operator can flip the
    probe on without restarting.
    """
    interval = _resolve_http_probe_interval()
    # First-tick delay — let DB migrations land + give the rest of
    # the lifespan a chance to come up.
    await asyncio.sleep(min(30, interval))
    tick = 0
    while True:
        try:
            master_enabled = get_setting_bool(Settings.HTTP_PROBE_ENABLED)
            # Gate ONLY on the master toggle — matching service_sampler.
            # http_probe is a master-toggle provider (the SPA's
            # hasHostStatsSource() special-cases it, NOT a host_stats_source
            # CSV member). Requiring the CSV token too was a desync trap:
            # if the toggle was enabled before _sync_host_stats_source landed
            # (or via a path that didn't re-save settings), the token never
            # entered the CSV and the sampler stayed permanently dormant
            # → "Updated 18d ago" with no obvious cause. Per-host opt-in is
            # already enforced by _curated_http_probe_hosts() (http_probe.enabled
            # + resolvable URLs), so dropping the CSV gate cannot over-probe.
            if not master_enabled:
                pass  # globally disabled — stay alive for runtime toggle
            else:
                hosts = _curated_http_probe_hosts()
                if hosts:
                    sem = asyncio.Semaphore(
                        tuning.tuning_int(_Tunable.HTTP_PROBE_CONCURRENCY)
                    )
                    pause_threshold = tuning.tuning_int(_Tunable.HTTP_PROBE_FAILURE_PAUSE_ROUNDS)
                    ts = int(time.time())
                    n_queued = sum(len(h["urls"]) for h in hosts)
                    n_persisted = 0
                    n_skipped = 0
                    n_ok = 0
                    n_err = 0
                    outcomes = await asyncio.gather(
                        *(_probe_one_host(h, sem) for h in hosts),
                        return_exceptions=True,
                    )
                    # Record per-(http_probe, host) outcomes + persist rows.
                    # `record_provider_outcome` is mandatory per the
                    # eighth-pass review convention — fires on both
                    # branches except the cool-down / paused skip.
                    from logic.host_metrics_sampler import (
                        record_provider_outcome as _rec_outcome,
                    )
                    # Pre-tick snapshot of currently-failing hosts so we
                    # can detect the healthy → failing transition and
                    # fire one notification per transition (NOT per
                    # subsequent tick) per the security/info-events
                    # convention. Cheap dict scan; the failure-state
                    # table is small (one row per (provider, host)).
                    previously_failing: set[str] = set()
                    try:
                        with db_conn() as c:
                            for r in c.execute(
                                "SELECT host_id FROM host_failure_state "
                                "WHERE provider='http_probe' AND consecutive_failures > 0"
                            ).fetchall():
                                previously_failing.add(r[0])
                    except (sqlite3.Error, OSError):
                        pass  # transient — re-fires next tick are acceptable
                    for outcome in outcomes:
                        if isinstance(outcome, BaseException):
                            n_err += 1
                            print(f"[http_probe_sampler] unexpected probe exception: {outcome}")
                            continue
                        host_id = outcome.get("host_id") or "?"
                        if outcome.get("skipped_paused"):
                            n_skipped += 1
                            continue
                        results = outcome.get("results") or []
                        any_ok = bool(outcome.get("any_ok"))
                        n_persisted += _persist_rows(host_id, results, ts)
                        if any_ok:
                            n_ok += 1
                            # Per-(http_probe, host) recovery — any URL
                            # OK clears the auto-pause counter. Mirrors
                            # the Ping convention (treat the host as up
                            # when ANY transport reports alive).
                            await _rec_outcome(host_id, "http_probe", True)
                        else:
                            n_err += 1
                            err_msg = ""
                            failing_url = ""
                            for r in results:
                                if r.get("error"):
                                    err_msg = str(r.get("error"))
                                    failing_url = str(r.get("url") or "")
                                    break
                            await _rec_outcome(
                                host_id, "http_probe", False,
                                error=err_msg or "all URLs failed",
                                round_threshold=pause_threshold,
                            )
                            # Fire the `http_probe_failure` notification
                            # on the healthy → failing transition only.
                            # Subsequent ticks where the host stays
                            # failing don't re-notify (operator gets ONE
                            # alert per outage, not a tick-rate stream).
                            if host_id not in previously_failing:
                                try:
                                    from logic.ops import notify as _notify
                                    await _notify(
                                        f"HTTP probe failed: {host_id}",
                                        f"URL: {failing_url}\nError: {err_msg or 'all URLs failed'}",
                                        "error",
                                        event="http_probe_failure",
                                        target_kind="host",
                                        target_id=host_id,
                                        metadata={"url": failing_url, "host": host_id},
                                    )
                                except asyncio.CancelledError:
                                    raise
                                except Exception as notify_err:  # noqa: BLE001
                                    print(
                                        f"[http_probe_sampler] {host_id!r} "
                                        f"http_probe_failure notify deferred: {notify_err}"
                                    )
                    print(
                        f"[http_probe_sampler] tick: {len(hosts)} hosts / "
                        f"{n_queued} URLs / {n_persisted} rows / "
                        f"{n_ok} healthy / {n_err} failing / "
                        f"{n_skipped} skipped (paused)"
                    )
            interval = _resolve_http_probe_interval()
            days = tuning.tuning_int(_Tunable.STATS_HISTORY_DAYS)
            if tick % max(1, 3600 // interval) == 0:
                # Offload prune to worker thread (same pattern as
                # host_metrics_sampler) — keeps the event loop
                # responsive during the hourly DELETE.
                from logic.sampler_metrics import prune_with_metrics
                n = await prune_with_metrics("host_http_sampler", _prune_old_samples)
                if n:
                    print(f"[http_probe_sampler] pruned {n} rows older than {days}d")
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001
            print(f"[http_probe_sampler] tick error: {e}")
        tick += 1
        try:
            await asyncio.sleep(interval)
        except asyncio.CancelledError:
            raise


def bulk_latest_for_hosts(host_ids: list) -> dict:
    """bulk version of :func:`latest_for_host` for many hosts.

    Returns ``{host_id: latest_dict}`` (same dict shape as `latest_for_host`).
    Hosts with no samples are absent. ONE SQL query independent of fleet size
    (capped by SQLite's parameter limit; chunk past ~500 hosts).

    Used by ``/api/hosts/list``'s bulk loop to replace N per-host queries with
    a single bulk read; passed through to ``populate_host_http_merge`` via the
    ``_latest`` kwarg so the per-host stamping logic stays the single source
    of truth.
    """
    ids = [hid for hid in (host_ids or []) if hid]
    if not ids:
        return {}
    try:
        with db_conn() as c:
            placeholders = ",".join(["?"] * len(ids))
            rows = c.execute(
                f"SELECT s.host_id, s.ts, s.url, s.status_code, s.status_ok, "
                f"       s.content_match_ok, s.tls_expires_in_days, s.tls_subject, "
                f"       s.tls_issuer, s.tls_error, s.dns_resolved, s.latency_ms, s.error "
                f"FROM host_http_samples s "
                f"INNER JOIN (SELECT host_id, MAX(ts) AS mts FROM host_http_samples "
                f"            WHERE host_id IN ({placeholders}) GROUP BY host_id) m "
                f"ON s.host_id = m.host_id AND s.ts = m.mts",
                ids,
            ).fetchall()
    except (sqlite3.Error, OSError) as e:
        print(f"[http_probe_sampler] bulk_latest_for_hosts skipped: {e}")
        return {}
    bucket: dict = {}
    ts_by_host: dict = {}
    for r in rows:
        hid = r["host_id"]
        bucket.setdefault(hid, []).append(r)
        ts_by_host[hid] = int(r["ts"] or 0)
    out: dict = {}
    for hid, host_rows in bucket.items():
        url_rows: list = []
        n_ok = 0
        min_tls: Optional[int] = None
        for r in host_rows:
            ok = bool(r["status_ok"]) and bool(r["content_match_ok"])
            url_rows.append(_build_url_row(r))
            if ok:
                n_ok += 1
            tls = r["tls_expires_in_days"]
            if tls is not None:
                if min_tls is None or tls < min_tls:
                    min_tls = tls
        out[hid] = {
            "ts": ts_by_host[hid],
            "urls": url_rows,
            "url_count_total": len(url_rows),
            "url_count_ok": n_ok,
            "any_ok": n_ok > 0,
            "min_tls_expires_in_days": min_tls,
        }
    return out


def latest_for_host(host_id: str) -> dict:
    """Latest per-URL probe outcome for one host — used by
    ``/api/hosts/list`` (skeleton) AND ``_merge_one_host`` (detail)
    so both endpoints surface the same on-disk state.

    Returns:

    .. code-block:: python

        {
            "ts": int,                    # newest ts across the urls
            "urls": [{url, status_code, status_ok, ...}, ...],
            "url_count_total": int,
            "url_count_ok": int,
            "any_ok": bool,
            "min_tls_expires_in_days": Optional[int],
        }

    Empty dict when no samples found.
    """
    if not host_id:
        return {}
    try:
        with db_conn() as c:
            # Get the most recent timestamp for this host.
            row = c.execute(
                "SELECT MAX(ts) FROM host_http_samples WHERE host_id=?",
                (host_id,),
            ).fetchone()
            if not row or row[0] is None:
                return {}
            newest_ts = int(row[0])
            # Pull every URL row at that timestamp. The sampler writes
            # all of a host's URLs in one batch so they share a ts.
            rows = c.execute(
                "SELECT url, status_code, status_ok, content_match_ok, "
                "tls_expires_in_days, tls_subject, tls_issuer, tls_error, "
                "dns_resolved, latency_ms, error "
                "FROM host_http_samples WHERE host_id=? AND ts=?",
                (host_id, newest_ts),
            ).fetchall()
    except (sqlite3.Error, OSError) as e:
        print(f"[http_probe_sampler] latest_for_host({host_id!r}) skipped: {e}")
        return {}
    if not rows:
        return {}
    url_rows: list[dict] = []
    n_ok = 0
    min_tls: Optional[int] = None
    for r in rows:
        ok = bool(r["status_ok"]) and bool(r["content_match_ok"])
        url_rows.append(_build_url_row(r))
        if ok:
            n_ok += 1
        tls = r["tls_expires_in_days"]
        if tls is not None:
            if min_tls is None or tls < min_tls:
                min_tls = tls
    return {
        "ts": newest_ts,
        "urls": url_rows,
        "url_count_total": len(url_rows),
        "url_count_ok": n_ok,
        "any_ok": n_ok > 0,
        "min_tls_expires_in_days": min_tls,
    }


async def probe_and_persist_host(host_id: str) -> dict:
    """On-demand: probe ONE curated host's HTTP-probe URLs NOW and persist
    the per-URL rows to ``host_http_samples`` (same shape + schema as a
    sampler tick). Used by the host-drawer Refresh button so a freshly
    added / edited URL — and the current up/down verdict — appears
    immediately instead of waiting out the next sampler interval (which
    is also why a stale "N down" lingered after the URLs went green).

    Returns ``{ok, probed, persisted, skipped, error}``. Never raises —
    a resolution / probe failure collapses into the result dict.
    """
    hid = (host_id or "").strip()
    if not hid:
        return {"ok": False, "skipped": True, "error": "no host_id"}
    try:
        matching = [h for h in _curated_http_probe_hosts() if h.get("id") == hid]
        if not matching or not (matching[0].get("urls") or []):
            return {"ok": False, "skipped": True, "error": "no URLs resolved"}
        host = matching[0]
        sem = asyncio.Semaphore(max(1, tuning.tuning_int(_Tunable.HTTP_PROBE_CONCURRENCY)))
        outcome = await _probe_one_host(host, sem)
        if outcome.get("skipped_paused"):
            return {"ok": False, "skipped": True, "error": "provider paused"}
        results = outcome.get("results") or []
        persisted = _persist_rows(hid, results, int(time.time()))
        return {"ok": True, "probed": len(results), "persisted": persisted, "skipped": False}
    except asyncio.CancelledError:
        raise
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "skipped": False, "error": f"{type(e).__name__}: {str(e)[:120]}"}


def populate_host_http_merge(host_id: str, merged: dict, *, _latest: Optional[dict] = None) -> None:
    """Stamp ``host_http_*`` fields onto the merged dict for one host.

    Shared helper called from BOTH ``/api/hosts/list`` (skeleton) AND
    ``_merge_one_host`` (per-host detail) so both endpoints surface
    the same on-disk state. Reads from ``host_http_samples`` via
    :func:`latest_for_host`; no live probe.

    Stamps:
      - ``host_http_status_ok``         (bool — any URL OK)
      - ``host_http_status_code``       (newest URL's status_code; or
                                         None if no rows)
      - ``host_http_content_match_ok``  (bool — newest URL)
      - ``host_http_tls_expires_in_days`` (min across URLs)
      - ``host_http_tls_subject``       (newest URL with a cert)
      - ``host_http_dns_resolved``      (bool — newest URL)
      - ``host_http_latency_ms``        (newest URL's latency)
      - ``host_http_error``             (newest URL's error or None)
      - ``host_http_url_count_total``   (int)
      - ``host_http_url_count_ok``      (int)
      - ``host_http_urls``              (list — per-URL detail for
                                         the drawer card)

    Stamps nothing when there are no samples yet — caller's
    ``apply_host_snapshot_fallback`` may surface previous values
    via the stale-data path.
    """
    if not host_id:
        return
    # bulk callers pre-fetch via bulk_latest_for_hosts and pass the
    # per-host result through `_latest`; falls back to the per-host query when
    # called from the detail path (`_merge_one_host`).
    latest = _latest if _latest is not None else latest_for_host(host_id)
    if not latest:
        return
    urls = latest.get("urls") or []
    if not urls:
        return
    # Pick a representative URL row — operator-friendliness > strict-
    # determinism: first OK URL wins, else first row. Drives the
    # single-value fields like status_code / latency / tls_subject.
    primary = next((u for u in urls if u.get("status_ok")), urls[0])
    merged["host_http_status_ok"] = bool(latest.get("any_ok"))
    merged["host_http_status_code"] = primary.get("status_code")
    merged["host_http_content_match_ok"] = bool(primary.get("content_match_ok"))
    merged["host_http_tls_expires_in_days"] = latest.get("min_tls_expires_in_days")
    # Surface TLS cert metadata + handshake error on the merged row.
    # `tls_subject` / `tls_issuer` populated when the cert parse
    # succeeded; `tls_error` populated when the TLS handshake failed
    # (cert expired, hostname mismatch, broken chain). Drawer card
    # binds to all three so operators can spot a soon-to-expire cert
    # OR a misconfigured TLS endpoint at a glance.
    merged["host_http_tls_subject"] = primary.get("tls_subject") if isinstance(primary.get("tls_subject"), str) else None
    merged["host_http_tls_issuer"] = primary.get("tls_issuer") if isinstance(primary.get("tls_issuer"), str) else None
    merged["host_http_tls_error"] = primary.get("tls_error") if isinstance(primary.get("tls_error"), str) else None
    merged["host_http_dns_resolved"] = bool(primary.get("dns_resolved"))
    merged["host_http_latency_ms"] = primary.get("latency_ms")
    merged["host_http_error"] = primary.get("error") or None
    merged["host_http_url_count_total"] = latest.get("url_count_total") or 0
    merged["host_http_url_count_ok"] = latest.get("url_count_ok") or 0
    merged["host_http_urls"] = urls
    merged["host_http_ts"] = latest.get("ts")


def recent_samples(host_id: str, since_ts: int, limit: int = 1000) -> list[dict]:
    """Oldest-first rows for one host back to ``since_ts``.

    Used by the host drawer chart range queries.
    """
    if not host_id:
        return []
    try:
        with db_conn() as c:
            rows = c.execute(
                "SELECT ts, url, status_code, status_ok, content_match_ok, "
                "tls_expires_in_days, tls_subject, tls_issuer, tls_error, "
                "dns_resolved, latency_ms, error "
                "FROM host_http_samples WHERE host_id=? AND ts >= ? "
                "ORDER BY ts ASC LIMIT ?",
                (host_id, int(since_ts), int(limit)),
            ).fetchall()
    except (sqlite3.Error, OSError) as e:
        print(f"[http_probe_sampler] recent_samples({host_id!r}) skipped: {e}")
        return []
    return [{
        "ts": int(r["ts"]),
        "url": r["url"],
        "status_code": r["status_code"],
        "status_ok": bool(r["status_ok"]),
        "content_match_ok": bool(r["content_match_ok"]),
        "tls_expires_in_days": r["tls_expires_in_days"],
        "tls_subject": r["tls_subject"],
        "tls_issuer": r["tls_issuer"],
        "tls_error": r["tls_error"],
        "dns_resolved": bool(r["dns_resolved"]),
        "latency_ms": r["latency_ms"],
        "error": r["error"],
    } for r in rows]


# Public alias for cross-module use. Underscore-prefixed name is the
# canonical in-module declaration; this single-line re-export keeps PyCharm
# happy when main.py imports the helper for the on-demand /api/hosts/{id}/
# http-probe/test endpoint.
curated_http_probe_hosts = _curated_http_probe_hosts
