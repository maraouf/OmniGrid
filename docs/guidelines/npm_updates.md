# npm / front-end dep updates — OmniGrid

## How the front-end deps ship

npm is used on the DEV MACHINE ONLY. The server never runs npm or node.

- `package.json` + `package-lock.json` — committed, dev-only metadata (the deploy workflow excludes
  them from rsync).
- `node_modules/` — committed AND shipped to the server via rsync. Served at `/node_modules/`
  by an **allowlist-gated route** in `main.py` (`api_node_modules` + `_NPM_ALLOWED` set,
  registered above the `StaticFiles` catch-all at `/`). The earlier wildcard `app.mount(...)`
  was replaced by the allowlist-gated route — see comment block in `main.py` near the route.
  Files NOT in
  `_NPM_ALLOWED` 404 even though they're on disk; this keeps the publicly-reachable surface
  tight.
- HTML references the dist files directly:

  ```html
  <script src="/node_modules/@tailwindcss/browser/dist/index.global.js"></script>
  <script defer src="/node_modules/alpinejs/dist/cdn.min.js"></script>
  <script src="/node_modules/sweetalert2/dist/sweetalert2.all.min.js"></script>
  ```

Everything is "pre-built by upstream" — no compile step, no bundler, no `npm run vendor`.
Upgrading is just `npm install` + commit.

A weekly **npm audit** CI workflow runs `npm audit` against the lockfile on a cron schedule and
Apprise-pings on findings — it is a passive monitor, no auto-fix.

## Dependencies currently pinned

| Package                       | Version | Purpose                                                                  |
| ----------------------------- | ------- | ------------------------------------------------------------------------ |
| `@tailwindcss/browser`        | v4.x    | Tailwind v4's in-browser JIT runtime. Replaces the retired v3 Play CDN.  |
| `alpinejs`                    | 3.x     | Reactive SPA component framework.                                        |
| `sweetalert2`                 | 11.x    | Confirm / prompt modals.                                                 |
| `@xterm/xterm`                | 5.x     | Terminal renderer used by the SSH console drawer.                        |
| `@xterm/addon-fit`            | 0.x     | xterm fit-to-container addon.                                            |
| `@xterm/addon-web-links`      | 0.x     | xterm clickable-URL addon.                                               |
| `qrcode-generator`            | 1.x     | TOTP enrolment QR code rendering (Settings → Security card).             |

When adding a new dep that needs serving, ALSO add its dist path to `_NPM_ALLOWED` in
`main_pkg/users_routes.py` — the route 404s anything not in the set. The current allowlist
holds 8 paths (one per script/css tag in `index.html` / `login.html`).

## Checking for updates

```bash
# Simple list of what's outdated:
npm outdated

# More detail with semver ranges:
npm outdated --long

# What changed between your current and the latest:
npm view alpinejs versions --json
npm view sweetalert2 versions --json
npm view @tailwindcss/browser versions --json
```

## Bumping one package to the latest

```bash
npm install alpinejs@latest
# …or a specific version:
npm install alpinejs@3.14.5
```

Check the UI still works in a local dev server (or just smoke-test after pushing — auth flows +
a stack-update op are the blast-radius areas that depend on Alpine + SweetAlert2 respectively).

After the install, `npm` will:

- Update the version string in `package.json`.
- Update `package-lock.json` with the new integrity hashes.
- Refresh the subtree under `node_modules/alpinejs/`.

Commit all three:

```bash
git add package.json package-lock.json node_modules/alpinejs
git commit -m "Bump alpinejs to X.Y.Z"
git push
```

## Bumping everything at once

```bash
npm update                 # respects semver ranges in package.json
# OR for major bumps too:
npm install alpinejs@latest sweetalert2@latest @tailwindcss/browser@latest
```

Then commit `package.json`, `package-lock.json`, and the changed directories under
`node_modules/`.

Verify nothing broke in the browser:

- Login page (`static/login.html` references the same deps).
- Any SweetAlert confirm (e.g. deleting a user in Admin → Users).
- Any interactive element (e.g. the user-menu dropdown — tests Alpine).
- Tailwind utility classes render (check the filter-chip pill colours, button states, dark
  theme).

## Adding a new dependency

```bash
# 1) Install it
npm install <pkg>

# 2) Figure out the dist path (usually node_modules/<pkg>/dist/<x>.js).
#    Peek at the package:
ls node_modules/<pkg>/
cat node_modules/<pkg>/package.json | jq .main,.module

# 3) Add a <script> / <link> in static/index.html referencing the
#    /node_modules/<pkg>/... path. Put it with the others near the top.

# 4) Commit package.json + package-lock.json + node_modules/<pkg>.
```

**Security note**: everything under `/node_modules/` becomes browser-reachable once committed.
Only install things you actually want exposed at `/node_modules/<pkg>/...` — avoid dev-tools
that inspect source, etc.

## Removing a dependency

```bash
npm uninstall <pkg>
# Remove the <script>/<link> tag from static/index.html.
# Commit package.json + package-lock.json + the deletion under
# node_modules/ (git will show it as a large deletion — expected).
```

## Audit + vulnerability checks

```bash
npm audit                  # summary of known CVEs in your tree
npm audit fix              # auto-fix where possible (semver-compatible)
npm audit fix --force      # also apply semver-major bumps (risky)
```

Don't `--force` without testing — it can pull in breaking changes.

## Clean re-install (when things feel weird)

```bash
rm -rf node_modules package-lock.json
npm install
```

This rebuilds `node_modules/` from `package.json` and writes a fresh lockfile. After confirming
the site still works, commit the new `package-lock.json` and the `node_modules/` subtrees that
actually changed.

## What's committed where — quick reference

| Path                   | git | dev | server |
| ---------------------- | --- | --- | ------ |
| `package.json`         | ✓   | ✓   | ✗ (rsync-excluded) |
| `package-lock.json`    | ✓   | ✓   | ✗ (rsync-excluded) |
| `node_modules/`        | ✓   | ✓   | ✓                  |
| `static/vendor/`       | —   | —   | — (removed — nothing vendors here anymore) |

## Why `node_modules` is committed

Counter-intuitive vs. typical Node projects, on purpose:

1. The deploy runner doesn't need Node/npm — simpler, fewer moving parts.
2. What the server serves is exactly what was tested in dev — no "CI pulled a different
   transitive than my laptop did" class of bug.
3. Offline / air-gap friendly — a checkout is self-contained.
4. The packages we use are small (Alpine + SweetAlert2 + a Tailwind runtime). Total
   `node_modules` is ~10 MB, fine for git.

If the tree ever balloons (e.g. adding a large package with many deps), reconsider this model
and move to a CI-side `npm ci` instead. The per-commit cost of committing `node_modules` is real
but currently small.
