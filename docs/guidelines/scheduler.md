# Scheduler runbook — OmniGrid

## What it does

Admins define scheduled jobs in the UI (Admin → Schedules). A lifespan task ticks once per
minute; any enabled schedule whose `next_run_at <= now` fires its configured "kind" handler.
The ops system handles actual execution — scheduled runs show up in `/api/ops` live and in
`/api/history` as any other operation would, but with `actor='scheduler'` so the Queue tab can
filter them.

```
Schedule → tick loop → kind handler → Operation
                                    ↓
                         history table ← waiter stamps last_*
```

## Tables

Defined in `logic/schedules.py`, owned by that module, created in `init_db()` via
`schedules.init_schedules_schema(c)`:

```sql
schedules(
  id INTEGER PK,
  name TEXT UNIQUE NOT NULL,
  kind TEXT NOT NULL,              -- see SCHEDULE_KINDS
  params TEXT,                     -- JSON, kind-specific
  interval_seconds INTEGER,        -- seconds between runs (>= 60)
  cadence_mode TEXT,               -- 'interval' | 'daily' | 'weekly' | 'monthly'
  run_at_hhmm TEXT,                -- "HH:MM" 24-hour, container local time (optional)
  day_of_month INTEGER,            -- 1-31 (monthly cadence)
  days_of_week TEXT,               -- JSON array of 0-6 (weekly cadence)
  enabled INTEGER DEFAULT 1,
  last_run_at INTEGER,             -- epoch, NULL = never run
  last_duration INTEGER,           -- seconds
  last_status TEXT,                -- 'success' | 'error'
  last_op_id TEXT,                 -- op.id — links to /api/ops + history
  created_at, updated_at INTEGER
)
```

A scheduled run gets stamped into `history` just like any user-click op (because the kind
handler uses `new_op(…, actor="scheduler")`). Three exceptions:

- `gather_refresh` bypasses `ops.py`, so the runner writes a minimal history row itself (status
  + duration only, no events log).
- `backup` uses a synthetic op_id (no `ops.py` Operation) and writes a direct history row from
  the runner.
- `asset_inventory_refresh` is the same shape as `backup` — synthetic op_id, direct history
  row, no per-target Operation.

All appear in the Queue tab uniformly.

### Schedule shape derived on read

`next_run_at` is **never stored** — `_row_to_dict` computes it on every read based on the
active `cadence_mode`. This keeps the tick loop uniform: "due" is always
`next_run_at <= now`.

## Endpoints

All admin-only; CSRF is enforced by middleware.

| Method   | Route                                      | Purpose                                                                                                                                        |
| -------- | ------------------------------------------ | ---------------------------------------------------------------------------------------------------------------------------------------------- |
| `GET`    | `/api/schedules`                           | List of schedules with `next_run_at` enriched.                                                                                                 |
| `POST`   | `/api/schedules`                           | Create; body `{name, kind, params, interval_seconds, enabled}` plus optional `cadence_mode` / `run_at_hhmm` / `day_of_month` / `days_of_week`. |
| `PATCH`  | `/api/schedules/{id}`                      | Partial update (any of the above).                                                                                                             |
| `DELETE` | `/api/schedules/{id}`                      | Remove.                                                                                                                                        |
| `POST`   | `/api/schedules/{id}/run`                  | Fire immediately; returns `{op_id}`.                                                                                                           |
| `GET`    | `/api/schedules/queue?limit=50`            | Recent scheduler-driven rows from history.                                                                                                     |

## Seeded defaults

Run once on first boot when the `schedules` table is empty:

| Name                          | Kind             | Interval  | Enabled |
| ----------------------------- | ---------------- | --------- | ------- |
| `Refresh fleet cache`         | `gather_refresh` | 900 s     | yes     |
| `Prune <first-known-node>`    | `prune_node`     | 86400 s   | **no**  |

Destructive kinds (`prune_node`) default disabled — an admin must toggle enabled to use them.
Seeding is skipped entirely if no nodes are visible yet at lifespan-start (brand-new install
without Portainer configured).

## Kinds — extending

Each entry in `SCHEDULE_KINDS` (declared near the bottom of `logic/schedules.py`) is an async
callable that returns `(op_id, awaitable_done)`. The awaitable resolves to
`(duration_seconds, "success" | "error")` so the waiter stamps the schedule row with the real
outcome.

Adding a new kind: follow the shape of `_run_prune_node` (for `ops.py`-backed handlers) or
`_run_gather_refresh` (for pure async functions with no Operation wrapper). Register in the
`SCHEDULE_KINDS` dict. Add a UI option if admins should see it in the create-schedule
dropdown.

### Current kinds

| Kind                       | Params                       | Behaviour                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                         |
| -------------------------- | ---------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `prune_node`               | `{"hostname": str}`          | Runs `ops.do_prune_node` on one node.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                             |
| `prune_all_nodes`          | `{}`                         | Fans out `ops.do_prune_node` to every hostname in `gather._cache.nodes_info` at fire time. One schedule row → N child ops (all `actor='scheduler'`).                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                              |
| `gather_refresh`           | `{}`                         | Runs `logic.gather.gather()`.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                     |
| `backup`                   | `{}`                         | Runs `logic.backups.create_backup()` in the default executor. Synthetic op_id (no `ops.py` Operation); writes a direct history row. Use this with `run_at_hhmm` for "nightly backup at 01:00" style schedules.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                    |
| `config_backup`            | `{}`                         | Runs `logic.config_export.save_snapshot_to_disk()` — writes a Settings-as-Code JSON snapshot (settings KV + schedules + ai_memory; secrets redacted to `__OMITTED__`) to `/app/data/config_backups/config_<ts>.json`. Distinct from `backup` (which is the full SQLite zip). Operators commit these snapshots to a private git repo for change tracking; restore from Admin → Config backup. Retention via `tuning_config_backup_retention_count` (default 30 = ~one month at daily cadence; 0 = unlimited). Synthetic op_id; writes a direct history row.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                        |
| `asset_inventory_refresh`  | `{}`                         | Reloads the `<asset-api-host>/admin/api` asset cache via `logic.asset_inventory.refresh_cache()`. Synthetic op_id; writes a direct history row.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                   |
| `prune_logs`               | `{"days": int?}`             | Sweeps `/app/data/logs/omnigrid-YYYY-MM-DD.log` files older than `params.days` (clamped to `TUNABLES["tuning_log_retention_days"]` `[1, 365]` range; falls back to the live tunable when omitted). Synthetic op_id; writes a direct history row. Same operation the lifespan-managed pruner runs hourly — schedule this for ad-hoc / one-shot cleanups. `target_name` records the resolved `days` so audits show whether the param-override or the tuning fallback fired.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                         |
| `prune_notifications`      | `{"days": int?}`             | Sweeps rows from the `notifications` table older than `params.days` (clamped to `TUNABLES["tuning_notification_retention_days"]` `[1, 3650]` range; falls back to the live tunable when omitted). Synthetic op_id; writes a direct history row. Operators usually want a longer trail than persistent logs (default 90 d) so quarterly review of "what fired" stays available without exporting to an external store. `target_name` records the resolved `days` value.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                            |
| `prune_config_backups`     | `{}`                         | Sweeps older config-backup snapshots from `/app/data/config_backups/` down to `tuning_config_backup_retention_count`. Distinct from the `config_backup` kind — which CREATES a snapshot and runs retention as a side-effect: this kind ONLY prunes, so the operator can split "snapshot daily" from "retention sweep weekly" if they want tighter retention enforcement than the snapshot cadence. Idempotent — if nothing exceeds the retention count, removes 0 files. Retention=0 disables (matches `prune_logs` semantics; a 0-retention run writes a no-op history row so the operator can audit the schedule fired without surprising mass deletion). Synthetic op_id; writes a direct history row. `target_name` shape `"<N> config backup(s) (keep=<retention>)"` so audits show what landed.                                                                                                                                                                                                                                                             |
| `swarm_agent_health`       | `{}`                         | Watches the live `_agent_health` map (populated by every `gather_stats` cycle). When a node's Portainer agent has been failing per-task `/containers/{cid}/stats` calls past `tuning_swarm_agent_unhealthy_threshold` consecutive cycles, fires the configured action — either `notify` (default — Apprise + in-app event `swarm_agent_unhealthy`, transition-only so a single incident emits one alert and one matching `swarm_agent_recovered`) or `restart` (bumps the agent service's `TaskTemplate.ForceUpdate` via `_do_swarm_agent_restart`). Restart action observes `tuning_swarm_autoheal_cooldown_minutes` (default 30 min, persisted across container restarts via `swarm_autoheal_last_restart_ts` setting) so a thrashing agent can't pin the manager in a restart loop. Bootstrap helper `bootstrap_swarm_agent_health_schedule` auto-creates one default 5-minute schedule on first boot when Portainer is configured AND the deploy hasn't opted out via `swarm_autoheal_bootstrap_enabled=false`. Synthetic op_id; writes a direct history row. |
| `port_scan_refresh`        | `{}`                         | Periodically re-scans port-scan-enabled hosts. Each fire picks oldest-scanned-first up to `tuning_port_scan_schedule_max_hosts_per_tick` (default 5), skips hosts whose last scan is younger than `tuning_port_scan_schedule_min_age_seconds` (default 1800), runs `tuning_port_scan_schedule_per_host_concurrency` scans in parallel within the tick (default 1, sequential), and routes each scan through the SAME `_run_port_scan_async` helper the on-demand drawer button uses — same persistence, same SSE, same `port_scan_new_port` notify path. Skips disabled / no-address / paused hosts up-front. Synthetic op_id; writes one aggregate `op_type=port_scan_refresh` history row PLUS one `op_type=port_scan` row per host scanned. Skip-if-running gate prevents overlap on frequent ticks. Honours the `port_scan_enabled` master toggle (off → no-op success).                                                                                                                                                                                      |

Examples of likely next kinds (not yet implemented):

- `update_stack_tag` — `params={"stack_id": int, "tag": str}`.
- `restart_service` — `params={"service_id": str}`.

## Cadence modes: interval vs fixed clock-time

`CADENCE_MODES = ("interval", "daily", "weekly", "monthly")` (see `logic/schedules.py:55`).
Each schedule has both `interval_seconds` (>= 60) and optional calendar-cadence fields
(`run_at_hhmm`, `day_of_month`, `days_of_week`). Only one mode is active at a time; the API
layer validates the combination.

| Mode       | How `next_run_at` is computed                                                                                                                                                                                     |
| ---------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `interval` | `(last_run_at or created_at) + interval_seconds`. Fires every `interval_seconds` since last fire. Original behaviour.                                                                                             |
| `daily`    | `_next_fixed_time_run(hh, mm, last_run)`. Fires once per day at the configured clock time; `interval_seconds` is ignored (still persisted so flipping back to interval mode doesn't require re-entering a value). |
| `weekly`   | `_next_weekly_run(hh, mm, days_of_week, last_run)`. Fires at `run_at_hhmm` on the specified weekdays (0–6).                                                                                                       |
| `monthly`  | `_next_monthly_run(hh, mm, day_of_month, last_run)`. Fires at `run_at_hhmm` on the given day of each month.                                                                                                       |

The tick loop is uniform — `_row_to_dict` computes `next_run_at` based on whichever mode is
active, and "due" is always `next_run_at <= now`.

### Legacy rows — cadence_mode inference

Pre-`cadence_mode` legacy rows land in `_row_to_dict` with `cadence_mode` NULL. The code
(`logic/schedules.py:295-298`) infers the mode from the other columns:

- `run_at_hhmm` set → `daily`.
- otherwise → `interval`.

Re-saving the schedule in the UI normalises the DB row (writes the explicit `cadence_mode`).
Weekly / monthly rows created via the UI always include the explicit mode.

Field-confirmed: a `Nightly backup` row drifting with `cadence_mode=''` — firing via interval
inference rather than at the 01:00 anchor — needed one re-save from the UI to normalise. If a
daily schedule isn't firing at the right time, check `cadence_mode` in the DB row first; a blank
value means the legacy inference path is in effect and a one-click re-save normalises it.

### Edge cases

- **Process restart**: a daily schedule whose anchor passed while the process was down will
  fire on the first tick after startup (`last_run_at < today's anchor` and
  `now >= today's anchor` → due). After firing, `next_run_at` jumps to tomorrow's anchor.
- **DST transitions**: `HH:MM` is interpreted against the scheduler timezone. When
  `scheduler_timezone` setting is set (e.g. `Africa/Cairo`), anchor calculations route through
  `zoneinfo.ZoneInfo` via `logic.schedules._scheduler_tz()`; blank uses container-local time.
  Invalid IANA names are rejected at the `POST /api/settings` layer. `docker-compose.yml` also
  sets `TZ=Africa/Cairo` and bind-mounts `/etc/localtime` so libc paths agree, but the DB
  setting is authoritative for scheduler math.
- **Clock skew on the host**: if the wall clock jumps forward past the anchor, the schedule
  fires when it next ticks. Clock jumps backward: the schedule will re-fire next time the
  anchor is crossed (because `last_run_at > anchor` makes it "not due today").

## Safety properties

- **Rate clamp.** `interval_seconds` is validated `>= 60` at the API layer. Prevents hot loops
  if an admin typos a zero.
- **Persistently-broken schedules.** If `fire_schedule()` raises (bad params, unknown kind), the
  tick loop still stamps `last_run_at` + an error status so the schedule doesn't retry every
  tick forever.
- **Skip-if-running.** If the previous fire is still in flight (`last_op_id` set but
  `last_duration` NULL, or the op is live in `ops.ops[id]` with `status='running'`), the tick
  skips the schedule entirely and tries again next minute. Prevents overlapping prune_nodes
  against the same daemon.
- **Waiter timeout.** 1800 s. If an op runs longer than that, the schedule row won't get its
  `last_duration` / `last_status` updated — not a correctness bug, just a "stale last_run"
  display issue for very long-running ops. Re-fire won't happen because of skip-if-running.
- **Stuck-run self-heal.** A schedule with `last_op_id` set and `last_duration` NULL whose
  fire timestamp is older than `tuning_schedule_stuck_run_threshold_seconds` (default 3600 s
  = 1 hour) is treated as wedged — the next tick stamps `last_duration=0, last_status='error'`
  and lets the schedule re-fire. Closes the "container restarted mid-run, waiter died, schedule
  permanently skipped until manual intervention" failure mode. Visible in Admin → Logs as
  `[schedules] wedged-run self-heal: <name> (last_op_id=<id> age=<s>s)`.
- **No concurrent schedules-of-the-same-kind guard.** Two different `prune_node` schedules
  targeting different hostnames will still run in parallel (intentional — separate nodes, no
  contention).
- **Restart behaviour.** `scheduler_loop()` sleeps `TICK_INTERVAL_SECONDS` (60 s) BEFORE its
  first pass. A schedule that was due at process-restart time will fire 60 s after boot, not
  immediately.

### Gotcha — stale `last_status` can lie

`record_run(conn, schedule_id, op_id, duration=None, status=None)` at
`logic/schedules.py:555-583` is called twice per fire:

1. Immediately after kicking the op off, with `duration=None, status=None`, so the fire time
   moves forward even if the op hangs.
2. By the waiter coroutine when the op completes, with the real `duration` + `status`.

Passing `None` for either field means "don't touch it". That means if the waiter never
resolves (op crashed mid-flight, process restarted, waiter timed out), the row still shows the
**previous** run's `last_status` — so a schedule that succeeded yesterday can display
"success" in the UI even though today's fire is dead. If this matters, cross-reference against
`last_op_id` in `/api/ops` or `/api/history` to confirm the latest fire actually completed.

## Upgrade paths (tracked, not built)

- **Cron expressions.** Add `cron_expr TEXT` column alongside `interval_seconds` (either/or per
  schedule). Add `croniter` dep. Update the tick loop's due check + the UI's "interval" field
  to accept a cron string OR a seconds-interval.
- **Run history per schedule.** Today the Queue tab shows the global scheduler history. Add a
  "per-schedule history" drill-down by filtering `history.target_id` on the schedule's name or
  id.
- **Per-schedule notifications.** A flag that routes Apprise only when this schedule's status
  changes (success → error etc.) instead of every run. Reduces noise for frequent schedules.

## Verification

```bash
# Seed state
curl -sS -b /tmp/c https://omnigrid.<host>/api/schedules | jq

# Create a new one (note the CSRF header — middleware enforces it)
CSRF=$(awk '/og_csrf/ {print $7}' /tmp/c)
curl -sS -b /tmp/c -H "X-CSRF-Token: $CSRF" -H "Content-Type: application/json" \
  -X POST https://omnigrid.<host>/api/schedules \
  -d '{"name":"Hourly refresh","kind":"gather_refresh","params":{},"interval_seconds":3600,"enabled":true}'

# Run now
curl -sS -b /tmp/c -H "X-CSRF-Token: $CSRF" -X POST \
  https://omnigrid.<host>/api/schedules/<id>/run

# Queue
curl -sS -b /tmp/c https://omnigrid.<host>/api/schedules/queue?limit=10 | jq

# Toggle enabled
curl -sS -b /tmp/c -H "X-CSRF-Token: $CSRF" -H "Content-Type: application/json" \
  -X PATCH https://omnigrid.<host>/api/schedules/<id> \
  -d '{"enabled":false}'
```

Browser: Admin → Schedules. Click Run now on the seeded "Refresh fleet cache" — ops panel
flashes, Last execution / Last duration / Last status update within ~2 s.

## Example — prune every node every 4 hours

Use the `prune_all_nodes` kind — one schedule row fans out to every hostname in the current
gather snapshot, so new nodes joining the swarm get picked up automatically on the next fire.

```bash
# Create the schedule (destructive → opt-in via enabled:true)
CSRF=$(awk '/og_csrf/ {print $7}' /tmp/c)
curl -sS -b /tmp/c -H "X-CSRF-Token: $CSRF" -H "Content-Type: application/json" \
  -X POST https://omnigrid.<host>/api/schedules \
  -d '{"name":"Prune all nodes (4h)","kind":"prune_all_nodes","params":{},"interval_seconds":14400,"enabled":true}'
```

Browser equivalent: Admin → Schedules → New schedule, kind = Prune all nodes, interval =
14400 s, Enabled.

Notes on the fan-out:

- Each fire spawns one `prune_node` child op per visible hostname. Every child writes its own
  history row (`actor='scheduler'`), so the Queue tab shows N rows per fire — one per node.
- The schedule's `last_duration` = longest child's wall time (children run in parallel),
  `last_status` = `'success'` iff every child succeeded, otherwise `'error'`.
- Skip-if-running still works: until the waiter resolves every child, `last_duration` is NULL
  and the tick loop will not re-fire. So a slow node can't cause overlapping prune passes.
- Apprise fires per child op (N notifications per scheduled prune). If that's noisy, silence
  prune_node in your Apprise rules or drop back to one `prune_node` schedule on the busiest
  node only.
- Empty node list raises at fire time — stamps the schedule `'error'` rather than silently
  `'success'`, so a broken Portainer connection surfaces in `last_status`.

**Fallback**: the legacy per-host pattern — one `prune_node` schedule per hostname — is still
supported. Use it when you need per-node intervals (e.g. daily on one node, hourly on another)
or per-node disable.

## Example — nightly backup at 01:00

Uses the `backup` kind plus the `run_at_hhmm` daily anchor. Interval is irrelevant when the
anchor is set but must still be valid (`>= 60 s`) so the form validates; the backend stores it
as a fallback if the anchor is later cleared.

```bash
CSRF=$(awk '/og_csrf/ {print $7}' /tmp/c)
curl -sS -b /tmp/c -H "X-CSRF-Token: $CSRF" -H "Content-Type: application/json" \
  -X POST https://omnigrid.<host>/api/schedules \
  -d '{"name":"Nightly backup","kind":"backup","params":{},"interval_seconds":86400,"cadence_mode":"daily","run_at_hhmm":"01:00","enabled":true}'
```

Browser equivalent: Admin → Schedules → New schedule, kind = Backup database, Cadence = Daily,
Run at = 01:00, Enabled. The Interval field greys out once the anchor is set (daily mode takes
over). The Scheduled table shows "Daily @ 01:00" in the Interval column for these rows.

To flip back to interval mode later (e.g. "every 6 hours"), clear the Run-at field in the edit
modal and set `interval_seconds` to 21600.
