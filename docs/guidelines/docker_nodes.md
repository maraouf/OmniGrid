# Direct-Docker nodes (Portainer-less, over SSH)

OmniGrid normally reads and manages your fleet through **Portainer**. A node that
runs **plain Docker with no Portainer** can still be surfaced and managed: OmniGrid
opens an **SSH connection to the node's Docker socket** (`/var/run/docker.sock`)
and speaks the Docker Engine API straight over it. Its containers appear in the
**Stacks** and **Nodes** views alongside the Portainer fleet, tagged with a
**"Direct"** pill, and the per-container actions (restart / remove / update) work
the same way.

This is **additive** — Portainer stays the backend for your existing nodes; a
direct-Docker node is just a second backend. It also works as the **sole**
backend if you don't run Portainer at all.

## What you can do

- **See** every container grouped by its `docker compose` project, with a Node
  card showing CPU / memory / disk (Docker daemon footprint), engine version, OS,
  and container counts.
- **Restart** a container, **remove** a container, and **update** a container
  (recreate with a fresh image pull — same image ref, latest digest; volumes /
  networks / env / restart-policy are preserved).
- Live **CPU / memory / size** stats per container.

## What you can't do (and why)

- **Update a stack** (compose-level pull + up): a raw Docker daemon stores no
  compose file, so there is nothing for OmniGrid to re-deploy. Update each
  container individually instead (the per-container "update" recreates it with a
  fresh pull). The stack shows an **"external"** chip and no "Update stack" button.
- **Swarm service / agent / overlay actions**: not applicable to standalone
  Docker — those controls are hidden for direct-Docker items.

## Setup

1. **Enable SSH on the node** and make sure OmniGrid can reach it.
2. **Make sure the SSH user can read the Docker socket.** The user must be
   `root`, or be a member of the `docker` group (so `/var/run/docker.sock` is
   readable). Test from any shell: `ssh <user>@<node> "docker version"` — if that
   prints the server version, OmniGrid will work.
3. **Credentials**: OmniGrid reuses the **global SSH key / password** from
   **Admin → SSH** (the same credentials the SSH console uses). You can override
   the user / port / password per node in the editor. SSH key material stays
   global; only the user / port / password are per-node.
4. **Admin → Docker Nodes** → **Add Docker node**:
   - **Label** — a display name (e.g. `TrueNAS`).
   - **Address** — the node's host / IP.
   - **SSH user / port / password** — optional overrides (blank inherits Admin → SSH).
   - **Icon** — optional brand-icon slug (e.g. `truenas`, `truenas-scale`,
     `docker`, `server`) shown on the Node card.
   - **Docker socket path** — advanced; defaults to `/var/run/docker.sock`.
5. **Test connection** — confirms the SSH tunnel + Docker API (shows the Docker
   version on success).
6. **Save** — the node's containers appear within a gather cycle.

## TrueNAS SCALE specifics

TrueNAS SCALE (Electric Eel and later) runs **native Docker** for its apps, so the
socket is at the standard `/var/run/docker.sock`.

- **Enable SSH** under **Services → SSH** and use an account that can access the
  Docker socket. A root-capable / `docker`-group account is required to read the
  socket.
- **App containers are TrueNAS-middleware-managed.** Restarting one is safe.
  **Recreating** (the "update" action) a container that TrueNAS's app system
  manages may be **reverted or reconciled** by its middleware — prefer updating
  those apps through the TrueNAS UI. Containers you run yourself (raw
  `docker run` / a compose project you manage) recreate cleanly.
- Pick the `truenas` or `truenas-scale` **icon** so the Node card is recognisable.

## Tuning

- `DOCKER_DIRECT_TIMEOUT_SECONDS` (default `20`, Admin → Config → "Direct-Docker
  node timeout") — per-call wall-clock budget covering the SSH connect + the
  tunnel to the daemon socket + one Docker API request. Raise it on a high-latency
  link or a node with a slow daemon.
- A failed SSH auth backs off for the shared 5-minute SSH auth cool-down (the same
  one the SSH console uses), keyed per node — fix the credential and wait before
  retrying.

## How it works

`logic/docker_direct.py` opens one SSH connection per gather / action (the
handshake cost is paid once), then each Docker API call is a cheap UNIX-domain
channel on that connection. A small self-contained HTTP/1.1 client speaks the
Docker Engine API over the channel. Items are tagged `backend="docker:<id>"` so
write-ops route to the direct client instead of Portainer. See
`logic/gather.py:merge_docker_nodes_into_cache` for the gather merge and
`logic/ops_extras.py` for the routed container ops.
