# OmniGrid

A Portainer-native update dashboard for Docker Swarm clusters. Scans every service, cross-checks registry digests, and shows you **which stacks have updates available**. One click runs the same `Prune=true, PullImage=true` update flow the Portainer UI does — except you can do a whole cluster from one screen.

Built as a friendlier replacement for Diun Dash: Diun only **observes**; OmniGrid **acts**.

![screenshot placeholder — deploy it and see for yourself]

## Features

- **Three views**: stack-grouped (default) · flat services table · persistent update history
- **Digest-level detection** — compares your running `image@sha256:...` against the remote manifest, supports Docker Hub, GHCR, lscr.io, and any registry v2
- **Click-to-act** — Update Stack (prune+repull+redeploy), Recreate container, Restart service (no pull)
- **Bulk operations** — checkbox multi-select, dedupes by stack so one stack = one update call
- **Live operations panel** — watches running updates with a streaming event log, floats bottom-right
- **Update history** — every op persisted to SQLite, browseable with expandable event logs
- **Ignore list** — pin certain images or stacks to skip (e.g. pinned `:v2.1.0` tags you don't want bumped)
- **Apprise notifications** — success/failure push to your existing Apprise hub
- **Node placement & replica health** — see exactly which Swarm node each task runs on, in the detail drawer
- **Auto-refresh** (Off / 30s / 1m / 5m), global search (`/`), keyboard shortcuts
- **No Docker socket** — talks to Portainer via API only. Runs as a normal Swarm service.
- **No image build** — uses `python:3.12-slim` with your code bind-mounted from `/opt/portaupdate/app`

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
- **`/opt/portaupdate/data/portaupdate.db`** — SQLite for history, ignores, settings.

## Deploy

**1. Prep the host** (on the Swarm manager node):

```bash
sudo mkdir -p /opt/portaupdate/app /opt/portaupdate/data
sudo chown -R $USER:$USER /opt/portaupdate
```

**2. Copy the app files:**

```bash
scp main.py requirements.txt [email protected]:/opt/portaupdate/app/
scp -r static [email protected]:/opt/portaupdate/app/
```

**3. Create a Portainer API key**:
Portainer UI → profile menu → *My account* → *Access tokens* → add a new token. Give it admin scope (it needs to update any stack).

**4. Deploy the stack**:
Portainer → *Stacks* → *Add stack* → paste `docker-compose.yml`, fill in `PORTAINER_API_KEY` / `PORTAINER_URL` / `PORTAINER_ENDPOINT_ID` in the environment fields → Deploy.

**5. Point NPM at it**:
Proxy host: `omnigrid.w.oufa.co` (external) / `omnigrid.www.home.lan` (internal) → `http://<manager>:8088`. The app has its own local login + optional Authentik OIDC SSO; no reverse-proxy auth gymnastics required. See `notes/note_authentik.txt` to wire up SSO.

**6. Open it up**, hit ⚙️ Settings, configure:
- Apprise URL: e.g. `http://apprise.home.lan:8005/notify/OmniGrid` (or with a tag)
- Portainer public URL: e.g. `https://portainer.home.lan` (for the "Open in Portainer" deep links)

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
| `PORTAINER_URL` | — | Required. E.g. `https://portainer.home.lan` |
| `PORTAINER_API_KEY` | — | Required. Starts with `ptr_` |
| `PORTAINER_ENDPOINT_ID` | `1` | The Swarm endpoint id |
| `CACHE_TTL_SECONDS` | `900` | How long to trust a digest poll |
| `VERIFY_TLS` | `true` | Set `false` if your Portainer uses a cert not in the CA bundle |
| `REGISTRY_CONCURRENCY` | `8` | Parallel manifest requests |
| `DB_PATH` | `/app/data/portaupdate.db` | SQLite location |
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

Because the code is bind-mounted from `/opt/portaupdate/app`, you just:

```bash
# overwrite the files on the host
scp main.py static/index.html [email protected]:/opt/portaupdate/app/...

# redeploy the single service (no full stack update needed)
docker service update --force portaupdate_portaupdate
```

Or, of course, use OmniGrid itself to update… itself. Fun thought.
