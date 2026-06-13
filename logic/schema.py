"""SQLite schema bootstrap — the init_db() boot orchestrator.

Extracted from main.py (where it was the ~1000-line tail of the module).
init_db() creates every core table idempotently (CREATE TABLE IF NOT
EXISTS + an additive ALTER list whose per-statement OperationalError is
swallowed), delegates to the module-owned schema hooks
(``auth.init_auth_schema`` / ``schedules.init_schedules_schema``), runs the
numbered migrations (``logic/migrations.py``), then seeds the built-in
service catalog + the canonical AI-memory baseline. Called once from
main.py's boot wrapper, before any background worker starts.

Lives in logic/ alongside db.py + migrations.py. Imports only sqlite3 +
time + db_conn + the auth/schedules modules — no dependency on main, so
there's no import cycle (main imports this; this never imports main).
"""
import sqlite3
import time

from logic.db import db_conn
from logic import auth, schedules

# Canonical baseline AI memories. Seeded on every boot via init_db; the
# duplicate-text guard there makes the operation idempotent. Source
# 'system' (with actor 'bootstrap') distinguishes these from operator-
# added or AI-emitted memories in the Admin → AI → Memory pane.
# Each entry is a single string — the AI's palette user-prompt prepends
# the full set so every conversation starts with this baseline knowledge.
_AI_MEMORY_SEEDS: tuple[str, ...] = (
    # Swarm task-ID suffix gotcha — the AI tried to drill into a service-
    # named container (`tracearr_tracearr`) with docker_container_du and
    # got 'No such container'. The actual container name carries a
    # dynamic task-ID suffix (`tracearr_tracearr.1.glr5r6sv31fcz8e0p019m1sbm`)
    # that has to be discovered via docker_ps_with_sizes first.
    "When dealing with Docker Swarm containers, the running container name carries a "
    "DYNAMIC TASK-ID SUFFIX (e.g. `tracearr_tracearr.1.glr5r6sv31fcz8e0p019m1sbm`), "
    "NOT the bare service name (`tracearr_tracearr`). For docker_container_du and any "
    "`docker exec`-style operation, ALWAYS resolve the full container name first via "
    "`ssh_diag preset=docker_ps_with_sizes` (or a `docker ps` lookup) and use the EXACT "
    "name from that output. Single-replica compose containers use `<stack>_<service>_1` "
    "shape (no task ID); standalone containers carry whatever name was passed to "
    "`docker run --name`.",
)


# noinspection DuplicatedCode
def init_db():
    """Boot orchestrator — create all SQLite tables idempotently and apply pending migrations."""
    with db_conn() as c:
        # Wrap the whole schema-create script in an explicit transaction
        # so a power loss / hard kill mid-init can't leave a half-applied
        # schema. Every statement in here is idempotent (CREATE IF NOT
        # EXISTS / ALTER ... except OperationalError) so the worst case
        # was always recoverable, but rolling-back an interrupted boot
        # is cleaner than racing with `IF NOT EXISTS` on the next start.
        # in the code review.
        c.executescript("""
        BEGIN;
        -- target_kind taxonomy column added in migration 3 (separate
        -- from op_type which names the action). Values used today:
        -- 'op' (container / stack / service write op), 'schedule'
        -- (scheduler-fired runs), 'ssh' (admin SSH console), 'hosts'
        -- (curated-config bulk actions), 'auth' (password / token
        -- changes), 'system' (catch-all). Index supports the Admin →
        -- History bucket-by-kind filter.
        CREATE TABLE IF NOT EXISTS history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts REAL NOT NULL, op_type TEXT NOT NULL,
            target_kind TEXT,
            target_name TEXT, target_id TEXT,
            status TEXT NOT NULL, duration REAL,
            events TEXT, error TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_history_ts ON history(ts DESC);
        CREATE INDEX IF NOT EXISTS idx_history_op_type ON history(op_type);
        CREATE INDEX IF NOT EXISTS idx_history_target_name ON history(target_name);
        CREATE INDEX IF NOT EXISTS idx_history_status ON history(status);
        -- Composite (actor, ts DESC): serves both the actor-filtered COUNT(*)
        -- AND the `WHERE actor=? ORDER BY ts DESC` page in the schedule-queue /
        -- audit views. Without it, COUNT(*) WHERE actor=? full-scans history
        -- (a ~290ms slow query on a large audit trail).
        CREATE INDEX IF NOT EXISTS idx_history_actor_ts ON history(actor, ts DESC);
        -- idx_history_target_kind is created by migration — keep it
        -- there so legacy DBs (where the table exists without the
        -- column at init_db time) don't fail the executescript before
        -- migrations get a chance to ADD COLUMN.

        CREATE TABLE IF NOT EXISTS ignores (
            pattern TEXT PRIMARY KEY, kind TEXT NOT NULL,
            reason TEXT, created REAL NOT NULL
        );

        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY, value TEXT
        );

        -- Per-item CPU/memory time-series for 24h sparklines + drift graphs.
        -- Written by the lifespan-managed stats sampler every
        -- STATS_SAMPLE_INTERVAL seconds; pruned to STATS_HISTORY_DAYS.
        CREATE TABLE IF NOT EXISTS stats_samples (
            ts REAL NOT NULL,
            item_id TEXT NOT NULL,
            cpu REAL, mem_used REAL, mem_limit REAL,
            size_root REAL
        );
        CREATE INDEX IF NOT EXISTS idx_stats_samples_item_ts
            ON stats_samples(item_id, ts DESC);
        CREATE INDEX IF NOT EXISTS idx_stats_samples_ts
            ON stats_samples(ts);

        -- Database file-size history for the Stats -> Database growth
        -- projection. One row per DB_SIZE_SAMPLE_INTERVAL (default daily),
        -- written by the lifespan stats sampler; pruned to
        -- DB_SIZE_HISTORY_DAYS (default 120, >= the 30-day regression
        -- window the projection fits over). `bytes` is the total of the
        -- main SQLite file + its -wal / -shm siblings. Grounds the 90-day
        -- forward projection in real measured growth instead of a synthetic
        -- per-day constant, and feeds the actual (past) portion of the
        -- two-tone chart (-30..0 actual, 0..+90 projection).
        CREATE TABLE IF NOT EXISTS db_size_samples (
            ts INTEGER NOT NULL,
            bytes INTEGER NOT NULL,
            PRIMARY KEY (ts)
        );
        CREATE INDEX IF NOT EXISTS idx_db_size_samples_ts
            ON db_size_samples(ts);

        -- Net-I/O fallback series per curated host. Populated by
        -- logic/host_net_sampler.py when node-exporter is the only
        -- network-counter source (Beszel agents with NICS= unset emit
        -- all-zero nr/ns, which is what `isNetSeriesFlat` detects on
        -- the frontend). Rates are pre-computed across consecutive NE
        -- probes; counter jumps / rollovers are SKIPPED rather than
        -- recorded as synthesized zeros — see
        -- logic.host_net_sampler._sanity_bounds().
        CREATE TABLE IF NOT EXISTS host_net_samples (
            ts INTEGER NOT NULL,
            host_id TEXT NOT NULL,
            rx_bytes_per_s REAL NOT NULL,
            tx_bytes_per_s REAL NOT NULL,
            PRIMARY KEY (host_id, ts)
        );
        CREATE INDEX IF NOT EXISTS idx_host_net_samples_host_ts
            ON host_net_samples(host_id, ts DESC);
        -- Plain (ts) index for the hourly prune's `WHERE ts < ?` predicate.
        -- The composite above can't seek from the leading column when
        -- only `ts` is filtered, so without this the prune degrades to
        -- a full scan. Matches the canonical pattern (see stats_samples).
        CREATE INDEX IF NOT EXISTS idx_host_net_samples_ts
            ON host_net_samples(ts);

        -- Per-host historical CPU/memory/disk/network samples for
        -- node-exporter-only hosts (no Beszel agent). Populated by
        -- logic/host_metrics_sampler.py at STATS_SAMPLE_INTERVAL_SECONDS
        -- cadence; pruned to STATS_HISTORY_DAYS. Sibling table to
        -- host_net_samples — same skip-don't-synthesize discipline for
        -- the net rate columns. CPU/mem/disk are point-in-time gauges
        -- and stored verbatim (NULL when the probe didn't return a
        -- meaningful value).
        CREATE TABLE IF NOT EXISTS host_metrics_samples (
            ts             INTEGER NOT NULL,
            host_id        TEXT    NOT NULL,
            cpu_percent    REAL,
            mem_used       INTEGER,
            mem_total      INTEGER,
            disk_used      INTEGER,
            disk_total     INTEGER,
            net_rx_bps     REAL,
            net_tx_bps     REAL,
            disk_read_bps  REAL,
            disk_write_bps REAL,
            PRIMARY KEY (ts, host_id)
        );
        CREATE INDEX IF NOT EXISTS idx_host_metrics_samples_host_ts
            ON host_metrics_samples(host_id, ts DESC);
        -- Plain (ts) index for the hourly prune predicate; without it
        -- the composite above can't seek from ts alone and the prune
        -- degrades to a full scan (see stats_samples for the pattern).
        CREATE INDEX IF NOT EXISTS idx_host_metrics_samples_ts
            ON host_metrics_samples(ts);

        -- Pulse-only history. Mirrors host_metrics_samples shape so
        -- the SPA's chart helpers + inline sparkline data-source
        -- ladder treat Pulse-only hosts identically to NE-only hosts.
        -- Separate table so a host running BOTH Pulse and NE doesn't
        -- get double-writes from two samplers — each table has one
        -- writer, one consumer. Pulse doesn't expose disk read/write
        -- counters so those columns aren't on this table; the
        -- history_series envelope returns 0 for those keys instead.
        CREATE TABLE IF NOT EXISTS host_pulse_samples (
            ts             INTEGER NOT NULL,
            host_id        TEXT    NOT NULL,
            cpu_percent    REAL,
            mem_total      INTEGER,
            mem_used       INTEGER,
            disk_total     INTEGER,
            disk_used      INTEGER,
            net_rx_bps     REAL,
            net_tx_bps     REAL,
            PRIMARY KEY (ts, host_id)
        );
        CREATE INDEX IF NOT EXISTS idx_host_pulse_samples_host_ts
            ON host_pulse_samples(host_id, ts DESC);
        -- Plain (ts) index for the hourly prune predicate; without it
        -- the composite above can't seek from ts alone and the prune
        -- degrades to a full scan (see stats_samples for the pattern).
        CREATE INDEX IF NOT EXISTS idx_host_pulse_samples_ts
            ON host_pulse_samples(ts);

        -- Beszel-only history. Same shape as host_pulse_samples; the
        -- separate table is the canonical "every provider has its own
        -- local store" pattern — pre-fix Beszel was the read-through-
        -- only outlier (every chart query hit the PocketBase hub
        -- directly), so when the hub's `1m` aggregation tier aged out
        -- (~1h retention) the data was gone and OmniGrid had no local
        -- cache to fall back on. Drove visible chart "cuts" at the
        -- head of any window > 1h. With this table + the lifespan
        -- `host_beszel_sampler` writing one row per host per tick,
        -- Beszel data lives in OmniGrid's own retention window
        -- (default 7d) regardless of hub-side retention.
        CREATE TABLE IF NOT EXISTS host_beszel_samples (
            ts             INTEGER NOT NULL,
            host_id        TEXT    NOT NULL,
            cpu_percent    REAL,
            mem_total      INTEGER,
            mem_used       INTEGER,
            disk_total     INTEGER,
            disk_used      INTEGER,
            net_rx_bps     REAL,
            net_tx_bps     REAL,
            -- Beszel chart-extras: per-tick captures of fields beyond
            -- the basic CPU/Mem/Disk/Net set the other samplers
            -- carry. Beszel agents expose load avg / swap / temps /
            -- GPUs out of the box; preserving them in the local table
            -- means the host drawer's Load / Swap / Temperature /
            -- GPU chart cards keep working when the hub ages out
            -- (same chart-cut class the basic samples columns
            -- prevent for CPU/Mem/Disk/Net). Variable-shape payloads
            -- (temperatures dict, GPUs list) ride as JSON TEXT —
            -- mirrors the SNMP sampler's `cpu_per_core` blob pattern;
            -- callers parse on read.
            load_1m            REAL,
            load_5m            REAL,
            load_15m           REAL,
            swap_percent       REAL,
            swap_used          REAL,
            bandwidth          REAL,
            containers         INTEGER,
            temperatures_json  TEXT,
            gpus_json          TEXT,
            PRIMARY KEY (ts, host_id)
        );
        CREATE INDEX IF NOT EXISTS idx_host_beszel_samples_host_ts
            ON host_beszel_samples(host_id, ts DESC);
        -- Plain (ts) index for the hourly prune predicate; without it
        -- the composite above can't seek from ts alone and the prune
        -- degrades to a full scan (see stats_samples for the pattern).
        CREATE INDEX IF NOT EXISTS idx_host_beszel_samples_ts
            ON host_beszel_samples(ts);

        -- Beszel per-host systemd unit table — one row per
        -- (host_id, service_name) tuple, snapshot of the latest
        -- observed state. Sampler UPSERTs on every tick so an
        -- operator can answer "which units are currently failed
        -- on web01?" without round-tripping to the Beszel hub. The
        -- per-row `last_seen_ts` lets the SPA detect units that
        -- have aged out (Beszel agent stopped tracking the unit).
        -- `last_change_ts` lets the drawer surface "failed since
        -- 2h ago" without scanning a transition log.
        CREATE TABLE IF NOT EXISTS host_beszel_services (
            host_id         TEXT    NOT NULL,
            service_name    TEXT    NOT NULL,
            state           INTEGER,
            sub_state       INTEGER,
            last_seen_ts    INTEGER NOT NULL,
            last_change_ts  INTEGER NOT NULL,
            PRIMARY KEY (host_id, service_name)
        );
        CREATE INDEX IF NOT EXISTS idx_host_beszel_services_host_state
            ON host_beszel_services(host_id, state);
        -- Plain (last_seen_ts) index so the chunked retention prune
        -- (DELETE ... WHERE last_seen_ts < ?) seeks instead of scanning.
        CREATE INDEX IF NOT EXISTS idx_host_beszel_services_last_seen_ts
            ON host_beszel_services(last_seen_ts);

        -- Webmin-only history. Same shape as host_pulse_samples;
        -- separate table so a host with both Webmin AND NE doesn't
        -- get double-writes from two samplers. Webmin is per-host
        -- (Miniserv per target box, like NE); the sampler fans out
        -- across curated rows with a webmin_url set.
        CREATE TABLE IF NOT EXISTS host_webmin_samples (
            ts             INTEGER NOT NULL,
            host_id        TEXT    NOT NULL,
            cpu_percent    REAL,
            mem_total      INTEGER,
            mem_used       INTEGER,
            disk_total     INTEGER,
            disk_used      INTEGER,
            net_rx_bps     REAL,
            net_tx_bps     REAL,
            PRIMARY KEY (ts, host_id)
        );
        CREATE INDEX IF NOT EXISTS idx_host_webmin_samples_host_ts
            ON host_webmin_samples(host_id, ts DESC);
        -- Plain (ts) index for the hourly prune predicate; without it
        -- the composite above can't seek from ts alone and the prune
        -- degrades to a full scan (see stats_samples for the pattern).
        CREATE INDEX IF NOT EXISTS idx_host_webmin_samples_ts
            ON host_webmin_samples(ts);

        -- SNMP-specific time-series. Separate from
        -- host_metrics_samples because: (a) SNMP exposes per-core CPU
        -- + buffers/cached memory that the unified `host_metrics_samples`
        -- schema doesn't carry; (b) the rate-derivation contract for
        -- net/disk doesn't apply (SNMP gives gauges, not counters
        -- here); (c) keeps SNMP enrichment additive — operators with
        -- only Beszel/NE pay zero query cost. JSON cpu_per_core blob
        -- is fine because the row count is one per host per tick
        -- and we never query INTO the JSON; bulk reads return the
        -- raw text + frontend parses. Skip-don't-synthesize discipline
        -- still applies — the sampler does NOT insert when memTotal is
        -- 0 / undefined (would mask "host disappeared" as flat zeros).
        CREATE TABLE IF NOT EXISTS host_snmp_samples (
            ts            INTEGER NOT NULL,
            host_id       TEXT    NOT NULL,
            cpu_per_core  TEXT,
            cpu_used_pct  REAL,
            load_1m       REAL,
            load_5m       REAL,
            load_15m      REAL,
            mem_total     INTEGER,
            mem_used      INTEGER,
            mem_buffers   INTEGER,
            mem_cached    INTEGER,
            mem_free      INTEGER,
            disk_total    INTEGER,
            disk_used     INTEGER,
            PRIMARY KEY (ts, host_id)
        );
        CREATE INDEX IF NOT EXISTS idx_host_snmp_samples_host_ts
            ON host_snmp_samples(host_id, ts DESC);
        -- Plain (ts) index for the hourly prune predicate; without it
        -- the composite above can't seek from ts alone and the prune
        -- degrades to a full scan (see stats_samples for the pattern).
        CREATE INDEX IF NOT EXISTS idx_host_snmp_samples_ts
            ON host_snmp_samples(ts);

        -- per-interface SNMP counter samples for switch / router
        -- per-port throughput charts. One row per (ts, host_id, ifname);
        -- counters are cumulative IF-MIB ifHCInOctets / ifHCOutOctets
        -- (with 32-bit fallback inside the extractor). Chart layer
        -- computes per-pair deltas → bps and applies skip-don't-
        -- synthesize on out-of-bounds (counter wrap, reboot, gap).
        CREATE TABLE IF NOT EXISTS host_snmp_iface_samples (
            ts        INTEGER NOT NULL,
            host_id   TEXT    NOT NULL,
            ifname    TEXT    NOT NULL,
            in_bytes  INTEGER,
            out_bytes INTEGER,
            PRIMARY KEY (ts, host_id, ifname)
        );
        CREATE INDEX IF NOT EXISTS idx_host_snmp_iface_samples_host_ts
            ON host_snmp_iface_samples(host_id, ts DESC);
        -- Plain (ts) index for the hourly prune predicate; without it
        -- the composite above can't seek from ts alone and the prune
        -- degrades to a full scan (see stats_samples for the pattern).
        CREATE INDEX IF NOT EXISTS idx_host_snmp_iface_samples_ts
            ON host_snmp_iface_samples(ts);

        -- Per-temperature-probe history for Dell server hosts. One row per (ts, host_id, probe_idx); the
        -- temperatureProbeTable typically reports 4-12 probes per
        -- server (Inlet / Exhaust / CPU1 / CPU2 / chipset / etc.) and
        -- the chart card renders one polyline per probe. probe_name
        -- denormalised onto every row so the chart can label the
        -- legend without joining back to the latest per-probe row.
        -- value_c is degrees Celsius (already converted from MIB's
        -- deci-degC at extraction time).
        CREATE TABLE IF NOT EXISTS host_snmp_temp_samples (
            ts         INTEGER NOT NULL,
            host_id    TEXT    NOT NULL,
            probe_idx  TEXT    NOT NULL,
            probe_name TEXT,
            value_c    REAL,
            PRIMARY KEY (ts, host_id, probe_idx)
        );
        CREATE INDEX IF NOT EXISTS idx_host_snmp_temp_samples_host_probe_ts
            ON host_snmp_temp_samples(host_id, probe_idx, ts DESC);
        -- Plain (ts) index for the hourly prune predicate.
        CREATE INDEX IF NOT EXISTS idx_host_snmp_temp_samples_ts
            ON host_snmp_temp_samples(ts);

        -- Per-host rolling baseline (median + IQR) for drift detection.
        -- One row per (host_id, metric) — UPSERT on every recompute.
        -- Metric is one of: cpu_pct / mem_pct / disk_pct / ping_rtt_ms.
        -- Computed hourly by `host_baseline_sampler`
        -- from the matching time-series table (host_metrics_samples for
        -- cpu/mem/disk; ping_samples for rtt). Drives the ▲/▼/━ drift
        -- indicator on every Hosts row.
        CREATE TABLE IF NOT EXISTS host_baselines (
            host_id     TEXT NOT NULL,
            metric      TEXT NOT NULL,
            median      REAL,
            iqr         REAL,
            sample_count INTEGER,
            computed_ts INTEGER NOT NULL,
            PRIMARY KEY (host_id, metric)
        );
        CREATE INDEX IF NOT EXISTS idx_host_baselines_computed_ts
            ON host_baselines(computed_ts DESC);

        -- Append-only transition log for the host pause/resume
        -- lifecycle. Pre-fix the timeline endpoint synthesised
        -- `provider_paused` / `provider_recovered` events from
        -- the CURRENT `host_failure_state` snapshot — meaning a
        -- host that had paused → resumed → paused → resumed showed
        -- only the LATEST state, not the history. This table
        -- captures every transition so the timeline reflects the
        -- true incident sequence. Pruned by the same retention
        -- knob the time-series tables use (`tuning_stats_history_days`).
        --
        -- Schema:
        --   host_id  — BARE host_id (not the prefixed `<provider>:<id>`
        --              form used by host_failure_state rows). Always
        --              the operator-visible identifier so the timeline
        --              filters on host_id IN (...) work.
        --   provider — '' for whole-host events; '<provider>' for
        --              per-(provider, host) events.
        --   kind     — 'paused' | 'recovered' (extensible — future
        --              kinds like 'manual_pause' / 'manual_resume'
        --              can drop in without schema migration).
        --   error    — last error string (only set on 'paused'
        --              events; truncated to 500 chars).
        --   actor    — 'sampler' | 'admin:<username>' | 'scheduler'
        --              so audit trails can distinguish auto vs manual.
        CREATE TABLE IF NOT EXISTS host_failure_events (
            id       INTEGER PRIMARY KEY AUTOINCREMENT,
            ts       REAL NOT NULL,
            host_id  TEXT NOT NULL,
            provider TEXT NOT NULL DEFAULT '',
            kind     TEXT NOT NULL,
            error    TEXT,
            actor    TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_host_failure_events_ts
            ON host_failure_events(ts DESC);
        CREATE INDEX IF NOT EXISTS idx_host_failure_events_host_ts
            ON host_failure_events(host_id, ts DESC);

        -- Last-known per-host nodes_info blob (Beszel / Pulse /
        -- node-exporter / Webmin merged). Written at the end of every
        -- successful gather, read at startup AND on every gather to
        -- fill in missing host_* fields when a provider is down.
        -- Operators see "stale" data instead of empty bars when a
        -- provider goes offline. One row per host (PK = host); the
        -- ``data`` column carries the JSON blob.
        CREATE TABLE IF NOT EXISTS host_snapshots (
            host TEXT PRIMARY KEY,
            ts REAL NOT NULL,
            data TEXT NOT NULL
        );
        -- Cross-restart persistence for the items / stacks / nodes
        -- gather cache. Single-row table — `id=1` always —
        -- carrying a JSON blob with `items` / `stacks` / `nodes` /
        -- `nodes_info` / `ts`. Written at the end of every successful
        -- `_gather()`, read at lifespan startup so the FIRST
        -- `/api/items` after a container restart returns the prior
        -- snapshot instantly while the live gather runs in the
        -- background. Without this, post-restart the in-memory `_cache`
        -- is empty and the first request blocks on the full Portainer
        -- fan-out + image-digest probe (10-30s). Single-row design —
        -- the gather replaces the snapshot wholesale, so stale-ignore
        -- / removed-item cleanup is automatic on the next successful
        -- gather. Cleared by an `INSERT OR REPLACE` on each save.
        CREATE TABLE IF NOT EXISTS items_snapshot (
            id INTEGER PRIMARY KEY,
            ts REAL NOT NULL,
            data TEXT NOT NULL
        );
        -- Permanent-fail tracking. One row per (host, provider) whose
        -- sampler has hit consecutive probe failures. When ``paused`` flips
        -- to 1, the sampler short-circuits subsequent ticks (no probe
        -- attempt, no log spam) until the operator explicitly resumes via
        -- POST /api/hosts/{id}/resume-sampling.
        --
        -- Schema after migration 2 (split_provider_host_pk):
        --   host_id  — bare host identifier (operator-visible).
        --   provider — '' for whole-host pauses (legacy bare-id rows
        --              from /api/hosts/{id}/pause-sampling); '<name>' for
        --              per-(provider, host) pauses driven by
        --              record_provider_outcome.
        -- Composite PK (host_id, provider) replaces the legacy prefixed
        -- "<provider>:<host_id>" key. Reads now use direct equality lookups
        -- instead of full-table-scan WHERE host_id LIKE '%:hid'.
        CREATE TABLE IF NOT EXISTS host_failure_state (
            host_id              TEXT NOT NULL,
            provider             TEXT NOT NULL DEFAULT '',
            first_failure_ts     REAL NOT NULL,
            consecutive_failures INTEGER NOT NULL DEFAULT 0,
            paused               INTEGER NOT NULL DEFAULT 0,
            paused_at            REAL,
            last_error           TEXT,
            last_failure_ts      REAL,
            PRIMARY KEY (host_id, provider)
        );
        -- idx_host_failure_state_provider is created by migration —
        -- on legacy DBs the table exists WITHOUT the `provider` column
        -- at this point in init_db (CREATE TABLE IF NOT EXISTS is a
        -- no-op when the table already exists), so the index creation
        -- has to wait until migration has rebuilt the table.

        -- Per-(provider, host) last-successful-probe timestamp.
        -- Distinct from host_failure_state which only exists during a
        -- failure streak. last_ok lives ALWAYS — every
        -- record_provider_outcome with ok=True UPSERTs here. After
        -- migration, host_id and provider are separate columns with
        -- composite PK. Drives the "Updated Xm ago" subtitle on each
        -- provider chip in the host drawer's Enabled-agents card.
        CREATE TABLE IF NOT EXISTS host_provider_last_ok (
            host_id    TEXT NOT NULL,
            provider   TEXT NOT NULL,
            last_ok_ts INTEGER NOT NULL,
            PRIMARY KEY (host_id, provider)
        );
        -- idx_host_provider_last_ok_provider — same story as above;
        -- migration owns the index creation so legacy DBs don't
        -- fail the executescript before migrations get to run.
        -- Composite (provider, last_ok_ts DESC) speeds up the
        -- "every host's freshness for ONE provider, newest first" read
        -- pattern (chip-strip render across a 200-host fleet, with
        -- the NE sampler also UPSERTing here every tick).
        -- Additive — safe to run on existing deployments;
        -- SQLite no-ops if the index already exists.
        CREATE INDEX IF NOT EXISTS idx_host_provider_last_ok_provider_ts
        ON host_provider_last_ok (provider, last_ok_ts DESC);

        -- Ping reachability time-series. Populated by
        -- logic/ping_sampler.py at tuning_ping_interval_seconds
        -- cadence; pruned to tuning_stats_history_days (reuses the
        -- existing retention knob — no separate ping retention).
        -- ``alive`` is INTEGER 0/1 (SQLite has no native bool). RTT
        -- columns NULL when the probe got no responses.
        CREATE TABLE IF NOT EXISTS ping_samples (
            ts         INTEGER NOT NULL,
            host_id    TEXT    NOT NULL,
            alive      INTEGER NOT NULL,
            rtt_ms     REAL,
            rtt_min_ms REAL,
            rtt_max_ms REAL,
            loss_pct   REAL,
            PRIMARY KEY (ts, host_id)
        );
        CREATE INDEX IF NOT EXISTS idx_ping_samples_host_ts
            ON ping_samples(host_id, ts DESC);
        -- Plain (ts) index for the hourly prune predicate; without it
        -- the composite above can't seek from ts alone and the prune
        -- degrades to a full scan (see stats_samples for the pattern).
        CREATE INDEX IF NOT EXISTS idx_ping_samples_ts
            ON ping_samples(ts);

        -- FlareSolverr per-chip open-session-count history. The FlareSolverr
        -- API exposes only the CURRENT open sessions (sessions.list) with no
        -- historical / request-volume data, so the lifespan
        -- ``flaresolverr_sampler`` records the live count per tick to give the
        -- card a 30-day usage trend. One row per (host_id, service_idx, tick).
        -- ``ready`` is INTEGER 0/1 (was the solver up at sample time).
        CREATE TABLE IF NOT EXISTS flaresolverr_session_samples (
            ts          INTEGER NOT NULL,
            host_id     TEXT    NOT NULL,
            service_idx INTEGER NOT NULL,
            sessions    INTEGER NOT NULL,
            ready       INTEGER NOT NULL,
            PRIMARY KEY (ts, host_id, service_idx)
        );
        CREATE INDEX IF NOT EXISTS idx_flaresolverr_sessions_chip_ts
            ON flaresolverr_session_samples(host_id, service_idx, ts DESC);
        -- Plain (ts) index for the hourly prune predicate (seek from ts alone;
        -- see ping_samples / stats_samples for the same pattern).
        CREATE INDEX IF NOT EXISTS idx_flaresolverr_sessions_ts
            ON flaresolverr_session_samples(ts);

        -- ddns-updater per-chip history. ddns-updater exposes NO JSON API and
        -- no historical data, so the lifespan ``ddns_updater_sampler`` records
        -- each configured chip's current public IP + record totals + failing
        -- count per tick. Diffing consecutive ``public_ip`` values yields a
        -- public-IP-change timeline (the headline); ``fail_count`` drives a
        -- daily-max sparkline. One row per (host_id, service_idx, tick).
        -- ``public_ip`` is TEXT (may be empty when the UI hasn't reported one).
        CREATE TABLE IF NOT EXISTS ddns_samples (
            ts            INTEGER NOT NULL,
            host_id       TEXT    NOT NULL,
            service_idx   INTEGER NOT NULL,
            public_ip     TEXT    NOT NULL DEFAULT '',
            records_total INTEGER NOT NULL DEFAULT 0,
            fail_count    INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (ts, host_id, service_idx)
        );
        CREATE INDEX IF NOT EXISTS idx_ddns_samples_chip_ts
            ON ddns_samples(host_id, service_idx, ts DESC);
        -- Plain (ts) index for the hourly prune predicate (seek from ts alone;
        -- see ping_samples / stats_samples for the same pattern).
        CREATE INDEX IF NOT EXISTS idx_ddns_samples_ts
            ON ddns_samples(ts);

        -- Fing online-device occupancy history. Fing's Local API is current-
        -- state-only, so the lifespan ``fing_sampler`` records each configured
        -- Fing chip's total / online device counts + a new-device count per
        -- tick. The daily-MAX of ``devices_online`` drives the expanded card's
        -- occupancy sparkline ("how many devices are typically on the network");
        -- ``new_devices`` flags days an unknown device first appeared. One row
        -- per (host_id, service_idx, tick).
        CREATE TABLE IF NOT EXISTS fing_samples (
            ts             INTEGER NOT NULL,
            host_id        TEXT    NOT NULL,
            service_idx    INTEGER NOT NULL,
            devices_total  INTEGER NOT NULL DEFAULT 0,
            devices_online INTEGER NOT NULL DEFAULT 0,
            new_devices    INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (ts, host_id, service_idx)
        );
        CREATE INDEX IF NOT EXISTS idx_fing_samples_chip_ts
            ON fing_samples(host_id, service_idx, ts DESC);
        -- Plain (ts) index for the hourly prune predicate (seek from ts alone).
        CREATE INDEX IF NOT EXISTS idx_fing_samples_ts
            ON fing_samples(ts);

        -- AdGuard Home Sync sync-outcome history. The sync API is current-state
        -- only, so this sampler records each configured AGS chip's replica
        -- in-sync count per tick so the card can draw a sync-RELIABILITY trend
        -- (how often every replica was in sync) that survives the tool keeping
        -- no history of its own. ``origin_ok`` flags whether the origin was
        -- reachable on that tick. One row per (host_id, service_idx, tick).
        CREATE TABLE IF NOT EXISTS adguardhome_sync_samples (
            ts             INTEGER NOT NULL,
            host_id        TEXT    NOT NULL,
            service_idx    INTEGER NOT NULL,
            replicas_total INTEGER NOT NULL DEFAULT 0,
            replicas_ok    INTEGER NOT NULL DEFAULT 0,
            origin_ok      INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (ts, host_id, service_idx)
        );
        CREATE INDEX IF NOT EXISTS idx_adguardhome_sync_samples_chip_ts
            ON adguardhome_sync_samples(host_id, service_idx, ts DESC);
        -- Plain (ts) index for the hourly prune predicate (seek from ts alone).
        CREATE INDEX IF NOT EXISTS idx_adguardhome_sync_samples_ts
            ON adguardhome_sync_samples(ts);

        -- Speedtest Tracker long-horizon history. Speedtest Tracker KEEPS its
        -- own results history, but prunes it on the operator's configured
        -- retention schedule — so the lifespan ``speedtest_tracker_sampler``
        -- ingests every result it sees into this table (keyed on the upstream
        -- result's own created_at epoch, so re-ingesting the same result is an
        -- INSERT OR IGNORE no-op) to give OmniGrid an INDEPENDENT trend that
        -- survives the upstream ageing its data out. ``ts`` IS the test's
        -- created_at epoch (NOT the sampler wall-clock). download / upload are
        -- Mbps; ping / jitter are ms; packet_loss is a percent.
        CREATE TABLE IF NOT EXISTS speedtest_samples (
            ts          INTEGER NOT NULL,
            host_id     TEXT    NOT NULL,
            service_idx INTEGER NOT NULL,
            download    REAL    NOT NULL DEFAULT 0,
            upload      REAL    NOT NULL DEFAULT 0,
            ping        REAL    NOT NULL DEFAULT 0,
            jitter      REAL    NOT NULL DEFAULT 0,
            packet_loss REAL    NOT NULL DEFAULT 0,
            PRIMARY KEY (ts, host_id, service_idx)
        );
        CREATE INDEX IF NOT EXISTS idx_speedtest_samples_chip_ts
            ON speedtest_samples(host_id, service_idx, ts DESC);
        -- Plain (ts) index for the hourly prune predicate (seek from ts alone;
        -- see ping_samples / stats_samples for the same pattern).
        CREATE INDEX IF NOT EXISTS idx_speedtest_samples_ts
            ON speedtest_samples(ts);

        -- AdGuard Home per-(host, tick) snapshot. AdGuard keeps only a short
        -- rolling stats window (and the counters reset on restart), so the
        -- lifespan ``adguardhome_sampler`` records each AdGuard host's current
        -- queries/blocked/clients counters per tick. ``trend_summary`` buckets
        -- these by day, takes the daily MAX per host (the cumulative
        -- today-counter peaks just before the daily reset ≈ that day's total),
        -- sums across the fleet, and derives a daily blocked-% trend that
        -- outlives AdGuard's own retention. ``ts`` is the sampler wall-clock.
        CREATE TABLE IF NOT EXISTS adguard_samples (
            ts          INTEGER NOT NULL,
            host_id     TEXT    NOT NULL,
            service_idx INTEGER NOT NULL,
            queries     INTEGER NOT NULL DEFAULT 0,
            blocked     INTEGER NOT NULL DEFAULT 0,
            clients     INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (ts, host_id, service_idx)
        );
        CREATE INDEX IF NOT EXISTS idx_adguard_samples_chip_ts
            ON adguard_samples(host_id, service_idx, ts DESC);
        -- Plain (ts) index for the hourly prune predicate (seek from ts alone;
        -- see ping_samples / stats_samples for the same pattern).
        CREATE INDEX IF NOT EXISTS idx_adguard_samples_ts
            ON adguard_samples(ts);

        -- Pi-hole per-(host, tick) snapshot. Pi-hole's FTL keeps its own long
        -- DB, but the today-counters reset on restart, so the lifespan
        -- ``pihole_sampler`` records each Pi-hole host's current
        -- queries/blocked/clients counters per tick. ``trend_summary`` derives
        -- the same fleet blocked-% daily trend as AdGuard (daily MAX per host →
        -- summed across the fleet → blocked %). ``ts`` is the sampler
        -- wall-clock.
        CREATE TABLE IF NOT EXISTS pihole_samples (
            ts          INTEGER NOT NULL,
            host_id     TEXT    NOT NULL,
            service_idx INTEGER NOT NULL,
            queries     INTEGER NOT NULL DEFAULT 0,
            blocked     INTEGER NOT NULL DEFAULT 0,
            clients     INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (ts, host_id, service_idx)
        );
        CREATE INDEX IF NOT EXISTS idx_pihole_samples_chip_ts
            ON pihole_samples(host_id, service_idx, ts DESC);
        -- Plain (ts) index for the hourly prune predicate (seek from ts alone;
        -- see ping_samples / stats_samples for the same pattern).
        CREATE INDEX IF NOT EXISTS idx_pihole_samples_ts
            ON pihole_samples(ts);

        -- Seerr (Overseerr / Jellyseerr) request-backlog history. Seerr's own
        -- dashboard shows CURRENT counts only, so the lifespan ``seerr_sampler``
        -- snapshots each configured Seerr chip's request-queue gauges per tick
        -- (pending / processing / available / open issues). ``trend_summary``
        -- derives a daily-avg pending-backlog sparkline so an operator can spot
        -- "processing has been stuck at 5 for a week" / "pending spiked when I
        -- shared the server". ``ts`` is the sampler wall-clock; the columns are
        -- GAUGES (current depth), not cumulative counters.
        CREATE TABLE IF NOT EXISTS seerr_samples (
            ts          INTEGER NOT NULL,
            host_id     TEXT    NOT NULL,
            service_idx INTEGER NOT NULL,
            pending     INTEGER NOT NULL DEFAULT 0,
            processing  INTEGER NOT NULL DEFAULT 0,
            available   INTEGER NOT NULL DEFAULT 0,
            issues_open INTEGER NOT NULL DEFAULT 0,
            approved    INTEGER NOT NULL DEFAULT 0,
            declined    INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (ts, host_id, service_idx)
        );
        CREATE INDEX IF NOT EXISTS idx_seerr_samples_chip_ts
            ON seerr_samples(host_id, service_idx, ts DESC);
        -- Plain (ts) index for the hourly prune predicate (seek from ts alone;
        -- see ping_samples / stats_samples for the same pattern).
        CREATE INDEX IF NOT EXISTS idx_seerr_samples_ts
            ON seerr_samples(ts);

        -- Servarr-family (Radarr / Sonarr / Lidarr / Readarr) per-(host, tick)
        -- snapshot. The *arr apps expose no upstream history for library size /
        -- missing backlog / free disk, so the shared lifespan ``servarr_sampler``
        -- records each *arr instance's normalised gauges per tick.
        -- ``trend_summary`` rolls them into a daily-AVERAGE sparkline (library
        -- growth + missing backlog) and a disk-free-runway projection (linear
        -- fit over the daily ``disk_free_gb`` points). ``slug`` distinguishes
        -- which *arr produced the row so one table serves all four apps.
        -- ``ts`` is the sampler wall-clock.
        CREATE TABLE IF NOT EXISTS servarr_samples (
            ts           INTEGER NOT NULL,
            host_id      TEXT    NOT NULL,
            service_idx  INTEGER NOT NULL,
            slug         TEXT    NOT NULL DEFAULT '',
            total        INTEGER NOT NULL DEFAULT 0,
            missing      INTEGER NOT NULL DEFAULT 0,
            queue        INTEGER NOT NULL DEFAULT 0,
            disk_free_gb REAL    NOT NULL DEFAULT 0,
            PRIMARY KEY (ts, host_id, service_idx)
        );
        CREATE INDEX IF NOT EXISTS idx_servarr_samples_chip_ts
            ON servarr_samples(host_id, service_idx, ts DESC);
        -- Plain (ts) index for the hourly prune predicate (seek from ts alone;
        -- see ping_samples / stats_samples for the same pattern).
        CREATE INDEX IF NOT EXISTS idx_servarr_samples_ts
            ON servarr_samples(ts);

        -- qBittorrent per-(host, tick) snapshot. qBittorrent exposes only the
        -- CURRENT transfer speeds + free disk (no built-in speed history), so the
        -- lifespan ``qbittorrent_sampler`` records each instance's dl/up speed +
        -- free-disk + torrent count per tick. ``trend_summary`` draws the
        -- transfer-speed sparkline + a free-disk-runway projection (linear fit
        -- over the daily ``free_space_gb`` points). ``ts`` is the sampler
        -- wall-clock; speeds are bytes/s, free space is GiB.
        CREATE TABLE IF NOT EXISTS qbittorrent_samples (
            ts            INTEGER NOT NULL,
            host_id       TEXT    NOT NULL,
            service_idx   INTEGER NOT NULL,
            dl_speed      INTEGER NOT NULL DEFAULT 0,
            up_speed      INTEGER NOT NULL DEFAULT 0,
            free_space_gb REAL    NOT NULL DEFAULT 0,
            torrents      INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (ts, host_id, service_idx)
        );
        CREATE INDEX IF NOT EXISTS idx_qbittorrent_samples_chip_ts
            ON qbittorrent_samples(host_id, service_idx, ts DESC);
        -- Plain (ts) index for the hourly prune predicate (seek from ts alone;
        -- see ping_samples / stats_samples for the same pattern).
        CREATE INDEX IF NOT EXISTS idx_qbittorrent_samples_ts
            ON qbittorrent_samples(ts);

        -- UniFi client-occupancy retention. One row per (unifi chip, tick). The
        -- Integration API is current-state-only (no client-count history), so
        -- this sampler is the trend source for the "clients over time" card
        -- chart + "peak N clients". All columns are point-in-time GAUGES.
        CREATE TABLE IF NOT EXISTS unifi_samples (
            ts               INTEGER NOT NULL,
            host_id          TEXT    NOT NULL,
            service_idx      INTEGER NOT NULL,
            clients          INTEGER NOT NULL DEFAULT 0,
            clients_wireless INTEGER NOT NULL DEFAULT 0,
            devices_online   INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (ts, host_id, service_idx)
        );
        CREATE INDEX IF NOT EXISTS idx_unifi_samples_chip_ts
            ON unifi_samples(host_id, service_idx, ts DESC);
        -- Plain (ts) index for the hourly prune predicate (seek from ts alone).
        CREATE INDEX IF NOT EXISTS idx_unifi_samples_ts
            ON unifi_samples(ts);

        -- Bazarr subtitle-backlog retention. One row per (bazarr chip, tick).
        -- episodes_missing / movies_missing are point-in-time GAUGES (the
        -- current wanted-subtitle counts); Bazarr keeps no history of its own,
        -- so this sampler is the trend source for the backlog-over-time chart +
        -- the "backlog down N this week" stat.
        CREATE TABLE IF NOT EXISTS bazarr_samples (
            ts               INTEGER NOT NULL,
            host_id          TEXT    NOT NULL,
            service_idx      INTEGER NOT NULL,
            episodes_missing INTEGER NOT NULL DEFAULT 0,
            movies_missing   INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (ts, host_id, service_idx)
        );
        CREATE INDEX IF NOT EXISTS idx_bazarr_samples_chip_ts
            ON bazarr_samples(host_id, service_idx, ts DESC);
        -- Plain (ts) index for the hourly prune predicate (seek from ts alone).
        CREATE INDEX IF NOT EXISTS idx_bazarr_samples_ts
            ON bazarr_samples(ts);

        -- Plex concurrent-stream retention. One row per (plex chip, tick).
        -- sessions_active / sessions_transcoding / bandwidth_kbps are point-in-
        -- time GAUGES (current playback). Plex's PMS keeps no easy long history,
        -- so this sampler is the trend source for the streams-over-time chart +
        -- the "peak N concurrent streams today" stat.
        CREATE TABLE IF NOT EXISTS plex_samples (
            ts                   INTEGER NOT NULL,
            host_id              TEXT    NOT NULL,
            service_idx          INTEGER NOT NULL,
            sessions_active      INTEGER NOT NULL DEFAULT 0,
            sessions_transcoding INTEGER NOT NULL DEFAULT 0,
            bandwidth_kbps       INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (ts, host_id, service_idx)
        );
        CREATE INDEX IF NOT EXISTS idx_plex_samples_chip_ts
            ON plex_samples(host_id, service_idx, ts DESC);
        -- Plain (ts) index for the hourly prune predicate (seek from ts alone).
        CREATE INDEX IF NOT EXISTS idx_plex_samples_ts
            ON plex_samples(ts);

        -- Tdarr transcode-pipeline retention. One row per (tdarr chip, tick).
        -- ``space_saved_gb`` + ``transcodes`` are CUMULATIVE running totals
        -- (Tdarr's StatisticsJSONDB.sizeDiff / totalTranscodeCount), so the
        -- trend reads them as a cumulative line ("reclaimed X TB and counting")
        -- + a per-day DIFF (throughput). ``transcode_queue`` is a GAUGE (daily
        -- avg → burn-down). ``ts`` is the sampler wall-clock.
        CREATE TABLE IF NOT EXISTS tdarr_samples (
            ts              INTEGER NOT NULL,
            host_id         TEXT    NOT NULL,
            service_idx     INTEGER NOT NULL,
            total_files     INTEGER NOT NULL DEFAULT 0,
            transcode_queue INTEGER NOT NULL DEFAULT 0,
            space_saved_gb  REAL    NOT NULL DEFAULT 0,
            transcodes      INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (ts, host_id, service_idx)
        );
        CREATE INDEX IF NOT EXISTS idx_tdarr_samples_chip_ts
            ON tdarr_samples(host_id, service_idx, ts DESC);
        -- Plain (ts) index for the hourly prune predicate.
        CREATE INDEX IF NOT EXISTS idx_tdarr_samples_ts
            ON tdarr_samples(ts);

        -- Kavita library-growth retention. One row per (kavita chip, tick). All
        -- columns are CUMULATIVE running totals (a library only grows), so the
        -- trend reads them as each day's LAST value (a growth line). ``ts`` is
        -- the sampler wall-clock; total_size is bytes.
        CREATE TABLE IF NOT EXISTS kavita_samples (
            ts            INTEGER NOT NULL,
            host_id       TEXT    NOT NULL,
            service_idx   INTEGER NOT NULL,
            series_count  INTEGER NOT NULL DEFAULT 0,
            volume_count  INTEGER NOT NULL DEFAULT 0,
            chapter_count INTEGER NOT NULL DEFAULT 0,
            total_size    INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (ts, host_id, service_idx)
        );
        CREATE INDEX IF NOT EXISTS idx_kavita_samples_chip_ts
            ON kavita_samples(host_id, service_idx, ts DESC);
        -- Plain (ts) index for the hourly prune predicate.
        CREATE INDEX IF NOT EXISTS idx_kavita_samples_ts
            ON kavita_samples(ts);

        -- Prowlarr counter-rate retention. One row per (prowlarr chip, tick).
        -- All columns are CUMULATIVE lifetime counters (indexerstats totals), so
        -- the trend DIFFS consecutive days into per-day query / grab throughput
        -- + a daily failure rate (a negative delta = a stats reset → clamped 0).
        CREATE TABLE IF NOT EXISTS prowlarr_samples (
            ts            INTEGER NOT NULL,
            host_id       TEXT    NOT NULL,
            service_idx   INTEGER NOT NULL,
            total_queries INTEGER NOT NULL DEFAULT 0,
            total_grabs   INTEGER NOT NULL DEFAULT 0,
            total_failed  INTEGER NOT NULL DEFAULT 0,
            -- Fleet query-weighted avg response time (ms) — a GAUGE, not a
            -- counter (P3 "indexers getting slower" trend). Added via idempotent
            -- ALTER for existing deploys; here for fresh ones.
            response_ms   INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (ts, host_id, service_idx)
        );
        CREATE INDEX IF NOT EXISTS idx_prowlarr_samples_chip_ts
            ON prowlarr_samples(host_id, service_idx, ts DESC);
        -- Plain (ts) index for the hourly prune predicate.
        CREATE INDEX IF NOT EXISTS idx_prowlarr_samples_ts
            ON prowlarr_samples(ts);

        -- Public-IP change history. Records every CHANGED outcome from
        -- logic.public_ip.fetch() (operator-opt-in, gated by
        -- public_ip_enabled). ONE row per change — duplicate IPs
        -- from the cache hit OR consecutive fetches returning the same
        -- value DON'T write a row. Drives the AI palette's ability to
        -- answer "when did my IP / ISP last change?" + the Admin →
        -- Public IP history table. Never pruned by the standard
        -- tuning_stats_history_days retention — IP-change events are
        -- low-volume + high-value (operators want a year+ of history).
        CREATE TABLE IF NOT EXISTS public_ip_history (
            ts           INTEGER PRIMARY KEY,
            ip           TEXT NOT NULL,
            isp          TEXT,
            asn          TEXT,
            country      TEXT,
            city         TEXT,
            country_code TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_public_ip_history_ip
            ON public_ip_history(ip);

        -- WeatherAPI.com per-tick samples. ONE row per (ts, lat, lon).
        -- Lat / lon are quantised to 2 decimals (matches logic.weather
        -- ._quantise_key) so the AI palette + Telegram /weather + UI
        -- charts can answer "what was the temperature here yesterday
        -- afternoon", "when was the last full moon" etc. Written by
        -- logic.weather_sampler at tuning_weather_sampler_interval_seconds
        -- cadence (default 3600s = hourly; 0 disables the sampler entirely).
        -- Pruned hourly to tuning_weather_history_retention_days (default
        -- 90; 0 disables pruning — keep every sample forever).
        -- `condition` carries the human-readable phrase (e.g. "Partly
        -- cloudy"), `code` the WeatherAPI numeric. `moon_phase` /
        -- `moon_illumination` are the astronomy fields the AI uses to
        -- answer moon-related questions. `raw_json` stores the full
        -- forecast block (sunrise/sunset/per-day rollups) so historical
        -- queries can drill into days that aren't the current hour.
        CREATE TABLE IF NOT EXISTS weather_samples (
            ts                INTEGER NOT NULL,
            lat               REAL    NOT NULL,
            lon               REAL    NOT NULL,
            label             TEXT,
            temp_c            REAL,
            humidity          REAL,
            wind_kmh          REAL,
            condition         TEXT,
            code              INTEGER,
            moon_phase        TEXT,
            moon_illumination REAL,
            raw_json          TEXT,
            PRIMARY KEY (ts, lat, lon)
        );
        CREATE INDEX IF NOT EXISTS idx_weather_samples_ts
            ON weather_samples(ts DESC);
        CREATE INDEX IF NOT EXISTS idx_weather_samples_coord_ts
            ON weather_samples(lat, lon, ts DESC);

        -- AlAdhan prayer-times per-day samples. ONE row per
        -- (greg_date, lat, lon, method, school) — prayer timings are
        -- daily-static for a given location + calculation config, so the
        -- composite primary key deduplicates to a single row per day per
        -- config (a re-fetch within the same day INSERT-OR-REPLACEs, just
        -- refreshing `ts`). Lat / lon quantised to 3 decimals (matches
        -- logic.prayer_times's cache key). Written by
        -- logic.prayer_times_sampler at
        -- tuning_prayer_times_sampler_interval_seconds cadence (default
        -- 21600s = 6h; daily-static data doesn't need hourly; 0 disables
        -- the sampler entirely). Pruned hourly to
        -- tuning_prayer_times_history_retention_days (default 90; 0 keeps
        -- every sample forever). Stores the five obligatory prayers +
        -- Sunrise (HH:MM) + the Hijri date text so the Admin → Prayer
        -- Times history table + AI palette can answer "what time was Fajr
        -- last Friday" without re-hitting api.aladhan.com.
        CREATE TABLE IF NOT EXISTS prayer_times_samples (
            ts          INTEGER NOT NULL,
            greg_date   TEXT    NOT NULL,
            lat         REAL    NOT NULL,
            lon         REAL    NOT NULL,
            label       TEXT,
            method      INTEGER NOT NULL,
            school      INTEGER NOT NULL,
            fajr        TEXT,
            sunrise     TEXT,
            dhuhr       TEXT,
            asr         TEXT,
            maghrib     TEXT,
            isha        TEXT,
            hijri_text  TEXT,
            timezone    TEXT,
            PRIMARY KEY (greg_date, lat, lon, method, school)
        );
        CREATE INDEX IF NOT EXISTS idx_prayer_times_samples_ts
            ON prayer_times_samples(ts DESC);
        CREATE INDEX IF NOT EXISTS idx_prayer_times_samples_coord_ts
            ON prayer_times_samples(lat, lon, ts DESC);

        -- Prayer-reminder dedup ledger. ONE row per (user, day, prayer)
        -- that has already had its "N minutes before" reminder delivered,
        -- so the lifespan reminder loop (logic.prayer_reminders) never
        -- double-fires across its frequent ticks OR across a container
        -- restart mid-window. greg_date is YYYY-MM-DD; prayer_key is one
        -- of fajr/dhuhr/asr/maghrib/isha. Pruned to a few days (the loop
        -- only cares about today; older rows are housekeeping).
        CREATE TABLE IF NOT EXISTS prayer_reminders_sent (
            username   TEXT    NOT NULL,
            greg_date  TEXT    NOT NULL,
            prayer_key TEXT    NOT NULL,
            ts         INTEGER NOT NULL,
            PRIMARY KEY (username, greg_date, prayer_key)
        );
        CREATE INDEX IF NOT EXISTS idx_prayer_reminders_sent_ts
            ON prayer_reminders_sent(ts);

        -- HTTP / TLS-cert / DNS health probe (seventh host-stats provider).
        -- ONE row per (host_id, url, ts). Written by
        -- logic/host_http_sampler.py at
        -- tuning_http_probe_sample_interval_seconds cadence (default
        -- 0 = inherit global stats interval). Pruned to
        -- tuning_stats_history_days. Composite primary key allows
        -- multiple URLs per host per tick (operator monitoring
        -- several services on one host).
        CREATE TABLE IF NOT EXISTS host_http_samples (
            ts                   INTEGER NOT NULL,
            host_id              TEXT    NOT NULL,
            url                  TEXT    NOT NULL,
            status_code          INTEGER,
            status_ok            INTEGER NOT NULL,
            content_match_ok     INTEGER NOT NULL,
            tls_expires_in_days  INTEGER,
            tls_subject          TEXT,
            tls_issuer           TEXT,
            tls_error            TEXT,
            dns_resolved         INTEGER NOT NULL,
            latency_ms           INTEGER,
            error                TEXT,
            PRIMARY KEY (ts, host_id, url)
        );
        CREATE INDEX IF NOT EXISTS idx_host_http_samples_host_ts
            ON host_http_samples(host_id, ts DESC);
        -- Plain (ts) index for the hourly prune predicate; without it
        -- the composite above can't seek from ts alone and the prune
        -- degrades to a full scan (see stats_samples for the pattern).
        CREATE INDEX IF NOT EXISTS idx_host_http_samples_ts
            ON host_http_samples(ts);
        -- Covering index for the per-host `last_ok` derivation query
        -- in `main_pkg/hosts_routes.py:_build_provider_state_index`
        -- (`SELECT host_id, MAX(ts) FROM host_http_samples
        --  WHERE status_ok = 1 GROUP BY host_id`). The existing
        -- (host_id, ts DESC) composite can't be used by the planner
        -- because the query has no `host_id` equality predicate;
        -- without this covering index the planner falls back to a
        -- full scan that holds the writer lock for hundreds of ms
        -- and triggers the "slow query storm" of `PRAGMA
        -- busy_timeout=2000` waits queuing behind it. Leading
        -- `status_ok` discriminates the filter, then `host_id`
        -- supports the GROUP BY, then `ts DESC` supports MAX(ts).
        -- EXPLAIN QUERY PLAN shows SEARCH USING INDEX (not SCAN).
        CREATE INDEX IF NOT EXISTS idx_host_http_samples_ok_host_ts
            ON host_http_samples(status_ok, host_id, ts DESC);

        -- Per-service reachability probe results — one row per
        -- (host_id, service_idx, ts). `service_idx` is the position
        -- of the service in `hosts_config[host_id].services[]` at
        -- sampler-tick time; we accept that an operator reorder will
        -- mis-attribute pre-reorder rows because the alternative
        -- (sliding-window UUID assignment on every service-list edit)
        -- is much more complex. Operators don't reorder often; the
        -- chart's freshness label highlights when the data is from
        -- before the most recent edit so the operator can recompute.
        -- `alive=1` is the success signal; `rtt_ms` populated only
        -- on alive=1 ticks (skipped on failures per the skip-don't-
        -- synthesize rule).
        -- `port` column distinguishes per-port samples (port=80/443/etc)
        -- from rollup samples (port=0 sentinel — chip-level status). The
        -- sentinel value (0 — not a valid TCP/UDP port per RFC) is used
        -- instead of NULL because SQLite treats every NULL as distinct
        -- in PRIMARY KEY uniqueness checks, which breaks the
        -- INSERT OR REPLACE upsert pattern. Pre-migration installs
        -- (no `port` column) are upgraded in-place by
        -- `_migration_005_service_samples_port_column` which rebuilds
        -- the table with the new PK + backfills existing rows to
        -- port=0. Single-port chips emit ONLY the rollup row; multi-port
        -- chips emit one rollup row PLUS one row per port.
        CREATE TABLE IF NOT EXISTS service_samples (
            ts            INTEGER NOT NULL,
            host_id       TEXT    NOT NULL,
            service_idx   INTEGER NOT NULL,
            port          INTEGER NOT NULL DEFAULT 0,
            alive         INTEGER NOT NULL,
            rtt_ms        INTEGER,
            error         TEXT,
            PRIMARY KEY (ts, host_id, service_idx, port)
        );
        CREATE INDEX IF NOT EXISTS idx_service_samples_host_ts
            ON service_samples(host_id, ts DESC);
        -- Plain (ts) index for the hourly prune predicate; without it
        -- the composite above can't seek from ts alone and the prune
        -- degrades to a full scan (see stats_samples for the pattern).
        CREATE INDEX IF NOT EXISTS idx_service_samples_ts
            ON service_samples(ts);
        CREATE INDEX IF NOT EXISTS idx_service_samples_host_idx_ts
            ON service_samples(host_id, service_idx, ts DESC);
        -- Covering index for the per-host `last_ok` derivation
        -- query in `main_pkg/hosts_routes.py:_build_provider_state_index`
        -- (`SELECT host_id, MAX(ts) FROM service_samples
        --  WHERE alive = 1 GROUP BY host_id`). Same pattern as
        -- `idx_host_http_samples_ok_host_ts` above — leading `alive`
        -- discriminates the filter, then `host_id` supports the
        -- GROUP BY, then `ts DESC` supports MAX(ts). Pre-fix this
        -- query was the smoking-gun root cause of the "slow_query"
        -- storm operators saw: a full table scan on a multi-million
        -- row sample table held the writer lock for ~450ms; every
        -- reader landing during that window queued up + reported as
        -- a separate slow-query warning (linear-growth pattern).
        -- EXPLAIN QUERY PLAN should change from SCAN -> SEARCH
        -- USING INDEX after this index lands.
        CREATE INDEX IF NOT EXISTS idx_service_samples_alive_host_ts
            ON service_samples(alive, host_id, ts DESC);
        -- Partition-matching index for the Apps "latest per (host, chip,
        -- port)" + "rollup history per (host, chip)" window queries in
        -- logic/service_sampler.py (latest_per_port_all_for_hosts /
        -- history_rollup_all_for_hosts / bulk_latest_per_port_for_hosts).
        -- Those PARTITION BY (host_id, service_idx, port) ORDER BY ts DESC
        -- (or the MAX(ts) GROUP BY equivalent). None of the indexes above
        -- match that key order, so each ran ROW_NUMBER()/GROUP BY over a
        -- full table SCAN of the (multi-million-row) sample table — the
        -- 1.5-2.4s slow_query warnings operators saw. With the leading
        -- columns in partition order + ts DESC last, SQLite walks each
        -- partition pre-sorted: the window/MAX collapses to an index
        -- range-scan that takes the first row per group (no sort, no
        -- scan). EXPLAIN QUERY PLAN flips SCAN -> SEARCH USING INDEX.
        CREATE INDEX IF NOT EXISTS idx_service_samples_chip_port_ts
            ON service_samples(host_id, service_idx, port, ts DESC);

        -- Apps feature — reusable service templates ("catalog"). Each row
        -- is a recipe an operator can bind to N hosts (Radarr / Sonarr /
        -- Plex / Portainer / etc.) so they don't redefine probe path +
        -- default ports + icon slug per host. Per-host instances continue
        -- to live in `hosts_config[].services[]`; chips may now carry
        -- `catalog_id` (numeric FK to this table) to inherit the template's
        -- defaults. `default_ports_json` is a JSON array of
        -- `{port, protocol, label, probe_path, probe_status}` so a template
        -- can carry multi-port shape (Portainer 8000 + 8443). `icon` is the
        -- brand-icon slug (resolved by static/js/app.js:iconUrlFor).
        -- `source = 'builtin' | 'operator'` distinguishes shipped seed
        -- templates from operator-added ones; builtin rows are idempotently
        -- seeded on first boot when the table is empty.
        CREATE TABLE IF NOT EXISTS service_catalog (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            name            TEXT    NOT NULL,
            slug            TEXT    NOT NULL UNIQUE,
            icon            TEXT,
            description     TEXT,
            default_ports_json TEXT NOT NULL DEFAULT '[]',
            -- Per-template "show extras panel on app cards" default.
            -- The APC template emits a UPS-stats panel today (Battery /
            -- Output load / Runtime / Battery temp / Battery state);
            -- future templates with rich per-host data follow the same
            -- gate. Per-host chip's `show_extras` overrides this when
            -- set. Default 0 (= hidden): extras are OPT-IN — the operator
            -- ticks "Show extras" on the template to enable the panel.
            show_extras     INTEGER NOT NULL DEFAULT 0,
            source          TEXT    NOT NULL DEFAULT 'operator',
            created_ts      INTEGER NOT NULL DEFAULT (CAST(strftime('%s','now') AS INTEGER)),
            updated_ts      INTEGER NOT NULL DEFAULT (CAST(strftime('%s','now') AS INTEGER))
        );
        -- Additive ALTER for pre-existing deploys (the column above is
        -- only honoured on first CREATE). `IF NOT EXISTS` SQLite hack
        -- via try/except around the bare ALTER lives in `_safe_alter`;
        -- here we trigger via a defensive duplicate-column-tolerant
        -- ALTER that no-ops cleanly when the column is already there.
        -- Schema drift defence: every ALTER lands without breaking
        -- a re-run of init_db (per the project conventions additive-schema rule).
        CREATE INDEX IF NOT EXISTS idx_service_catalog_slug
            ON service_catalog(slug);

        -- Port-scan provider results. ONE ROW PER OPEN PORT per scan;
        -- closed-port rows would balloon the table on multi-host scans.
        -- `scan_id` groups rows from one scan so the SPA can fetch a
        -- whole scan via `scan_id` OR the latest scan per host via
        -- `(host_id, ts DESC, scan_id) LIMIT 1` to find the head + then
        -- `scan_id = ?` to pull every row in that scan. `service_hint`
        -- is a tiny lookup-table guess (port 32400 → "plex") for chip
        -- labels; NOT a fingerprint — Stage 2's banner-grab path will
        -- replace this with real version detection.
        CREATE TABLE IF NOT EXISTS host_port_scans (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            ts              INTEGER NOT NULL,
            host_id         TEXT    NOT NULL,
            scan_id         TEXT    NOT NULL,
            port            INTEGER NOT NULL,
            service_hint    TEXT,
            banner_excerpt  TEXT,
            protocol        TEXT    DEFAULT 'tcp'
        );
        CREATE INDEX IF NOT EXISTS idx_host_port_scans_host_ts
            ON host_port_scans(host_id, ts DESC);
        CREATE INDEX IF NOT EXISTS idx_host_port_scans_scan
            ON host_port_scans(scan_id);
        -- Plain (ts) index so the retention prune (DELETE ... WHERE ts < ?)
        -- seeks instead of scanning — the (host_id, ts) composite above can't
        -- be seeked on a ts-only predicate.
        CREATE INDEX IF NOT EXISTS idx_host_port_scans_ts
            ON host_port_scans(ts);

        -- In-app notifications store. One row per notification dispatched
        -- through `logic.ops.notify`'s `app` medium (sibling of the
        -- existing `apprise` medium). Drives the avatar badge unread-count,
        -- the Notifications page, and SSE pushes. Pruned on schedule
        -- (`prune_notifications` kind) by `tuning_notification_retention_days`.
        -- `severity` mirrors the four levels operators see in the persistent
        -- log viewer (info / warning / error / success). `metadata` is a
        -- free-form JSON blob the renderer can read for richer formatting
        -- (icons, links, durations) without breaking the column shape.
        -- `read_at` NULL = unread; epoch seconds when the operator marked it
        -- read. Index on read_at where NULL gives the unread-count probe an
        -- O(unread) scan rather than O(total).
        CREATE TABLE IF NOT EXISTS notifications (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            ts          INTEGER NOT NULL,
            event       TEXT    NOT NULL,
            severity    TEXT    NOT NULL,
            title       TEXT    NOT NULL,
            body        TEXT    NOT NULL,
            actor       TEXT,
            target_kind TEXT,
            target_id   TEXT,
            metadata    TEXT,
            read_at     INTEGER
        );
        CREATE INDEX IF NOT EXISTS idx_notifications_ts
            ON notifications(ts DESC);
        CREATE INDEX IF NOT EXISTS idx_notifications_unread
            ON notifications(read_at) WHERE read_at IS NULL;
        -- AI integration (Stage 1 foundation). One row per call to an
        -- AI provider. Stage 1 ships the schema + admin surface; the
        -- writer lives in `logic/ai.py` (Stage 2+) — when wired up,
        -- every provider call records here so the dashboard can render
        -- token usage / cost / pass-rate / accuracy / response-time
        -- aggregates without needing a separate metrics store.
        --
        --   provider          — claude / gemini / chatgpt / deepseek
        --   model             — provider-specific model id at call time
        --                        (e.g. claude-opus-4-7, gpt-4o,
        --                        gemini-2.5-pro, deepseek-chat). Stored
        --                        per-row so the dashboard can break
        --                        token usage down by model.
        --   kind              — what the call was for (free-form;
        --                        Stage 2+ defines the canonical kinds).
        --   status            — running / success / error.
        --   prompt_tokens     — input tokens consumed.
        --   completion_tokens — output tokens generated.
        --   total_tokens      — sum (or provider-reported total when it
        --                        differs from prompt+completion).
        --   cost_usd          — operator-visible cost in USD; computed
        --                        from per-provider rate cards by the
        --                        writer at insert time so historical
        --                        rows survive a rate-card change.
        --   response_time_ms  — end-to-end latency the writer measured.
        --   accuracy_score    — 0..1 score from the optional accuracy
        --                        check; NULL when the call was not
        --                        validated.
        --   accuracy_check    — JSON metadata about the validation
        --                        (which check ran, expected vs actual,
        --                        etc).
        --   error             — short error message when status='error'.
        --   metadata          — JSON catch-all (request id, retries,
        --                        whatever the writer wants to keep).
        CREATE TABLE IF NOT EXISTS ai_jobs (
            id                INTEGER PRIMARY KEY AUTOINCREMENT,
            ts                INTEGER NOT NULL,
            provider          TEXT    NOT NULL,
            model             TEXT,
            kind              TEXT,
            status            TEXT    NOT NULL,
            prompt_tokens     INTEGER,
            completion_tokens INTEGER,
            total_tokens      INTEGER,
            cost_usd          REAL,
            response_time_ms  INTEGER,
            accuracy_score    REAL,
            accuracy_check    TEXT,
            error             TEXT,
            metadata          TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_ai_jobs_ts
            ON ai_jobs(ts DESC);
        CREATE INDEX IF NOT EXISTS idx_ai_jobs_provider_ts
            ON ai_jobs(provider, ts DESC);
        -- AI memory table — durable lessons the AI has learned about
        -- this specific OmniGrid deployment. Populated when an AI
        -- reply emits a `MEMORY: ...` line; injected into every
        -- subsequent palette call's system prompt so the AI accumulates
        -- knowledge across sessions and avoids repeating mistakes.
        --   text   — the memory body (one-line directive, no newlines).
        --   source — 'ai' when emitted by a model reply, 'operator'
        --            when added manually via the admin UI.
        --   actor  — username of the operator whose conversation produced
        --            the memory (or 'system' when seeded).
        CREATE TABLE IF NOT EXISTS ai_memory (
            id     INTEGER PRIMARY KEY AUTOINCREMENT,
            ts     INTEGER NOT NULL,
            text   TEXT    NOT NULL,
            source TEXT    NOT NULL DEFAULT 'ai',
            actor  TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_ai_memory_ts
            ON ai_memory(ts DESC);
        -- Apps custom-dashboard named "views" — shareable across users.
        -- Pre-this-table every view lived only in the owning user's
        -- ui_prefs.apps_custom_views blob (private by construction). To let
        -- a view be PUBLIC (readable — and optionally writable — by other
        -- users) the views must live in a cross-user table.
        --   id              — 'view-<uuid>', minted client-side.
        --   owner_username  — the creator; the only one who can delete the
        --                     view or change its sharing settings.
        --   layout          — JSON {sections, unsectioned_collapsed}.
        --   visibility      — 'private' (owner only) | 'public' (every
        --                     signed-in user can see it).
        --   edit_permission — 'owner' (read-only to non-owners) | 'all'
        --                     (any non-readonly-role user can rearrange it).
        --                     Only meaningful when visibility='public'.
        CREATE TABLE IF NOT EXISTS app_views (
            id              TEXT    PRIMARY KEY,
            owner_username  TEXT    NOT NULL,
            name            TEXT    NOT NULL,
            layout          TEXT    NOT NULL DEFAULT '{}',
            visibility      TEXT    NOT NULL DEFAULT 'private',
            edit_permission TEXT    NOT NULL DEFAULT 'owner',
            created_at      INTEGER NOT NULL,
            updated_at      INTEGER NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_app_views_owner
            ON app_views(owner_username);
        CREATE INDEX IF NOT EXISTS idx_app_views_visibility
            ON app_views(visibility);
        COMMIT;
        """)
        # Idempotent column additions for existing deployments. SQLite pre-3.35
        # has no "ADD COLUMN IF NOT EXISTS", so we catch the OperationalError
        # that gets raised when the column already exists. Safe to re-run on
        # every boot.
        for ddl in (
                "ALTER TABLE history ADD COLUMN actor TEXT DEFAULT 'ui'",
                "ALTER TABLE history ADD COLUMN target_stack TEXT",
                # target_kind taxonomy column — also handled by migration 3
                # which runs at the end of init_db, but adding it here too
                # means any code path that touches `target_kind` BEFORE the
                # migration applies (e.g. an executescript reference earlier
                # in init_db) doesn't fail. Idempotent via the
                # OperationalError catch below.
                "ALTER TABLE history ADD COLUMN target_kind TEXT",
                # disk I/O rates, derived per-tick by
                # host_metrics_sampler from node_disk_{read,written}_bytes_total.
                # Same skip-don't-synthesize discipline as the net rate columns;
                # NULL when the delta is out of bounds.
                "ALTER TABLE host_metrics_samples ADD COLUMN disk_read_bps REAL",
                "ALTER TABLE host_metrics_samples ADD COLUMN disk_write_bps REAL",
                # Per-item image-disk footprint (`size_root` in bytes) — added
                # so Stacks / Services / Containers can render a disk sparkline
                # mirroring the CPU / Memory ones. Pre-fix `stats_samples` only
                # stored CPU + memory, so disk had no time-series and the UI
                # could only show a CURRENT snapshot bar. Sampler writes
                # `s.get("size_root")` each tick.
                "ALTER TABLE stats_samples ADD COLUMN size_root REAL",
                # Seerr request-state composition history (P2 stacked chart) —
                # approved / declined depth per tick, alongside the existing
                # pending / processing / available gauges.
                "ALTER TABLE seerr_samples ADD COLUMN approved INTEGER DEFAULT 0",
                "ALTER TABLE seerr_samples ADD COLUMN declined INTEGER DEFAULT 0",
                # Prowlarr fleet avg response time (ms) gauge — P3 slowness trend.
                "ALTER TABLE prowlarr_samples ADD COLUMN response_ms INTEGER NOT NULL DEFAULT 0",
                # wall-clock of the MOST RECENT probe failure.
                # ``first_failure_ts`` already records the start of the
                # streak; this is the timestamp of the latest failed
                # probe so the drawer can render "last error N seconds
                # ago" instead of leaving the operator wondering whether
                # the issue may have already cleared.
                "ALTER TABLE host_failure_state ADD COLUMN last_failure_ts REAL",
                # host uptime in SECONDS per SNMP probe. Lets the
                # drawer surface a current-uptime pill AND detect reboots:
                # when sample[N].uptime_s < sample[N-1].uptime_s the host
                # rebooted in the gap (sysUpTime counter resets at boot).
                # Stored as seconds (not raw TimeTicks) so it matches the
                # `host_uptime_s` field convention every other provider
                # uses. Additive — NULL for pre-uptime-column rows.
                "ALTER TABLE host_snmp_samples ADD COLUMN uptime_s INTEGER",
                # switch total throughput. Stored as the cumulative
                # IF-MIB ifHCInOctets / ifHCOutOctets sums (excluding
                # loopback / docker-bridge / virtual ifaces — same exclusion
                # set as Beszel / NE). The chart layer computes deltas at
                # render time. Skip-don't-synthesize: sampler inserts NULL
                # when SNMP didn't return either counter, so the chart can
                # tell "host stopped responding" from "0 bps idle".
                "ALTER TABLE host_snmp_samples ADD COLUMN net_rx_total_bytes INTEGER",
                "ALTER TABLE host_snmp_samples ADD COLUMN net_tx_total_bytes INTEGER",
                # printer lifetime page count (Printer-MIB
                # prtMarkerLifeCount). Cumulative monotonic counter; the
                # SPA computes per-interval deltas → pages/day for the
                # sparkline + reads the live value as the lifetime
                # headline. NULL for non-printer hosts.
                "ALTER TABLE host_snmp_samples ADD COLUMN printer_page_count INTEGER",
                # per-iface link speed (Mbps) so the per-port
                # utilization heatmap can compute throughput ÷ link
                # capacity. NULL when the agent doesn't expose ifHighSpeed
                # (older IF-MIB-v1-only devices) — heatmap renders such
                # ifaces in grey ("unknown speed") instead of red.
                "ALTER TABLE host_snmp_iface_samples ADD COLUMN link_speed_mbps INTEGER",
                # APC UPS time-series fields. Sampler writes the live
                # values per probe so the host drawer can render Output
                # Load %, Battery %, Battery temperature charts over the
                # picker window. NULL for non-UPS hosts. Reads come from
                # `host_load_percent` / `host_battery_percent` /
                # `host_battery_temp_c` extracted in `logic/snmp.py` via
                # PowerNet-MIB OIDs (1.3.6.1.4.1.318.1.1.1.x).
                "ALTER TABLE host_snmp_samples ADD COLUMN load_percent REAL",
                "ALTER TABLE host_snmp_samples ADD COLUMN battery_percent REAL",
                "ALTER TABLE host_snmp_samples ADD COLUMN battery_temp_c REAL",
                # APC UPS string + runtime fields persisted alongside the
                # numeric percentages so the Apps APC card can render its
                # full panel (battery state / output status / runtime
                # remaining) straight from the sample row — the card reads
                # the DB, never a live host probe. ups_status /
                # battery_status carry the PowerNet-MIB label strings
                # ("onLine" / "batteryNormal"); battery_runtime_s is the
                # decoded seconds-remaining. NULL for non-UPS hosts.
                "ALTER TABLE host_snmp_samples ADD COLUMN ups_status TEXT",
                "ALTER TABLE host_snmp_samples ADD COLUMN battery_status TEXT",
                "ALTER TABLE host_snmp_samples ADD COLUMN battery_runtime_s INTEGER",
                # public-IP geo flag — persist the 2-letter ISO country
                # code alongside the change-history row so the widget's
                # stale-fallback (last-known IP when the live lookup
                # fails) can still render the country flag instead of
                # the globe placeholder. NULL on legacy rows recorded
                # before this column existed.
                "ALTER TABLE public_ip_history ADD COLUMN country_code TEXT",
                # Aggregate disk totals — added so SNMP-only hosts can
                # render the inline disk sparkline. Pre-fix the table
                # carried CPU + memory + load + UPS + interface data
                # but no disk percent, so the SPA's hostInlineSparkline
                # SNMP fallback explicitly skipped disk ("SNMP series
                # doesn't carry it"). Operator reported on a dd-wrt +
                # WDMyCloud NAS where the row's disk bar correctly
                # showed live percent but the sparkline beneath stayed
                # absent. Sampler now writes both columns; SPA derives
                # disk % from the pair (matches the `mem_used/mem_total`
                # branch's pattern). Aggregate values respect the same
                # exclude-mounts list `extract_storage` honours, so
                # phantom rows (dd-wrt's `/opt`) don't pollute history.
                "ALTER TABLE host_snmp_samples ADD COLUMN disk_total INTEGER",
                "ALTER TABLE host_snmp_samples ADD COLUMN disk_used INTEGER",
                # HTTP probe — TLS certificate metadata + DNS / TLS
                # error strings persisted alongside the numeric outcome
                # so the drawer card can surface cert subject / issuer
                # without cross-referencing an external monitor.
                # tls_error carries the exception text when the TLS
                # handshake failed (cert chain broken, hostname mismatch,
                # expired). Idempotent additive adds.
                "ALTER TABLE host_http_samples ADD COLUMN tls_subject TEXT",
                "ALTER TABLE host_http_samples ADD COLUMN tls_issuer TEXT",
                "ALTER TABLE host_http_samples ADD COLUMN tls_error TEXT",
                # Per-template "show extras panel on app cards" default.
                # Drives the APC UPS-stats panel today; future
                # extras-capable templates (Plex now-playing widget,
                # Sonarr queue summary, etc.) follow the same gate.
                # Default 0 = hidden: extras are OPT-IN (the operator ticks
                # "Show extras" on the template). Existing rows that were
                # added under the prior DEFAULT 1 are flipped to 0 by
                # migration 006 so the unchecked state matches the render.
                "ALTER TABLE service_catalog ADD COLUMN show_extras INTEGER NOT NULL DEFAULT 0",
        ):
            try:
                c.execute(ddl)
            except sqlite3.OperationalError:
                pass
        c.execute(
            "CREATE INDEX IF NOT EXISTS idx_history_target_stack "
            "ON history(target_stack)"
        )
        # Auth schema — users / sessions / api_tokens. Owned by auth.py but
        # created here so there's a single init_db() entry point.
        auth.init_auth_schema(c)
        # Scheduler schema — admin-defined recurring jobs. Same pattern:
        # owned by logic/schedules.py, created here.
        schedules.init_schedules_schema(c)
        # Schema migrations infrastructure. Adds the
        # `schema_migrations` table and applies any pending migrations
        # registered in `logic/migrations.py:MIGRATIONS`. Empty registry
        # today — additive changes still go in the CREATE TABLE block
        # above. Non-additive changes (renames, type changes, data
        # migrations) get a numbered migration function. Boot halts on
        # migration failure so a half-applied schema can't slip through.
        from logic import migrations as _migrations
        _migrations.init_migrations_schema(c)
        _migrations.apply_pending(c)
        # First-boot ai_memory seed — canonical lessons every deploy
        # benefits from regardless of conversation history. Idempotent
        # via the duplicate-text guard so re-running on existing
        # databases doesn't accumulate duplicates. Source `system` so
        # the Admin → AI → Memory pane can distinguish seeded baseline
        # lessons from `ai`-emitted or `operator`-added ones.
        for canonical_text in _AI_MEMORY_SEEDS:
            try:
                already = c.execute(
                    "SELECT 1 FROM ai_memory WHERE text = ? LIMIT 1",
                    (canonical_text,),
                ).fetchone()
                if already:
                    continue
                c.execute(
                    "INSERT INTO ai_memory (ts, text, source, actor) "
                    "VALUES (?, ?, 'system', 'bootstrap')",
                    (int(time.time()), canonical_text),
                )
            except sqlite3.Error:
                # Seed is best-effort — a one-off insert failure
                # doesn't block init_db.
                pass

        # Apps feature — service_catalog built-in templates. The boot
        # seed adds any builtin that's NEW to _BUILTIN (tracked via a
        # seeded-slug ledger) so a builtin shipped in a later release
        # appears automatically on the next deploy, while builtins the
        # operator deleted on purpose stay gone. Operator edits to a
        # builtin already in the table are never overwritten.
        try:
            from logic.service_catalog import seed_builtins as _seed_catalog
            # noinspection PyArgumentEqualDefault
            n_added = _seed_catalog(force=False)
            if n_added:
                print(f"[service_catalog] seeded {n_added} built-in templates")
        except (sqlite3.Error, ImportError, OSError) as e:
            print(f"[service_catalog] seed deferred: {e}")
