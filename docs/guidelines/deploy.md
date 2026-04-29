# Deploy runbook — OmniGrid

Forgejo Actions deployment setup. **Image-build deploy** — the in-tree `Dockerfile` bakes
deps + source + static assets + `node_modules/` into an `omnigrid:<version>` image; the
pipeline builds on the Swarm manager itself and rolls the new tag in via `docker stack
deploy` + `docker service update --force`.

| Field           | Value                                                       |
| --------------- | ----------------------------------------------------------- |
| Workflow        | `.forgejo/workflows/deploy.yml`                             |
| Target          | `pi@docker.example.com:/opt/omnigrid/app` (build context)   |
| Runner          | `home-runner` on `git.example.com` (shared, INSTANCE scope) |
| Build           | `docker build --build-arg VERSION=<new> -t omnigrid:<new>` (on the manager) |
| Stack apply     | `docker stack deploy --resolve-image=always --compose-file docker-compose.yml omnigrid` |
| Force tag swap  | `docker service update --force --image omnigrid:<new> omnigrid_omnigrid` |

The deploy target is a Debian 13 VM (amd64, 16 GB / 100 GB) reachable at `pi@docker.example.com`.
The username `pi` is just a unix account name — it is NOT a Raspberry Pi.

Bind mounts on the host (production runtime — only stateful surfaces remain):

- `/opt/omnigrid/data` → `/app/data` (writable — SQLite DB, backups, avatars).
- `/opt/omnigrid/app/.env` → `/app/.env` (read-only — secrets / SESSION_SECRET / bootstrap admin).
- `/etc/ssl/certs` → `/etc/ssl/certs:ro` (host CA bundle for HTTPS calls to internal CAs).
- `/etc/localtime` → `/etc/localtime:ro` (libc timezone fallback).

`/opt/omnigrid/app` is now JUST the **build context** rsynced from the runner — no longer a
runtime bind mount. The image build reads it once via `COPY . /app` (filtered by `.dockerignore`)
and the running container has no view of the host directory thereafter.

The SQLite database lives at `/opt/omnigrid/data/omnigrid.db` on the host; inside the container
it's at `/app/data/omnigrid.db` (the `DB_PATH` default).

## 0. One runner, many repos (the preference for this homelab)

We deliberately do NOT create a second forgejo-runner. The existing `home-runner` service on
`git.example.com` is shared across projects. Only its registration scope matters:

| Scope     | Reach                                                                                        |
| --------- | -------------------------------------------------------------------------------------------- |
| Repo      | Usable by ONE repo only (the default in the original TelegramHomeBot setup notes).            |
| User/Org  | Usable by every repo you own.                                                                 |
| Instance  | Usable by every repo on Forgejo (admin-only). **Chosen.**                                     |

**Decision**: register at INSTANCE scope. One runner, one registration, every current and future
repo on this Forgejo instance can use it without any per-repo dance. Requires site-admin access
(you have it, it's your box).

If `home-runner` is currently registered at the repo level for another project (e.g.
TelegramHomeBot), re-register it at instance scope.

### Re-registration steps

1. **Remove the old repo-scoped entry FIRST** to avoid a name collision. The new instance-level
   runner reuses the same display name (`home-runner`) so every existing workflow's
   `runs-on: home-runner` keeps working with zero edits.

   Forgejo UI → TelegramHomeBot → Settings → Actions → Runners → delete `home-runner`.

2. **Get an INSTANCE-level token** in the Forgejo UI:

   Site Administration → Actions → Runners → "Create new Runner".

   | Field       | Value                                                                                                                                                                                                                                                             |
   | ----------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
   | Name        | `home-runner` (keep the same name).                                                                                                                                                                                                                                |
   | Description | Shared self-hosted runner on `git.example.com` (Debian 13, amd64). Instance-level; used by every repo on this Forgejo for rsync+ssh deploys to home-lab hosts (TelegramHomeBot → `automation.example.com`, OmniGrid → `docker.example.com`, etc). |

   Copy the entire `server:` YAML block.

   **Note on labels (Forgejo 15.x)**: the registration dialog no longer has a Labels field.
   Labels are declared by the runner daemon itself in `/opt/forgejo-runner/config.yml`, using
   the format `<name>:<runtime>`:

   ```yaml
   runner:
     labels:
       - self-hosted:host    # TelegramHomeBot match
       - home-runner:host    # OmniGrid match
       - linux:host
   ```

   `:host` = job runs on the runner box; `:docker://<img>` would run it inside a one-shot
   container. Our deploys need rsync + ssh keys, so every label is `:host`. In a workflow,
   `runs-on: home-runner` matches `home-runner:host` (runtime suffix implicit).

   (The user-level route is still available but we're NOT taking it for this homelab:
   `https://git.example.com/-/user/settings/actions/runners`.)

3. **Re-register on `git.example.com`**:

   ```bash
   sudo systemctl stop forgejo-runner
   sudo -iu forgejo-runner
   cd /opt/forgejo-runner
   rm -f .runner                           # old repo-scoped state
   ```

   Update `config.yml`: replace the entire `server:` block with the new one from the
   registration dialog (url, uuid, token). Also confirm the `runner.labels:` list contains:
   `home-runner`, `linux`, `amd64`. Reference template (already committed with the current
   values): `notes/forgejo_runner_config.yml`.

   Easiest: re-scp the template from the laptop after editing it there:

   ```bash
   scp notes/forgejo_runner_config.yml \
       forgejo-runner@git.example.com:/opt/forgejo-runner/config.yml
   ```

   Otherwise edit in place:

   ```bash
   nano config.yml

   # expect: "declared successfully" + "[poller] launched"
   ./forgejo-runner daemon --config config.yml & sleep 3; kill %1

   exit                                    # IMPORTANT: back to the sudoer
                                           # shell (pi) — forgejo-runner is
                                           # not in sudoers, so systemctl
                                           # here will ask for a password
                                           # that doesn't exist.
   sudo systemctl start forgejo-runner
   sudo systemctl status forgejo-runner --no-pager
   ```

4. In the old repo's Settings → Actions → Runners, delete the now-stale repo-scoped runner
   entry. The shared one shows up as "inherited from user/instance" on every repo.

**Runner capacity**: keep `runner.capacity: 1` in `config.yml` for homelab workloads. Two queued
deploys are fine; actually parallel deploys to different hosts rarely matter.

## Deploy SSH keys — one per target host

Both original deploy keys (for `automation.example.com` and `docker.example.com`) were `shred`'d on
disk right after being pasted into their Forgejo secrets. We now rebuild with cleaner isolation:
one key per target, private half living only inside that project's `DEPLOY_SSH_KEY` secret.

| Key                          | Target                     | Project         |
| ---------------------------- | -------------------------- | --------------- |
| `forgejo_deploy_docker`      | `pi@docker.example.com`       | OmniGrid        |
| `forgejo_deploy_automation`  | `pi@automation.example.com`   | TelegramHomeBot |

If one project's key leaks, rotating it doesn't touch the other.

Both halves below are REQUIRED — section A gets OmniGrid working, section B rebuilds
TelegramHomeBot's deploy auth from the same runner box. The sequence is identical; only the key
filename and target host differ.

### A) OmniGrid key (required now)

```bash
# On git.example.com, as forgejo-runner:
sudo -iu forgejo-runner
cd /opt/forgejo-runner
mkdir -p .ssh && chmod 700 .ssh

# 1) Generate
ssh-keygen -t ed25519 -N '' -C 'forgejo-deploy-docker' \
    -f .ssh/forgejo_deploy_docker

# 2) Install public half on docker.example.com. Prompts for pi's password
#    (or falls through whatever SSH auth you already have to that box).
HOME=/opt/forgejo-runner ssh-copy-id \
    -i .ssh/forgejo_deploy_docker.pub pi@docker.example.com

# 3) Sanity-check key-only login works
ssh -i .ssh/forgejo_deploy_docker \
    -o PasswordAuthentication=no -o PreferredAuthentications=publickey \
    pi@docker.example.com 'echo OK'

# 4) Capture the private key — paste this entire blob (incl. the
#    BEGIN/END lines) into the OmniGrid DEPLOY_SSH_KEY secret
#    in the next section BEFORE running step 5.
cat .ssh/forgejo_deploy_docker

# 5) Once saved to Forgejo, wipe the on-disk private copy. Public half
#    (.pub) stays for reference.
shred -u .ssh/forgejo_deploy_docker
exit
```

### B) TelegramHomeBot key rotation

Required — the original private key for `automation.example.com` was also `shred`'d and is no
longer recoverable from disk, same situation as the docker key above. Same five-step sequence;
different key + target.

```bash
# On git.example.com, as forgejo-runner:
sudo -iu forgejo-runner
cd /opt/forgejo-runner
mkdir -p .ssh && chmod 700 .ssh

# 1) Generate
ssh-keygen -t ed25519 -N '' -C 'forgejo-deploy-automation' \
    -f .ssh/forgejo_deploy_automation

# 2) Install public half on automation.example.com (prompts for pi's
#    password or uses whatever SSH auth you already have to that box).
HOME=/opt/forgejo-runner ssh-copy-id \
    -i .ssh/forgejo_deploy_automation.pub pi@automation.example.com

# 3) Sanity-check key-only login works
ssh -i .ssh/forgejo_deploy_automation \
    -o PasswordAuthentication=no -o PreferredAuthentications=publickey \
    pi@automation.example.com 'echo OK'

# 4) Capture the private key — paste this entire blob into the
#    TelegramHomeBot repo's DEPLOY_SSH_KEY secret
#    (Forgejo UI -> <owner>/TelegramHomeBot -> Settings -> Actions
#     -> Secrets -> DEPLOY_SSH_KEY -> edit/replace value) BEFORE step 5.
cat .ssh/forgejo_deploy_automation

# 5) Wipe the on-disk private copy once Forgejo has it.
shred -u .ssh/forgejo_deploy_automation

# 6) OPTIONAL: prune the stale authorized_keys entry from the OLD
#    deploy key (it was tagged 'forgejo-deploy' in its comment, the
#    new one is 'forgejo-deploy-automation'). Only do this AFTER
#    confirming step 3 works and the TelegramHomeBot deploy pipeline
#    runs green with the new secret:
# ssh pi@automation.example.com \
#     "sed -i '/ forgejo-deploy$/d' ~/.ssh/authorized_keys"

exit
```

### Rule of thumb

Every `DEPLOY_SSH_KEY` secret stays at REPO scope (OmniGrid, TelegramHomeBot). Do NOT promote
either key to user scope — that re-couples the two projects.

## 1. Forgejo secrets & variables

**Prerequisites** (from the [Deploy SSH keys](#deploy-ssh-keys--one-per-target-host) section
above):

- Section A completed: `forgejo_deploy_docker` installed on `docker.example.com`, the key-only
  sanity check printed `OK`. The private key must still be on disk (don't `shred -u` it until
  [step 1.6](#16-shred-the-on-disk-private-keys)).
- Section B completed: same for `forgejo_deploy_automation`, needed if you're also rotating
  TelegramHomeBot's secret ([step 1.4](#14-rotate-telegramhomebots-secret-optional)).

### Reading the private key

Two ways — the private file lives under the `forgejo-runner` user, NOT `pi`, so `cat`'ing it
from the pi shell fails with "No such file or directory":

```bash
# Option 1 — stay as pi and read with sudo:
sudo cat /opt/forgejo-runner/.ssh/forgejo_deploy_docker
sudo cat /opt/forgejo-runner/.ssh/forgejo_deploy_automation

# Option 2 — switch into the forgejo-runner session first:
sudo -iu forgejo-runner
cat /opt/forgejo-runner/.ssh/forgejo_deploy_docker
cat /opt/forgejo-runner/.ssh/forgejo_deploy_automation
exit
```

Copy each blob (BEGIN/END lines inclusive) into the corresponding Forgejo secret in the steps
below.

### Why we set these

The `deploy.yml` workflow reads:

```yaml
${{ secrets.DEPLOY_SSH_KEY }}
${{ vars.DEPLOY_HOST   || 'docker.example.com' }}
${{ vars.DEPLOY_USER   || 'pi' }}
${{ vars.DEPLOY_PATH   || '/opt/omnigrid/app' }}
${{ vars.SERVICE_NAME  || '' }}
```

The secret is required — without it the Configure SSH step fails immediately. The variables are
optional (the `|| default` fallbacks are the production values). Setting them anyway is worth it
because (a) you can change the target host/path without editing the workflow, and (b) the
Forgejo UI shows the live configuration for whoever looks.

### Scope

All of these go at REPO scope on OmniGrid. Do NOT promote to user scope — we're keeping
per-host keys isolated (see the SSH keys section). TelegramHomeBot's own `DEPLOY_*` entries are
untouched; they live on that repo and the TelegramHomeBot workflow still needs them.

### 1.1 Add the `DEPLOY_SSH_KEY` secret

UI path:

- Browser → `https://git.example.com/<owner>/OmniGrid`.
- Top tab → Settings.
- Left nav → Actions → Secrets.
- Button → "Add Secret" (or "Create new secret").

Fill in:

- **Name**: `DEPLOY_SSH_KEY`.
- **Value**: paste the complete output of `cat .ssh/forgejo_deploy_docker` from section A.

The pasted blob MUST include:

```
-----BEGIN OPENSSH PRIVATE KEY-----
(base64 body, several lines)
-----END OPENSSH PRIVATE KEY-----
<trailing newline>
```

Click "Add Secret". Forgejo masks the value immediately after save — you cannot view it again.
If you screwed up the paste, just "Edit" the secret (Forgejo lets you overwrite the value even
when it can't read it back) or regenerate the key from section A.

### 1.2 Add the OmniGrid variables

Same UI, one nav click over:

- Settings → Actions → Variables.
- Button → "Add Variable".

Create each variable:

| Name          | Value                  |
| ------------- | ---------------------- |
| `DEPLOY_HOST` | `docker.example.com`      |
| `DEPLOY_USER` | `pi`                   |
| `DEPLOY_PATH` | `/opt/omnigrid/app`    |

`SERVICE_NAME` is intentionally omitted — the workflow auto-discovers the service by querying
`docker service ls --filter label=com.docker.stack.namespace=<stack>` on the remote, where
`<stack>` = `basename(dirname(DEPLOY_PATH))`. For `DEPLOY_PATH=/opt/omnigrid/app` that's
`omnigrid`, and Swarm returns the single service in that stack (`omnigrid_omnigrid`).

Only set `SERVICE_NAME` explicitly if:

- The stack contains more than one service (auto-discover errors out and tells you to set it).
- Your stack name differs from the parent directory of `DEPLOY_PATH`.

Variables ARE viewable and editable post-save (no masking). Names are case-sensitive and must
match the workflow exactly.

### 1.3 Verify the secrets / variables pane

Secrets tab should list:

- `DEPLOY_SSH_KEY` (added `<timestamp>`)

Variables tab should list:

- `DEPLOY_HOST` → `docker.example.com`
- `DEPLOY_USER` → `pi`
- `DEPLOY_PATH` → `/opt/omnigrid/app`
- (`SERVICE_NAME`) not set — dynamically resolved at run time.

Optional variables for notifications / probes (defaults baked in the workflow already point at
your home-lab, so skip unless you want to override):

| Name          | Default                                             |
| ------------- | --------------------------------------------------- |
| `APPRISE_URL` | `http://apprise.example.com:8005/notify/apprise`       |
| `APPRISE_TAG` | `omnigrid`                                          |
| `PROBE_URL`   | `http://docker.example.com:9500/api/healthz`           |

If any required name is missing or misspelled, fix it before running the workflow — a typo'd
`DEPLOY_USER` would fall through to the `|| 'pi'` default silently, but a typo'd
`DEPLOY_SSH_KEY` name = no key at all and a broken SSH step.

### 1.4 Rotate TelegramHomeBot's secret (optional)

Only if you ran section B above:

- Browser → `https://git.example.com/<owner>/TelegramHomeBot`.
- Settings → Actions → Secrets.
- `DEPLOY_SSH_KEY` → "Edit".
  - **Value**: paste output of `cat .ssh/forgejo_deploy_automation` from section B.
- Save.

TelegramHomeBot's `DEPLOY_HOST` / `DEPLOY_USER` / `DEPLOY_PATH` variables stay as they are —
only the secret changes.

### 1.5 Housekeeping (optional)

If you previously had `DEPLOY_SSH_KEY` or other entries at USER scope (from an earlier plan that
promoted them), DELETE those now:

- `git.example.com` → avatar → Settings → Actions → Secrets/Variables.
- Delete any leftover `DEPLOY_*` entries at this scope.

Repo-scoped entries take precedence anyway, but removing the user-scope copies avoids ambiguity
about which key is really in use.

### 1.6 Shred the on-disk private keys

Only once steps 1.1 and 1.4 are done — if you do this too early you lose the private key
forever and have to regenerate both from scratch.

```bash
# On git.example.com, as forgejo-runner:
sudo -iu forgejo-runner
cd /opt/forgejo-runner
shred -u .ssh/forgejo_deploy_docker     2>/dev/null || true
shred -u .ssh/forgejo_deploy_automation 2>/dev/null || true
ls .ssh/                                 # only *.pub remain
exit
```

(If you already ran `shred -u` in section A step 5 / section B step 5, this is a no-op and just
confirms the .pub-only state.)

## 2. Target host (`docker.example.com`) — one-time

### 2.1 Check what's already installed

Skip the install line for anything that's present:

```bash
ssh pi@docker.example.com '
    for p in rsync docker ssh sudo; do
        if command -v "$p" >/dev/null; then
            printf "%-8s  OK    (%s)\n" "$p" "$(command -v $p)"
        else
            printf "%-8s  MISSING\n" "$p"
        fi
    done
    echo "---"
    echo -n "pi in docker group? "
    id pi | grep -q "(docker)" && echo yes || echo no
    echo -n "/opt/omnigrid/app exists? "
    [ -d /opt/omnigrid/app ] && echo yes || echo no
    echo -n "/opt/omnigrid/app owner: "; stat -c "%U:%G" /opt/omnigrid/app 2>/dev/null || echo "(dir missing)"
'
```

Typical result on a Swarm manager: `rsync` MISSING, `docker` + `ssh` + `sudo` present,
`/opt/omnigrid/app` exists (because docker-compose bind-mounts it), ownership is `root:root`.

### 2.2 Install rsync (and anything else flagged missing)

```bash
ssh pi@docker.example.com 'sudo apt update && sudo apt install -y rsync'

# Add more packages to the install line as needed:
#   ssh pi@docker.example.com 'sudo apt install -y rsync curl ca-certificates'
```

### 2.3 Directory ownership

`pi` must own `/opt/omnigrid/app` so rsync over ssh can write to it. `/opt/omnigrid/data`
(SQLite store) has the same requirement so the running container can update it:

```bash
ssh pi@docker.example.com '
    sudo mkdir -p /opt/omnigrid/app /opt/omnigrid/data &&
    sudo chown -R pi:pi /opt/omnigrid &&
    ls -la /opt/omnigrid
'
```

### 2.4 Docker permissions for the pi user

The image-build pipeline now needs `pi` to run `docker build`, `docker stack deploy`,
`docker service ls/ps/update`, and `docker image prune` non-interactively. The narrow
sudoers approach from the old bind-mount deploy (one whitelisted `service update` line)
no longer scales — go with **Option A**.

**Option A — add pi to docker group** (recommended):

```bash
ssh pi@docker.example.com 'sudo usermod -aG docker pi'
# pi must log out and back in for the new group to take effect.
# After re-login, sanity-check every command the pipeline will run:
ssh pi@docker.example.com '
  docker info >/dev/null && echo "info OK" &&
  docker build --help >/dev/null && echo "build OK" &&
  docker stack ls >/dev/null && echo "stack OK" &&
  docker service ls >/dev/null && echo "service OK"
'
```

**Option B — passwordless sudo for the full set** (if your security policy forbids
group membership): whitelist every command the pipeline runs. Keep the list narrow
to the `pi` user and just these subcommands:

```bash
ssh pi@docker.example.com "
    sudo tee /etc/sudoers.d/forgejo-omnigrid > /dev/null <<'EOF'
pi ALL=(root) NOPASSWD: /usr/bin/docker build *
pi ALL=(root) NOPASSWD: /usr/bin/docker stack deploy *
pi ALL=(root) NOPASSWD: /usr/bin/docker service ls *
pi ALL=(root) NOPASSWD: /usr/bin/docker service ps *
pi ALL=(root) NOPASSWD: /usr/bin/docker service update *
pi ALL=(root) NOPASSWD: /usr/bin/docker image prune *
pi ALL=(root) NOPASSWD: /usr/bin/docker info
EOF
    sudo chmod 440 /etc/sudoers.d/forgejo-omnigrid &&
    sudo visudo -cf /etc/sudoers.d/forgejo-omnigrid && echo OK
"
```

If you pick option B, prefix every `docker` invocation in the deploy workflow's
heredoc with `sudo -n` instead of running bare `docker`.

### 2.5 Sanity: `/opt/omnigrid/data`

Holds the SQLite db (`omnigrid.db` — history / ignores / settings / users / sessions /
`stats_samples` / schedules). The workflow's `--exclude 'data/'` prevents `rsync --delete` from
wiping it. Confirm it's present and owned by pi:

```bash
ssh pi@docker.example.com 'ls -la /opt/omnigrid/data 2>/dev/null || echo "NOT CREATED YET — will be made on first run"'
```

### 2.6 Sanity: `/opt/omnigrid/app/.env`

The image build deliberately does NOT bake `.env` (it's in `.dockerignore`); secrets ride a
per-file bind mount in `docker-compose.yml`. Pre-create the file BEFORE the first deploy or
the container will start without `SESSION_SECRET` / `BOOTSTRAP_ADMIN_*` and you'll be stuck
on the login page with no way in.

```bash
ssh pi@docker.example.com '
  test -f /opt/omnigrid/app/.env && echo "OK ($(stat -c %a /opt/omnigrid/app/.env))" \
    || echo "MISSING — copy your secrets file there before the first deploy"
'
```

Copy it from your dev machine over SSH:

```bash
scp .env pi@docker.example.com:/opt/omnigrid/app/.env
ssh pi@docker.example.com 'chmod 600 /opt/omnigrid/app/.env'
```

The compose bind is `:ro`, so the container can't modify the file (intentional — secrets
should only flow operator → host, never the other way).

## 3. First run

Either:

- Push a commit to `main` (any change, even a whitespace nudge), OR
- In Forgejo: OmniGrid → Actions → "Deploy to Swarm" → Run workflow (requires the
  `workflow_dispatch` trigger, already present in `deploy.yml`).

Watch the run. Expected behaviour:

1. **Checkout (manual SHA-256)** — green, < 5 s. Forces `git init --object-format=sha256`
   because actions/checkout@v4 still defaults to SHA-1 and would fail against the SHA-256
   server.
2. **Configure SSH** — green, writes `deploy_key` and `ssh-keyscan`.
3. **Rsync source to target build context** — green. Same exclude list as `.dockerignore` so
   the build context the runner ships matches what `docker build` will read.
4. **Build image, deploy stack, force update, verify** — single SSH heredoc that:
   1. Confirms `docker info` reports `Swarm.LocalNodeState == active`.
   2. Reads previous version from `/api/version`, increments PATCH, computes `NEW`.
   3. Runs `docker build --pull --build-arg VERSION=$NEW -t omnigrid:latest -t omnigrid:$NEW .`
      (cold builds on the Pi take ~3–8 min; layer-cached static-only iterations ~10–30 s).
   4. `docker stack deploy --resolve-image=always --compose-file docker-compose.yml omnigrid`.
   5. Resolves the service name (auto-discovered via `label=com.docker.stack.namespace=omnigrid`).
   6. `docker service update --force --image omnigrid:$NEW <service>`. The version-tagged
      image is pinned in Swarm's task spec so a manual rollback has a discrete tag to point at.
   7. Polls `docker service ps ... --format {{.CurrentState}}` for up to 60 s; fails if any
      task is Failed / Rejected / Shutdown.
   8. Curls `$PROBE_URL` from the manager; must return 200 within ~30 s. Catches ingress / NPM
      breakage that the in-container healthcheck can't see.
   9. Curls `/api/version` and asserts it equals the freshly built tag. Fails loudly (and
      notifies) if Swarm rolled back or kept the old tag for any reason.
   10. `docker image prune -f` removes dangling untagged layers (does NOT touch tagged
       version images — manual rollback targets stay intact).
5. **Cleanup SSH key** — always runs, removes `deploy_key`.
6. **Apprise notifications** — success on green, failure on any red step.

Total pipeline: cold first build ~3–8 min on a Pi, ~30 s thereafter (layer cache holds for
deps + static assets). Service downtime during `--force` rolling update: ~5–15 s (single
replica rolling restart with `order: start-first`).

## Image-build deploy — every push rebuilds

Unlike the previous bind-mount deploy, EVERY successful rsync triggers a full image rebuild +
stack deploy. There is no static-only short-circuit because:

- The image is the deployment unit; you can't ship "just `static/`" without rebuilding.
- Docker's layer cache makes static-only iterations fast: only the `COPY . /app` layer
  invalidates — the deps install + base image are reused.
- `?v=<APP_VERSION>` cache-busting on asset URLs needs the version to bump on every push,
  which the auto-PATCH path provides.

Manual rollback: SSH to the manager and re-tag a previous version onto the running service:

```bash
ssh pi@docker.example.com '
  docker image ls omnigrid --format "{{.Tag}}" | sort -V | tail -10   # see what is available
  docker service update --force --image omnigrid:1.0.42 omnigrid_omnigrid
'
```

Swarm honours the `start-first` + `failure_action: rollback` update_config — if the rolled-back
image fails its healthcheck, Swarm restores the previous one automatically.

## VERSION.txt bump model (SemVer MAJOR.MINOR.PATCH)

Version is **baked into the image at build time**, not bind-mounted. The Dockerfile has:

```dockerfile
ARG VERSION=0.0.0-dev
RUN echo "$VERSION" > /app/VERSION.txt
LABEL org.opencontainers.image.version="$VERSION"
```

The pipeline overrides `ARG VERSION` via `--build-arg`, so the file inside the image always
reflects the version that was built. `main.py:_read_version()` reads `/app/VERSION.txt` from
inside the container at startup; a local `docker build` with no `--build-arg` produces an
image whose `/api/version` shows `0.0.0-dev` (visible signal).

The repo-root `VERSION.txt` is no longer the runtime source of truth — it remains as a
dev-time hint for IDEs and `_read_version()`'s repo-fallback path, but the deploy pipeline
never reads or writes it.

See `docs/RELEASE_PROCESS.md` for the full operator runbook. Quick summary:

- **MAJOR** — operator-controlled. Reserved for breaking changes. Resets MINOR + PATCH to 0.
  Cut by hand-building an `omnigrid:<X.0.0>` tag on the manager and `docker service update
  --image omnigrid:<X.0.0> omnigrid_omnigrid`. The next CI deploy reads `/api/version=X.0.0`
  and increments PATCH → `X.0.1`.
- **MINOR** — operator-controlled, periodic. When a batch of PATCH-shipped items feels
  release-worthy, the operator runs a manual `docker build --build-arg VERSION=<X.Y.0>`
  + `service update --image omnigrid:<X.Y.0>` on the manager (or pushes a one-off tagged
  image). The next CI deploy increments PATCH → `X.Y.1`. CI never touches MINOR autonomously.
- **PATCH** — CI-controlled, automatic. Every successful CI deploy resolves the previous
  version from THREE sources and takes the highest semver, then increments PATCH by 1 and
  passes it to `docker build --build-arg VERSION=$NEW`. MAJOR + MINOR are preserved. The
  three sources, in order of authority:
    1. **Live `/api/version`** on the running service — most authoritative when reachable.
    2. **`VERSION.txt` from the rsynced build context** (`/opt/omnigrid/app/VERSION.txt`) —
       file-grounded floor; survives a brief outage of the live service. Operator can also
       hand-edit this in the repo to SEED a MAJOR/MINOR bump (the next CI deploy will read
       it as the new floor and bump from there).
    3. **Highest existing `omnigrid:<X.Y.Z>` tag in the local image registry** on the
       manager — covers post-rollback scenarios where you've manually swapped to an older
       tag; the registry still knows the highest version that ever shipped, so the next CI
       deploy won't accidentally re-issue an existing tag.
  Pipeline log line `[deploy] version sources: live='X.Y.Z' file='X.Y.Z' image='X.Y.Z' →
  resolved=X.Y.Z` shows exactly which source won and what the bump computes from.
- The post-deploy "Verify deployed version matches" step asserts `/api/version` equals what
  was just built. Catches the situation where the build succeeded but `service update --force
  --image omnigrid:<new>` rolled the wrong tag, or the new task failed health and Swarm rolled
  back. Mismatch → ❌ Apprise ping.
- **First-deploy bootstrap**: if `/api/version` is unreachable (no prior deployment) or
  returns `0.0.0-dev` (image built without `--build-arg VERSION`), the pipeline seeds at
  `1.0.0` then increments to `1.0.1`.
- **Legacy migrations** are handled inline in deploy.yml's bump step:
    - 3-part `2.x.y` (an older flavour we briefly used) one-shot collapses to `1.0.<counter>`
      — the counter keeps moving forward rather than resetting.
    - 2-part `M.N` (the brief MAJOR.MINOR-only experiment) backfills PATCH=0 then bumps to
      `M.N.1`.

The old Admin → Version UI was a direct VERSION.txt editor that worked because of a writable
per-file bind mount on the host. Under the image-build model that file lives INSIDE the image
and isn't writable from inside the container; the equivalent operator workflow is to build a
new image with the desired `--build-arg VERSION=...` and force-update the service onto it.

## Apply Swarm update-config without redeploying the stack

`docker-compose.yml` has been updated with a zero-downtime rolling-update config
(`parallelism=1`, `delay=0s`, `order=start-first`, `failure_action=rollback`, `monitor=30s`).
Portainer holds the live stack definition in its own DB, so changing the compose file in the
repo doesn't propagate until the stack is redeployed.

Quick one-shot to apply the same settings to the running service without touching Portainer:

```bash
ssh pi@docker.example.com "
  docker service update \
    --update-parallelism 1 \
    --update-delay 0s \
    --update-order start-first \
    --update-failure-action rollback \
    --update-monitor 30s \
    --rollback-parallelism 1 \
    --rollback-delay 0s \
    --rollback-order start-first \
    omnigrid_omnigrid
"
```

Confirm it stuck:

```bash
ssh pi@docker.example.com \
  "docker service inspect omnigrid_omnigrid \
     --format '{{json .Spec.UpdateConfig}}'" | jq
```

From here on the CI deploy's "Force Swarm service update" step will roll start-first (new task
healthy before old one stops), typically cutting the step from ~42 s down to roughly one
healthcheck warmup (~10–15 s).

## Reverse-proxy timeouts — `/api/hosts/one/{id}` budget vs NPM `proxy_read_timeout`

If you see HTTP 504s on the Hosts view (gateway timeout, generic NPM error page rather than
OmniGrid's own JSON `{"detail": "per-host probe budget exceeded (30s) for <id>"}`), the
upstream proxy is timing out before OmniGrid gets a chance to surface its own actionable error.

Contract (#506):

- OmniGrid wraps each `/api/hosts/one/{id}` call in `asyncio.wait_for(timeout=30.0)`. If the
  inner probe sequence (single-flight Beszel + Pulse hub + per-host NE / Webmin) runs past
  30s, OmniGrid returns `504` with a `detail` string identifying the host.
- Nginx Proxy Manager's default `proxy_read_timeout` is **60 seconds**, leaving 30s of
  headroom — OmniGrid's 504 should always fire first.
- If your proxy is set lower than 30s, the operator sees NPM's generic `504 Gateway Time-out`
  page instead of OmniGrid's identifying detail, and `/api/events` SSE connections die after
  the same window.

If your proxy enforces a shorter read timeout (Cloudflare, some load-balancer defaults, very
strict NPM tweaks), raise the per-route timeout to at least 35 seconds:

```nginx
# In the OmniGrid host's NPM "Advanced" tab, or your nginx server block:
proxy_read_timeout 60s;
proxy_send_timeout 60s;
```

Other concurrency knobs that affect this surface:

- `HOSTS_PARALLEL_FETCH` (default 6, range 1–32; #508) caps the SPA's fan-out concurrency on
  `/api/hosts/one/{id}`. Lower if the upstream pool is thin or slow Webmin / NE probes saturate
  the loop. Edit live from Admin → Config — no restart needed.
- The single-flight `_host_provider_lock` collapses parallel cold-cache hub probes into one
  shared call, so raising `HOSTS_PARALLEL_FETCH` doesn't multiply the Beszel / Pulse load.
- `_webmin_host_fail_cache` caches Webmin probe FAILURES for 5s so an unreachable Miniserv
  short-circuits the 20s timeout for the rest of a fan-out burst, with recovery felt within
  one refresh cycle.

## Cleanup — delete failed run history

UI sometimes has no delete button. Use the API:

```bash
FJ=https://git.example.com
OWNER=<owner>
REPO=OmniGrid
TOK=<token from User Settings -> Applications (scope: write:repository)>

curl -sS -H "Authorization: token $TOK" \
  "$FJ/api/v1/repos/$OWNER/$REPO/actions/tasks?limit=50" \
| jq -r '.workflow_runs[] | select(.conclusion=="failure") | .id' \
| while read id; do
    curl -sS -X DELETE -H "Authorization: token $TOK" \
      "$FJ/api/v1/repos/$OWNER/$REPO/actions/runs/$id"
  done
```
