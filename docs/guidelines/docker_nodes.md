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
- **Update a compose stack** — when you record its compose-file path (see
  "Compose-level stack update" below).
- **Manage Swarm services** — when the node is a Swarm manager (see "Swarm
  manager nodes" below): see services with replica / health / update status and
  **Restart** them.
- Reach the daemon over **SSH** (default) **or TCP+TLS** (see "Transport").

## What you can't do (and why)

- **Update a compose stack WITHOUT a recorded compose-file path**: a raw Docker
  daemon stores no compose file, so OmniGrid can't infer it. Record the path (or
  update each container individually — the per-container "update" recreates it
  with a fresh pull). A stack with no recorded path shows an **"external"** chip
  and no "Update stack" button.
- **Swarm overlay / agent autoheal actions**: those are Portainer/Swarm-tooling
  specific and stay hidden for direct-Docker items (service restart IS available
  on a manager node).

## Transport (SSH or TCP+TLS)

Each node has a **Transport**:

- **SSH** (default, recommended) — OmniGrid opens an SSH channel to the daemon's
  UNIX socket. One credential covers both the node's SSH console and its Docker
  API, and it's the only transport that supports the compose-stack update (which
  needs to run `docker compose` on the node).
- **TCP + TLS** — OmniGrid talks to `tcp://host:2376` directly with a client
  certificate. Paste the daemon's **CA certificate**, a **client certificate**,
  and the **client private key** (PEM; the key is write-only — leave it blank to
  keep the stored one). Use this when the daemon already exposes a TLS socket
  (`dockerd --tlsverify`) and you'd rather not enable SSH. Container + service ops
  and stats all work over TLS; **compose-stack update does not** (no shell
  channel).

## Compose-level stack update

A standalone daemon has no stored compose file, so to update a `docker compose`
project OmniGrid needs to know **which file backs it**. In **Admin → Docker
Nodes**, under a node's **Compose projects**, add a row mapping the **project
name** (the `com.docker.compose.project` label — the stack name shown in the
Stacks view) to its **compose-file path on the node**. The stack then gets a
normal **"Update stack"** button that runs `docker compose -p <project> -f <path>
pull && up -d --remove-orphans` over SSH; the command output is captured in the
op log. (SSH transport only.)

## Swarm manager nodes

If a direct-Docker node is a **Swarm manager** (OmniGrid auto-detects this from
`docker info`), its **services** appear alongside its containers — with replica
counts, health, image-update status and placements — and **Restart** works on
them (it bumps the service's force-update counter, same as the Portainer path).
Running Swarm task containers are folded into their service. Only the manager's
own Node card is shown today (per-Swarm-node cards from the manager's node list
are a future enhancement).

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
- **Give the SSH account Docker-socket access — and note the builtin-group trap.**
  The socket is `srw-rw---- root docker`, so the SSH user must be in the `docker`
  group. On TrueNAS SCALE the `admin` user is **not** in it by default, and the
  UI **refuses** to add it (`docker` is a builtin sub-1000 system group →
  *"membership of this builtin group may not be altered"*). Work around it with a
  raw `usermod`, which the middleware validation doesn't block:
  1. Apply now (the `admin` account has sudo): `sudo usermod -aG docker admin`
  2. Make it survive reboots (TrueNAS regenerates `/etc/group` from its config
     DB): **System → Advanced → Init/Shutdown Scripts → Add**, Type **Command**,
     When **Post Init**, Command `usermod -aG docker admin`.
  3. Open a **new** SSH session (groups load at login) and confirm `id admin`
     lists `docker` and `docker version` prints the **Server** block.
  Caveat: editing the user in the TrueNAS UI later can drop the membership until
  the next boot re-runs the Post-Init script — just re-run the `usermod` line.
  (Alternatively, point the node at **root**, which owns the socket, if you have
  root SSH set up.)
- **Enable SSH socket forwarding — needs TWO directives.** OmniGrid reaches the
  daemon by asking the SSH server to forward a channel to `/var/run/docker.sock`.
  That requires **both** of these in the SSH **Auxiliary Parameters**:
  ```
  AllowTcpForwarding local
  AllowStreamLocalForwarding yes
  ```
  The gotcha: OpenSSH gates the socket (`direct-streamlocal`) forward behind the
  *general* port-forwarding permission, so **`AllowTcpForwarding no` blocks it
  even when `AllowStreamLocalForwarding yes` is set** — and hardened / appliance
  builds (including TrueNAS) ship `AllowTcpForwarding no` by default. `local` is
  the tightest value that works (client-initiated forwards only). After setting
  them, **restart the SSH service** and verify the *effective* config:
  ```
  sudo sshd -T | grep -iE 'allowtcpforwarding|streamlocal'
  # want: allowtcpforwarding local   +   allowstreamlocalforwarding yes
  ```
  Until both are set, the Test fails with *"couldn't open the Docker socket … the
  SSH server refused the socket forward"*.
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

## Stats fallback for Portainer-managed worker nodes

Separately from managing a Portainer-less node, the same SSH-to-the-daemon
transport can act as a **stats fallback** for nodes you manage through
**Portainer**. When Portainer's per-container stats call fails for a worker-node
container — the "Portainer agent unreachable" case that shows the amber
*Swarm agent unhealthy* banner — OmniGrid can SSH to that node's Docker daemon
and read the container's CPU / memory directly, so the bars stay populated.

This is **off by default** and gated twice:

1. The master toggle **Admin → Docker Nodes → "Use direct-Docker SSH as a stats
   fallback for Portainer worker nodes."**
2. Per node, the node's curated host in **Admin → Hosts** must have **SSH
   enabled** (the same per-host opt-in the SSH console uses) — that flag is the
   per-node authorisation on top of the master toggle.

For a Swarm node to be matched to its SSH credentials, its **hostname must match
a curated host** by `address`, `label`, `id`, or one of the provider-name
aliases. Credentials resolve through the same per-host → group → global ladder
the SSH console uses. The fallback only fetches stats for containers Portainer
*couldn't* — it never replaces a working Portainer stats call, and the
*Swarm agent unhealthy* banner still fires (the agent really is down; the
fallback just keeps the data visible).

## Troubleshooting

- **"couldn't open the Docker socket … the SSH server refused the socket
  forward" (a channel-open failure).** The SSH login worked, but the SSH server
  refused to forward a channel to the Docker socket. In order of likelihood:
  1. **Forwarding is disabled — check BOTH directives.** OmniGrid forwards a
     channel to the UNIX socket, which OpenSSH gates behind the *general*
     port-forwarding permission. So you need **both** `AllowStreamLocalForwarding
     yes` AND `AllowTcpForwarding` set to `local` (or `yes`) — `AllowTcpForwarding
     no` blocks the socket forward even with StreamLocal on, and hardened / NAS
     builds ship it `no`. Set both, reload SSH, and verify the *effective* config:
     `sudo sshd -T | grep -iE 'allowtcpforwarding|streamlocal'` (want
     `allowtcpforwarding local` + `allowstreamlocalforwarding yes` — NOT
     `allowtcpforwarding no`).
  2. **The SSH user can't access the socket.** It must be `root` or in the
     `docker` group (the socket is `srw-rw---- root docker`). Verify from a shell:
     `ssh <user>@<node> "docker version"` — if that prints the *Server* version,
     the socket is reachable. (On TrueNAS the UI won't let you add the builtin
     `docker` group to a user — see the TrueNAS SCALE section for the `usermod`
     workaround.)
  3. **Wrong socket path.** The default is `/var/run/docker.sock`; override it in
     the node's *Docker socket path* field only if your daemon uses a different
     path.
- **"SSH auth failed" / repeated cool-down.** Fix the credential (global key /
  password in Admin → SSH, or the per-node override) and wait out the 5-minute
  cool-down before re-testing.

## How it works

`logic/docker_direct.py` opens one SSH connection per gather / action (the
handshake cost is paid once), then each Docker API call is a cheap UNIX-domain
channel on that connection. A small self-contained HTTP/1.1 client speaks the
Docker Engine API over the channel. Items are tagged `backend="docker:<id>"` so
write-ops route to the direct client instead of Portainer. See
`logic/gather.py:merge_docker_nodes_into_cache` for the gather merge and
`logic/ops_extras.py` for the routed container ops.
