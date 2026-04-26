# OmniGrid

A Portainer-native update dashboard for Docker Swarm clusters. Scans every service, cross-checks registry digests, and shows you **which stacks have updates available**. One click runs the same `Prune=true, PullImage=true` update flow the Portainer UI does — except you can do a whole cluster from one screen.

Built as a friendlier replacement for Diun Dash: Diun only **observes**; OmniGrid **acts**.

<!-- Screenshots live under `docs/screenshots/` — see the gallery below
     for the full set. The hero shot is the Nodes view (Stacks grouped
     by host node + live HOST CPU/MEM/DISK bars). -->
<p align="center">
  <img src="docs/screenshots/nodes-view.png" alt="OmniGrid Nodes view — stacks grouped by host with live CPU / Memory / Disk bars" width="960" />
</p>

## Features

- **Three views**: stack-grouped (default) · flat services table · persistent update history
- **Digest-level detection** — compares your running `image@sha256:...` against the remote manifest, supports Docker Hub, GHCR, lscr.io, and any registry v2
- **Click-to-act** — Update Stack (prune+repull+redeploy), Recreate container, Restart service (no pull)
- **Bulk operations** — checkbox multi-select, dedupes by stack so one stack = one update call
- **Live operations panel** — watches running updates with a streaming event log, floats bottom-right
- **Update history** — every op persisted to SQLite, browseable with expandable event logs
- **Ignore list** — pin certain images or stacks to skip (e.g. pinned `:v1.2.3` tags you don't want bumped)
- **Apprise notifications** — success/failure push to your existing Apprise hub
- **Node placement & replica health** — see exactly which Swarm node each task runs on, in the detail drawer
- **Auto-refresh** (Off / 30s / 1m / 5m), global search (`/`), keyboard shortcuts
- **No Docker socket** — talks to Portainer via API only. Runs as a normal Swarm service.
- **No image build** — uses `python:3.12-slim` with your code bind-mounted from `/opt/omnigrid/app`

## Architecture

```
┌───────────────┐       ┌──────────────┐       ┌──────────────┐
│   Browser     │──────▶│  OmniGrid │──────▶│  Portainer   │
│ (Alpine+Tail) │  REST │   (FastAPI)  │  REST │   (Swarm)    │
└───────────────┘       └──────┬───────┘       └──────────────┘
                               │
                               │ HEAD /v2/*/manifests/<tag>
                               ▼
                    ┌──────────────────────┐
                    │  Docker registries   │
                    │ (hub, ghcr, lscr, …) │
                    └──────────────────────┘
```

- **`main.py`** — single-file FastAPI backend. Aggregates data from Portainer (services, tasks, nodes, stacks, containers), resolves remote digests in parallel, runs background update jobs, persists history/ignores/settings to SQLite, fires Apprise webhooks.
- **`static/index.html`** — single-page Alpine.js + Tailwind UI.
- **`/opt/omnigrid/data/omnigrid.db`** — SQLite for history, ignores, settings.

## Deploy

**1. Prep the host** (on the Swarm manager node):

```bash
sudo mkdir -p /opt/omnigrid/app /opt/omnigrid/data
sudo chown -R $USER:$USER /opt/omnigrid
```

**2. Copy the app files:**

```bash
scp main.py requirements.txt [email protected]:/opt/omnigrid/app/
scp -r static [email protected]:/opt/omnigrid/app/
```

**3. Create a Portainer API key**:
Portainer UI → profile menu → *My account* → *Access tokens* → add a new token. Give it admin scope (it needs to update any stack).

**4. Deploy the stack**:
Portainer → *Stacks* → *Add stack* → paste `docker-compose.yml`, fill in `PORTAINER_API_KEY` / `PORTAINER_URL` / `PORTAINER_ENDPOINT_ID` in the environment fields → Deploy.

**5. Point NPM at it**:
Proxy host: `omnigrid.w.<asset-api-host>` (external) / `omnigrid.example.com` (internal) → `http://<manager>:8088`. The app has its own local login + optional Authentik OIDC SSO; no reverse-proxy auth gymnastics required. See `docs/guidelines/authentik.md` to wire up SSO.

**6. Open it up**, hit ⚙️ Settings, configure:
- Apprise URL: e.g. `http://apprise.example.com:8005/notify/OmniGrid` (or with a tag)
- Portainer public URL: e.g. `https://portainer.example.com` (for the "Open in Portainer" deep links)

## How updates work

| Item type | What happens on click |
|---|---|
| Service in a Portainer stack | `PUT /api/stacks/{id}?endpointId={eid}` with `{Prune:true, PullImage:true}` — identical to Portainer UI's "Update the stack + re-pull + prune" |
| Standalone compose container | `POST /api/docker/{eid}/containers/{id}/recreate?PullImage=true` |
| Swarm service without a Portainer-managed stack | Update button disabled. Use Restart (ForceUpdate bump) or redeploy via CLI. |
| Restart action (drawer) | Bumps `TaskTemplate.ForceUpdate` and calls `POST /services/{id}/update` — same image, fresh tasks |

## Environment variables

| Var | Default | Notes |
|---|---|---|
| `PORTAINER_URL` | — | Required. E.g. `https://portainer.example.com` |
| `PORTAINER_API_KEY` | — | Required. Starts with `ptr_` |
| `PORTAINER_ENDPOINT_ID` | `1` | The Swarm endpoint id |
| `CACHE_TTL_SECONDS` | `900` | How long to trust a digest poll |
| `VERIFY_TLS` | `true` | Set `false` if your Portainer uses a cert not in the CA bundle |
| `REGISTRY_CONCURRENCY` | `8` | Parallel manifest requests |
| `DB_PATH` | `/app/data/omnigrid.db` | SQLite location |
| `DOCKERHUB_USER` / `DOCKERHUB_TOKEN` | — | Optional. Bypass anonymous Hub rate limits |

## API (if you want to script it)

```
GET  /api/items                      all services+containers with status
POST /api/update/stack/{id}          → {op_id}
POST /api/update/container/{id}      → {op_id}
POST /api/restart/service/{id}       → {op_id}
GET  /api/ops                        list active+recent ops (in-memory, last 50)
GET  /api/ops/{op_id}                single op with event log
GET  /api/history?limit=100          persisted completed ops
DELETE /api/history                  clear history
GET  /api/ignores
POST /api/ignores                    body: {pattern, kind: "image"|"stack"}
DELETE /api/ignores/{pattern}
GET  /api/settings
POST /api/settings                   body: {apprise_url, apprise_tag, portainer_public_url}
POST /api/notify-test                fires a test Apprise notification
GET  /api/healthz
```

## Limitations

- **External stacks** (deployed via `docker stack deploy` CLI and then "discovered" by Portainer) have no compose file stored in Portainer → stack update returns HTTP 400. The Update button is disabled and the detail drawer explains this. Workaround: redeploy via CLI or use the Restart (no-pull) action.
- **No live Docker events.** The ops panel polls the in-memory event log from background tasks at 1.5s intervals — good enough for the "kicked off → succeeded / failed" loop, but not a real-time `docker events` stream.
- **Single-replica only.** Running multiple replicas would split the in-memory ops dict. Placement constraint pins it to one manager node.

## Updating OmniGrid itself

Because the code is bind-mounted from `/opt/omnigrid/app`, you just:

```bash
# overwrite the files on the host
scp main.py static/index.html [email protected]:/opt/omnigrid/app/...

# redeploy the single service (no full stack update needed)
docker service update --force omnigrid_omnigrid
```

Or, of course, use OmniGrid itself to update… itself. Fun thought.

## Documentation

- [`docs/README.md`](docs/README.md) — index of operator runbooks (auth, OIDC,
  deploy, env reference, scheduler, metrics, npm updates, Beszel agent setup).
- [`CHANGELOG.md`](CHANGELOG.md) — release notes per Keep a Changelog (root
  per convention so git hosts and packagers auto-detect it).
- [`docs/RELEASE_PROCESS.md`](docs/RELEASE_PROCESS.md) — SemVer cadence,
  PATCH auto-bump on deploy, periodic MINOR cuts, MAJOR breaking-change ritual.

## Screenshots

The Nodes view at the top of this README is the dashboard's most-used surface.
The full gallery lives under [`docs/screenshots/`](docs/screenshots/) — a quick
tour:

### Cluster overview

| | |
| --- | --- |
| ![Stacks](docs/screenshots/stacks-view-light.png) | **Stacks view** — grouped table, expand-per-stack, the default landing surface. |
| ![Services](docs/screenshots/services-view-light.png) | **Services view** — flat sortable list of every Swarm service. |
| ![Service detail](docs/screenshots/service-detail-drawer.png) | **Service detail drawer** — image / digest / actions (Restart / Recreate / Ignore). |
| ![Nodes](docs/screenshots/nodes-view.png) | **Nodes view** — stacks grouped by Swarm node with live HOST CPU / MEM / DISK / UPTIME bars. |
| ![History](docs/screenshots/history-audit-log.png) | **History (audit log)** — every operation persisted with filterable when / op / target columns. |

### Hosts

| | |
| --- | --- |
| ![Hosts (light)](docs/screenshots/hosts-view-light.png) | **Hosts view (light)** — curated host inventory grouped by `custom_number` ranges. |
| ![Hosts (dark)](docs/screenshots/hosts-view-dark.png) | **Hosts view (dark)** — same data, dark theme. |
| ![Hardware drawer](docs/screenshots/host-drawer-hardware.png) | **Host drawer — hardware** — vendor / model / serial / OS / kernel / network details. |
| ![Charts drawer](docs/screenshots/host-drawer-charts.png) | **Host drawer — charts** — CPU / Mem / Disk / Net In/Out / Load / Bandwidth time-series. |
| ![Charts drawer (bottom)](docs/screenshots/host-drawer-charts-bottom.png) | **Host drawer — bandwidth + swap** — scrolled view of the chart grid. |

### Admin / operations

| | |
| --- | --- |
| ![SSH run](docs/screenshots/host-drawer-ssh-run.png) | **Host drawer — SSH-run** — admin one-shot command runner with dry-run, destructive-pattern guard, full audit. |
| ![SSH terminal](docs/screenshots/host-drawer-ssh-terminal.png) | **Host drawer — SSH terminal** — interactive xterm.js session via WSS to the backend's asyncssh PTY. |
| ![Hosts editor](docs/screenshots/admin-hosts-editor.png) | **Admin → Hosts editor** — paginated curated-host CRUD with live discovery from each provider. |
| ![Schedules](docs/screenshots/admin-schedules.png) | **Admin → Schedules** — cron-like recurring jobs (gather refresh / prune / backup / asset refresh). |
| ![Backups](docs/screenshots/admin-backups.png) | **Admin → Backups** — DB + avatars snapshot zips with download / restore. |
| ![Profile](docs/screenshots/settings-profile.png) | **Settings → Profile** — account info, display name / email / avatar, password change. |
| ![Debug drawer](docs/screenshots/host-drawer-debug.png) | **Host drawer — debug** — raw provider-payload view (Beszel / Pulse / NE / Webmin) for troubleshooting empty rows. |
