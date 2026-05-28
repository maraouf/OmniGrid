# Beszel agent configuration

OmniGrid consumes host-level CPU / memory / disk / network stats from a
Beszel Hub (`logic/beszel.py`). The Hub aggregates data pushed by the
**Beszel agent** running on every target host. Two pieces have to be
right before charts light up in OmniGrid:

1. Each host runs a Beszel agent with matching `KEY`, `PORT`, and (for
   network stats) a `NICS=` env variable pointing at the real NIC.
2. The Hub knows about the host under a `name` / `host` that matches
   the `beszel_name` you set in **Admin → Hosts** on OmniGrid.

This runbook covers (1) — the `NICS=` piece specifically, since
"Net In / Net Out chart is flat at zero" is the most common support
ticket and it's always the same root cause.

## Local sample store

OmniGrid persists every Beszel-tracked host's stats to its own local
SQLite tables — same canonical pattern Pulse / Webmin / NE / SNMP /
Ping use. Two tables:

- `host_beszel_samples` — per-tick CPU / memory / disk / net rates
  PLUS chart-extras columns (`load_1m / 5m / 15m`, `swap_percent /
  used`, `bandwidth`, `containers`, `temperatures_json`, `gpus_json`).
  Written by the lifespan-managed `host_beszel_sampler` every
  `BESZEL_SAMPLE_INTERVAL_SECONDS` (0 = inherit
  `STATS_SAMPLE_INTERVAL_SECONDS`).
- `host_beszel_services` — per-(host, service) snapshot of every
  systemd unit the Beszel agent reports (state / sub_state /
  last_seen_ts / last_change_ts). UPSERT on every tick; rows whose
  `last_seen_ts` predates `STATS_HISTORY_DAYS` are pruned.

`/api/hosts/history` prefers the local table when the requested
window is covered; it falls back to a live PocketBase fetch only
when the local table doesn't yet reach the requested window
(fresh deploy, just-enabled provider).

`GET /api/hosts/{host_id}/beszel/services` (admin-only) returns
the full per-unit list for a host: `[{name, state, sub_state,
last_seen_ts, last_change_ts}, …]` with failed units first. Used
by the host-drawer per-service detail pane and the AI palette
context for service-related questions.

### Beszel section tunables

Three knobs live in **Admin → Providers → Beszel**:

| Env var | Default | Range | Purpose |
| --- | --- | --- | --- |
| `BESZEL_PROBE_TIMEOUT_SECONDS`        | `15`    | `1..120`  | Wall-clock timeout on each `probe_hub` (systems + system_stats + systemd_services). |
| `BESZEL_SAMPLE_INTERVAL_SECONDS`      | `0`     | `0..3600` | Sampler tick cadence. `0` = inherit `STATS_SAMPLE_INTERVAL_SECONDS`. |
| `BESZEL_FAILURE_PAUSE_ROUNDS`         | `5`     | `0..50`   | Per-(beszel, host) auto-pause threshold. `0` disables. Hub-fetch-OK gate so a global hub outage doesn't cascade-pause every host. |

Each can also be set via the matching DB key `tuning_beszel_*` from
Admin → Config; the DB value wins over the env var which wins over
the code default (the standard three-tier resolver).

---

## Symptom

In OmniGrid → Hosts → expand a host, the Net In and Net Out cards
show:

> **No NIC activity reported**
> The Beszel agent on this host isn't tracking any NIC. Add
> `NICS=eth0` (or your NIC name from `ip a`) to the agent's env,
> then restart it.

OR the `stats_row.nr` and `stats_row.ns` values in the debug panel
(Show debug data → Raw · Beszel) are both `0` across every sample.

That means the Beszel agent on that host is running but lacks the
`NICS=` env var, so it reports zero for every interface. OmniGrid
surfaces a node-exporter fallback automatically if node-exporter is
configured on that host — but for the Beszel numbers themselves to be
accurate, the agent needs to be told which NIC to read.

---

## Find the right interface name

Run the matching command on the target host.

### Linux / Debian / Ubuntu / RHEL

```bash
ip -o link show | awk -F': ' '{print $2}'
```

You'll see `lo`, `eth0`, `ens192`, `enp0s3`, `wlan0`, plus possibly
`docker0`, `br-<hex>`, `veth<hex>` (Docker internals — skip those).
Pick the physical interface carrying real traffic.

### FreeBSD / OPNsense

```bash
ifconfig -l
```

Returns `igb0 igb1 em0 lo0 …` — pick the WAN / LAN interface.

### Windows (Beszel Windows agent)

```powershell
Get-NetAdapter | Select-Object Name, InterfaceDescription, LinkSpeed
```

Use the `Name` field (e.g. `Ethernet`, `Wi-Fi`).

---

## Set `NICS=` on the agent

### Docker Compose (most homelabs)

Edit the agent's `docker-compose.yml`:

```yaml
services:
  beszel-agent:
    image: henrygd/beszel-agent:latest
    environment:
      - KEY=${BESZEL_KEY}       # existing — the hub's public key
      - PORT=45876               # existing — the agent's listen port
      - NICS=eth0                # NEW — comma-separated for multi-NIC
      # EXTRA_FILESYSTEMS=/mnt/data  # optional, for per-mount disk detail
    network_mode: host           # recommended — NICS needs the real iface
```

Then:

```bash
docker compose up -d beszel-agent
```

### Plain `docker run`

Stop and relaunch with the env var:

```bash
docker stop beszel-agent
docker rm beszel-agent
docker run -d --name beszel-agent --restart unless-stopped \
  --network host \
  -e KEY="$BESZEL_KEY" -e PORT=45876 -e NICS=eth0 \
  henrygd/beszel-agent:latest
```

### Systemd (bare-metal install)

**Auto-detect one-liner (recommended — no editor, no hardcoded NIC):**

Detects the interface carrying the default route and pins the agent
to it. Works on any single-NIC Debian / Ubuntu / RHEL host without
having to read `ip -o link show` first:

```bash
NIC=$(ip -o route show default | awk '{print $5}' | head -1) && \
echo "Detected NIC: $NIC" && \
sudo mkdir -p /etc/systemd/system/beszel-agent.service.d && \
printf '[Service]\nEnvironment="NICS=%s"\n' "$NIC" | sudo tee /etc/systemd/system/beszel-agent.service.d/override.conf && \
sudo systemctl daemon-reload && sudo systemctl restart beszel-agent
```

**Multi-NIC auto-detect** (joins every non-virtual, non-container UP
interface into `NICS=nic1,nic2,…`). Use this on bonded hosts, hosts
with a WireGuard tunnel you want tracked alongside the LAN NIC, or
dual-WAN boxes:

```bash
NIC=$(ip -o link show up | awk -F': ' '{print $2}' | grep -Ev '^(lo|docker|br-|veth|cni|cali|flannel|vmnet|tun)' | paste -sd, -) && \
echo "Detected NICs: $NIC" && \
sudo mkdir -p /etc/systemd/system/beszel-agent.service.d && \
printf '[Service]\nEnvironment="NICS=%s"\n' "$NIC" | sudo tee /etc/systemd/system/beszel-agent.service.d/override.conf && \
sudo systemctl daemon-reload && sudo systemctl restart beszel-agent
```

**Explicit one-liner** (if you already know the NIC name):

```bash
sudo mkdir -p /etc/systemd/system/beszel-agent.service.d && \
printf '[Service]\nEnvironment="NICS=eth0"\n' | sudo tee /etc/systemd/system/beszel-agent.service.d/override.conf && \
sudo systemctl daemon-reload && sudo systemctl restart beszel-agent
```

Replace `eth0` with your actual interface.

**Interactive alternative:**

```bash
sudo systemctl edit beszel-agent
```

Opens the same override drop-in in `$EDITOR`. Add:

```ini
[Service]
Environment="NICS=eth0"
```

Save + exit, then:

```bash
sudo systemctl daemon-reload
sudo systemctl restart beszel-agent
```

**Verify it stuck:**

```bash
sudo systemctl cat beszel-agent | grep NICS           # unit sees it
sudo systemctl show beszel-agent -p Environment       # process env has it
```

### FreeBSD / OPNsense (rc.d)

Add to the agent's rc.conf snippet (typically
`/etc/rc.conf.d/beszel_agent`):

```sh
beszel_agent_env="NICS=igb0 EXTRA_FILESYSTEMS=/mnt/data"
```

Then restart the service via OPNsense's service panel or:

```sh
service beszel_agent restart
```

### Windows service

Edit the service's environment via the Services MMC snap-in, or via
PowerShell:

```powershell
$svc = "beszel-agent"
[Environment]::SetEnvironmentVariable("NICS", "Ethernet", "Machine")
Restart-Service $svc
```

---

## Multiple NICs (VLAN / trunk / bond)

Comma-separated list — Beszel reports per-interface RX/TX and
aggregates the totals into `nr` / `ns`:

```
NICS=eth0,eth1,wg0
```

For bonded interfaces, point at the bond master (`bond0`) rather than
the slaves — otherwise you'll double-count.

---

## Verify it worked

After restarting the agent, wait ~60s for a new sample, then open
OmniGrid → Hosts → expand the host.

- **Net In / Net Out cards**: should show real bytes/s instead of the
  "No NIC activity reported" hint.
- **Admin → Hosts → Test providers button** (per-row): should flip
  Beszel's result detail from `mem=?/disk=?` to including `mem=X GB ·
  disk=Y GB` with real numbers.
- **Debug panel** (admin only, bottom of the drawer): open "Show
  debug data" → **Raw · Beszel → stats_row** — `nr` and `ns` should be
  non-zero after traffic.

If it's still flat at zero but OmniGrid's node-exporter is configured
for the same host, OmniGrid will auto-fill Net I/O from node-exporter
counters (sampler writes `host_net_samples` every 5 min; Beszel
history merges NE samples in when Beszel's numbers are all zero — see
`logic/host_net_sampler.py` + `logic/beszel.py:fetch_system_history`).

---

## Systemd service tracking (drawer "{N} services · X failed" badge)

The HARDWARE card in the host drawer shows a per-host service count
("12 services · 0 failed") sourced from Beszel's `systemd_services`
PocketBase collection. This is OPT-IN per agent — the agent must be
configured to monitor systemd units; if it isn't, the row hides
cleanly and there's nothing to fix at the OmniGrid end.

**Enable on the agent.** Beszel's agent docs change between
versions, but the typical knob is a comma-separated list of unit
names (or a glob) passed via env. Check the Beszel agent release
notes / repo for the exact variable name; recent versions read
`SYS_SERVICES` (or similar) at startup. Once the agent starts
emitting service records, OmniGrid's `_fetch_systemd_services`
(in `logic/beszel.py`) picks them up on the next gather.

Backend chain when the badge is missing:

- OmniGrid does THREE PocketBase fetches per gather: `systems` +
  `system_stats` + `systemd_services`. The third drives the badge.
- `_fetch_systemd_services` paginates via `?page=N&perPage=500` until
  `totalPages` is exhausted (cap: 20 pages = 10 000 records). PB's
  default `perPage` silently truncates without pagination.
- `_services_summary(records)` counts records and flags `state == 3`
  (systemd `failed` ActiveState) as failed. Other ActiveState values
  (`active`, `inactive`, `reloading`) are non-failed — intentionally
  stopped units don't pollute the count.
- Diagnostic log lines: `[beszel] systemd_services: N records across
  M systems` confirms the fetch worked; `attached to N/M hosts`
  confirms the per-host system_id → records grouping landed.

If the badge is missing on a host you expect to see it on:

1. Check Admin → Logs for the `[beszel] systemd_services` lines
   above. Zero records = agent isn't tracking; non-zero records but
   `attached to 0/N hosts` = system_id mismatch (look at the sample
   FK keys printed in the same log line).
2. Confirm the agent emits services in Beszel's own UI first — the
   per-system Services tab in the Hub renders the same data we read.

---

## Hosts API — split endpoints (2026-04-24)

`/api/hosts` is legacy (still works, polled by nothing in the SPA).
The frontend now uses two dedicated endpoints backed by a 10 s
module-level TTL cache in `main.py` that memoises Beszel+Pulse batch
maps so a burst of per-host calls doesn't re-probe the hubs:

- `GET /api/hosts/list` — skeleton only. Fast; no provider probes.
- `GET /api/hosts/one/{host_id}` — single merged host + status.

The SPA fans out `/api/hosts/one/{id}` across curated hosts with
concurrency=6 (`refreshHostRow(id)` in `static/js/app.js`). Helpers
shared between both endpoints: `_get_host_provider_state()`,
`_merge_one_host(h, state)`, `_shape_host_api_row(h, merged, providers)`.

Host status emitted by `_shape_host_api_row`: `up` / `paused` / `down`
/ `unknown` / `unconfigured` / `loading`. Frontend dot colours:
up=green, paused=amber, down|unreachable|unknown=red,
loading|unconfigured=grey. Rationale: a host with provider fields
mapped but no response = red (real outage); a host with NO provider
fields set = grey (nothing to probe).

## Webmin 2.x — three-tier fallback

`logic/webmin.py:_fetch_first_working` tries XML → JSON →
HTML-scrape (BeautifulSoup) in that order. Structured alternates fire
in PARALLEL via `asyncio.as_completed`; the first success cancels
stragglers; 401 / 403 short-circuits the fallback chain and arms the
auth cool-down (default 5 min, tunable via
`tuning_auth_failure_cooldown_seconds`). Per-request `httpx` timeout
is 6 s; the per-host probe is wrapped in
`asyncio.wait_for(..., timeout=tuning_webmin_probe_budget_seconds)`
(default 20 s) so a hung Miniserv can't bust NPM's `proxy_read_timeout`.
HTML scrapers: `_scrape_package_updates` / `_scrape_mounts` /
`_scrape_net` / `_scrape_system_status`. Dep: `beautifulsoup4`
(pinned in `requirements.txt`, lazy-imported).

---

## Common pitfalls

- **Agent running in a container without `--network host`** — Beszel
  sees only the container's eth0 (the Docker virtual NIC), not the
  host's real interface. Traffic numbers will be nonsense or zero.
  Use `network_mode: host` in compose or `--network host` in `docker
  run`.
- **NICS points at a non-existent interface** — agent silently reports
  zero. Always confirm the name with `ip -o link show` / `ifconfig -l`
  before setting.
- **Docker internal bridges included** — don't add `docker0` / `br-*`
  / `veth*` to `NICS=`; you'll be counting container → container
  traffic that never leaves the host. Stick to physical interfaces
  and wireguard / tailscale tunnels if relevant.
- **Beszel version too old** — `NICS=` support landed in newer agent
  releases. Pre-0.10 agents emit `b` (combined bandwidth) but not
  `nr`/`ns` separately. `logic/beszel.py` falls back to `b` and
  half-splits it for the In/Out charts when only `b` is present, so
  the chart still shows something — but the split is synthetic.
  Upgrade the agent if you want accurate directionality.
- **Beszel agent pushes but Hub rejects** — check Hub logs; the Hub
  refuses pushes with the wrong `KEY`. Operator fix is hub-side.
