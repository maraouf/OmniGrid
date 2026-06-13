"""Continuation of `logic.ops` — extracted to keep that
module under the line-count "uncomfortable to navigate" threshold.
Imported back via `from logic.ops_extras import *` at the bottom
of `logic/ops.py` so every existing `from logic.ops
import X` consumer keeps resolving without changes.
"""
"""User-triggered write operations and the in-memory op log.

Five ``_do_*`` handlers (update stack, update container, restart service,
restart container, remove container) wrap Portainer calls with:

  - structured event logging via :class:`Operation.log`
  - persistent history row on completion (``persist_history``)
  - notification fan-out via :func:`notify` (Apprise + in-app store)
  - gather-cache invalidation so the UI re-polls after the mutation

The ``ops`` dict + ``ops_order`` list hold the last 50 operations in
memory for the ``/api/ops`` live-status polling loop — they're NOT the
source of truth for history (the ``history`` SQLite table is). If ops
ever need to outlive a process restart, wire a persistence hook in
:func:`new_op`, but the single-replica invariant (the project conventions) makes
in-memory fine for now.

Notification dispatcher
-----------------------
:func:`notify` is the single entry point used by every _do_* handler
plus the host_metrics_sampler / login paths. It resolves the per-event
toggle (``notify_event_<name>``), then fans out to every enabled
medium in :data:`NOTIFY_MEDIUMS`. Mediums today: ``app`` (in-app
store backed by the ``notifications`` table) and ``apprise`` (HTTP
POST to the operator's Apprise instance). Each medium honours its own
admin-side enable flag (``notify_medium_<name>``) — the per-event
toggle gates the WHOLE notification, the per-medium toggle gates ONE
delivery channel without disabling the event entirely.

Adding a medium: see the project conventions "Canonical extension pattern: add a
notification medium" — six steps (module + dispatcher + toggle + UI
+ i18n + CHANGELOG).

Notification templates
----------------------
Each event has a hard-coded default title + body baked into the
``NOTIFY_TEMPLATE_DEFAULTS`` map below; admins can override either via
the DB-backed ``notify_template_<event>_title`` /
``notify_template_<event>_body`` settings. :func:`render_template`
runs ``str.format_map`` against a :class:`SafeDict` so unknown
``{placeholder}`` tokens render verbatim instead of crashing the
notification dispatch. The set of placeholders supplied per call lives
in :data:`NOTIFY_PLACEHOLDERS` (curated whitelist) — see
``main.api_admin_notify_templates`` for the full surface.
"""
import asyncio
import time
from typing import Optional

import httpx

from logic import gather, portainer
from logic.tuning import tuning_int as _tuning_int, Tunable as _Tunable

# Cyclic-import note: `logic.ops` loads this module from its tail via
# `from logic.ops_extras import *`. By that point `logic.ops`'s body
# has finished defining every symbol below, so these explicit imports
# resolve via the partially-loaded parent module. Underscore-prefixed
# names (`_portainer_op_timeout` / `_truncate_for_log` / `_human_bytes`)
# DON'T propagate via the star-import re-export, so they're imported
# here directly. Non-underscore names (`notify`, `Operation`,
# `persist_history`) WOULD round-trip but explicit imports keep the
# IDE happy + the dependency obvious at a glance.
from logic.datetime_fmt import format_duration  # noqa: E402
from logic.ops import (  # noqa: E402
    Operation,
    _human_bytes,
    _portainer_op_timeout,
    _truncate_for_log,
    notify,
    persist_history,
)


async def notify_with_retry(
    title: str, body: str, status: str = "info", *,
    event: Optional[str] = None,
    actor_username: Optional[str] = None,
    target_kind: Optional[str] = None,
    target_id: Optional[str] = None,
    metadata: Optional[dict] = None,
    retries: int = 1,
    retry_after: float = 60.0,
    label: str = "notify",
) -> None:
    """Fire-and-forget `notify` with bounded retry on dispatch failure.

    extracted from `host_metrics_sampler._record_failure`'s
    inner closure so other callers (login event, future schedule kinds,
    anomaly watchers) get the same retry semantics without copy-pasting.
    `label` is a short tag prepended to error logs so the operator can
    tell two parallel notify chains apart in Admin → Logs.

    Retries on ANY exception from `notify()` after `retry_after` seconds;
    capped at `retries` extra attempts (default 1 = at most two total
    dispatches). Caller is expected to spawn this via
    `asyncio.create_task(...)` — running inline would block the
    triggering path on the retry sleep.
    """
    for attempt in range(retries + 1):
        try:
            await notify(
                title, body, status,
                event=event, actor_username=actor_username,
                target_kind=target_kind, target_id=target_id,
                metadata=metadata,
            )
            if attempt > 0:
                print(f"[{label}] retry succeeded on attempt {attempt + 1}")
            return
        except (asyncio.CancelledError, KeyboardInterrupt):
            raise
        except Exception as e: # noqa: BLE001
            if attempt >= retries:
                # `dropped` keeps the persistent-log severity classifier
                # off the ERROR bucket — caller already sees a
                # per-medium ERROR line on the actual delivery failure.
                print(f"[{label}] notify dropped (giving up after "
                      f"{attempt + 1} attempts): {e}")
                return
            print(f"[{label}] notify primary deferred: {e} — "
                  f"retrying in {retry_after:.0f}s")
            try:
                await asyncio.sleep(retry_after)
            except asyncio.CancelledError:
                raise
            except (RuntimeError, OSError):
                return


# ---------------------------------------------------------------------
# Write ops. Each follows the same pattern: try/except/finally with
# persist_history + cache invalidation in finally.
# ---------------------------------------------------------------------
def _resolve_compose_var(image_expr: str, env_map: dict[str, str]) -> Optional[str]:
    """Resolve a single compose image expression that may carry one or
    more ``${VAR}`` references against an env-map.

    Supports the canonical Compose variable shapes:
      - ``$VAR`` — bare reference
      - ``${VAR}`` — braced reference
      - ``${VAR:-default}`` / ``${VAR-default}`` — fallback to default
        when VAR is unset / empty
      - ``${VAR:?error}`` / ``${VAR?error}`` — required (error if
        missing); we resolve to '' (fail-safe) on missing rather than
        raising so the retag matcher just doesn't match this line.

    Returns the fully-resolved literal (e.g. ``ghcr.io/goauthentik/
    server:2026.2.2``) or ``None`` when ANY referenced variable is
    missing from `env_map`. Pure string substitution — does NOT do
    nested resolution (compose itself doesn't either).
    """
    import re as _re
    if "$" not in image_expr:
        return image_expr
    pattern = _re.compile(
        r"\$(?:\{(?P<braced>[A-Za-z_][A-Za-z0-9_]*)"
        r"(?::?[-?](?P<default>[^}]*))?}"
        r"|(?P<bare>[A-Za-z_][A-Za-z0-9_]*))"
    )
    missing = False

    def _sub(m: "_re.Match[str]") -> str:
        nonlocal missing
        name = m.group("braced") or m.group("bare")
        default = m.group("default")
        if name in env_map and env_map[name] != "":
            return env_map[name]
        if default is not None:
            return default
        # `${VAR:?error}` and bare unset → caller treats this image as
        # unresolvable.
        missing = True
        return ""

    resolved = pattern.sub(_sub, image_expr)
    if missing:
        return None
    return resolved


def _retag_compose_to_latest(
    content: str,
    target_image_repo: Optional[str] = None,
    new_tag: str = "latest",
    env_map: Optional[dict[str, str]] = None,
) -> tuple[str, list[tuple[str, str]], list[str], dict[str, str], list[str]]:
    """Rewrite every ``image: <repo>:<tag>`` line in a compose file to
    ``image: <repo>:<new_tag>``. Returns
    ``(new_content, replacements, repos_found)`` where ``replacements``
    is a list of ``(old_image, new_image)`` pairs in the order they
    appeared, and ``repos_found`` is the list of every repo string the
    matcher saw — useful for diagnosing "no match" errors (caller can
    log it so operators see what repos were actually in the file).

    When ``target_image_repo`` is supplied (e.g. ``"ghcr.io/foo/bar"``),
    matching is tolerant in this order:
      1. Exact equality with the compose's repo.
      2. Suffix match — `target_image_repo` ends the compose repo (so
         `goauthentik/server` matches `ghcr.io/goauthentik/server`).
      3. Suffix match the other way — the compose repo ends with the
         target (registry-less compose entries match registry-prefixed
         targets).
    Match requires a ``/`` boundary so ``foo/server`` doesn't
    accidentally match ``foo/server-extra``.

    ``new_tag`` defaults to ``"latest"`` for back-compat with the
    original "Switch to :latest" code paths; operators can pass any
    valid Docker tag (e.g. ``"2"``, ``"v2-stable"``) when they want to
    track a moving sub-version tag instead of ``:latest``.

    The matcher tolerates: optional surrounding quotes, leading
    whitespace, and the ``@sha256:...`` digest suffix (digest is
    dropped on retag — a moving tag with a pinned digest defeats the
    point). Lines already at ``:<new_tag>`` AND with no digest are
    left alone (idempotent — the helper can re-run without churn).
    """
    import re as _re
    nt = (new_tag or "latest").strip() or "latest"
    env_map_local = dict(env_map or {})
    # Two-pattern strategy: the legacy literal-image regex covers
    # `image: <repo>:<tag>` lines; a SECOND pattern catches lines
    # whose image expression contains compose-variable references
    # (`image: ${VAR}` / `image: ${VAR}:tag` / `image: ${VAR:-default}`).
    # Both fire — literal lines stay handled by the rewrite path; the
    # var-bearing lines route to `env_updates` instead so the caller
    # can patch the stack's Env array (compose body stays unchanged
    # because `${VAR}` still resolves correctly post-update).
    literal_pattern = _re.compile(
        r"""(?P<indent>^\s*)image\s*:\s*(?P<quote>['"]?)(?P<repo>[^:'"@\s${}]+(?::[0-9]+)?(?:/[^:'"@\s${}]+)*)(?::(?P<tag>[^@'"\s]+))?(?:@sha256:[0-9a-f]+)?(?P=quote)\s*$""",
        _re.MULTILINE,
    )
    var_pattern = _re.compile(
        r"""(?P<indent>^\s*)image\s*:\s*(?P<quote>['"]?)(?P<expr>(?:\$\{[^}]+}|\$[A-Za-z_][A-Za-z0-9_]*|[^'"@\s]+)+)(?P=quote)\s*$""",
        _re.MULTILINE,
    )
    replacements: list[tuple[str, str]] = []
    repos_found: list[str] = []
    env_updates: dict[str, str] = {}
    # Lines whose repo MATCHES the filter but whose tag is already
    # the target — short-circuit as no-op (no replacement appended)
    # but track separately so the caller can distinguish "no match"
    # (real failure) from "already at target" (idempotent success).
    already_at_target: list[str] = []

    def _repo_matches(compose_repo: str, target: str) -> bool:
        """Tolerance ladder — exact, then either-side suffix match
        with `/` boundary so `foo/server` matches `ghcr.io/foo/server`
        AND `goauthentik/server` matches `ghcr.io/goauthentik/server`,
        but `foo/server` doesn't accidentally match `foo/server-extra`.

        Normalises both sides (strip whitespace, casefold) before
        comparison — Docker registries are case-insensitive per the
        OCI spec, and trailing-whitespace drift from YAML quoting /
        SPA-side string handling has historically caused false
        no-match outcomes."""
        a = (compose_repo or "").strip().casefold()
        b = (target or "").strip().casefold()
        if not a or not b:
            return False
        if a == b:
            return True
        if a.endswith("/" + b):
            return True
        if b.endswith("/" + a):
            return True
        return False

    def _split_repo_tag(expr: str) -> tuple[str, str]:
        """Split a literal `repo[:tag][@digest]` into (repo, tag).
        Strips any `@sha256:...` suffix. Returns ('', '') on parse
        failure so the caller can skip cleanly."""
        # Strip digest first
        if "@" in expr:
            expr = expr.split("@", 1)[0]
        # The tag is everything after the LAST `:` unless that colon
        # is inside a registry-port spec (heuristic: numeric port
        # immediately after the last colon AND another `/` follows
        # before the tag).
        last_colon = expr.rfind(":")
        last_slash = expr.rfind("/")
        if last_colon > last_slash:
            return expr[:last_colon], expr[last_colon + 1:]
        return expr, ""

    # Diagnostic capture — every _repo_matches call's input + outcome,
    # so the caller can log a per-comparison decision trail when the
    # outer replacement check finds nothing. Cleared every invocation
    # because the function captures these in its closure.
    match_trace: list[dict] = []

    def _literal_repl(m: "_re.Match[str]") -> str:
        indent = m.group("indent")
        quote = m.group("quote") or ""
        repo = m.group("repo")
        old_tag = m.group("tag") or ""
        full_match = m.group()
        repos_found.append(repo)
        if target_image_repo:
            matched = _repo_matches(repo, target_image_repo)
            match_trace.append({
                "src": "literal",
                "repo": repo,
                "repo_bytes": repo.encode().hex() if repo else "",
                "target": target_image_repo,
                "target_bytes": target_image_repo.encode().hex(),
                "matched": matched,
                "old_tag": old_tag,
            })
            if not matched:
                return full_match
        if old_tag == nt and "@sha256:" not in full_match:
            # Idempotent no-op — the line already carries the target
            # tag (caller short-circuits as "already at target" rather
            # than raising "no match"). Track separately so the empty-
            # replacements check at the caller can distinguish real
            # failures from idempotent re-runs.
            already_at_target.append(f"{repo}:{old_tag}")
            return full_match
        old_image = repo + (f":{old_tag}" if old_tag else "")
        new_image = f"{repo}:{nt}"
        replacements.append((old_image, new_image))
        return f"{indent}image: {quote}{new_image}{quote}"

    def _var_repl(m: "_re.Match[str]") -> str:
        """Handle image lines with ${VAR} references. We DON'T rewrite
        the compose line itself — `${VAR}` references continue to
        work post-update because we update the Env value instead.
        Tracks every (var_name, new_value) pair in env_updates."""
        full_match = m.group()
        expr = m.group("expr")
        if "$" not in expr:
            # Falls into literal regex's domain — skip.
            return full_match
        # Find every `${VAR}` / `$VAR` token in the expression and
        # try to resolve. The compose-var resolver handles defaults
        # (`${VAR:-x}`) and required (`${VAR:?err}`) shapes.
        resolved = _resolve_compose_var(expr, env_map_local)
        if resolved is None or not resolved:
            repos_found.append(expr)
            return full_match
        # Split resolved image into repo + old_tag
        repo, old_tag = _split_repo_tag(resolved)
        if not repo:
            repos_found.append(expr)
            return full_match
        repos_found.append(repo)
        if target_image_repo and not _repo_matches(repo, target_image_repo):
            return full_match
        if old_tag == nt and "@sha256:" not in resolved:
            # Idempotent no-op — see _literal_repl's same branch.
            already_at_target.append(f"{repo}:{old_tag}")
            return full_match
        # Strategy 1: when the entire image value comes from ONE env
        # var (most common — `image: ${AUTHENTIK_IMAGE}`), update that
        # var's value end-to-end.
        # Strategy 2: when the env var carries just the repo and the
        # tag is literal in the compose (`image: ${REPO}:2026.2.2`),
        # rewrite the compose line's tag and leave the env var alone.
        # Strategy 3: when tag itself is also var-driven
        # (`image: ${REPO}:${TAG}`), update the TAG var's value.
        bare_var_re = _re.compile(
            r"^\$\{(?P<braced>[A-Za-z_][A-Za-z0-9_]*)(?::?[-?][^}]*)?}$"
            r"|^\$(?P<bare>[A-Za-z_][A-Za-z0-9_]*)$"
        )
        whole_match = bare_var_re.match(expr)
        if whole_match:
            # Strategy 1 — the WHOLE expression is a single var ref.
            var_name = whole_match.group("braced") or whole_match.group("bare")
            new_value = f"{repo}:{nt}"
            env_updates[var_name] = new_value
            replacements.append((resolved, new_value))
            return full_match
        # Look for `${REPO_VAR}:${TAG_VAR}` shape.
        repo_tag_var_re = _re.compile(
            r"^\$\{(?P<repo_var>[A-Za-z_][A-Za-z0-9_]*)}:\$\{(?P<tag_var>[A-Za-z_][A-Za-z0-9_]*)}$"
        )
        rt_match = repo_tag_var_re.match(expr)
        if rt_match:
            tag_var = rt_match.group("tag_var")
            env_updates[tag_var] = nt
            replacements.append((resolved, f"{repo}:{nt}"))
            return full_match
        # Strategy 2 — `${REPO_VAR}:literal_tag` shape: rewrite the
        # compose line's tag literal.
        repo_var_literal_tag_re = _re.compile(
            r"^(?P<var_part>\$\{[A-Za-z_][A-Za-z0-9_]*}|\$[A-Za-z_][A-Za-z0-9_]*):(?P<lit_tag>[^@'\"\s]+)$"
        )
        rvlt_match = repo_var_literal_tag_re.match(expr)
        if rvlt_match:
            var_part = rvlt_match.group("var_part")
            new_expr = f"{var_part}:{nt}"
            quote = m.group("quote") or ""
            indent = m.group("indent")
            replacements.append((resolved, f"{repo}:{nt}"))
            return f"{indent}image: {quote}{new_expr}{quote}"
        # Fallback — un-handled compose-var shape (e.g. multiple
        # vars interleaved with literals beyond the patterns above).
        # Leave alone but log the resolved value into repos_found so
        # the operator sees a diagnostic.
        return full_match

    # Apply literal pattern first — its restrictive repo class
    # (excludes `$`, `{`, `}`) prevents it from accidentally chewing
    # var-bearing lines. The var pattern then picks up what was left.
    new_content = literal_pattern.sub(_literal_repl, content)
    new_content = var_pattern.sub(_var_repl, new_content)
    # Stash match_trace onto a function attribute so the caller can
    # read it for diagnostic logging without changing the public
    # return-tuple shape (callers in tests pin on the 5-tuple).
    _retag_compose_to_latest._last_match_trace = match_trace  # type: ignore[attr-defined]
    return new_content, replacements, repos_found, env_updates, already_at_target


async def _await_stack_convergence(
    client: httpx.AsyncClient, stack: dict, op: "Operation",
) -> None:
    """Block until every Swarm service in this stack's namespace has
    finished rolling out the new image, OR until the timeout fires.

    Why: Portainer's ``PUT /api/stacks/{id}?Prune+PullImage`` accepts
    the request in ~5s and returns 200, but the actual pull + recreate
    runs asynchronously on the docker daemon (often 30-60s+ for real
    image changes). Pre-fix ``do_update_stack`` called ``op.done()``
    immediately after the PUT — the SPA's busy-state cleared while the
    daemon was still rolling, operator's button reverted to "Update"
    before the work was actually done.

    Convergence signal: for every service whose
    ``com.docker.stack.namespace`` label matches this stack's name,
    check ``UpdateStatus.State``. While ANY shows ``"updating"``, keep
    polling. Two consecutive clean polls debounce against the brief gap
    between services in a multi-service stack rolling one at a time.

    Polling cadence + timeout are operator-tunable via
    ``tuning_stack_update_observe_poll_seconds`` (default 15s, range
    5..120) and ``tuning_stack_update_observe_timeout_seconds``
    (default 300s, range 30..1800). Defensive: a timeout WARN-logs but
    still lets the caller stamp ``op.done("success")`` — Portainer
    accepted the request, the rollback is a separate concern.
    """
    stack_name = (stack or {}).get("Name") or ""
    if not stack_name:
        op.log("Convergence wait: stack name missing — skipping poll", "warning")
        return
    try:
        timeout_s = _tuning_int(_Tunable.STACK_UPDATE_OBSERVE_TIMEOUT_SECONDS)
        poll_s = _tuning_int(_Tunable.STACK_UPDATE_OBSERVE_POLL_SECONDS)
    except (KeyError, ValueError, TypeError):
        timeout_s, poll_s = 300, 15
    eid = portainer.PORTAINER_ENDPOINT_ID
    services_url = f"{portainer.PORTAINER_URL}/api/endpoints/{eid}/docker/services"
    deadline = time.time() + timeout_s
    clean_polls = 0
    op.log(f"Waiting for stack convergence (timeout={timeout_s}s, poll={poll_s}s)…")
    while time.time() < deadline:
        try:
            r = await client.get(services_url, headers=portainer.headers())
            if r.status_code >= 400:
                op.log(
                    f"Convergence poll: HTTP {r.status_code} listing services — "
                    f"falling back to time-only wait", "warning",
                )
                await asyncio.sleep(poll_s)
                continue
            services = r.json() or []
        except (httpx.HTTPError, OSError, ValueError) as e:
            op.log(f"Convergence poll: {type(e).__name__}: {e}", "warning")
            await asyncio.sleep(poll_s)
            continue
        any_updating = False
        in_stack_count = 0
        # Collect stuck-updating services for the WARN log line so
        # operators can see WHICH service is still mid-rollout +
        # Swarm's own status message (e.g. "task xxx failed to
        # start", "image pull from registry failed"). Pre-fix the
        # log only said "Waiting for stack convergence" with no
        # service-level detail; reading Admin → Logs was guesswork.
        stuck_services: list[tuple[str, str]] = []
        for svc in services:
            if not isinstance(svc, dict):
                continue
            spec = svc.get("Spec") or {}
            labels = spec.get("Labels") or {}
            ns = labels.get("com.docker.stack.namespace") or ""
            if ns != stack_name:
                continue
            in_stack_count += 1
            us = svc.get("UpdateStatus") or {}
            state = (us.get("State") or "").strip().lower()
            if state == "updating":
                any_updating = True
                svc_name = (spec.get("Name") or "").strip()
                svc_msg = (us.get("Message") or "").strip()
                if svc_name:
                    stuck_services.append((svc_name, svc_msg))
        if in_stack_count == 0:
            # Stack has no Swarm services (compose-only stack, or
            # external/stopped stack). Nothing to wait for —
            # Portainer's PUT-side work is the entire op.
            op.log("Convergence: no Swarm services in stack namespace — done")
            return
        if any_updating:
            clean_polls = 0
            # Surface the stuck services + Swarm's per-service status
            # message so operators reading Admin → Logs see WHICH
            # service is still rolling and WHY (when Swarm bothers to
            # populate the Message field). Capped at first 3 to
            # avoid log-line bloat on big stacks; the count is
            # included so operators know there are more.
            preview = stuck_services[:3]
            extra = len(stuck_services) - len(preview)
            preview_str = "; ".join(
                f"{name}" + (f" ({msg[:80]})" if msg else "")
                for name, msg in preview
            )
            tail = f" (+{extra} more)" if extra > 0 else ""
            op.log(
                f"Convergence poll: {len(stuck_services)} service(s) "
                f"still updating: {preview_str}{tail}",
                "warning",
            )
            await asyncio.sleep(poll_s)
            continue
        clean_polls += 1
        if clean_polls >= 2:
            op.log(f"Stack converged ({in_stack_count} service(s) idle)", "success")
            return
        await asyncio.sleep(poll_s)
    op.log(
        f"Convergence wait: hit {timeout_s}s timeout — marking op done; "
        f"actual rollout may still be in progress",
        "warning",
    )


# noinspection DuplicatedCode
async def do_update_stack(
    op: Operation,
    stack_id: int,
    *,
    retag_to_latest: bool = False,
    target_image_repo: Optional[str] = None,
    new_tag: str = "latest",
) -> None:
    """Pull-and-recreate the named Swarm stack via Portainer's
    ``Prune + PullImage`` stack-update endpoint. Optionally retags
    every (or one specific) ``image:`` line in the compose file to
    ``new_tag`` first — used by the operator-flow "switch this stack
    to :latest" button. Logs progress to the in-memory Operation;
    fires a notification on completion + the cache invalidation
    after persist_history."""
    try:
        op.log(f"Starting stack update (id={stack_id}, retag={retag_to_latest}"
               + (f", new_tag={new_tag!r}" if retag_to_latest else "")
               + ")")
        async with portainer.write_client(timeout=_portainer_op_timeout("long")) as client:
            stack = await portainer.pg(client, f"/api/stacks/{stack_id}")
            op.log(f"Resolved stack: {stack['Name']}")
            try:
                file_data = await portainer.pg(client, f"/api/stacks/{stack_id}/file")
            except httpx.HTTPError as e:
                raise RuntimeError(f"Can't fetch compose file (external stack?): {e}")
            op.log("Fetched compose file from Portainer")
            content = file_data["StackFileContent"]
            # Build env_map from stack.Env so the retag matcher can
            # resolve `${VAR}` references in image lines (Authentik's
            # canonical compose uses `image: ${AUTHENTIK_IMAGE}`).
            stack_env: list = stack.get("Env") or []
            env_map: dict[str, str] = {}
            for ev in stack_env:
                if isinstance(ev, dict):
                    name = (ev.get("name") or "").strip()
                    val = ev.get("value") or ""
                    if name:
                        env_map[name] = str(val)
            if retag_to_latest:
                content, replacements, repos_found, env_updates, already_at_target = _retag_compose_to_latest(
                    content, target_image_repo, new_tag=new_tag, env_map=env_map,
                )
                if not replacements:
                    # Diagnostic: log the EXACT bytes of target +
                    # every captured repo so any invisible-mismatch
                    # cases (trailing whitespace, case drift, unicode
                    # lookalike) surface in the History op log rather
                    # than being silently rejected by _repo_matches.
                    op.log(
                        f"Retag matcher diagnostic — target_image_repo={target_image_repo!r}, "
                        f"target_bytes={(target_image_repo or '').encode().hex()}, "
                        f"repos_found={[repr(r) for r in repos_found]}, "
                        f"already_at_target={already_at_target}, "
                        f"env_updates={env_updates}",
                        "warning",
                    )
                    # Per-call decision trail — shows the actual
                    # _repo_matches outcome for every line the regex
                    # captured, with both sides as utf-8 hex so any
                    # invisible-byte drift (NBSP, ZWSP, BOM, unicode
                    # lookalikes) jumps out at the operator.
                    trace = getattr(_retag_compose_to_latest, "_last_match_trace", [])
                    for i, t in enumerate(trace):
                        op.log(
                            f"Retag trace[{i}] src={t.get('src')} matched={t.get('matched')} "
                            f"repo={t.get('repo')!r} repo_bytes={t.get('repo_bytes')} "
                            f"target_bytes={t.get('target_bytes')} old_tag={t.get('old_tag')!r}",
                            "warning",
                        )
                    # Idempotent success path — the compose ALREADY
                    # tags the target image at the requested version.
                    # Log + skip the Portainer PUT (which would still
                    # be a Prune+PullImage cycle the operator didn't
                    # ask for). The op stamps `success` outside this
                    # block so the SPA shows a green tick + the History
                    # row carries the "already at :<tag>" note.
                    if already_at_target:
                        for ai in already_at_target:
                            op.log(f"Already at target: {ai} — no change needed")
                        op.log(
                            f"Retag to :{new_tag} skipped — every matching "
                            f"image already at this tag ({len(already_at_target)} line(s))"
                        )
                        op.done("success")
                        await notify(
                            f"Stack '{stack['Name']}' already at :{new_tag}",
                            "",
                            event="stack_update_success",
                            actor_username=op.actor,
                            target_kind="stack", target_id=str(stack_id),
                        )
                        return
                    # Surface the actual repos we DID find so the
                    # operator can spot a registry-path mismatch
                    # (e.g. compose uses `authentik/server` while the
                    # target filter expects `ghcr.io/goauthentik/server`).
                    # The matcher is suffix-tolerant — when this error
                    # still fires, the compose's repo path doesn't
                    # share even a tail-segment with the target.
                    found_msg = (
                        f"; compose contains image lines for: {', '.join(repos_found) or '(none)'}"
                        if repos_found else "; compose has no image: lines at all"
                    )
                    raise RuntimeError(
                        f"Retag to :{new_tag} requested but no image: lines matched"
                        + (f" (repo filter: {target_image_repo}" if target_image_repo else "")
                        + (")" if target_image_repo else "")
                        + found_msg
                    )
                for old, new in replacements:
                    op.log(f"Retagged {old} → {new}")
                # Apply env-var updates returned by the matcher in
                # place on the stack's Env array. The compose body's
                # `${VAR}` reference stays as-is — the value the
                # variable resolves to is what we're changing.
                if env_updates:
                    for ev in stack_env:
                        if not isinstance(ev, dict):
                            continue
                        name = (ev.get("name") or "").strip()
                        if name in env_updates:
                            old_val = ev.get("value") or ""
                            ev["value"] = env_updates[name]
                            op.log(f"Updated stack env {name}: {old_val} → {ev['value']}")
            body = {
                "StackFileContent": content,
                "Env": stack_env,
                "Prune": True,
                "PullImage": True,
            }
            op.log("Calling Portainer: Prune=true, PullImage=true")
            r = await client.put(
                f"{portainer.PORTAINER_URL}/api/stacks/{stack_id}"
                f"?endpointId={portainer.PORTAINER_ENDPOINT_ID}",
                json=body, headers=portainer.headers(),
            )
            if r.status_code >= 400:
                raise RuntimeError(f"HTTP {r.status_code}: {r.text[:300]}")
            op.log(f"Portainer accepted update (HTTP {r.status_code})", "success")
            # Portainer's PUT returns "accepted" in ~5s but the actual
            # `Prune + PullImage` runs ASYNC on the docker daemon (often
            # 30-60s+). Without the poll below the op marks "done" while
            # the daemon is still rolling — operator's SPA button reverts
            # to "Update" while the stack is still mid-rollout. Wait for
            # convergence by polling Swarm-service UpdateStatus on every
            # service in this stack's namespace.
            await _await_stack_convergence(client, stack, op)
        op.done("success")
        await notify(
            f"✅ Stack updated: {op.target_name}",
            f"Duration: {format_duration(op.to_dict()['duration'])}", "success",
            event="stack_update_success", actor_username=op.actor,
            target_kind="stack", target_id=str(op.target_id),
        )
    except (asyncio.CancelledError, KeyboardInterrupt):
        raise
    except Exception as e: # noqa: BLE001
        op.log(str(e), "error")
        op.done("error", str(e))
        await notify(f"❌ Stack update failed: {op.target_name}", str(e)[:500], "error",
                     event="stack_update_failure", actor_username=op.actor,
                     target_kind="stack", target_id=str(op.target_id))
    finally:
        persist_history(op)
        gather.invalidate_cache()


async def do_update_container(op: Operation, container_id: str) -> None:
    """Recreate one standalone container via Portainer's
    ``/containers/{id}/recreate?PullImage=true`` endpoint. Resolves
    the target's Swarm node from the gather cache and threads
    `X-PortainerAgent-Target` so worker-node containers route
    correctly. Logs to the Operation, notifies on completion,
    invalidates the gather cache in the finally block.

    Fallback path — when Portainer's `/recreate` endpoint refuses the
    request (any 4xx/5xx response or network error), automatically
    falls through to the manual inspect → pull → stop → remove →
    create → start flow via :func:`_recreate_container_in_place`.
    Portainer's recreate endpoint is unreliable for containers it
    didn't itself deploy (Komodo-managed, raw `docker run`, external
    compose stacks): operator reported the SPA's bulk-update + the
    Telegram `/update all` both silently fail for those containers
    even though their image manifests have updates available. The
    fallback is the same recreate primitive the operator-confirmed
    `do_retag_container_to_latest` uses, just without the retag
    step — so volumes / networks / env / config survive the same way
    Portainer's own recreate would have preserved them.
    """
    try:
        node = portainer.node_for_container(gather.get_cache(), container_id)
        op.log("Recreating container with PullImage=true"
               + (f" on node '{node}'" if node else ""))
        # Capture the pre-recreate image digest so we can detect the
        # "Portainer /recreate returned 200 but did NOTHING" failure
        # mode — operator-flagged for external (Komodo / raw-`docker run`)
        # containers where Portainer's /recreate endpoint accepts the
        # request, returns 200, but no actual pull+recreate happens.
        # Pre-fix the op landed as `success` with no indication the
        # container was unchanged. Post-fix: if the local digest is
        # identical after the /recreate call, fall through to the
        # manual recreate path the same way a 4xx/5xx response does.
        pre_digest: Optional[str] = None
        async with portainer.write_client(timeout=_portainer_op_timeout("short")) as _digest_client:
            try:
                _r = await _digest_client.get(
                    f"{portainer.PORTAINER_URL}/api/endpoints/"
                    f"{portainer.PORTAINER_ENDPOINT_ID}"
                    f"/docker/containers/{container_id}/json",
                    headers=portainer.headers(agent_target=node),
                )
                if _r.status_code == 200:
                    _j = _r.json() or {}
                    pre_digest = (_j.get("Image") or "").strip() or None
                    op.log(f"Pre-recreate container image-id: {pre_digest[:19] + '…' if pre_digest else '(unknown)'}")
                else:
                    op.log(f"Pre-recreate inspect returned HTTP {_r.status_code} — digest comparison disabled", "warning")
            except (httpx.HTTPError, OSError) as _e:
                op.log(f"Pre-recreate inspect failed ({type(_e).__name__}: {_e}) — digest comparison disabled", "warning")
        recreate_endpoint_error: Optional[str] = None
        recreate_response_full: str = ""
        # The container ID the FALLBACK should inspect. Defaults to the
        # original `container_id`; reassigned to the new ID once we parse
        # the /recreate response body. When Portainer's /recreate spawns
        # a fresh container, the OLD `container_id` is reaped — the
        # manual-fallback inspect MUST point at the live new container
        # OR it will 404 and abort before pulling the fresh image.
        new_container_id: str = container_id
        async with portainer.write_client(timeout=_portainer_op_timeout("long")) as client:
            try:
                # `json={}` is REQUIRED — Portainer's recreate endpoint
                # rejects an empty request body with
                # `HTTP 400 {"message":"Invalid request payload","details":"EOF"}`
                # on newer versions. The body is otherwise unused (the
                # `?PullImage=true` query param drives the actual recreate
                # behaviour); it just needs to be valid JSON so the
                # backend's body-parser doesn't EOF before reading
                # anything. Operator-flagged 2026-05-10 against Portainer
                # CE recent.
                r = await client.post(
                    f"{portainer.PORTAINER_URL}/api/docker/{portainer.PORTAINER_ENDPOINT_ID}"
                    f"/containers/{container_id}/recreate?PullImage=true",
                    headers=portainer.headers(agent_target=node),
                    json={},
                )
                # Keep the FULL response text for downstream JSON parsing
                # (the new container ID lives in `Id` — Docker inspect JSON
                # is typically multi-KB, so a pre-parse truncation would
                # chop the body mid-string and json.loads would raise). The
                # 500-char copy is only for the operator-facing log lines.
                recreate_response_full = r.text or ""
                # Three log-line audiences: the in-handler clip (500
                # chars — generous so the body survives a follow-up
                # error-branch re-clip), the HTTP-error detail
                # (300 chars), the success-log preview (200 chars).
                # Centralised via `_truncate_for_log` so the convention
                # stays consistent + new log lines pick up the same
                # ellipsis-on-truncation marker.
                recreate_response_body = _truncate_for_log(recreate_response_full, 500)
                if r.status_code >= 400:
                    recreate_endpoint_error = (
                        f"HTTP {r.status_code}: "
                        f"{_truncate_for_log(recreate_response_body, 300)}"
                    )
                else:
                    op.log(
                        f"Portainer /recreate accepted (HTTP {r.status_code}); "
                        f"response body: "
                        f"{_truncate_for_log(recreate_response_body) or '(empty)'}",
                        "success",
                    )
            except (httpx.HTTPError, OSError) as e:
                # Network-level failure talking to Portainer — also a
                # fallback trigger. The manual path opens its own
                # client so a flaky Portainer connection might recover
                # for the inspect+pull+create dance even if the
                # `/recreate` call itself dropped.
                recreate_endpoint_error = f"{type(e).__name__}: {e}"
        # Silent-recreate detection: Portainer /recreate has TWO failure
        # modes the operator hit in succession on external containers:
        #   (a) returns 200, container is unchanged (no new container ID).
        #   (b) returns 200, NEW container is created BUT the image was
        #       NOT pulled — `?PullImage=true` was silently ignored, so
        #       the new container runs the SAME image as the old one.
        # Both need to fall through to `_recreate_container_in_place`.
        # Detection: parse the NEW container ID from the /recreate
        # response body (when present), inspect IT instead of the old
        # ID (which Docker has already reaped), compare its `Image`
        # field to the pre-recreate digest. If unchanged → no-op.
        if recreate_endpoint_error is None and pre_digest:
            # Extract the new container ID from the response body. The
            # /recreate endpoint returns the full inspect JSON of the
            # new container; `Id` is the canonical sha256 of the new
            # container, which we need to inspect since the old `container_id`
            # is now 404. `new_container_id` is hoisted above so the
            # fallback path can also reach it.
            try:
                import json as _json_post
                # Parse the FULL body, not the 500-char log preview —
                # Docker inspect JSON is multi-KB and truncating before
                # parsing made json.loads raise on every successful
                # recreate, leaving `new_container_id` pointing at the
                # already-reaped old container so the inspect below 404'd.
                _resp_json = _json_post.loads(recreate_response_full) if recreate_response_full else None
                if isinstance(_resp_json, dict):
                    # Defensive: ``Id`` is spec'd as a string but guard
                    # against a future Portainer version returning a
                    # non-string (the outer except catches ValueError /
                    # TypeError, not AttributeError, so an unguarded
                    # ``.strip()`` would propagate past the handler).
                    _raw_id = _resp_json.get("Id")
                    if isinstance(_raw_id, str) and _raw_id.strip():
                        new_container_id = _raw_id.strip()
                    else:
                        new_container_id = container_id
            except (ValueError, TypeError) as _parse_err:
                op.log(
                    f"Failed to parse /recreate response body as JSON "
                    f"({type(_parse_err).__name__}); will inspect old container_id "
                    f"as a fallback and likely get 404",
                    "warning",
                )
            if new_container_id != container_id:
                op.log(f"Portainer /recreate spawned new container {new_container_id[:12]} (was {container_id[:12]})")
            async with portainer.write_client(timeout=_portainer_op_timeout("short")) as _post_client:
                try:
                    _r2 = await _post_client.get(
                        f"{portainer.PORTAINER_URL}/api/endpoints/"
                        f"{portainer.PORTAINER_ENDPOINT_ID}"
                        f"/docker/containers/{new_container_id}/json",
                        headers=portainer.headers(agent_target=node),
                    )
                    if _r2.status_code == 200:
                        post_digest = ((_r2.json() or {}).get("Image") or "").strip()
                        op.log(f"Post-recreate container image-id: {post_digest[:19] + '…' if post_digest else '(unknown)'}")
                        if post_digest and post_digest == pre_digest:
                            recreate_endpoint_error = (
                                f"Portainer /recreate spawned a new container ({new_container_id[:12]}) "
                                f"BUT the image-id is unchanged ({pre_digest[:19]}…) — `?PullImage=true` "
                                f"was silently ignored. Common for external / non-Portainer-deployed containers; "
                                f"the manual fallback path will force the pull"
                            )
                    elif _r2.status_code == 404:
                        # Both old AND new IDs 404 — recreate landed but the
                        # new container immediately exited / was removed.
                        # Can't verify the digest. Log it so the operator
                        # sees the path that ran; don't auto-fallback (the
                        # manual recreate would also fail to find a target).
                        op.log(
                            f"Post-recreate inspect 404 on new container {new_container_id[:12]} — "
                            f"can't verify image-id; the new container may have exited immediately. "
                            f"Check `docker ps -a` on the node",
                            "warning",
                        )
                    else:
                        op.log(f"Post-recreate inspect HTTP {_r2.status_code} — assuming success", "warning")
                except (httpx.HTTPError, OSError) as _e:
                    op.log(f"Post-recreate inspect failed ({type(_e).__name__}: {_e}) — assuming success", "warning")
        if recreate_endpoint_error:
            # If Portainer's /recreate already spawned a fresh container
            # (no-op'd the pull but DID swap the container), the OLD
            # `container_id` is reaped — the fallback's inspect would
            # 404. Point it at the live new container so the manual
            # pull + recreate operates on the right target. When no new
            # ID was produced (Portainer returned an error before
            # spawning anything), `new_container_id` is still equal to
            # `container_id` and the fallback behaves as before.
            fallback_target = new_container_id if new_container_id != container_id else container_id
            op.log(
                f"Portainer /recreate refused or no-op'd ({recreate_endpoint_error}); "
                f"falling back to manual inspect + pull + stop + remove + "
                f"create + start"
                + (f" (against new container {fallback_target[:12]})"
                   if fallback_target != container_id else ""),
                "warning",
            )
            await _recreate_container_in_place(op, fallback_target)
            op.log("Container recreated (manual fallback)", "success")
        op.done("success")
        await notify(f"✅ Container updated: {op.target_name}", "", "success",
                     event="container_update_success", actor_username=op.actor,
                     target_kind="container", target_id=str(op.target_id))
    except (asyncio.CancelledError, KeyboardInterrupt):
        raise
    except Exception as e: # noqa: BLE001
        await _notify_container_op_failure(op, e, kind_label="update")
    finally:
        persist_history(op)
        gather.invalidate_cache()


# ----------------------------------------------------------------------------
# Shared container-write primitives — extracted to dedupe the inspect /
# capture-config / stop-remove-create-connect-start blocks that
# `_recreate_container_in_place` and `do_retag_container_to_latest` would
# otherwise duplicate. Each helper accepts an already-open
# `portainer.write_client(...)` so the outer Operation lifecycle (logging,
# `op.done`, `persist_history`, `notify`) stays with the caller. The
# `log_prefix` knob lets the manual-fallback path retain its `[fallback]`
# prefix on every log line — that's the only operator-visible difference
# between the two consumers' inline copies.
# ----------------------------------------------------------------------------
async def _inspect_container_or_raise(
    client: httpx.AsyncClient, node: Optional[str], container_id: str,
) -> dict:
    """GET the container inspect payload via Portainer. Returns the
    parsed JSON dict. Raises ``RuntimeError`` with a clipped body on
    any non-2xx. Extracted because both retag + recreate need an
    identical inspect step (URL + headers + status-check + parse)."""
    inspect_url = (
        f"{portainer.PORTAINER_URL}/api/endpoints/"
        f"{portainer.PORTAINER_ENDPOINT_ID}"
        f"/docker/containers/{container_id}/json"
    )
    r = await client.get(inspect_url, headers=portainer.headers(agent_target=node))
    if r.status_code >= 400:
        raise RuntimeError(f"inspect HTTP {r.status_code}: {r.text[:300]}")
    return r.json()


def _extract_container_create_inputs(
    inspect: dict, new_image_ref: str,
) -> tuple[dict, dict, dict, Optional[str], list, dict]:
    """Pull every field a `POST /containers/create` payload needs out
    of a Portainer inspect dict + the chosen new image ref. Returns
    ``(cfg, host_cfg, networks, first_network_name, extra_networks,
    networking_config)`` where:

    * ``cfg`` is a copy of the container's `Config` block with `Image`
      overridden to ``new_image_ref``.
    * ``host_cfg`` is a copy of `HostConfig` (verbatim).
    * ``networks`` is the full ``NetworkSettings.Networks`` dict.
    * ``first_network_name`` is the first network attached at create
      time (Docker only allows ONE on the create call; remaining
      networks are reconnected post-create via ``/networks/<id>/connect``).
    * ``extra_networks`` is the list of `(name, endpoint)` tuples that
      ``_stop_remove_create_connect_start`` will reconnect.
    * ``networking_config`` is the ready-to-POST `NetworkingConfig`
      block carrying just the first network's endpoint."""
    cfg = dict(inspect.get("Config") or {})
    host_cfg = dict(inspect.get("HostConfig") or {})
    net_settings = inspect.get("NetworkSettings") or {}
    networks = dict((net_settings.get("Networks") or {}))
    cfg["Image"] = new_image_ref

    first_network_name = next(iter(networks), None)
    extra_networks = list(networks.items())[1:] if first_network_name else []
    networking_config: dict = {}
    if first_network_name:
        first_endpoint = networks[first_network_name] or {}
        networking_config = {
            "EndpointsConfig": {
                first_network_name: first_endpoint,
            }
        }
    return cfg, host_cfg, networks, first_network_name, extra_networks, networking_config


async def _stop_remove_create_connect_start(
    client: httpx.AsyncClient, op: "Operation", *,
    node: Optional[str], container_id: str, old_name: str,
    cfg: dict, host_cfg: dict, networking_config: dict, extra_networks: list,
    log_prefix: str = "",
) -> str:
    """Stop the old container, remove it, create a new one from
    ``cfg`` / ``host_cfg`` / ``networking_config``, reconnect any
    ``extra_networks`` post-create, then start. Returns the new
    container's id.

    ``log_prefix`` is prepended to every operator-visible log line so
    the manual-fallback path can keep its `[fallback] ` prefix; the
    retag path passes the empty string. Stop / remove tolerate 304 +
    404 (already-stopped / already-gone) — only 5xx fails.
    `Hostname` is stripped from the create payload because Docker
    rejects re-using the old container's id-hostname; the new
    container picks one up from its own id."""
    pfx = log_prefix
    # ---- Stop the old container --------------------------------------
    op.log(f"{pfx}Stopping old container…")
    r = await client.post(
        f"{portainer.PORTAINER_URL}/api/endpoints/"
        f"{portainer.PORTAINER_ENDPOINT_ID}"
        f"/docker/containers/{container_id}/stop?t=10",
        headers=portainer.headers(agent_target=node),
    )
    # 304 = already stopped, OK; 404 = already gone, OK; >= 500 fails.
    if r.status_code >= 500:
        raise RuntimeError(f"stop HTTP {r.status_code}: {r.text[:300]}")

    # ---- Remove the old container ------------------------------------
    op.log(f"{pfx}Removing old container…")
    r = await client.delete(
        f"{portainer.PORTAINER_URL}/api/endpoints/"
        f"{portainer.PORTAINER_ENDPOINT_ID}"
        f"/docker/containers/{container_id}?force=true&v=false",
        headers=portainer.headers(agent_target=node),
    )
    if r.status_code >= 500:
        raise RuntimeError(f"remove HTTP {r.status_code}: {r.text[:300]}")

    # ---- Create new ---------------------------------------------------
    create_body = {
        **{k: v for k, v in cfg.items() if k != "Hostname"},
        "HostConfig": host_cfg,
        "NetworkingConfig": networking_config,
    }
    op.log(f"{pfx}Creating new container '{old_name}'…")
    r = await client.post(
        f"{portainer.PORTAINER_URL}/api/endpoints/"
        f"{portainer.PORTAINER_ENDPOINT_ID}"
        f"/docker/containers/create?name={old_name}",
        headers=portainer.headers(agent_target=node),
        json=create_body,
    )
    if r.status_code >= 400:
        raise RuntimeError(f"create HTTP {r.status_code}: {r.text[:300]}")
    new_container_id = (r.json() or {}).get("Id") or ""
    if not new_container_id:
        raise RuntimeError("create returned no container Id")
    op.log(f"{pfx}Created {new_container_id[:12]}")

    # ---- Reconnect extra networks ------------------------------------
    for net_name, endpoint in extra_networks:
        connect_body = {
            "Container": new_container_id,
            "EndpointConfig": endpoint or {},
        }
        r = await client.post(
            f"{portainer.PORTAINER_URL}/api/endpoints/"
            f"{portainer.PORTAINER_ENDPOINT_ID}"
            f"/docker/networks/{net_name}/connect",
            headers=portainer.headers(agent_target=node),
            json=connect_body,
        )
        if r.status_code >= 400:
            op.log(f"{pfx}warn: network connect '{net_name}' "
                   f"HTTP {r.status_code}: {r.text[:200]}", "warning")

    # ---- Start --------------------------------------------------------
    op.log(f"{pfx}Starting new container…")
    r = await client.post(
        f"{portainer.PORTAINER_URL}/api/endpoints/"
        f"{portainer.PORTAINER_ENDPOINT_ID}"
        f"/docker/containers/{new_container_id}/start",
        headers=portainer.headers(agent_target=node),
    )
    if r.status_code >= 400:
        raise RuntimeError(f"start HTTP {r.status_code}: {r.text[:300]}")
    return new_container_id


def _bump_force_update(svc: dict, op: "Operation") -> tuple[dict, int]:
    """Increment ``Spec.TaskTemplate.ForceUpdate`` on a Swarm service
    spec in place. Returns ``(spec, version)`` ready to POST to
    ``/services/{id}/update?version=<version>``. Used by both
    `do_restart_service` (one-service restart) and
    `do_restart_swarm_agent` (Portainer-agent global service)."""
    version = svc["Version"]["Index"]
    spec = svc["Spec"]
    tt = spec.setdefault("TaskTemplate", {})
    tt["ForceUpdate"] = int(tt.get("ForceUpdate", 0)) + 1
    op.log(f"Bumping ForceUpdate to {tt['ForceUpdate']}")
    return spec, version


async def _notify_container_op_failure(op: "Operation", e: Exception, *, kind_label: str) -> None:
    """Container write-op error handler: log + done + notify. ``kind_label``
    fills the user-visible "Container <kind_label> failed" verb (e.g.
    `"update"` / `"retag"`). Both consumers use the same
    ``container_update_failure`` event name so a single Apprise
    template covers them."""
    op.log(str(e), "error")
    op.done("error", str(e))
    await notify(
        f"❌ Container {kind_label} failed: {op.target_name}",
        str(e)[:500], "error",
        event="container_update_failure", actor_username=op.actor,
        target_kind="container", target_id=str(op.target_id),
    )


async def _recreate_container_in_place(op: Operation, container_id: str) -> None:
    """Manual recreate primitive: pull-fresh-manifest + stop + remove +
    create + start, preserving the container's current image REF.

    Mirrors the recreate flow inside :func:`do_retag_container_to_latest`
    but skips the retag step — the new container uses the same image
    ref the old one had, so this is the "Portainer's /recreate endpoint
    refused, but we can still pull a fresh digest under the same tag and
    recreate" path. Used as a fallback by :func:`do_update_container`.

    Does NOT call ``op.done`` / ``persist_history`` / ``notify`` —
    those belong to the caller's outer Operation lifecycle. Raises
    ``RuntimeError`` on any failure with a diagnostic message.

    Volumes / networks / env / restart policy survive because we copy
    Config + HostConfig + NetworkSettings.Networks from the inspect.
    Anonymous volumes are lost on remove — same as Portainer's own
    `/recreate` endpoint, no new risk.

    Intentional: write-ops do NOT consult ``host_failure_state`` /
    ``host_provider_last_ok``. The per-(provider, host) auto-pause
    machinery is designed for sampler load-shedding (skip probing a
    host whose providers are flapping), NOT for gating user-initiated
    write actions. An operator who explicitly clicks Update on a
    paused host is making an informed choice — likely TRYING to fix
    the host's outage by recreating its container. Adding a
    write-op gate here would block exactly the workflow the pause
    indicator is meant to suggest. The Portainer write path is its
    own load-balancing surface; we don't double-gate.
    """
    node = portainer.node_for_container(gather.get_cache(), container_id)
    op.log("[fallback] Inspecting container"
           + (f" on node '{node}'" if node else ""))
    async with portainer.write_client(timeout=_portainer_op_timeout("long")) as client:
        # ---- 1. Inspect ------------------------------------------------
        inspect = await _inspect_container_or_raise(client, node, container_id)
        old_name = (inspect.get("Name") or "").lstrip("/")
        old_image_ref = (inspect.get("Config") or {}).get("Image") or ""
        if not old_image_ref:
            raise RuntimeError("inspect returned no Config.Image — cannot recreate")
        # Strip any `@sha256:…` digest so the pull resolves the current
        # manifest tag (the whole point of an update is to land on the
        # latest digest for the same tag). A digest-pinned ref would
        # silently re-pull the SAME bits and recreate with no actual
        # update.
        target_image_ref = old_image_ref.split("@", 1)[0]
        op.log(f"[fallback] Image ref {old_image_ref!r} "
               + (f"→ {target_image_ref!r} (digest stripped)"
                  if target_image_ref != old_image_ref else "(unchanged)"))

        # ---- 2. Pull a fresh manifest under the same tag ---------------
        pull_url = (
            f"{portainer.PORTAINER_URL}/api/endpoints/"
            f"{portainer.PORTAINER_ENDPOINT_ID}"
            f"/docker/images/create?fromImage={target_image_ref}"
        )
        op.log(f"[fallback] Pulling fresh image manifest for {target_image_ref!r}…")
        r = await client.post(pull_url, headers=portainer.headers(agent_target=node))
        if r.status_code >= 400:
            raise RuntimeError(f"pull HTTP {r.status_code}: {r.text[:300]}")

        # ---- 3. Capture config (same shape as do_retag uses) -----------
        cfg, host_cfg, _networks, _first_net, extra_networks, networking_config = (
            _extract_container_create_inputs(inspect, target_image_ref)
        )

        # ---- 4-6. Stop + remove old / create new / reconnect / start ---
        await _stop_remove_create_connect_start(
            client, op,
            node=node, container_id=container_id, old_name=old_name,
            cfg=cfg, host_cfg=host_cfg,
            networking_config=networking_config, extra_networks=extra_networks,
            log_prefix="[fallback] ",
        )


def _retag_image_string(
    image: str,
    target_repo: Optional[str] = None,
    new_tag: str = "latest",
) -> Optional[str]:
    """Strip tag + digest from `image`, append ``:<new_tag>``. Returns
    None if the image already tracks ``:<new_tag>`` (no work to do) or
    the parse fails. ``target_repo`` (when supplied) gates the retag to
    a single repo so multi-image stacks aren't surprised — Komodo-style
    single-container case ignores it.

    ``new_tag`` defaults to ``"latest"`` for back-compat with the
    original "Switch to :latest" code paths; operators can pass any
    valid Docker tag (e.g. ``"2"``, ``"2.4"``, ``"v2-stable"``) when
    they want to track a moving sub-version tag instead of the
    moving-and-could-be-anything ``:latest``. The Komodo case shipped
    2026-05-10 was specifically: ``komodo-core:2.0.0-dev`` → ``:2`` so
    the operator gets v2 patch updates without bumping to the
    bleeding-edge dev tag.
    """
    if not image:
        return None
    nt = (new_tag or "latest").strip() or "latest"
    no_digest = image.split("@", 1)[0]
    last_slash = no_digest.rfind("/")
    last_colon = no_digest.rfind(":")
    if last_colon > last_slash:
        repo = no_digest[:last_colon]
        tag = no_digest[last_colon + 1:]
    else:
        repo = no_digest
        tag = ""
    if target_repo and repo != target_repo:
        return None
    if tag == nt and "@" not in image:
        return None
    return f"{repo}:{nt}"


# noinspection DuplicatedCode
async def do_retag_container_to_latest(
    op: Operation, container_id: str, new_tag: str = "latest",
) -> None:
    """Switch a non-Portainer-managed container's image tag to
    ``:<new_tag>`` (defaults to ``:latest`` for back-compat with the
    original "Switch to :latest" code path).

    Workflow (preserves volumes, networks, env, command, restart policy):

      1. Inspect the running container — capture name + Config +
         HostConfig + NetworkSettings.Networks.
      2. Compute the new image ref by stripping the current tag and
         appending ``:<new_tag>``.
      3. Pull the new image via ``POST /images/create?fromImage=...``.
      4. Stop + remove the old container.
      5. Create a fresh container with the SAME name + the captured
         Config / HostConfig but with ``Image`` overridden to the new
         ref. Networks beyond the first are reconnected via
         ``POST /networks/{id}/connect`` since Docker's create endpoint
         only attaches the first network from EndpointsConfig.
      6. Start the new container.

    Failure handling: any step before the remove succeeds raises and
    leaves the original container intact. After the remove, a failure
    leaves the operator with no running container — the operator
    confirm flagged that risk before dispatch. Volumes survive because
    they're named (not anonymous) on every well-formed container; if
    the operator runs anonymous volumes those are lost on recreate
    regardless of which path triggered it (this matches Portainer's
    own "Recreate container" behaviour).
    """
    try:
        node = portainer.node_for_container(gather.get_cache(), container_id)
        op.log("Inspecting container" + (f" on node '{node}'" if node else ""))
        async with portainer.write_client(timeout=_portainer_op_timeout("long")) as client:
            inspect = await _inspect_container_or_raise(client, node, container_id)
            old_name = (inspect.get("Name") or "").lstrip("/")
            old_image_ref = (inspect.get("Config") or {}).get("Image") or ""
            new_image_ref = _retag_image_string(old_image_ref, new_tag=new_tag)
            if not new_image_ref:
                raise RuntimeError(
                    f"Image already tracks :{new_tag} or unparseable ({old_image_ref!r})"
                )
            op.log(f"Retag {old_image_ref} → {new_image_ref}")

            # ---- 2. Pull the new image -------------------------------------
            pull_url = (
                f"{portainer.PORTAINER_URL}/api/endpoints/"
                f"{portainer.PORTAINER_ENDPOINT_ID}"
                f"/docker/images/create?fromImage={new_image_ref}"
            )
            op.log("Pulling new image…")
            r = await client.post(pull_url, headers=portainer.headers(agent_target=node))
            if r.status_code >= 400:
                raise RuntimeError(f"pull HTTP {r.status_code}: {r.text[:300]}")

            # ---- 2b. Inspect old + new image configs ----------------------
            # Captured Config from the running container conflates two
            # things: the image's Dockerfile defaults (ENTRYPOINT, CMD,
            # WORKDIR, etc.) AND any operator-level overrides (compose
            # `command:`, `docker run --entrypoint=...`, etc.). When we
            # recreate with a NEW image whose filesystem layout differs
            # (e.g. Komodo moved `entrypoint.sh` between :2.0.0-dev and
            # :latest), copying the OLD image's defaults forces them on
            # the new image and the container fails to start.
            #
            # Fix: for each ambiguous field (Entrypoint, Cmd, WorkingDir,
            # User), if the captured value matches the OLD image's
            # default (operator wasn't overriding) → drop it from the
            # create payload so the NEW image's default applies. If it
            # differs → keep it (genuine operator override). Env is
            # handled the same way at the per-key level so image-defined
            # env vars don't leak into the new container while operator-
            # set env vars survive.
            from urllib.parse import quote as _qt

            async def _image_config(ref: str, label: str) -> dict:
                # Image refs contain `:` and `/` (e.g. `ghcr.io/foo/bar:latest`).
                # `quote(safe='/:')` keeps both literal so Docker's route
                # handler `/images/{name:.+}/json` matches cleanly. httpx
                # generally preserves these characters anyway, but doing
                # it explicitly removes any ambiguity across versions.
                encoded = _qt(ref, safe='/:')
                u = (f"{portainer.PORTAINER_URL}/api/endpoints/"
                     f"{portainer.PORTAINER_ENDPOINT_ID}"
                     f"/docker/images/{encoded}/json")
                try:
                    resp = await client.get(u, headers=portainer.headers(agent_target=node))
                except (httpx.HTTPError, OSError) as inspect_err:
                    op.log(f"image inspect ({label}) failed: {inspect_err}", "warning")
                    return {}
                if resp.status_code >= 400:
                    op.log(
                        f"image inspect ({label}) HTTP {resp.status_code}: "
                        f"{resp.text[:200]}", "warning",
                    )
                    return {}
                return (resp.json() or {}).get("Config") or {}

            old_image_cfg = await _image_config(old_image_ref, "old")
            new_image_cfg = await _image_config(new_image_ref, "new")
            # Diagnostic — surface what each image declared so the
            # operator can correlate the drop-decisions below with the
            # actual Dockerfile defaults. Without these lines a "still
            # crashes on entrypoint" failure mode looks identical to
            # an "inspect call returned empty" failure mode.
            op.log(
                f"old image defaults: Entrypoint={old_image_cfg.get('Entrypoint')!r} "
                f"Cmd={old_image_cfg.get('Cmd')!r} "
                f"WorkingDir={old_image_cfg.get('WorkingDir')!r}"
            )
            op.log(
                f"new image defaults: Entrypoint={new_image_cfg.get('Entrypoint')!r} "
                f"Cmd={new_image_cfg.get('Cmd')!r} "
                f"WorkingDir={new_image_cfg.get('WorkingDir')!r}"
            )
            op.log(
                f"captured from running: Entrypoint={(inspect.get('Config') or {}).get('Entrypoint')!r} "
                f"Cmd={(inspect.get('Config') or {}).get('Cmd')!r} "
                f"WorkingDir={(inspect.get('Config') or {}).get('WorkingDir')!r}"
            )

            # ---- 3. Capture config -----------------------------------------
            cfg, host_cfg, _networks, _first_net, extra_networks, networking_config = (
                _extract_container_create_inputs(inspect, new_image_ref)
            )

            # Drop image-default fields that the operator didn't
            # explicitly override. Compare captured (Config from running
            # container) to OLD image's default — when equal, the
            # operator never set them, so let the NEW image's defaults
            # apply by removing the field from the create payload.
            for field in ("Entrypoint", "Cmd", "WorkingDir", "User"):
                captured = cfg.get(field)
                old_default = old_image_cfg.get(field)
                if captured is not None and captured == old_default:
                    cfg.pop(field, None)
                    op.log(f"Inheriting {field} from new image (was image-default)")
            # Env: filter out vars that came from the OLD image's ENV
            # block; keep operator-set vars (which include compose env
            # entries + `docker run -e ...`). The new image's ENV will
            # apply automatically because Docker layers image ENV under
            # the create-time ENV.
            captured_env = list(cfg.get("Env") or [])
            old_env_set = set(old_image_cfg.get("Env") or [])
            if captured_env and old_env_set:
                operator_env = [v for v in captured_env if v not in old_env_set]
                if len(operator_env) != len(captured_env):
                    op.log(
                        f"Stripped {len(captured_env) - len(operator_env)} image-default env "
                        f"var(s); kept {len(operator_env)} operator override(s)"
                    )
                cfg["Env"] = operator_env

            # ---- 4-6. Stop + remove old / create new / reconnect / start ---
            await _stop_remove_create_connect_start(
                client, op,
                node=node, container_id=container_id, old_name=old_name,
                cfg=cfg, host_cfg=host_cfg,
                networking_config=networking_config, extra_networks=extra_networks,
            )
            op.log("Container retagged + started", "success")
        op.done("success")
        await notify(
            f"✅ Container retagged: {op.target_name}",
            f"Switched to :latest — duration: {format_duration(op.to_dict()['duration'])}",
            "success",
            event="container_update_success", actor_username=op.actor,
            target_kind="container", target_id=str(op.target_id),
        )
    except (asyncio.CancelledError, KeyboardInterrupt):
        raise
    except Exception as e: # noqa: BLE001
        await _notify_container_op_failure(op, e, kind_label="retag")
    finally:
        persist_history(op)
        gather.invalidate_cache()


# noinspection DuplicatedCode
async def do_restart_container(op: Operation, container_id: str) -> None:
    """Restart one standalone container via Portainer's
    ``/containers/{id}/restart`` endpoint. Threads
    `X-PortainerAgent-Target` for worker-node containers; logs
    progress + fires the matching restart_success / restart_failure
    notification."""
    try:
        node = portainer.node_for_container(gather.get_cache(), container_id)
        op.log("Restarting container" + (f" on node '{node}'" if node else ""))
        async with portainer.write_client(timeout=_portainer_op_timeout("short")) as client:
            r = await client.post(
                f"{portainer.PORTAINER_URL}/api/endpoints/{portainer.PORTAINER_ENDPOINT_ID}"
                f"/docker/containers/{container_id}/restart",
                headers=portainer.headers(agent_target=node),
            )
            if r.status_code >= 400:
                raise RuntimeError(f"HTTP {r.status_code}: {r.text[:300]}")
            op.log("Container restarted", "success")
        op.done("success")
        await notify(f"🔄 Container restarted: {op.target_name}", "", "success",
                     event="container_restart_success", actor_username=op.actor,
                     target_kind="container", target_id=str(op.target_id))
    except (asyncio.CancelledError, KeyboardInterrupt):
        raise
    except Exception as e: # noqa: BLE001
        op.log(str(e), "error")
        op.done("error", str(e))
        await notify(f"❌ Container restart failed: {op.target_name}", str(e)[:500], "error",
                     event="container_restart_failure", actor_username=op.actor,
                     target_kind="container", target_id=str(op.target_id))
    finally:
        persist_history(op)
        gather.invalidate_cache()


# noinspection DuplicatedCode
async def do_remove_container(op: Operation, container_id: str) -> None:
    """Force-remove one container + its anonymous volumes via Portainer's
    ``DELETE /containers/{id}?force=true&v=true`` endpoint. Idempotent
    on HTTP 404 (the same end-state as a fresh delete — surface as
    success). Threads `X-PortainerAgent-Target` for worker-node
    containers; logs to the Operation, fires
    container_remove_success / _failure, invalidates the gather
    cache in the finally block."""
    try:
        node = portainer.node_for_container(gather.get_cache(), container_id)
        if node:
            op.log(f"Removing container on node '{node}' (force=true, v=true)")
        else:
            op.log("Removing container (force=true, v=true)")
        async with portainer.write_client(timeout=_portainer_op_timeout("short")) as client:
            r = await client.delete(
                f"{portainer.PORTAINER_URL}/api/endpoints/{portainer.PORTAINER_ENDPOINT_ID}"
                f"/docker/containers/{container_id}?force=true&v=true",
                headers=portainer.headers(agent_target=node),
            )
            # Idempotent removal: if the container is already gone (Swarm
            # cleanup, another operator, a previous click that succeeded
            # after a cache snapshot), 404 is the SAME end-state as a fresh
            # delete. Treat it as success so the operator doesn't see a
            # scary red toast for a no-op. The cache is invalidated in the
            # finally-block regardless, so the row will disappear on the
            # next refresh.
            if r.status_code == 404:
                # Message body avoids the literal word "success" so
                # the persistent-log classifier doesn't promote the
                # intra-op step line into the SUCCESS bucket. Reads
                # the same to operators ("no-op" = "nothing to do
                # because it was already gone"); the op's overall
                # outcome still records success via `op.done`.
                op.log("Container already gone — no-op (idempotent)", "success")
            elif r.status_code >= 400:
                raise RuntimeError(f"HTTP {r.status_code}: {r.text[:300]}")
            else:
                op.log("Container removed", "success")
        op.done("success")
        await notify(f"🗑 Container removed: {op.target_name}", "", "success",
                     event="container_remove_success", actor_username=op.actor,
                     target_kind="container", target_id=str(op.target_id))
    except (asyncio.CancelledError, KeyboardInterrupt):
        raise
    except Exception as e: # noqa: BLE001
        op.log(str(e), "error")
        op.done("error", str(e))
        await notify(f"❌ Container remove failed: {op.target_name}", str(e)[:500], "error",
                     event="container_remove_failure", actor_username=op.actor,
                     target_kind="container", target_id=str(op.target_id))
    finally:
        persist_history(op)
        gather.invalidate_cache()


# noinspection DuplicatedCode
async def do_restart_service(op: Operation, service_id: str) -> None:
    """Restart one Swarm service by bumping its `TaskTemplate.ForceUpdate`
    counter — Docker treats the increment as a "re-deploy without
    image change" trigger, so the tasks respawn without a fresh
    pull. Fires service_restart_success / _failure; invalidates the
    gather cache in the finally block."""
    try:
        op.log("Fetching current service spec")
        async with portainer.write_client(timeout=_portainer_op_timeout("medium")) as client:
            svc = await portainer.pg(
                client,
                f"/api/endpoints/{portainer.PORTAINER_ENDPOINT_ID}/docker/services/{service_id}",
            )
            spec, version = _bump_force_update(svc, op)
            r = await client.post(
                f"{portainer.PORTAINER_URL}/api/endpoints/{portainer.PORTAINER_ENDPOINT_ID}"
                f"/docker/services/{service_id}/update?version={version}",
                json=spec, headers=portainer.headers(),
            )
            if r.status_code >= 400:
                raise RuntimeError(f"HTTP {r.status_code}: {r.text[:300]}")
            op.log("Service restart triggered", "success")
        op.done("success")
        await notify(f"🔄 Service restarted: {op.target_name}", "", "success",
                     event="service_restart_success", actor_username=op.actor,
                     target_kind="service", target_id=str(op.target_id))
    except (asyncio.CancelledError, KeyboardInterrupt):
        raise
    except Exception as e: # noqa: BLE001
        op.log(str(e), "error")
        op.done("error", str(e))
        await notify(f"❌ Service restart failed: {op.target_name}", str(e)[:500], "error",
                     event="service_restart_failure", actor_username=op.actor,
                     target_kind="service", target_id=str(op.target_id))
    finally:
        persist_history(op)
        gather.invalidate_cache()


async def discover_swarm_agent_service(client: httpx.AsyncClient) -> tuple[Optional[str], Optional[str], list[dict]]:
    """Walk every Swarm service, identify the Portainer agent service.

    Returns ``(service_id, service_name, matches)`` —
      - On exactly one match: ``(id, name, [match_summary])``.
      - On zero matches: ``(None, None, [])``.
      - On multiple matches: ``(None, None, [{id, name, image}, ...])``
        so the caller can render a clear error listing every candidate
        and let the operator pick — auto-restarting the wrong service
        is not safe.

    Match heuristic:
      1. Image starts with one of the canonical Portainer agent
         repositories (``portainer/agent``, ``portainer/agent-ce``,
         ``portainer-ee/agent``). The image is the strongest signal —
         operator-renamed services keep their image label.
      2. Fallback: service name CONTAINS ``portainer`` AND ``agent``
         (case-insensitive). Catches operator-renamed services that
         use a non-canonical image (e.g. a pinned digest with no tag).
    """
    ep = f"/api/endpoints/{portainer.PORTAINER_ENDPOINT_ID}/docker"
    services = await portainer.pg(client, f"{ep}/services")
    canonical_image_prefixes = (
        "portainer/agent", "portainer/agent-ce",
        "portainer-ee/agent", "portainer-ce/agent",
    )
    matches: list[dict] = []
    for svc in services or []:
        spec = svc.get("Spec") or {}
        name = spec.get("Name") or ""
        cs = ((spec.get("TaskTemplate") or {}).get("ContainerSpec") or {})
        image = cs.get("Image") or ""
        # Image-prefix match — strip any tag / digest suffix first.
        image_repo = image.split("@", 1)[0].split(":", 1)[0].lower()
        is_canonical = any(image_repo.startswith(p) for p in canonical_image_prefixes)
        # Name fallback — case-insensitive substring match on both
        # `portainer` and `agent`. Avoids false-positives on services
        # named just `agent` or just `portainer` (the latter is
        # typically Portainer SERVER, not the per-node agent).
        nm = name.lower()
        is_name_match = ("portainer" in nm) and ("agent" in nm)
        if is_canonical or is_name_match:
            matches.append({"id": svc.get("ID"), "name": name, "image": image})
    if not matches:
        return None, None, []
    if len(matches) > 1:
        return None, None, matches
    return matches[0]["id"], matches[0]["name"], matches


# noinspection DuplicatedCode
async def do_restart_swarm_agent(op: Operation) -> None:
    """Force-update the Portainer agent global service so every node
    restart-spawns its agent task and re-registers with the manager.

    Wraps the same `service update` mechanic as `do_restart_service`
    but discovers the target service automatically. On ambiguous
    discovery (multiple Portainer-agent services), records the
    candidates in the op log + errors out so the operator can pick
    rather than risk restarting the wrong service.
    """
    try:
        async with portainer.write_client(timeout=_portainer_op_timeout("medium")) as client:
            op.log("Discovering Portainer agent service")
            sid, service_name, matches = await discover_swarm_agent_service(client)
            if not matches:
                raise RuntimeError(
                    "No Portainer agent service found — looked for image "
                    "prefix portainer/agent OR service name containing both "
                    "'portainer' and 'agent'. If you renamed the service or "
                    "use a non-canonical image, restart it manually via "
                    "`docker service update --force <service-name>` on the manager.")
            if len(matches) > 1:
                listing = "; ".join(f"{m['name']} ({m['image']})" for m in matches)
                raise RuntimeError(
                    f"Multiple Portainer agent candidates found — refusing "
                    f"to auto-pick. Candidates: {listing}. Restart manually "
                    f"via `docker service update --force <name>`.")
            # Single match — proceed.
            op.target_id = str(sid)
            op.target_name = service_name or "<portainer-agent>"
            op.log(f"Match: {service_name} (id {sid})")
            ep = f"/api/endpoints/{portainer.PORTAINER_ENDPOINT_ID}/docker"
            svc = await portainer.pg(client, f"{ep}/services/{sid}")
            spec, version = _bump_force_update(svc, op)
            r = await client.post(
                f"{portainer.PORTAINER_URL}{ep}/services/{sid}/update?version={version}",
                json=spec, headers=portainer.headers(),
            )
            if r.status_code >= 400:
                raise RuntimeError(f"HTTP {r.status_code}: {r.text[:300]}")
            op.log("Agent service restart triggered — re-registration "
                   "happens as each node's task respawns", "success")
        op.done("success")
        await notify(
            f"🔄 Portainer agent restarted: {op.target_name}",
            "Force-update applied; agents on every node will respawn "
            "and re-register with the manager.",
            "success",
            event="swarm_agent_restart_success", actor_username=op.actor,
            target_kind="service", target_id=str(op.target_id),
        )
    except (asyncio.CancelledError, KeyboardInterrupt):
        raise
    except Exception as e: # noqa: BLE001
        op.log(str(e), "error")
        op.done("error", str(e))
        await notify(
            f"❌ Portainer agent restart failed: {op.target_name or '<discovery>'}",
            str(e)[:500], "error",
            event="swarm_agent_restart_failure", actor_username=op.actor,
            target_kind="service", target_id=str(op.target_id or ""),
        )
    finally:
        persist_history(op)
        gather.invalidate_cache()


# noinspection DuplicatedCode
async def do_prune_node(op: Operation, hostname: str) -> dict:
    """Run a ``docker system prune``-equivalent on a single Swarm node.

    Matches ``docker system prune -f --volumes``: stopped containers,
    dangling images (not ``-a``), unused networks, unused local volumes,
    build cache. Targeted via ``X-PortainerAgent-Target`` so calls land
    on the right worker's daemon.

    Returns the aggregated totals dict so the caller can surface it
    (response payload, toast, Apprise message).
    """
    totals = {
        "containers": 0, "images": 0, "networks": 0, "volumes": 0,
        "space_reclaimed": 0,  # bytes
    }
    try:
        op.log(f"Starting docker prune on node '{hostname}' "
               "(stopped containers, dangling images, unused networks + volumes, build cache)")
        ep = f"/api/endpoints/{portainer.PORTAINER_ENDPOINT_ID}/docker"
        h = portainer.headers(agent_target=hostname)

        async with portainer.write_client(timeout=_portainer_op_timeout("medium")) as client:
            async def _prune(path: str, label: str, counter_key):
                """POST one of Docker's /prune endpoints. Log per step;
                one failing sub-call (e.g. volumes/prune with nothing
                eligible) shouldn't abort the rest of the pass.
                """
                try:
                    r = await client.post(f"{portainer.PORTAINER_URL}{path}", headers=h)
                    if r.status_code >= 400:
                        op.log(f"{label}: HTTP {r.status_code} — {r.text[:200]}", "error")
                        return
                    j = r.json() if r.content else {}
                    deleted_list = (
                        j.get("ContainersDeleted")
                        or j.get("ImagesDeleted")
                        or j.get("NetworksDeleted")
                        or j.get("VolumesDeleted")
                        or []
                    )
                    deleted = len(deleted_list) if isinstance(deleted_list, list) else 0
                    reclaimed = int(j.get("SpaceReclaimed") or 0)
                    if counter_key:
                        totals[counter_key] += deleted
                    totals["space_reclaimed"] += reclaimed
                    op.log(f"{label}: removed {deleted}, reclaimed {reclaimed:,} B")
                except (httpx.HTTPError, OSError, ValueError, KeyError) as prune_err:
                    op.log(f"{label}: {prune_err}", "error")

            # Order matches `docker system prune`: containers first (frees
            # their images), then images, networks, volumes, build cache.
            await _prune(f"{ep}/containers/prune", "containers/prune", "containers")
            # Dangling-only mirrors `docker system prune` (no `-a`). Filter
            # expressed in Portainer's accepted form (same as Docker CLI).
            await _prune(
                f'{ep}/images/prune?filters={{"dangling":["true"]}}',
                "images/prune (dangling)", "images",
            )
            await _prune(f"{ep}/networks/prune", "networks/prune", "networks")
            await _prune(f"{ep}/volumes/prune", "volumes/prune (unused)", "volumes")
            await _prune(f"{ep}/build/prune", "builder/prune", None)

        op.done("success")
        await notify(
            f"🧹 Prune complete on {hostname}",
            f"Reclaimed {_human_bytes(totals['space_reclaimed'])} across "
            f"{totals['containers']} containers / "
            f"{totals['images']} images / "
            f"{totals['networks']} networks / "
            f"{totals['volumes']} volumes",
            "success",
            event="prune_success", actor_username=op.actor,
            target_kind="host", target_id=hostname,
            metadata={"reclaimed_bytes": totals["space_reclaimed"], **totals},
        )
        return totals
    except (asyncio.CancelledError, KeyboardInterrupt):
        raise
    except Exception as e: # noqa: BLE001
        op.log(str(e), "error")
        op.done("error", str(e))
        await notify(f"❌ Prune failed on {hostname}", str(e)[:500], "error",
                     event="prune_failure", actor_username=op.actor,
                     target_kind="host", target_id=hostname)
        return totals
    finally:
        persist_history(op)
        gather.invalidate_cache()
