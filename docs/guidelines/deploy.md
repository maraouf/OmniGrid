# Deploy runbook — OmniGrid

Forgejo Actions deployment setup.

| Field     | Value                                             |
| --------- | ------------------------------------------------- |
| Workflow  | `.forgejo/workflows/deploy.yml`                    |
| Target    | `pi@docker.example.com:/opt/omnigrid/app`             |
| Runner    | `home-runner` on `git.example.com` (shared, INSTANCE scope) |
| Restart   | `docker service update --force omnigrid_omnigrid`  |

The deploy target is a Debian 13 VM (amd64, 16 GB / 100 GB) reachable at `pi@docker.example.com`.
The username `pi` is just a unix account name — it is NOT a Raspberry Pi. Bind mounts on the
host:

- `/opt/omnigrid/app` → `/app:ro` (source code, read-only, rsync target).
- `/opt/omnigrid/data` → `/app/data` (writable — SQLite DB, backups, avatars).
- `/opt/omnigrid/pip-cache` → `/pip-cache` (persistent pip cache across container restarts).

The SQLite database lives at `/opt/omnigrid/data/omnigrid.db` on the host; inside the container
it's at `/app/data/omnigrid.db` (which is the `DB_PATH` default).

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

Two choices — pick one.

**Option A — add pi to docker group** (simplest, no sudo in workflow):

```bash
ssh pi@docker.example.com 'sudo usermod -aG docker pi'
# pi must log out and back in for the new group to take effect
ssh pi@docker.example.com 'docker info >/dev/null && echo OK'
```

**Option B — passwordless sudo for just the one restart command**:

```bash
ssh pi@docker.example.com "
    sudo tee /etc/sudoers.d/forgejo-omnigrid > /dev/null <<'EOF'
pi ALL=(root) NOPASSWD: /usr/bin/docker service update --force omnigrid_omnigrid
EOF
    sudo chmod 440 /etc/sudoers.d/forgejo-omnigrid &&
    sudo visudo -cf /etc/sudoers.d/forgejo-omnigrid &&
    sudo -n docker service update --force omnigrid_omnigrid && echo OK
"
```

If you pick option B, change the restart step in `deploy.yml` to run
`sudo -n docker service update --force ...` instead of bare `docker`.

### 2.5 Sanity: `/opt/omnigrid/data`

Holds the SQLite db (`omnigrid.db` — history / ignores / settings / users / sessions /
`stats_samples` / schedules). The workflow's `--exclude 'data/'` prevents `rsync --delete` from
wiping it. Confirm it's present and owned by pi:

```bash
ssh pi@docker.example.com 'ls -la /opt/omnigrid/data 2>/dev/null || echo "NOT CREATED YET — will be made on first run"'
```

## 3. First run

Either:

- Push a commit to `main` (any change, even a whitespace nudge), OR
- In Forgejo: OmniGrid → Actions → "Deploy to Swarm" → Run workflow (requires the
  `workflow_dispatch` trigger, already present in `deploy.yml`).

Watch the run. Expected behaviour:

1. **Checkout** — green, < 5 s.
2. **Configure SSH** — green, writes `deploy_key` and `ssh-keyscan`.
3. **Rsync source** — green, transfers only files that changed.
4. **Bump VERSION.txt on server** — SSHes in, reads `/app/VERSION.txt`, bumps PATCH by 1.
5. **Resolve Swarm service name** (if restart needed) — auto-discovers the single service in the
   stack.
6. **Force Swarm update** — green, Swarm replaces the single replica.
7. **Verify service is running** — polls `docker service ps ... --format {{.CurrentState}}` for
   up to 60 s; fails if any task is Failed / Rejected / Shutdown.
8. **Verify HTTP endpoint** — curls `$PROBE_URL` from the runner; must return 200 within ~30 s.
   Catches ingress / NPM breakage that the in-container healthcheck can't see.
9. **Verify deployed version matches** — curls `/api/version` and asserts it equals the freshly
   bumped value. Fails loudly (and notifies) if VERSION.txt didn't propagate.
10. **Cleanup SSH key** — always runs, removes `deploy_key`.
11. **Apprise notifications** — success on successful restart, failure on any failure.

Total pipeline: ~20–40 seconds. Service downtime during the `--force` rolling update: ~5–15
seconds (single replica rolling restart with `order: start-first`).

## Smart deploy — skips the Swarm restart when only static assets changed

The workflow runs `rsync -i` (itemised changes) and parses which files were actually written on
the remote. Rule:

| Changed                                  | Behaviour                                       |
| ---------------------------------------- | ----------------------------------------------- |
| Any file outside `static/`               | Swarm service restart (uvicorn picks up new code). |
| Only `static/` files changed             | No restart; browser Ctrl+Shift+R.               |
| Nothing changed                          | No restart, no-op deploy.                       |

This means editing `index.html` / `style.css` / `img/` ships in a couple of seconds (rsync +
cleanup only) and downstream users just hard-refresh. Backend changes still go through the full
~15 s rolling update.

The "smart restart" decision is made by parsing `rsync -i` output: only lines where column 3 is
`+` / `c` (new file / content change) or column 4 is `s` (size change) count — pure mtime/perm
drift (from a fresh CI checkout) is intentionally ignored, otherwise every push would look like
a backend change.

**Manual override**: when triggering "Run workflow" in the Forgejo Actions tab you get a
`force_restart` dropdown. Set it to `true` to restart even on a static-only diff (useful if the
uvicorn process has drifted for unrelated reasons and you want to bounce it).

## VERSION.txt bump model (SemVer MAJOR.MINOR.PATCH)

The server owns `/app/VERSION.txt` — rsync deliberately excludes `VERSION.txt` so deploys never
overwrite a hand-pinned version. `main.py` reads `/app/VERSION.txt` (falls back to the
repo-local copy for dev); missing file returns `"0.0.0-dev"` as a visible signal. `compose.yml`
layers a per-file writable bind for `VERSION.txt` on top of the read-only `/app` mount so both
the deploy pipeline (writing from the host) and Admin → Version (writing from inside the
container) target the same path.

See `docs/RELEASE_PROCESS.md` for the full operator runbook. Quick summary:

- **MAJOR** — operator-controlled. Reserved for breaking changes. Resets MINOR + PATCH to 0.
- **MINOR** — operator-controlled, periodic. When a batch of PATCH-shipped items feels
  release-worthy, the operator hand-edits `/app/VERSION.txt` on the server (e.g. `1.0.47` →
  `1.1.0`) — or uses the Admin → Version page in the UI. Resets PATCH to 0. CI never touches
  MINOR.
- **PATCH** — CI-controlled, automatic. Every successful rsync increments PATCH by 1 via the
  "Bump VERSION.txt on server" step, which runs BEFORE the Swarm restart so the new container
  reads the bumped file at startup. Fires on every successful rsync (static-only included,
  because the `?v=__APP_VERSION__` cache-bust on assets needs a fresh value). MAJOR + MINOR
  are preserved.
- The post-deploy "Verify deployed version matches" step asserts `/api/version` equals what
  was just written. Catches the situation where the SSH bump succeeded but the container
  failed to read the new file (e.g. bind-mount stale). Mismatch → ❌ Apprise ping.
- **First-deploy bootstrap**: if `/app/VERSION.txt` doesn't exist, the bump step creates it at
  `1.0.0` then increments to `1.0.1`. Operator can also seed the file manually with any
  SemVer triple before the first deploy.
- **Legacy migrations** are handled inline in deploy.yml's bump step:
    - 3-part `2.x.y` (an older flavour we briefly used) one-shot collapses to `1.0.<counter>`
      — the counter keeps moving forward rather than resetting.
    - 2-part `M.N` (the brief MAJOR.MINOR-only experiment) backfills PATCH=0 then bumps to
      `M.N.1`.

Operator UI: **Admin → Version** is a direct VERSION.txt editor — Save writes the values
straight to the file. Use case: reset PATCH to 0 from the UI when cutting a MINOR release. The
writable per-file bind in `docker-compose.yml` (`/opt/omnigrid/app/VERSION.txt:/app/VERSION.txt`)
is what makes this possible — operators upgrading from older deploys must redeploy the stack
once for the new compose bind to take effect.

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
