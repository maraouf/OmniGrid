# Changelog

All notable changes to OmniGrid land here. Format adheres to
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project
follows [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

Cadence (see `docs/RELEASE_PROCESS.md` for the full release runbook):

- **`PATCH`** — CI bumps automatically on every successful deploy (one per shipped TODO item). The accumulating count between releases is the "is it time to cut a release" signal.
- **`MINOR`** — manually cut. When a batch of PATCH-shipped items feels release-worthy, `MINOR` is hand-edited on the server (which resets `PATCH` to `0`) and a new `[X.Y.0]` section is written here listing the items that landed since the last MINOR.
- **`MAJOR`** — breaking changes only (DB migrations that aren't forward-compatible, env-var renames, `/api` contract breakage). Migration notes ship alongside the release in `notes/MIGRATIONS.md`.

Categories per release follow Keep a Changelog:

- **Added** — new features.
- **Changed** — changes in existing functionality.
- **Deprecated** — features marked for removal in a future release.
- **Removed** — features that were dropped this release.
- **Fixed** — bug fixes.
- **Security** — fixes for vulnerabilities.
- **Internal** — refactors, doc work, build / CI changes that don't touch user-facing behaviour. (Non-standard but useful for a homelab tool where most work is internal.)

## [Unreleased]

Items shipped to the live deploy via the daily PATCH cadence that are
not yet rolled into a tagged `MINOR` release. The next MINOR cut renames
this whole block to `[X.Y.0]` and adds a fresh empty `[Unreleased]` above.

### Added

- Plex — the card's active-stream line now shows the **transcode-vs-direct-play split** (e.g. "2 transcoding · 3 direct") alongside the total stream bandwidth, bringing Plex to parity with the Jellyfin / Emby cards. The same split is reported in the **status** AI / Telegram action and the AI context. No extra calls — the data already comes from the active-sessions read.
- Bazarr — the "List missing subtitles" wanted list is now actionable: each movie / episode row gets a 🔍 **Search** button that searches every provider for that item's missing subtitles and grabs the best match. A matching **"Search subtitles for an item"** AI / Telegram action does the same by title ("search subtitles for Dune").
- Prowlarr — the drawer now draws a **per-day query / grab throughput** chart and a **daily failure-rate trend** over time, built from a new history table OmniGrid samples by diffing Prowlarr's lifetime counters (a stats reset is handled cleanly, never a false spike). Complements the existing point-in-time per-indexer failure stats with the over-time view. Sampler cadence and retention are tunable in Admin → Config.
- Tautulli — the card now shows the **top watcher** (last 30 days), and the drawer adds Tautulli's signature analytics: a **top-watchers** list (with avatars) and a **most-played media** list, plus **plays-by-day-of-week** and **plays-by-hour-of-day** distribution charts with a "busiest day / hour" caption. A new **"Most watched"** AI / Telegram action reports the top watchers + most-played titles. No new history table — Tautulli is itself the stats store.
- Kavita — two new AI / Telegram / drawer actions: **Recently added** (the latest series + new chapters) and **On deck** (your continue-reading list with per-title read progress), each rendered as a **rich poster list** (covers fetched server-side so your API key never reaches the browser). The drawer also draws a **library-growth trend** — series count + total size over time — from a new history table OmniGrid samples ("+N series this month"). Sampler cadence and retention are tunable in Admin → Config.
- Jellyfin / Emby — the card now shows the **total streaming bandwidth** and a **transcoding vs direct-play split** whenever something's playing, each Now-Playing row gets a ⏹ **Stop** button, and a new **"Stop a Jellyfin/Emby stream"** AI / Telegram action terminates a session by viewer name or title (confirm-gated) — closing the parity gap with Plex / Tautulli / Tracearr. Both brands get it from the shared base.
- Tdarr — the card now flags **failed transcodes**, shows the **live transcode speed** (frames/second across active workers) and the **average space reclaimed per transcode**, and — in the drawer — draws a **cumulative space-reclaimed line** ("reclaimed X TB and counting"), a **transcode-queue burn-down**, and a **per-day throughput** chart, all from a new history table OmniGrid samples (Tdarr keeps no time series of its own). The "Tdarr status" action also reports the live speed + failed count. Sampler cadence and retention are tunable in Admin → Config.
- qBittorrent — the card now shows the **global ratio**, **free disk**, **all-time** up/down totals and a warning when the client isn't connected, and — in the drawer — a **download/upload speed sparkline** plus a **free-disk-runway projection** ("disk full in ~N days at this fill rate"), all from a new history table OmniGrid samples (qBittorrent keeps no speed history of its own). The downloading list gains a per-row **pause** button alongside delete, and new AI / Telegram actions **pause / resume one torrent** by name. Sampler cadence and retention are tunable in Admin → Config.
- Prowlarr — the card and the assistant now surface **per-indexer performance**: the overall query failure rate, plus the **most-failing** indexer ("indexer X: 40% of 1,200 queries") and the **slowest** indexer — the single most actionable Prowlarr insight, built from data Prowlarr already tracks. A new **"indexer stats"** action lists every indexer's queries / grabs / failure-% / average response time, worst-first. And three new AI / Telegram actions act on one indexer by name: **enable**, **disable** (gated like a destructive action, since it cuts search coverage), and a live **test** that reports whether it's reachable (and flags a Cloudflare block).
- AI / Telegram — you can now ask about **upcoming releases across all your *arr apps** in one question: "what movies are coming this week", "when does the new season of X air", "any new albums or books releasing soon", "what's on the calendar". The assistant aggregates the release calendar from every configured Radarr / Sonarr / Lidarr / Readarr instance and answers with the full detail the calendar widget shows — title, release date and air time, runtime, and a one-line synopsis — optionally filtered to one media type or one title. There is also a dedicated Telegram **`/upcoming [days] [movies|series|music|books]`** slash command (in the bot's command menu and `/help`) that returns the next-N-days releases grouped by date with per-item type icons, dates and times following your profile's date/time format.
- Sonarr, Lidarr & Readarr — the same treatment Radarr just got, completing the *arr family. Each card now shows the app-specific completion stat (Sonarr: episodes have/total; Lidarr: albums + tracks; Readarr: books have/total), the count **below quality cutoff**, the **total library size on disk**, and — in the status reply — the actual health-warning messages. New **"search for a series / artist / author"** AI / Telegram actions kick a release search for one title, distinct from the library-wide missing search. In the drawer, each card draws **library-growth**, **missing-backlog**, and **free-disk** trend sparklines plus a **disk-free-runway projection**, all from the shared *arr history table OmniGrid already samples.
- Radarr — the app card now also shows the count of movies **below their quality cutoff** (distinct from "missing"), the **total library size on disk**, and — in the status reply — the actual health-warning messages rather than just a count. A new **"search for a movie"** AI / Telegram action kicks off a release search for one title (e.g. "search for Dune now"), distinct from the library-wide missing search. In the drawer, the card now draws **library-growth**, **missing-backlog**, and **free-disk** trend sparklines plus a **disk-free-runway projection** ("disk full in ~N days at this fill rate"), built from a new history table OmniGrid samples for the whole *arr family (Radarr / Sonarr / Lidarr / Readarr) — so the same trends are ready to switch on for the other three apps. Sampler cadence and retention are tunable in Admin → Config.
- Seerr (Overseerr / Jellyseerr) — new AI / Telegram actions turn Seerr from read-only into an approval flow: **Approve a request** / **Decline a request** by title (e.g. "approve Dune"), **Request a TV show** (all seasons), **Retry a failed request**, and **Resolve an issue** by title, alongside the existing request-a-movie. The card now also shows a **failed / stuck request** count and — in the drawer — a **pending-backlog trend** that OmniGrid samples into its own history table, so you can spot "pending has been stuck at 5 for a week". Sampler cadence and retention are tunable in Admin → Config.
- AdGuard Home Sync — the card now shows per-replica detail instead of just a list of failing names: each replica's in-sync state, how long ago it last synced, and (in the drawer) the error when it's failing — so you can see *which* replica is stale and *why*. When the sync API exposes them, the sync interval and total sync count are shown too. The "AdGuard sync status" AI / Telegram action includes the same per-replica staleness detail.
- Pi-hole — the aggregated fleet card now also shows the top queried domain and top client (alongside the existing top blocked), a **queries-vs-blocked activity chart** (from Pi-hole's own 24-hour history), and a **long-term blocked-% trend** that OmniGrid samples into its own history table so it survives Pi-hole's FTL counters resetting on a restart. New fleet actions via AI / Telegram: **Block a domain** / **Unblock a domain** across every Pi-hole host. Sampler cadence and retention are tunable in Admin → Config.
- AdGuard Home — the aggregated fleet card now shows more at a glance: the top queried domain and top client (alongside the existing top blocked), the safe-browsing / parental block split, a **queries-vs-blocked activity chart** (built from the data AdGuard already returns — no extra calls), and a **long-term blocked-% trend** that OmniGrid samples into its own history table so it survives AdGuard's short rolling stats window and a restart. New fleet actions: **Flush DNS cache** (a button on the card) and, via AI / Telegram, **Block a domain** / **Unblock a domain** across every AdGuard host. Sampler cadence and retention are tunable in Admin → Config.
- Speedtest Tracker — the app card now also shows connection-quality stats from each test: jitter and packet loss (when the upstream reports them), plus an ISP / test-server provenance line ("which ISP, which server"). The "latest speed test" AI / Telegram action includes jitter, packet loss, and the ISP/server too. (The trend chart already plotted download, upload, and ping.) The card additionally draws a **long-term trend** — a daily-median download sparkline plus the median download / upload / ping over a configurable window (default a year). OmniGrid samples each result into its own history table, so this trend survives Speedtest Tracker ageing out its own results; the sample cadence and retention are tunable in Admin → Config.
- ddns-updater — the app card now shows richer per-record detail (provider, IP version, and how long ago each record was last updated), a public-IP-change timeline (when your WAN IP last changed, newest first), and a 90-day failing-record sparkline. A lifespan sampler records each pinned ddns-updater chip's public IP + record totals + failing count over time (ddns-updater keeps no history of its own), so the timeline and trend build up automatically; sample cadence and retention are tunable in Admin → Config. A new "List DNS records" AI / Telegram action lists every record with its provider, status, and last-updated time.
- Proxmox VE per-app integration — admin-pinned Proxmox chips get an expanded stat card (nodes online / total, VMs running / total, LXC containers running / total, cluster CPU + memory utilisation, aggregate storage with shared-storage de-duplication, and the PVE version), sourced from a single `/cluster/resources` call. API-token auth (Datacenter → Permissions → API Tokens → Add; paste the whole `user@realm!tokenid=secret` value; a per-instance "Verify TLS" toggle, off by default for the self-signed cert). AI / Telegram skills: Proxmox status, List VMs, List containers, Start a guest, and Stop a guest (graceful shutdown; requires a confirm) — with per-row ▶ Start / ⏹ Stop buttons on the VM and container lists. Follows the per-app encapsulation pattern (module + extender + editor / extras partials + catalog template + icon + Test-connection).
- Plex — the app card and drawer now show how many active streams are transcoding and the total streaming bandwidth (when something's playing), each Now-Playing row shows whether it's a Direct Play or Transcode plus its bandwidth, and a new ⏹ Stop button on each stream (and a "Stop a Plex stream" AI / Telegram action) ends a playback session — confirm-gated.
- Tautulli — the app card now draws a 30-day plays-over-time sparkline (like the Tracearr graph) plus a "Plays (30d)" total, each active-stream row gets a ⏹ Stop button, and a new "Stop a Plex stream" AI / Telegram action terminates a session — confirm-gated.
- Release-calendar widget — a new Apps custom-dashboard widget showing upcoming movie / series / album / book releases from your configured Radarr / Sonarr / Lidarr / Readarr services in a modern month-grid calendar. Days with releases show service-coloured dots; click a day to open a floating popover listing that day's titles grouped by media type (Movies / Series / Albums / Books). Each title shows its poster, release time and runtime, a short synopsis, and quick links to open it in the source app plus IMDb / TMDB — Homarr-style. The popover floats above the tile (never cropped, even on a small tile) and stays open so you can scroll a busy day. Month navigation, and only the services you've actually configured contribute (Radarr disabled → no movies). The widget is hidden from the picker entirely when no *arr service is set up. Times follow your Settings → Profile time format. The widget settings also let you set an optional friendly "Open in app" link per service (e.g. a
  reverse-proxy hostname) so the popover's link points at your preferred URL while the calendar data keeps loading from the service's integration address.
- Apps custom-dashboard masonry layout — mixed-height tiles now pack upward to fill the space beside a taller tile instead of every column starting a fresh row below the tallest one, so the board has no awkward vertical gaps. Applies in both view and edit mode.
- GitSync Connector per-app integration — admin-pinned GitSync chips get an expanded stat card (sync pairs / enabled / paused, issue + commit + release mappings, synced refs, unacknowledged alerts, connector version) sourced from its REST API, plus app drawer + AI / Telegram actions: GitSync status, list sync pairs, sync / pause / resume all pairs, and sync / pause / resume a single pair by name. Bearer-token auth (GitSync → API → Create token). Pause actions require a confirm. Follows the per-app encapsulation pattern (module + extender + editor / extras partials + Test-connection). The List-sync-pairs view groups pairs by state (active / paused / disabled) with per-row Sync + Pause / Resume buttons; disabled pairs offer no actions, and each pair shows its destination git-host icon (GitHub / GCSR / Forgejo).
- Manual-update actions for non-Docker *arr apps — Radarr / Sonarr / Prowlarr / Lidarr / Readarr instances that aren't linked to a Docker container/stack get two new actions in the app drawer and via AI / Telegram: "Check for updates" (compares the running version against the latest available on the configured branch) and "Update <App>" (triggers the app's built-in updater, which downloads/installs/restarts itself). The update action requires a confirm. Both actions are hidden for Docker-linked instances, which update through their container/stack instead.
- Grafana per-app integration — admin-pinned Grafana chips get an expanded stat card (Dashboards / Folders / Datasources, plus Users / Orgs when a server-admin token is configured, with the org name + Grafana version as a footnote), sourced from the Grafana REST API. Service-account-token auth (Administration → Service accounts → Add service account token). AI / Telegram skills: Grafana status, List dashboards, List datasources, and Search dashboards (by name). Follows the per-app encapsulation pattern (module + extender + editor / extras partials + catalog template + icon + Test-connection).
- UniFi per-app integration — admin-pinned UniFi chips get an expanded stat card (devices online / total, access points / switches / gateways, connected clients with wired / wireless split, the configured Wi-Fi networks (with their SSID names), Network application version, and a firmware-update count when the console reports pending device updates) sourced from the official UniFi Network Integration API. Device and client lists are fully paginated so large fleets report accurate totals, and devices are classified by their interface shape (radios → access point, ports → switch) so non-standard AP / switch models still bucket correctly. API-key auth (UniFi site → Settings → Control Plane → Integrations → Create API Key; needs UniFi OS 4.x / Network 9.0+). AI / Telegram skills: UniFi status, List devices (grouped by type), Clients (top-10 by usage with a device-type icon, IP address, and — for wireless clients — the Wi-Fi network they're on), List Wi-Fi networks (each with its subnet /
  VLAN / security / band), and Restart a device (by name or MAC;
  requires a confirm). Follows the per-app encapsulation pattern (module + extender + editor / extras partials + catalog template + icon + Test-connection).
- netboot.xyz per-app integration — admin-pinned netboot.xyz chips get a stat card showing the number of boot options (the endpoints catalog) and downloaded boot assets in place, the installed boot-menu version, the web-app version, host CPU / RAM, and a "boot-menu update available" badge when the local menu is behind the latest upstream release. Stats are read from the web-app's dashboard over its socket.io transport (it exposes no plain-HTTP stats API). No authentication needed (just set the chip URL to the web-app root, e.g. http://host:3000). AI / Telegram skill: netboot.xyz status. Follows the per-app encapsulation pattern (module + extender + editor / extras partials + Test-connection).
- Rundeck per-app integration — admin-pinned Rundeck chips get an expanded stat card (projects, total job definitions, executions running right now, server version) sourced from the Rundeck REST API. Auth with a user API token (Profile → User API Tokens; set the chip URL to the Rundeck server, default port 4440; a per-instance "Verify TLS" toggle is available). AI / Telegram skills: Rundeck status, List jobs (with a ▶ run-now button per row), Running executions, Recent executions (the latest runs across projects with their ✅ / ❌ / ⏹️ / ⏱️ status), and Run a job by name (requires a confirm). Follows the per-app encapsulation pattern (module + extender + editor / extras partials + Test-connection).
- RustDesk per-app integration — admin-pinned RustDesk chips get an expanded stat card (registered devices online / total, console users, server version) sourced from the RustDesk Server **Pro** API. Log in with the Pro web-console username + password (set the chip URL to the console, default port 21114; a per-instance "Verify TLS" toggle is available). AI / Telegram skills: RustDesk status, List devices (online-first, with OS + ID), and Users. The open-source RustDesk server has no API, so the card shows a clear "needs RustDesk Server Pro" message there. Follows the per-app encapsulation pattern (module + extender + editor / extras partials + Test-connection). **Follow-up patch:** RustDesk accounts with 2FA (TOTP) enabled are now supported — paste the base32 2FA setup key in the editor (like NPM) and OmniGrid completes the login's two-factor step itself; if 2FA is on but no key is set, the card explains how to add it (or to use a dedicated non-2FA user). Connection failures now give
  an actionable message (point the URL at the Pro web console on port 21114, not the relay ports) instead of a bare "ConnectError".
- FlareSolverr per-app integration — admin-pinned FlareSolverr chips get a stat card showing whether the Cloudflare-challenge solver is ready, its version, the number of active browser sessions, the user-agent it presents, and a 30-day usage trend (peak / average / active-days + a sparkline of open sessions over time, since FlareSolverr itself keeps no history — OmniGrid samples the live session count in the background). No authentication needed (just set the chip URL to the FlareSolverr API root, e.g. http://host:8191). AI / Telegram skills: FlareSolverr status (now includes the 30-day usage summary), List sessions (each with a destroy button), and Destroy a session by id (requires a confirm — any indexer using it will have to re-solve the challenge). Follows the per-app encapsulation pattern (module + extender + editor / extras partials + Test-connection).
- Nginx Proxy Manager per-app integration — admin-pinned NPM chips get an expanded stat card (proxy hosts enabled / total, SSL certificates with a count of those expiring within 30 days, redirection hosts, TCP/UDP streams, 404 hosts, NPM version). Login with the NPM admin email + password (exchanged for a token internally; set the chip URL to the admin UI, default port 81). Supports NPM accounts with two-factor authentication — paste the base32 2FA setup key in the editor and OmniGrid generates the codes itself. A per-instance "Verify TLS certificate" toggle (off by default for NPM's self-signed admin cert) is available too. AI / Telegram skills: NPM status, List proxy hosts (with on/off state + forward target), List expiring SSL certificates (sorted by soonest expiry), and Enable / Disable a proxy host by domain (disable requires a confirm — it takes a site offline). Follows the per-app encapsulation pattern (module + extender + editor / extras partials + catalog template + icon +
  Test-connection).
- Bookmark / app tile favicons — tiles whose icon doesn't match a built-in brand or catalog icon now show the site's own favicon instead of a blank letter / link glyph. OmniGrid fetches the favicon server-side (so it works even when your browser can't reach the site directly), caches it on disk, and serves it from its own address. Private / internal targets are only fetched when the host is one of your configured hosts (so the feature can't be abused to probe your internal network).

### Internal

- Custom-dashboard widgets refactored into per-widget modules to match the per-app file model — each widget kind (clock, weather, moon, public IP, system stats, prayer times, release calendar) now lives in its own `static/js/widgets/<kind>.js` (render helpers + extender record) and `static/_partials/_components/widgets/<kind>.html` template partial, bundled by a widget registry that the generic helpers dispatch through (decoration icon, refresh support, freshness, availability) instead of a per-kind branch ladder. No user-facing behaviour change.
- Dev tooling — the in-repo linter's four weak-warning rules (enclosing-name shadowing, unused parameters, bare `str(.get())`, duplicate code blocks) are now first-class default-on checks in every mode, including the full-tree scan, after the entire pre-existing backlog was cleared to zero. Fresh instances of these are flagged on every per-edit, pre-commit, and full-tree run rather than only on touched files. The shadowing rule now correctly ignores `global` / `nonlocal` rebinds, and each rule gained a per-function `# noinspection` opt-out for the handful of sanctioned-pattern exceptions (contractually-fixed signatures, intentional shape-similar blocks, meaningful nested-helper name reuse). Also added sixteen new default-on checks mirroring common IDE inspections — real-bug guards (mutable default arguments, `is`/`is not` against a literal or collection, always-true `assert (cond, "msg")` tuples, duplicate dict-literal keys, `return`/`break`/`continue` inside `finally`, bare `except:`, unreachable code after a terminal statement, `x = x` self-assignment, `raise NotImplemented`) and style/redundancy warnings (placeholder-less f-strings, `== None`, `== True`/`== False`, negated `in`/`is` comparisons, `type(x) == T`, unused `except … as e` bindings, redundant `.keys()` in for-loops). No runtime behaviour change.

## [1.6.0] — 2026-06-10

Sixth MINOR cut on top of `1.5.0` — rolls up **326 closed issues** under the 1.6.0 milestone (211 enhancements, 115 bug fixes). Every entry shipped to the live deploy as a PATCH bump on the daily CI cadence; this MINOR bundles them under a single tag for rollback / changelog purposes.

### Highlights

- Major app-integration expansion — new per-app integrations wired end-to-end for Plex, Jellyfin, Emby, Tautulli, Tdarr, Tracearr, Kavita, Seerr (Overseerr / Jellyseerr), AdGuard Home Sync, ddns-updater, Apprise and Forgejo, each following the per-app encapsulation pattern with editor + extras partials, catalog templates, icons and AI skills — bringing the per-app integration roster to 23 modules.
- *arr stack & transcoding — Sonarr / Radarr / Lidarr / Readarr / Prowlarr / Bazarr / qBittorrent extras, Prowlarr add-indexer from AI / Telegram, and a full Tdarr workflow (check-bloated, requeue-bloated, requeue-failed) with background scans, auto-poll, measured ETAs and rich per-file result UIs.
- Apps page & custom dashboard — "By app" cards redesigned (logo left, content right), multiple named custom-dashboard views with per-view private/public visibility + edit permissions, per-app data-cache TTL configurable in the app, and per-app action (skill) buttons gated on the per-instance "show extras" toggle.
- Credential-safe media art — a new per-app authenticated image proxy (`/api/services/{host}/{idx}/image-proxy`) fetches posters and avatars through each app's own credential so it never reaches the browser DOM, with an SSRF guard and a 10 MB / 1-day cache (Plex / Tautulli / Bazarr / Seerr / Forgejo art).
- Telegram bot — a new `/skills` roster plus dynamic per-app `/<skill_id>` commands (e.g. `/run_speedtest`, `/adguard_status`), expanded command help + arg-skill handling, AI free-text grounded in real fleet state, per-pending-skill polling so long background jobs (e.g. Tdarr requeue) report completion automatically, and destructive-action confirm gating that honours each surface's policy.
- Stats dashboard — slow-query investigation plus event-loop-blocking fixes, chart memoization, Samples KPIs + i18n, and a performance pass that chunked / offloaded the retention prunes.
- Prayer Times & Weather — prayer reminders (a notification N minutes before each prayer, per-user opt-in with per-user medium selection across in-app / Telegram / Apprise), Prayer-Times admin fixes, and weather-sampler additions.
- AI assistant — answers per-host telemetry questions directly and auto-resolves a single matching host; host-status accuracy fix so it no longer mislabels reachable "problem" hosts as Down.
- HTTP / Service probe correctness — UDP + legacy-TLS endpoint handling, SNI / TLS unrecognized-name fixes, the Accepted-status-codes (CSV) field no longer clearing on keystroke, and open-as-URL port links no longer appending health-check probe paths.
- Stability & performance — the tracemalloc default-ON crash-loop root-caused and fixed; SPA perf work (xterm.js lazy-load, `x-show` → `x-if` unmounting of costly subtrees, sprite-preload + requestAnimationFrame violation cleanups); per-app first-load paint fixes.
- Hosts & discovery — port-scan default coverage extended to common TCP + UDP service ports, an Asset-Inventory collapsible JSON-tree viewer, Discovery-wizard improvements, scan-only port surfacing, and Apps-instance grouping (by host / service / none).
- Security — CodeQL SSRF + path-injection findings resolved, plus UTF-8 encoding hardening on file reads.
- Provider chips, icons & status pills + cross-app polish — unified Test-connection component, reload-button consistency, app-extras presentation unified across all apps, Public-IP widget country-flag display (new `flag-icons` dep), and a top-bar recent-tabs privacy fix (a user only ever sees their own tabs).

### Telegram bot

- Telegram `/help` curation -- hide /link for already-linked users, gate /ip on `tuning_public_ip_enabled`, s... (#1079) [Enhancement]
- Telegram AI free-text non-response + missing "Thinking" indicator (#1087) [Bug]
- Notification test messages -- include the provider name so Apprise vs Telegram test fires are distinguishable (#1119) [Enhancement]
- Telegram AI fixes (the web / system AI path was already correct -- it runs the two-round tool loop; this en... (#1141) [Bug]
- AI blind to app skills -- ROOT CAUSE behind "show me the latest speed test -> integration not configured" i... (#1147) [Enhancement]
- AdGuard Home per-app integration (backend module + APP-LEVEL aggregated extras + fleet skills + AI/Telegram) (#1159) [Enhancement]
- Telegram /help "App skills" + per-app SKILL slash commands (#1161) [Enhancement]
- Prayer Times card + AI/Telegram prayer-times & Hijri-calendar answers (#1174) [Enhancement]
- Prayer reminders — send a notification N minutes before each prayer, per-user opt-in with per-user medium s... (#1182) [Enhancement]
- HTTP/service probe-failure notifications ignored a user's per-channel Profile routing and still fired to Te... (#1186) [Bug]
- Telegram "Test connection" was broadcasting the probe message to EVERY chat in the telegram_chat_id CSV (#1189) [Bug]
- Per-app skill commands (and /whoami-style commands) in Telegram AI replies were copyable-only, not clickabl... (#1190) [Enhancement]
- Telegram AI Seerr suggestion-filter fixes (two related fixes, merged): (A) "exclude movies from Spain and D... (#1191) [Bug]
- Telegram /help — collapse the App-skills section to ONE tappable entry per app (#1198) [Enhancement]
- Telegram /hijri — the Gregorian date now follows the user's profile date format (was ISO, e (#1207) [Enhancement]
- Telegram /prayer — prayer clock times now follow the user's 12h/24h profile format (were always 24h) (#1208) [Enhancement]
- Telegram /moon — moonrise/moonset times + the 'Next 2 days' outlook dates now follow the user's profile format (#1209) [Bug]
- Telegram /cleanup — the reply now NAMES the containers being removed (was just a count) (#1211) [Enhancement]
- Prowlarr add-indexer from AI/Telegram (new write capability on the existing Prowlarr app).(not just read):... (#1237) [Enhancement]
- New Telegram /skills command -- the per-app skill roster on its own (the '🧠 App skills (tap an app to see i... (#1239) [Enhancement]
- Telegram-AI skill replies showed literal special characters (e.g (#1240) [Enhancement]
- Telegram command help -- arg-skill commands no longer paste a placeholder (#1248) [Enhancement]
- Wire qBittorrent as a per-app integration -- SINGLE-instance per chip (NOT fleet) so 2+ instances each rend... (#1250) [Enhancement]

### Stats dashboard

- service_probe / http_probe now render as ACTIVE provider chips (with stats), not muted (#1047) [Bug]
- Stats -> Samplers sub-tab icon -- was `icon-activity` (same as Network sub-tab), now `icon-loader` so the t... (#1075) [Enhancement]
- Slow-query log line -- caller-site identification (#1078) [Bug]
- Stats Incidents header -- range-picker + reload clipped off the right edge (should stay fully visible like... (#1110) [Bug]
- Stats section findings, worked one-by-one (#1111) [Enhancement]
- Unified the Admin -> AI Usage dashboard time-range filter with the Stats -> AI Cost picker (#1114) [Enhancement]
- Stats -> Database growth projection now grounded in real measured history (was a synthetic +0.5%/day stub o... (#1133) [Enhancement]
- Sampler vs Stats Samples/Samplers drift audit (#1136) [Enhancement]
- Bug fix: Stats -> AI Cost showed no data while Admin AI did (#1137) [Bug]
- Stats slow_query log warnings -- investigated + one event-loop-blocking fix (#1140) [Bug]
- Slow-query / service_sampler perf batch (#1152) [Enhancement]
- Apps edit-instance editor polish: (a) 'Show extras (UPS stats etc.)' label (#1160) [Bug]
- Apps page — keep AdGuard / Pi-hole fleet ACTIONS in the app drawer only; the Apps-grid card shows STATS only (#1199) [Enhancement]
- Stats — added an 'Apps with extras' card after the Apps card (#1214) [Enhancement]
- A11y + i18n pass across the Apps / Stats / shared surfaces (#1235) [Enhancement]
- Stats dashboard -- add 6 quick-summary cards surfacing the headline number from the deeper Stats sub-pages... (#1243) [Bug]
- Seerr app -- expanded request stats on the card/extras (#1247) [Enhancement]
- Added the 6 missing stats.samples.kind_* i18n keys (kind_stats='Container stats', kind_host_beszel_services... (#1263) [Enhancement]
- Stats -> Database growth projection now tracks the recent pace instead of over-reading (#1266) [Bug]
- Performance-review pass — chunked/offloaded every retention prune, memoized the Stats charts, closed the au... (#1284) [Enhancement]

### Prayer Times & Weather

- Weather provider overhaul + moon-phase widget + per-widget refresh + bookmark icon-URL field (#1070) [Enhancement]
- Weather fetch silently masked WeatherAPI in-body errors -> null history rows + empty widgets (#1097) [Bug]
- Weather admin UI polish + weather/moon stale-fallback display (#1099) [Bug]
- Weather widget 4x1 -- show the full user-configured forecast-day count instead of capping at 4 (#1118) [Bug]
- Prayer Times admin fixes. (A) Migrated the Service-enabled master toggle from a TUNABLE (tuning_prayer_time... (#1175) [Bug]
- Prayer-times custom-dashboard widget (#1177) [Enhancement]
- Prayer Times admin tab now has a "Recent samples" DB history panel mirroring Weather/Public IP (#1180) [Enhancement]
- Hard rename: the per-user topbar "Weather location" is now the shared "Your location" (userLat/userLon/user... (#1183) [Enhancement]
- Prayer-times topbar widget (#1193) [Enhancement]
- Prayer reminders — Fajr reminder silently skipped for users east of UTC (#1202) [Bug]

### Speedtest

- Speedtest Tracker app -- full encapsulation under per-app file structure (#1077) [Enhancement]
- App-skill framework + Speedtest run_speedtest skill -- the first of a per-app AI-skill pattern (#1144) [Enhancement]
- AI speedtest skill — live "show latest" + the web-dispatch bug that blocked it (#1154) [Bug]
- App-drawer speedtest skill result -- download / upload icons now render in distinct colours (#1162) [Enhancement]
- Speedtest averages window made a per-instance setting + label clarified (#1164) [Enhancement]
- App "Show extras" unified into ONE bidirectional control across the gear-flip card settings + the Admin ->... (#1165) [Enhancement]
- Bazarr app integration with extras (per-app encapsulation pattern, like Speedtest/APC) (#1185) [Enhancement]
- APC app card — show a loading/error/empty placeholder on the Apps tile like Speedtest (#1201) [Bug]
- Speedtest result image in the app drawer is wider (200px -> 350px) -- it's a detailed chart, not a small po... (#1262) [Bug]
- Speedtest result image clipped off the right edge in the app drawer on mobile (operator screenshot: the SPE... (#1276) [Bug]

### Media requests (Seerr)

- Seerr (Overseerr / Jellyseerr) app integration + AI movie request/suggest + TMDB wiring (#1187) [Enhancement]
- App cards: show the running app version (like Seerr/Bazarr) on AdGuard Home + Pi-hole (#1188) [Enhancement]
- App drawer Seerr "suggest a movie" result was unusable: the prose lines (movie overview / "Say request …")... (#1192) [Bug]
- Seerr 'suggest a movie' — widen the candidate pool for heavily-filtered users + make it operator-tunable (#1197) [Enhancement]
- Seerr suggest-a-movie — added a top-billed cast line (#1215) [Enhancement]
- TMDB poster images in the AI skill panel (Seerr suggestions) rendered as DIRECT image.tmdb.org links, which... (#1232) [Enhancement]
- Seerr -- new "List requests" skill (seerr_requests) lists the actual request TITLES (with year) in the queu... (#1242) [Enhancement]
- Bug fixes in seerr / prowlarr / qbittorrent / snmp / port_scanner_udp / webauthn (#1273) [Bug]

### Media servers (Plex, Jellyfin, Emby, Tautulli)

- Wire Plex as a per-app integration (logic/apps/plex.py + registry + static/js/apps/plex.js + _registry + pl... (#1245) [Enhancement]
- Wire Tautulli (Plex monitoring + statistics) as a per-host app, full encapsulation like Kavita (bespoke aut... (#1253) [Enhancement]
- Plex/Tautulli media-display fixes (3 operator-reported): (1) Plex AND Tautulli 'Recently added' grouped onl... (#1260) [Enhancement]
- Wired Tracearr (Plex / Jellyfin / Emby fleet monitoring + account-sharing detection -- github.com/connorgal... (#1267) [Enhancement]
- Plex 'What's playing on Plex' skill -- enhance the UI from a single text line per stream to the rich-item c... (#1274) [Enhancement]
- Tautulli 'Who's watching now' skill -- same rich-item enhancement as the Plex now-playing one (operator: 'd... (#1275) [Enhancement]
- Wired Jellyfin (open-source media server -- github.com/jellyfin/jellyfin) as a per-app integration followin... (#1278) [Enhancement]
- Added Emby as a new app template + wired the Emby application end-to-end (operator: 'add emby as a new app... (#1279) [Enhancement]

### *arr stack, downloads & transcoding

- qBittorrent catalog template port changes (#1040) [Enhancement]
- Radarr app integration (movie library manager) (#1203) [Enhancement]
- Bazarr — wired meaningful skills beyond status (#1210) [Enhancement]
- Sonarr app integration (TV-series manager) (#1213) [Enhancement]
- Wired Lidarr (music *arr) as a per-host app, full encapsulation like Sonarr/Radarr (#1222) [Enhancement]
- Radarr/Sonarr/Lidarr extras (#1224) [Enhancement]
- Wired Readarr (book/audiobook *arr) as a per-host app, full encapsulation like Lidarr/Sonarr/Radarr (#1226) [Enhancement]
- Release dates in *arr upcoming/calendar skills shown as raw ISO YYYY-MM-DD (#1231) [Bug]
- Wire Prowlarr (indexer manager, *arr stack) as a per-host app, full encapsulation like Lidarr/Readarr (it's... (#1233) [Enhancement]
- Wire Kavita (self-hosted digital library / reader) as a per-host app, full encapsulation like the *arr fami... (#1234) [Enhancement]
- Prowlarr app card -- "Apps synced" line restyled from raw text to brand-icon chips (#1249) [Enhancement]
- Radarr 'Upcoming movies' skill result now shows movie POSTERS next to each title (richer drawer UX) (#1255) [Enhancement]
- qBittorrent VueTorrent WebUI check + auto-update AI skills (#1261) [Bug]
- Lidarr + Readarr download-queue posters now resolve reliably (operator: Radarr/Sonarr OK after the remote-f... (#1268) [Enhancement]
- Wired Tdarr (distributed media-transcode automation -- github.com/HaveAGitGat/Tdarr) as a per-app integrati... (#1271) [Enhancement]
- Enhanced the STORAGE display in the *arr app drawers (Radarr / Sonarr / Lidarr / Readarr) -- operator: 'mak... (#1277) [Enhancement]

### DNS, ad-blocking & notification apps

- AdGuard Home catalog template (#968) [Enhancement]
- Pi-hole per-app integration (#1167) [Enhancement]
- App-skill audit-trail gaps closed (surfaced by the review pass; directly affects the Pi-hole + AdGuard flee... (#1168) [Enhancement]
- AI-context ts_display never stamped for AdGuard / Pi-hole `last`: registry.available_app_skills_context rea... (#1169) [Bug]
- Public IP — OmniGrid lagged well behind a dedicated DDNS updater on WAN IP changes (#1205) [Enhancement]
- CodeQL py/weak-sensitive-data-hashing on logic/apps/pihole.py:_sid_key (#1217) [Bug]
- Wired AdGuard Home Sync (bakito/adguardhome-sync) as a per-host app, full encapsulation pattern (#1219) [Enhancement]
- Wired ddns-updater (qdm12/ddns-updater) as a per-host app (#1227) [Enhancement]
- AdGuard Home Sync 'Recent sync logs' skill result is now pretty-formatted instead of a raw JSON dump (#1257) [Enhancement]
- Wired Apprise (caronc/apprise-api -- the notification gateway OmniGrid itself uses) as a per-app integratio... (#1258) [Enhancement]

### Forgejo, Beszel, Pulse, Webmin, Node Exporter & Portainer

- Discovery wizard. Scan a curated host's port table (port-scan results + node-exporter listen ports if avail... (#962) [Enhancement]
- App catalog template batch (Dozzle / Forgejo / MariaDB / PostgreSQL / MongoDB / InfluxDB) + port-scan coverage (#1004) [Enhancement]
- Forgejo + Portainer app-icon render height (too tall vs sibling brand icons) (#1016) [Bug]
- Pulse changelog not displaying -- release-notes resolver `get_release_notes` in `logic/registry.py` require... (#1082) [Enhancement]
- Host-drawer Beszel services -- add a live Refresh that re-probes the hub for fresh per-unit status (#1123) [Enhancement]
- Per-app instance Test-connection now shows a '✓ Last tested Xm ago' chip (parity with Portainer/OIDC) (#1221) [Bug]
- Wired Forgejo (self-hosted Git service -- a Gitea fork) as a per-app integration end-to-end following the p... (#1280) [Enhancement]

### AI Assistant, Cmd-K & Conversations

- Apps discovery exposed to the AI command palette (code-review Audit O) (#983) [Enhancement]
- Public-IP widget in Apps shows no data + IP history persisted to DB for AI questions (#1066) [Enhancement]
- AI answers per-host telemetry questions directly + auto-resolves a single matching host (#1092) [Enhancement]
- Performance -- memoize the AI-sidebar markdown render (#1093) [Bug]
- AI assistant renders inline image previews (#1156) [Bug]
- AI host status — the assistant labelled all 'problem' hosts as 'Down' (7) while the web showed only 1 down (#1212) [Bug]
- AI cleanup count discrepancy (#1216) [Bug]
- Web AI — selecting a skill action (e (#1229) [Bug]
- AI palette can answer 'what was I looking at on the other tab / desktop / phone?' -- the cross-device-hando... (#1238) [Enhancement]

### HTTP probe & Service probe

- Apps HTTP probe now falls back to GET when a service rejects HEAD (#980) [Bug]
- HTTP probe failed with "server rejected the SNI (unrecognized name)" even with verify SSL unchecked (#1037) [Bug]

### Public IP widget

- Public-IP card -- ISP brand-icon graceful-fallback when the matched brand's SVG file doesn't exist on disk... (#1081) [Bug]
- Public-IP change-detection sampler + Admin loading-state (#1096) [Enhancement]
- Public-IP widget no-data + geo-flag fixes (#1108) [Bug]
- Public-IP widget -- 3x1 / 4x1 IP hero too big, hiding country + AS (#1128) [Bug]

### Authentication, passkeys, OIDC & 2FA

- Authentik Test ✗ surfaces actual detail + Test button stays clickable after a failure (#1071) [Bug]
- Authentik admin -- Clear / Copy buttons have different styles; unify them (#1116) [Enhancement]

### Security (CodeQL & token discipline)

- CodeQL SSRF + path-injection findings (#960) [Enhancement]
- CodeQL py/stack-trace-exposure fix at the release-notes handler (main.py api_registry_release_notes) (#994) [Bug]
- CodeQL security alerts #491 + #492 (two fixes) (#1195) [Enhancement]
- Remediated the CodeQL alert 'Use of a broken or weak cryptographic hashing algorithm on sensitive data' (py... (#1283) [Bug]

### Real-time / SSE event stream

- Cross-tab Cleanup sync. **Re-opened for validation (#1023) [Bug]

### SNMP

- SNMP iDRAC (Dell-marked host) timing out (#998) [Enhancement]

### Ping

- Ping Test double-checkmark fix (#1073) [Bug]

### UPS / battery

- Added APC built-in catalog template to `logic/service_catalog.py:_BUILTIN` (#1069) [Enhancement]
- APC app -- per-instance + per-template `Show extras` checkbox (#1076) [Bug]
- APC app card adjustments (5 parts) (#1132) [Enhancement]
- Renamed the .apps-card-ups-value CSS class to the generic .apps-card-stat-value across all 13 app-extras pa... (#1244) [Enhancement]

### Provider chips, icons & status pills

- Host-drawer Apps sub-tab + per-host chip strip (#963) [Bug]
- Dedupe service-chip probe-target resolution into one shared helper (#978) [Enhancement]
- Hosts page: app chips below the provider chips (#995) [Enhancement]
- i18n Audit P — hardcoded chart/chip text (#999) [Bug]
- App-chip icon resolution now prefers the catalog template's icon field over its slug (#1002) [Bug]
- Duplicate app chips on a host (#1026) [Bug]
- Port-chip status dot moved BEFORE the number for parity with Apps (#1039) [Enhancement]
- Unified the faulty/paused provider-chip colour to RED across every surface (#1043) [Bug]
- Host-drawer per-provider chip subtitle restored to STACKED lines (#1059) [Bug]
- App port-chip URL links de-blued (#1060) [Enhancement]
- Egyptian carrier brand icons + canonical-slug consolidation (#1083) [Enhancement]
- Release-notes (stack update "What's new") code chips shattered character-by-character at line wraps -- e.g (#1166) [Bug]
- Host-drawer Timeline range picker (24h/7d/30d) hand-rolls a btn-ghost chip strip with a :class active toggle (#1171) [Enhancement]
- Add app brand icons for GitHub + Google Cloud Source Repositories (GCSR) (#1196) [Enhancement]

### App cards, Apps page & custom dashboard

- Top-level "Apps" view + reusable service templates + multi-port probes (#961) [Enhancement]
- Apps view did not auto-load on page load / refresh (#974) [Enhancement]
- Apps section i18n — route tooltip / latency-unit concatenations through t() format strings (#975) [Bug]
- Apps view not reflecting instance edits (#985) [Enhancement]
- Apps view (main page) group-by-host mode (#987) [Enhancement]
- Apps-view + UI-review-finding batch (#1003) [Bug]
- Per-row apps-count badge on the Hosts view (#1009) [Enhancement]
- Apps-view app-detail drawer 'Show debug' now wired to the Admin -> Debug debug_panel_enabled tunable (+ adm... (#1019) [Enhancement]
- Apps view ("By app" cards) redesigned: 3x app logo on the LEFT, all content to its right (#1020) [Enhancement]
- Apps Custom dashboard — Homarr/Homepage-parity board (Phase 1, fully shipped; folds in the edit/lock + hete... (#1053) [Enhancement]
- Apps Custom Phase-1 polish (#1057) [Enhancement]
- Apps Custom layout not persisted across restart (#1065) [Bug]
- Apps Custom widget + card UX overhaul (#1067) [Enhancement]
- Apps Custom dashboard -- per-card SIZE presets + flip-to-settings UX (#1086) [Enhancement]
- Apps widget + card UI fit (chosen-size responsiveness, round 2) (#1095) [Bug]
- Apps first-load perf -- a batch of first-paint, render-cadence, and reactivity fixes (#1102) [Bug]
- Apps custom dashboard -- MULTIPLE NAMED VIEWS (#1104) [Enhancement]
- Bug: Apps custom-dashboard card/widget HEIGHT preset (short/tall) not persisted on reload -- only the width... (#1120) [Bug]
- Bug fix: Apps custom-dashboard dropdown showed the wrong view vs the one displayed (#1126) [Bug]
- Apps custom dashboard -- duplicate app cards across sections (shadow copies) (#1129) [Enhancement]
- Unsectioned staging tiles double width in Apps edit mode (#1158) [Enhancement]
- apps-card-meta (the grey app description/meta line) sat with a large gap below the app title, worst in the... (#1178) [Bug]
- Apps-with-extras 'Loading …' placeholders now show a subtle spinner before the text (#1218) [Enhancement]
- Apps view — added an 'Extras only' capability filter (#1220) [Enhancement]
- Apps custom-dashboard named "views" -- per-view visibility (private/public) + edit permission (#1246) [Enhancement]
- Apps custom-dashboard tiles now tile flush -- fixed the wasted right-edge gap (#1252) [Enhancement]
- Apps custom-dashboard bookmark tile at 1x1 (half width) now shows its URL (#1256) [Enhancement]
- Topbar/Apps widget 'Updated X ago' now ages from the real data fetch time, not the SPA receive time (#1264) [Bug]

### App drawer, skills & actions

- Docker-link inline actions (#964) [Enhancement]
- App instance editor Link-to-Docker is now a searchable combobox + the Instances table shows a Docker-linked... (#1044) [Enhancement]
- App drawer now renders the per-app extras box (#1143) [Bug]
- App drawer "Probe now" button gave no feedback on click -- a successful probe showed nothing (only an error... (#1146) [Bug]
- App drawer: per-app SKILL buttons moved into their OWN boxed card AFTER the extras box (was crammed into th... (#1149) [Enhancement]
- ai_phrases is shipped-but-DEAD: every per-app SKILL declares ai_phrases but available_app_skills_context em... (#1170) [Bug]
- Unify app skill-button styling across every app (#1194) [Enhancement]
- App drawer skill-result boxes (#1200) [Enhancement]
- App drawer — added a top refresh button (mirrors the host drawer's header refresh) (#1206) [Enhancement]
- Unify app-extras presentation across ALL apps (#1230) [Bug]
- Show-extras toggle -- per-instance for non-aggregate apps + a scope label for aggregate ones (#1251) [Bug]
- Admin -> Apps instance list: added a tiny 'has extra features' indicator (icon-zap, --info accent) next to... (#1254) [Enhancement]
- App drawer 'Open in Stacks' button for Docker-linked apps (#1259) [Enhancement]
- App drawer per-app ACTION (skill) buttons must be gated on the per-instance 'show extras' checkbox, same as... (#1272) [Enhancement]
- Unified the drawer-header close-button height across all drawers (operator screenshot: in the app drawer th... (#1281) [Enhancement]

### App catalog, templates & instances

- TTL'd module-level catalog cache for `_shape_host_apps` (#966) [Enhancement]
- Fix NameError "name '_ops_mod' is not defined" when saving or re-seeding an Apps service-catalog template (#969) [Bug]
- Surface why Apps instances show degraded (#970) [Bug]
- Catalog-pinned apps were never probed (#976) [Bug]
- Apps instance editor + template inheritance (#982) [Enhancement]
- Admin -> Apps Instances grouping (group by host / service / none) (#984) [Enhancement]
- Monitoring / backup agent catalog templates (#988) [Enhancement]
- Discovery wizard no longer proposes a second app for a port another app on the host already owns (#1001) [Enhancement]
- Discovery wizard now shows ALL applicable templates for an unclaimed open port (user-decided design) (#1005) [Enhancement]
- Add Splunk catalog template (8080) (#1012) [Enhancement]
- Port-scanner coverage batch + pinned-app catalog-port union (#1022) [Enhancement]
- Bulk delete for Apps instances (#1031) [Enhancement]
- Built-in catalog template changes now PROPAGATE to existing DB rows (#1032) [Bug]
- App Templates search / filter box (#1033) [Enhancement]
- Apps instance editor: couldn't delete a port + no title icon (#1036) [Enhancement]
- App Instances search box (#1041) [Enhancement]
- Unified the mail icon — the Email (SMTP) catalog template now uses the same icon as host mail (#1045) [Enhancement]
- Added a MySQL catalog template (#1049) [Enhancement]
- Added a Proxmox VE catalog template (#1051) [Enhancement]
- Add an OPNsense firewall catalog template: 80 http + 443 https (Web UI, open_url) (#1055) [Enhancement]
- Catalog-template probe semantics validated + fixed so a healthy app no longer reads 'down' (#1058) [Bug]
- Admin -> Config tunables save/dirty audit + registry drift audit (#1134) [Bug]
- Per-app data-cache TTL operator-configurable IN THE APP (not global Config TUNABLES) (#1173) [Enhancement]

### Drawer, charts & sparklines

- Apps detail + debug drawer (mirrors the host drawer) to diagnose why an app on a host isn't working (#977) [Enhancement]
- Host drawer port-scan -> mapped-app annotation (#989) [Enhancement]
- Copy button in the Apps debug drawer (#1010) [Enhancement]
- Host-drawer Apps "Probe all" button (4th report of "probe disabled" in the host drawer) (#1011) [Enhancement]
- Host-drawer HTTP-probe box 'Latency' value now thousand-separated (#1017) [Enhancement]
- Host-drawer Services/ports: documented asset ports NOT found by the latest port scan now show an alert marker (#1021) [Enhancement]
- Host-drawer FULL-refresh button (#1038) [Bug]
- Node-view sparklines sometimes blank -- two-cause fix (#1089) [Bug]
- Host-drawer Apps -- 'Probe failed' button to re-probe ONLY the down services (#1124) [Bug]
- Container / stack item drawer now shows the RUNNING app version after the name (#1223) [Enhancement]
- Item drawer Placement list showed phantom '? (#1225) [Bug]

### Hosts editor, Host groups & Hosts page

- Apps hosts_config writes routed through a single validated persist choke point (a code-review finding) (#981) [Enhancement]
- Fixed runtime NameError: name 'PortScanIn' is not defined on the port-scan route (#996) [Bug]
- Port scan now always includes the host's configured app/service ports (#997) [Enhancement]
- Pin-to-host picker converted to a searchable/filtering combobox (#1006) [Enhancement]
- Asset port section now surfaces scan-only ports (reverse of the existing scan->asset mismatch marker) (#1008) [Enhancement]
- app-asset.js IDE-warning cleanup (#1015) [Bug]
- Admin -> Hosts HTTP-probe: typed URLs were cleared on Save (#1018) [Bug]
- Port-scan: code DEFAULT_PORTS is now a FLOOR, custom CSV ADDS (no longer shadows) (#1025) [Bug]
- Remove-app-instance confirm popup now names the app + host (was generic) (#1027) [Bug]
- Pin-to-host modal: header pin icon + assign to MULTIPLE hosts at once (#1030) [Bug]
- Discover-on-host wizard polish (#1034) [Enhancement]
- Discover "pin selected" applies ONLY the host's matched ports (#1042) [Enhancement]
- Added port 7680 (WUDO — Windows Update Delivery Optimization peer-to-peer cache) to the port scanner's DEFA... (#1052) [Enhancement]
- Quieted the http_probe no-SNI-retry red-ERROR log noise for reachable-but-SNI-strict hosts (#1061) [Bug]
- Duplicate 'Host sampling paused' notifications -- one host re-notified every redeploy / hosts_config save i... (#1115) [Bug]
- Hosts top-nav red down-host count badge (parity with the Services offline badge) (#1121) [Enhancement]
- Distinct 'resuming' indicator for a just-resumed host (was indistinguishable from paused-in-error -- both red) (#1122) [Enhancement]
- Bug fix: Hosts provider / problem filter pills stopped filtering (#1125) [Bug]
- Bug fix: port_scan_refresh schedule starved hosts (most 5d old, some 22d old, despite a 1d schedule reporti... (#1139) [Bug]
- Apps card multi-host layout -- hosts side-by-side for 2+ host apps (#1163) [Enhancement]
- Port-scan default coverage extended to common TCP + UDP service ports so the app always probes the ports a... (#1181) [Enhancement]
- Hosts page — 'problem' filter showed a count that disagreed with the rendered rows (#1204) [Enhancement]
- Consolidated repeated external-URL/host string literals into a shared typed-constant module (operator asked... (#1265) [Enhancement]
- Admin -> Asset Inventory 'Cached snapshot' preview is now a collapsible JSON tree viewer instead of a flat... (#1285) [Enhancement]

### Admin & Settings pages

- Enforce typed-enum discipline for tunable / setting / env-var key references (#967) [Enhancement]
- Admin -> Apps tab strip restyle for cross-admin consistency (#986) [Enhancement]
- Admin -> Logs file viewer capped at 500 lines (#992) [Bug]
- Relocated three operator tunables out of Admin->Config into the domain sections that govern them (user-requ... (#1098) [Enhancement]
- settings_update audit now shows per-key OLD -> NEW values (#1100) [Enhancement]
- Admin tunables-UI consistency sweep (#1101) [Enhancement]
- Admin-section performance pass -- reactive-flush + event-loop optimisations across the admin surface, landi... (#1103) [Bug]
- Admin Providers -- per-provider collapsible Tunables menus (#1105) [Enhancement]
- Admin panel dirty/undirty corrections -- controls that didn't mark their panel's Save dirty (#1107) [Enhancement]
- Admin master-toggle loading indicator -- consistent spinner + "Loading…" pill on every master-toggle admin... (#1148) [Enhancement]
- Custom Apps edit-mode delete button -> pill (match settings/refresh) (#1157) [Enhancement]
- Admin-page gating consistency batch (#1176) [Enhancement]
- Admin -> Schedules "Create schedule" Kind dropdown now sorted alphabetically by its displayed (translated)... (#1179) [Enhancement]
- Image-proxy disk cache (operator request: cache images so the same picture isn't re-downloaded each view, e... (#1269) [Enhancement]

### Logs view & retention

- logs.py error-classification regex handles Python 3.11+ ExceptionGroup tracebacks (#972) [Bug]
- Thousands separators on ms log values (#1153) [Enhancement]

### Schedules & automation

- Scheduler wedged-run self-heal (#1050) [Bug]

### Notifications & toasts

- Update-stack 'What's new' release notes -- fixed raw HTML tags, GitHub alerts, and double bullets (#1138) [Enhancement]

### Topbar, login & branding

- app-topbar.js -- three IDE inspection findings cleared with genuine code fixes (no suppression) (#1088) [Bug]

### Filters, badges & pagination

- Apps: every app now shows its port pill (was multi-port only) + ports can be CLICKABLE links (#1028) [Enhancement]
- Stacks / Services -- hide the empty header-only table + show a reset-able empty-state when a filter matches... (#1130) [Bug]
- Bug fix: Nodes view kept showing the previous filter's containers after switching filters (#1131) [Bug]
- Apps-page tile refresh pill now spins + disables while the app loads its INITIAL extra/card data (operator:... (#1282) [Enhancement]

### Internationalisation & accessibility

- UI/UX review batch — frontend a11y / i18n / visual fixes, all shipped together (#1054) [Enhancement]
- UI/UX accessibility + token + i18n pass on the Apps surface (the ux-review action pass) (#1094) [Enhancement]
- Extended scripts/audit_html_drift.py with 3 commit-time a11y-structural checks: every aria-modal="true" dia... (#1236) [Enhancement]

### Database / migrations / data

- fmtResponseError migration (#1072) [Bug]
- Extract init_db() out of main.py into its own module (#1109) [Enhancement]
- Keyboard shortcuts — added 3 page jumps + rebalanced the cheat-sheet columns (#1241) [Enhancement]

### Internal cleanup, refactor & bug sweeps

- Consolidated per-sampler numeric-coercion helpers into a shared logic/coerce.py leaf module (a code-review... (#979) [Enhancement]
- app-minor-tools.js IDE-warning cleanup (#991) [Bug]
- PERF: frontend + backend performance optimization pass -- consolidates the perf-review implementation that... (#1013) [Enhancement]
- Code-review fix batch (all LOW findings from the review pass; shipped together) (#1056) [Bug]
- Code-review fix batch from the latest review report (#1064) [Bug]
- UX-review fix-all pass on the latest UX-review report (#1068) [Enhancement]
- Local linter (scripts/lint.py) -- IDE-inspection-mimicking + scripts/install_precommit.py hook installer (#1084) [Bug]
- Lint pre-commit shield -- checked-in, cross-machine, blocks on warnings (#1090) [Bug]
- Timestamp-format drift audit + consolidation (#1150) [Enhancement]

### Other improvements & fixes

- Per-port historical detail (#965) [Enhancement]
- UTF-8 encoding on Path.read_text() / open() calls (#971) [Enhancement]
- Post-split cross-module underscore-symbol wiring (#973) [Bug]
- http_probe TLS unrecognized-name (SNI) error (#990) [Bug]
- npm dev-dep bump: stylelint 16.26.1 -> 17.12.0 and stylelint-config-standard 36.0.1 -> 40.0.0 (package.json... (#993) [Bug]
- Convention-violations review pass (#1000) [Bug]
- Apps "probe not enabled / disabled" diagnosed + debug panel enhanced (#1007) [Bug]
- PERF: replace x-show with x-if on costly subtrees so they unmount when hidden (#1014) [Enhancement]
- Top-bar recent-tabs privacy: a user now only sees THEIR OWN tabs, never other users' (#1024) [Bug]
- Probe correctness for UDP + legacy-TLS endpoints (two user-reported "always down / handshake error" cases) (#1029) [Bug]
- HTTP-probe "Accepted status codes (CSV)" field cleared on every keystroke (#1035) [Bug]
- Open-as-URL port link no longer appends health-check probe paths (#1046) [Enhancement]
- App loading spinners now show a "Loading…" label (#1048) [Enhancement]
- Backend-unreachable top banner (#1062) [Enhancement]
- Service degraded/down counter not displayed (#1063) [Enhancement]
- MAJOR stability fix — tracemalloc default-ON was the crash-loop root cause (#1074) [Bug]
- SPA -- xterm.js lazy-load eliminates steady-state Chromium `[Violation] Added non-passive event listener to... (#1080) [Bug]
- SPA -- requestAnimationFrame violation probe (opt-in diagnostic) (#1085) [Enhancement]
- Sprite-preload "preloaded but not used" console warning fixed (#1091) [Bug]
- Update-stack release-notes popup -- render markdown/HTML annotations + Copy button (#1106) [Enhancement]
- Item/stack icon improvements (#1112) [Enhancement]
- Reload-button consistency (#1113) [Bug]
- Shared Test-connection component (#1117) [Enhancement]
- Moon widget -- phase name cropped at 1x2 / 2x2 (#1127) [Enhancement]
- Clock (digital) widget -- enlarge clock + date/location in 3x2 / 4x2 (#1135) [Enhancement]
- deps(pip): bump python-multipart from >=0.0.29 to >=0.0.30 in requirements.txt (Dependabot dependency bump) (#1142) [Enhancement]
- Remove-containers confirm dialog UI -- long Swarm task-container names (one unbreakable token) sat far righ... (#1145) [Enhancement]
- Run-speed-test skill observability (#1151) [Bug]
- Release-notes renderer now handles git-cliff / conventional-commit format (#1155) [Enhancement]
- Finish the [06] fleet-module dedup follow-up: after the _common.py fleet helpers, the run_skill dispatch la... (#1172) [Enhancement]
- Stacks/Services tables — long resource name (e (#1184) [Bug]
- Four raw issues fixed in one batch (#1228) [Enhancement]
- History page: a long op target (an ssh_run target is the FULL command -- hundreds of chars) overflowed the... (#1270) [Enhancement]

## [1.5.0] — 2026-05-23

Fifth MINOR cut on top of `1.4.0` — rolls up **162 closed issues** under the 1.5.0 milestone (115 enhancements, 47 bug fixes). Every entry shipped to the live deploy as a PATCH bump on the daily CI cadence; this MINOR bundles them under a single tag for rollback / changelog purposes.

### Highlights

- Telegram bot integration end-to-end — `/link` / `/whoami` / `/weather` / `/host` / `/help` / `/cleanup` / `/version` / `/update` / `/hosts` commands, free-text AI grounded in real fleet state with Thinking-indicator, AI-fallback security gate, multi-chat-id CSV, history-table audit per dispatch, `/update all` correctness pass, AI palette can route notifications to one chat.
- Stats dashboard family expanded — five new sub-pages (Database, Samples, Network, AI Cost, Incidents) with per-table KPIs, per-provider drill-down popovers, daily-INSERT bar charts, fleet-wide throughput KPIs, finance-style ai_jobs view, incident-centric host_failure_events view, deep-link routes, range-pickers honouring user Formats preference.
- AI palette diagnostic capabilities — multi-round tool-use orchestrator (DB queries + SSH-gated diagnostics), AUTONOMOUSLY diagnoses container-bloat / disk-growth questions via tool-use instead of dispensing shell commands, schedule CRUD actions, `send_notification` action, fuzzy name-matching, refresh / reload aliases, action coverage + dispatch-gap closures.
- HTTP probe + Service probe — 7th + 8th host-stats providers with native HTTP / TLS-cert / DNS health checks, per-host editor, drawer card with status pill strip + TLS-expiry pill + DNS pill, master toggle + alias CSV, one-shot Test endpoint, Test-before-Save gate.
- Provider-chip UX overhaul — per-provider chip click → popover with last-probe result, chip-strip vocabulary legend popover, distinct chip-state colour mapping (paused = blue not grey), chip-rendering consolidation via canonical `_PROVIDER_DEFS` registry, Hosts-view chip strip moved to its own line under the row subline.
- Drift-from-baseline + Drift-chip — schema `host_baselines(host_id, metric, median, iqr)`, in-place reconcile, drift indicator on Hosts rows with sparkline + drift-chip UI enhancement.
- Multi-tab activity sync + Reproduce-here handoff — desktop tab's filter / drawer / sub-tab state can be mirrored into a phone in one click; richer per-tab state surfaces under the user-avatar menu.
- Settings-as-Code + Config Backup — new admin tab + schedule kind to back up + restore the operator-tunable admin configuration; saved-snapshots table matches Admin → Backups styling.
- Telegram + AI cross-cutting plumbing — every authorised Telegram command writes ONE history row, AI free-text calls log into `ai_jobs` + `history`, AI palette context carries time + weather, free-text replies honour `tuning_ai_max_tokens`.
- Sortable tables fleet retrofit + per-tab tunable search affordance + Admin → Logs pattern-filter axis — operator-typed search filters tunables across Admin / Providers tabs; logs filter strip gained pattern chips (auth_cooldown / probe_timeout / sql_drift / provider_paused / ws_disconnect / sampler_skip / cors_csrf).
- Internal modularisation — split `static/js/app.js` (~40k lines) into native ES modules (no build step); EnvKey + Tunable enum families replace bare-string keys for IDE jump-to-definition + autocomplete + typo detection; defer chart rendering until host drawer opens.
- Stack-update + Single-update popups now carry blast-radius preview + release-notes integration (fetched from registry image labels), Switch-to-tag affordance generalised across container + Portainer-deployed Swarm services, Recreate-silent-success detection for external containers.

### Telegram bot

- Telegram notification medium (#866) [Enhancement]
- Telegram notification medium (#867) [Enhancement]
- Telegram Phase 2.2 — user mapping + /weather + /whoami + /link + AI fallback + security gate (#868) [Enhancement]
- Notifications admin — Save-button split + Telegram link admin datatable + /hosts grouped lists + weather re... (#869) [Enhancement]
- Profile → Telegram card now auto-refreshes when `/link` or `/unlink` runs in Telegram (#871) [Bug]
- Telegram free-text AI replies now honour `tuning_ai_max_tokens` instead of a hardcoded 512 cap (#872) [Bug]
- Telegram `/cleanup` command (#874) [Enhancement]
- Telegram AI free-text is now grounded in real fleet state (#875) [Enhancement]
- Telegram AI now shows a "Thinking…" placeholder + native typing indicator while the model is running, then... (#876) [Bug]
- Telegram free-text AI calls now log into the `ai_jobs` table (Admin → AI Usage dashboard) and the `history`... (#877) [Enhancement]
- Every authorised Telegram command now writes ONE row into the `history` table at the dispatcher level (#878) [Enhancement]
- Telegram `/host <target>` command (#879) [Enhancement]
- Telegram `/help` now lists every command + groups aliases inline on one line (#880) [Enhancement]
- Telegram `/link` now detects an already-linked sender and refuses with a clear message before falling throu... (#882) [Bug]
- Telegram AI palette context now carries current time + (when configured) weather, closing the gap that made... (#883) [Enhancement]
- Telegram bot improvements (#888) [Enhancement]
- Lint + audit-trail + Telegram-command-correctness sweep (#891) [Bug]
- Telegram chat_id CSV — accept multiple authorised chats (option 2) (#892) [Enhancement]
- Telegram admin tab — merge Test Connection + Save toasts into one feedback box (#893) [Enhancement]
- Telegram /update preview wrongly includes orphan-type containers (#895) [Bug]
- Telegram AI gave inaccurate host status (#898) [Bug]
- Notifications admin Save UX split + Telegram AI host-status reconciliation + Test-pass optimistic stamp (#900) [Enhancement]
- Telegram `/update all` (and SPA bulk update) (#913) [Bug]
- Telegram `/version` build-time line now honours the sender's date/time format pref + deployment TZ (#915) [Bug]

### Stats dashboard

- Stats → AI Cost — added a bar-chart visualisation of the existing `avg_response_time_trend` data (backend a... (#812) [Enhancement]
- Stats section in the user-avatar menu (#816) [Enhancement]
- Stats Dashboard — new Database sub-page (#821) [Enhancement]
- Stats — new Samples sub-page with per-table KPIs (#823) [Enhancement]
- Stats → Database polish — Rows column populated + thousands separator + chart gridlines (#824) [Enhancement]
- Stats — new Incidents sub-page (incident-centric view of host_failure_events) (#826) [Enhancement]
- Stats — new AI Cost sub-page (finance-style view of ai_jobs) (#827) [Enhancement]
- Stats — new Network sub-page (fleet-wide throughput KPIs) (#828) [Enhancement]
- Stats data wired into AI palette context (#829) [Enhancement]
- Stats deep-link routes — `/stats` + `/stats/<sub>` page-refresh returns 404 (#830) [Enhancement]
- Stats → Network — burst-rate table now follows the page's selected range (#833) [Enhancement]
- Stats charts loading effect (#834) [Enhancement]
- Stats → Samples — per-provider drill-down popup landed (#844) [Enhancement]
- Stats → Samples — added `<hr>` separators around the "Per table breakdown" section AND a new bar-chart sect... (#855) [Enhancement]
- Stats page rendering blank -> root-caused as a TWO-bug interaction (#884) [Bug]
- Stats sub-tab section-range pickers (#909) [Enhancement]
- Stats charts — every x-axis date label now honours the user's Formats preference (#910) [Enhancement]
- Native HTTP / TLS-cert / DNS health probe provider (7th host-stats provider) (#925) [Enhancement]
- Stale "Host stats" terminology cleanup + Pulse/Webmin tab-label i18n fix (#941) [Bug]
- i18n concat-shape sites migrated to canonical template-with-placeholders shape across the Stats dashboard t... (#947) [Enhancement]
- Stats dashboard grid-template extracted from inline `style=` to `.stats-card-grid` class (#948) [Enhancement]
- Bug batch — three user-flagged regressions caught after the per-chip popover + chip-strip legend + Stats →... (#956) [Enhancement]

### AI Assistant, Cmd-K & Conversations

- AI palette: schedule CRUD actions (operator-requested follow-up to the retag_image AI dispatch) (#810) [Enhancement]
- AI conversation history not preserved across different computers (#814) [Enhancement]
- Admin → AI Integration dashboard popups (#815) [Enhancement]
- AI sidebar mode select aria-describedby (#832) [Enhancement]
- AI palette dispatch gaps closed (#835) [Enhancement]
- i18n missing-key batch + Cmd-K verb-overlap fix (a) added 24 `command_palette.action.*` keys for the newly-... (#837) [Enhancement]
- AI palette refresh / reload aliases (#840) [Enhancement]
- AI palette name-matching — fuzzy search + forward stack field + raise item cap (#849) [Bug]
- History → AI-Diagnose button on error rows (#856) [Enhancement]
- AI palette system prompt — taught the AI how to diagnose "Collecting data" sparkline placeholders (#863) [Bug]
- AI palette action coverage + Portainer/asset-inventory/NE TUNABLE wiring discipline (#865) [Enhancement]
- AI reports wrong host count -> answers with the prompt-sample cap (30) instead of the actual fleet size (#886) [Bug]
- AI palette — new `send_notification` action lets the operator route a custom (operator-typed) message to ON... (#899) [Enhancement]
- AI palette deeper diagnostic capabilities (#902) [Enhancement]
- AI palette diagnostic tools (#906) [Enhancement]
- AI palette now AUTONOMOUSLY diagnoses container-bloat / disk-growth questions via tool-use instead of dispe... (#916) [Enhancement]
- SPA hint when AI provider times out without fallback engaging (#924) [Enhancement]
- app-ai.js noinspection-strategy revised + 5 fire-and-forget call sites made explicit (#939) [Bug]

### HTTP probe & Service probe

- Hosts view — Ping (HTTP probe) provider icon missing from the per-host provider-chip strip in the row header (#928) [Bug]

### Authentication, passkeys, OIDC & 2FA

- Portainer + OIDC test buttons (#820) [Enhancement]
- Lint-discipline batch — `_safe_int` / `_safe_float` / `_int_or_none` / `_float_or_none` helper pattern adop... (#890) [Bug]

### Security (CodeQL & token discipline)

- CodeQL `py/path-injection` alert on `api_serve_avatar` (#808) [Bug]
- CodeQL `py/path-injection` on `/api/admin/config-backup/saved/{name}` family (#817) [Enhancement]
- fixes — visual + CSS token discipline (#901) [Enhancement]

### Real-time / SSE event stream

- SSE cross-tab self-filter gap closed for `port_scan:completed` (#838) [Enhancement]
- Admin → Config Backup — Saved snapshots table now matches Admin → Backups table styling for cross-tab consi... (#853) [Enhancement]
- Multi-tab activity sync panel shipped (#857) [Enhancement]
- Tab-activity richer state + Reproduce-here handoff (#905) [Enhancement]

### SNMP

- SNMP gate consistency — chip + chart-mount + bar-capability all consume the SAME strict `_snmpHasProbeTarge... (#805) [Bug]
- Host status taxonomy false-positives — SNMP-mapped + _stale_fields fixes (#907) [Bug]

### UPS / battery

- Hosts page CPU bar locked at full red on APC UPS (and similar UPS / printer / dumb-network-gear hosts that... (#860) [Bug]

### Beszel, Pulse, Webmin, Node Exporter & Portainer

- Hosts-page header — add Portainer "Open" button alongside the existing Beszel + Pulse buttons (#803) [Enhancement]
- Container recreate fix — `_do_update_container` POSTed to `/containers/{id}/recreate?PullImage=true` with N... (#806) [Bug]
- Pulse + NE sample-interval tunables with inherit semantics shipped (#843) [Enhancement]
- Switch-to-tag affordance now available for Portainer-deployed Swarm services (was previously container-only) (#922) [Bug]

### Provider chips, icons & status pills

- 5G + FTTH router brand icon refresh (#798) [Enhancement]
- Host drawer enabled-agents chip (#842) [Enhancement]
- Host-row sparkline + drift-chip UI enhancement (#854) [Enhancement]
- Toolbar provider chip visibility now gates on CONFIGURED hosts, not probe-succeeded hosts (#926) [Bug]
- Admin → Hosts per-row provider chip strip (#934) [Enhancement]
- SPA chip-rendering consolidation + Hosts view chip-line layout (#936) [Enhancement]
- Per-provider chip click → small popover with most-recent probe result (workflow-compressor; no new backend... (#951) [Enhancement]
- Provider chip-state vocabulary popover (#952) [Enhancement]
- Provider chip-state colour distinctness (#957) [Bug]
- Profile → Notifications per-category provider chips now always render on their own line below the category... (#959) [Enhancement]

### Drawer, charts & sparklines

- Host drawer charts — X-axis flips to MMM-d (e (#813) [Enhancement]
- Host drawer Temperature + GPU Power Draw charts stuck on "Collecting data" when local samples lack temps/gpus (#825) [Bug]
- Host drawer charts — range-aware x-axis tick density (#845) [Enhancement]
- Drawer-chart bucketing — unified 120-point density across every host-drawer time-series endpoint (#846) [Bug]
- Two operator-flagged drawer-region cleanups (#847) [Bug]
- Debug panel for Stacks / Services / Nodes drawers (mirrors the host-drawer panel) (#861) [Enhancement]
- Idle-item "Collecting data" false-positive on 0% CPU services (#862) [Bug]
- Shared `og-range-picker` Alpine component template + multi-pass include expander (#903) [Enhancement]
- Service / container drawer now surfaces every exposed port with a clickable external link (#917) [Enhancement]
- Host-drawer chart `Last sample Xm ago` label now reflects newest raw-sample age regardless of window (#927) [Bug]
- Defer chart rendering until host drawer opens (#932) [Enhancement]
- Drawer-heading font-size token family extended for the 16-18 px range (#942) [Enhancement]
- SPA modules noinspection blocks expanded to match the categorical pattern across `app-charts.js`, `app-noti... (#943) [Bug]
- RTL flip for drawerNode + drawerItem + `.host-drawer` (#944) [Enhancement]
- Shared og-range-picker ARIA contract corrected (#946) [Enhancement]
- Asset-source SVG indicator in the host drawer header gained a screen-reader-readable accessible name (#949) [Enhancement]

### Hosts editor, Host groups & Hosts page

- Standalone containers on worker nodes (#804) [Bug]
- Retag affordance generalised — "Switch to :latest" becomes "Switch to tag…" (#807) [Enhancement]
- Single-update popup release-notes integration (#848) [Enhancement]
- Stack-update confirm dialog now carries a blast-radius preview (#851) [Enhancement]
- Drift-from-baseline indicator on Hosts rows (#859) [Bug]
- Consolidate the `hosts_config` JSON parse skeleton across 10+ sampler / consumer sites (#896) [Enhancement]
- Problem-hosts triage filter (#904) [Enhancement]
- Host status taxonomy false-positive (#908) [Bug]
- Recreate-silent-success on external containers (#914) [Enhancement]
- HTTP-probe per-host editor (#929) [Enhancement]
- Port-scan schedule fired every cadence-tick but EVERY host fell into `skipped["disabled"]` because the sche... (#933) [Bug]
- Removed redundant "N providers" / "2 providers" subline text from the host card on the Hosts view (#940) [Enhancement]
- Hosts view desktop grid — Disk column width parity with CPU + Memory; freed space reallocated to the Host (... (#945) [Bug]

### Admin & Settings pages

- Settings-as-Code — new admin tab + schedule kind to back up + restore the operator-tunable admin configurat... (#809) [Enhancement]
- Admin polish batch — three operator-flagged UI consistency passes shipped together (#811) [Enhancement]
- Audit log date filters honour the user profile date format (#818) [Enhancement]
- Test Connection buttons across admin (#819) [Enhancement]
- Stuck-disabled admin reload buttons (#822) [Bug]
- Admin write-actions audit-trail (#831) [Enhancement]
- Plain-settings escape hatch migration (#836) [Enhancement]
- TUNABLES coverage gap audit batch (#839) [Enhancement]
- Admin audit-trail coverage batch (#841) [Enhancement]
- Two new TUNABLES promoted from hardcoded constants (#852) [Enhancement]
- Tunable enum replaces bare-string tunable keys for IDE jump-to-definition + autocomplete + typo detection (#873) [Enhancement]
- Admin checkbox + table loading UI canon (#889) [Enhancement]
- Switch host_failure_events (incidents) from "keep forever" to a tunable 90-day default retention (#897) [Enhancement]
- Shared `<og-tab-strip>` partial (#950) [Enhancement]
- Admin → Logs filter strip gained a third axis (pattern) alongside the existing source-tag + severity filters (#953) [Enhancement]
- Sortable tables fleet retrofit (#954) [Enhancement]
- Per-tab tunable search affordance (#958) [Enhancement]

### Schedules & automation

- Incidents (host_failure_events) is no longer pruned (#885) [Enhancement]
- Schedule "Last execution" column displayed the WRONG fire-time after a container restart (#937) [Bug]

### Notifications & toasts

- Notifications popup — cluster-pivot toggle (#858) [Enhancement]
- Notification template `{time}` placeholder now renders against the recipient's `ui_prefs.datetime_format` i... (#870) [Enhancement]

### Topbar, login & branding

- Profile → Topbar widgets weather section °C / °F unit preference (#799) [Enhancement]

### Internationalisation & accessibility

- Missing i18n key `admin_hosts.ssh_password_override_placeholder` added to `static/i18n/en.json` (#920) [Enhancement]

### API endpoints & backend helpers

- EnvKey enum + env_get helper (#921) [Enhancement]

### Documentation

- Document GHCR pre-built images (#801) [Enhancement]

### Internal cleanup, refactor & bug sweeps

- Phantom stale-banner cleanup (#800) [Bug]
- batch — three visual-consistency fixes from the an earlier pass (#850) [Bug]
- Fix against the full bug list (#864) [Bug]
- Fixed HTML lint warnings on `static/index.html`: 6× "Namespace 'x-transition' is not bound" + "Element main... (#881) [Bug]
- Fix lint issues in main.py (#894) [Bug]
- IDE-inspector cleanup sweep across `logic/` (#911) [Bug]
- Alpine ReferenceError: rpLeadingLabel is not defined (#918) [Bug]
- main.py lint sweep — real-bug subset (#919) [Enhancement]
- Cleanup (N) button — multi-click bug + stale count (#923) [Bug]
- Code-review batch — bugs + enhancements landed together (#930) [Bug]
- Split `static/js/app.js` (~40k lines) into native ES modules (#931) [Enhancement]
- text-[9.5px] arbitrary Tailwind value re-introduced across 10 sites (#935) [Enhancement]
- Three operator-flagged CSS / loading-state fixes batched together (#938) [Enhancement]
- Stale-terminology lint — CI script greps user-visible surfaces for renamed-section leaks (#955) [Enhancement]

### Other improvements & fixes

- Port-scan known-ports + detected-ports UX polish (#802) [Enhancement]
- Standalone Public-IP / ISP / ASN lookup subsystem (#887) [Enhancement]
- UI busy-state never extended past 3 s (#912) [Enhancement]

## [1.4.0] — 2026-05-10

Fourth MINOR cut on top of `1.3.0` — rolls up **264 closed issues** under the 1.4.0 milestone (196 enhancements, 68 bug fixes). Each item shipped continuously through the PATCH cadence; this MINOR bundles them under a single tag for rollback / changelog reference.

### Highlights

- **AI Assistant** — full conversational sidebar replacing the modal Cmd-K target, multi-provider (OpenAI / Anthropic / Gemini / DeepSeek) with generic `ai_max_tokens` knob, log-context window with secret-pattern redaction, `MEMORY:` / `MEMORY-FORGET:` directives, host-identity enrichment, conversation export (TXT + JSON), Approval / Autonomous mode toggle, jump-to-latest pill, fenced-code-block code panels with copy buttons.
- **Unified Cmd-K / Ctrl-K command palette** — multi-word query support, action commands + AI assistant + Phase 1 bulk operations, multi-action queries, chart-kind heuristics, long-press shortcut overlay.
- **Curated `address` field** — single dedicated probe target across port-scan / ping / SNMP / SSH; resolution chain unified end-to-end (`aliases[id] → <provider>_name → address → SKIP`), drawer port-scan gate with address-required banner.
- **On-demand port-scan provider** — TCP + UDP, scheduled refresh kind (`port_scan_refresh`), default-ports fleet expansion (50+ new ports), section-owned tunables, standalone admin sub-tab placed after SSH.
- **SNMP infrastructure** — vendor-aware walk pruning + signature narrowing, per-host walk serialisation for slow BMC-class agents, per-host `walk_concurrency` override, storage extractor pseudo-FS filter + per-host exclusion list, SNMP-only host main-row visualisation, Test-connection cool-down bypass.
- **Beszel / Pulse / Webmin local sample storage** — Beszel local sample table closes the read-through-only gap (charts no longer empty when the hub's `1m` aggregation tier ages out); sister Pulse + Webmin samplers so Pulse-only hosts get history. node_exporter ZFS multi-dataset pool dedup + camelCase `MemTotal` fix, TrueNAS disk-aggregate fix, Pulse v4 `hosts`-array extractor.
- **Drawer chart polish** — per-host Health Score (0-100) chip + breakdown popover, "What changed" Timeline tab, inline 1h-trend sparklines overlaid on Hosts-row CPU / Memory / Disk stat-bars, gap-detection no-bridge across multi-hour samples, disk-projection chart with confidence-band fork, AI-Assisted Incident Triage Drawer.
- **Hosts-view bulk actions** — multi-host sticky bottom bar, per-host audit rows on bulk pause/resume, partial-failure breakdown toast, `host:bulk_action_applied` SSE event for cross-tab fan-out, step-up reauth gate on destructive admin actions, idle-time progressive fill complementing scroll-driven lazy load.
- **Notifications & Apprise** — in-app notifications feature with cross-tab refresh, per-medium preferences in Profile → Notifications, page UX overhaul, bulk-pattern picker, swarm-agent unhealthy banner + one-click restart, scheduled autoheal with cooldown anchors persisted across container restarts, toast pause-on-hover/focus + explicit copy button.
- **CodeQL security sweep** — `py/full-ssrf` (defence-in-depth shared helper), `js/insecure-randomness`, `py/url-redirection`, `py/clear-text-logging-sensitive-data`, `py/path-injection` (`_safe_avatar_path` + persistent-log endpoints + node_modules path-traversal hardening).
- **i18n + A11Y** — `NOTIFY_TEMPLATE_DEFAULTS` migrated to i18n via new backend `logic/i18n.py` loader, WAI-ARIA radiogroup keyboard nav across the three on-page radiogroups, chart `?` info-bubble fleet-wide a11y refactor, additional accessibility batches.
- **Admin tab refactor** — `static/index.html` admin sub-tabs extracted to per-tab partials under `static/_partials/admin/`, Settings sub-sections to per-section partials, Test-before-Save gating across Portainer / OIDC / Asset Inventory tabs, Settings → Profile → Formats with a user-configurable datetime token grammar that propagates across every date / datetime render.
- **User UI preferences** migrated from localStorage to the DB so theme / sidebar width / drawer pinning / datetime format travel cross-browser + cross-machine. Hardware card section gate accepts snapshot-fallback hits so cached fields stay rendered when every live provider is offline.

### Added

- AI Assistant — multi-turn conversational sidebar (Cmd-K target), multi-provider abstraction (`logic/ai.py:ask_provider` + `ask_provider_with_fallback`), inline charts (`memory_history` / `cpu_history` / `disk_projection`), per-call cost / latency / token-usage dashboard, retry-once-on-overload gate (`AI_RETRY_*` tunables), conversation export to TXT / JSON (gated behind `tuning_ai_conversation_export_enabled`), persistent log-context window (default 7 days, secret-redacted, capped at `tuning_ai_log_context_lines`).
- AI memory — durable per-deployment lessons via `MEMORY:` (append) and `MEMORY-FORGET:` (delete by exact text) directives, backed by the new `ai_memory` SQLite table; admin-only routes `GET / POST / DELETE /api/ai/memory[/{id}]` plus `POST /api/ai/memory/forget`.
- AI palette context enrichment — host telemetry / weather forecast / log context flow into the prompt; `_buildAiPaletteContext` is the single source of truth shared between the modal palette and the sidebar.
- Curated `address` field on every host row — provider-independent probe target consumed by port-scan, ping, SNMP, and SSH (replaces the bare `host_id` fallback that was the source of fan-out-against-non-mapped-hosts regressions).
- On-demand port-scan provider (`logic/port_scanner.py`) — TCP-first with optional UDP companion (`logic/port_scanner_udp.py`); per-host opt-in via `hosts_config[].port_scan = {enabled, ports?, timeout_s?, concurrency?}`; banner-grab via best-effort first-256-byte read; persists to `host_port_scans` (one row per detected open port); emits `port_scan:completed` SSE.
- Scheduled `port_scan_refresh` schedule kind — periodically re-scans port-scan-enabled hosts with oldest-scanned-first selection, per-tick host cap, min-age gate, and per-host parallelism (`tuning_port_scan_schedule_*` tunables).
- In-app notifications — SQLite-backed `notifications` table with per-medium master toggles + per-event admin gates + per-user opt-in/out; Notifications popup behind the user-avatar dropdown with severity / event / unread filters; `prune_notifications` schedule kind for retention.
- Notification template editor — admin-only `/api/admin/notify-templates*` routes; per-event title + body overrides via `notify_template_<event>_title` / `_body` settings; curated `{name}` / `{type}` / `{actor}` / `{host}` / `{time}` / `{error}` / `{status}` placeholder whitelist; live preview + Send-test path.
- `swarm_agent_health` schedule kind — watches the in-process `_agent_health` map; either notify-only (`swarm_agent_unhealthy` event) or auto-restart the Portainer agent service (gated by `tuning_swarm_autoheal_cooldown_minutes`, anchor persisted via `swarm_autoheal_last_restart_ts` so cooldown survives container restarts); first-boot bootstrap creates a default 5-minute schedule when Portainer is configured.
- Settings → Profile → Formats — user-configurable datetime token grammar (default `dd/MM/yyyy, HH:mm:ss`) persisted to `ui_prefs.datetime_format`; the new `_applyDateTimeFormat(d, fmt)` + `_userDateTimeFormat()` helpers route every date / datetime / clock render (`fmtDate` / `fmtDateOnly` / `fmtDateTimeShort` / `tickHeaderClock` / `hostTimelineTimeLabel` / persistent-log freshness / samples-table `fmtTs`) through one shared parser. Time-only renders strip time tokens via `_stripTimeTokens` so a single user pref drives every variant.
- Per-host Health Score — derived from CPU / memory / disk / net / load / errors with a breakdown popover; chip rendered next to the hostname when the underlying provider data is rich enough.
- Host drawer "What changed" Timeline tab — per-host transition log from `host_failure_events` joined with `history` rows where the host was the target.
- AI-Assisted Incident Triage drawer — surfaces clustered failure events from `logic/triage.py` and offers the AI palette as a "explain this" handoff.
- Inline 1h-trend sparklines overlaid on the Hosts-row CPU / Memory / Disk stat-bars (lazy-loaded via IntersectionObserver, with snapshot fallback paint on cold load).
- Hosts-view bulk-action sticky bottom bar — multi-host pause / resume / SNMP-vendor / SNMP-tunables apply, partial-failure breakdown toast, per-host audit rows, `host:bulk_action_applied` SSE event for cross-tab fan-out.
- Step-up reauth gate (`POST /api/admin/reauth`) on destructive admin actions for local-password users; SSO users bypass via `_user_has_local_password` short-circuit.
- WAI-ARIA radiogroup keyboard navigation for the three on-page radiogroups (drawer chart range picker, Health filter chips, stat-bar threshold picker).
- Backend i18n loader (`logic/i18n.py`) — used by `NOTIFY_TEMPLATE_DEFAULTS` migration; reads from `static/i18n/<lang>.json` with English fallback so notification titles / bodies localise per recipient.
- New SSE event types: `host:provider_probing` / `host:provider_done` (per-host probe slice progress), `host:bulk_action_applied` (cross-tab fan-out for bulk Hosts-view actions), `port_scan:completed`.
- New tunables: `tuning_ai_max_tokens`, `tuning_ai_log_context_hours`, `tuning_ai_log_context_lines`, `tuning_ai_retry_*`, `tuning_ai_fallback_max_depth`, `tuning_ai_sidebar_width_px`, `tuning_ai_conversation_export_enabled`, `tuning_port_scan_default_*`, `tuning_port_scan_schedule_*`, `tuning_port_scan_udp_default_*`, `tuning_swarm_autoheal_cooldown_minutes`. Full table in [`docs/guidelines/env_example.md`](docs/guidelines/env_example.md).
- AI sidebar launcher hide — Settings → Profile → Topbar widgets toggle (Cmd-K still opens the sidebar regardless).
- Beszel local sample tables (`host_beszel_samples`, `host_beszel_services`) + lifespan-managed `host_beszel_sampler` so chart queries no longer depend on the hub's transient aggregation tiers.
- Pulse + Webmin lifespan-managed samplers (`host_pulse_sampler`, `host_webmin_sampler`) writing to `host_pulse_samples` / `host_webmin_samples`.
- Pulse v4 `hosts`-array extractor (`extract_pulse_host_stats`) — Pulse-agent-tracked Linux hosts that don't share the PVE-guest schema.

### Changed

- AI sidebar dirty-cue contract — amber ring + Unsaved pulse-dot tied PURELY to `<name>Dirty()`, never gated on `canSave<Name>()`. Earlier iteration coupled the two and suppressed the cue when a post-test edit re-locked the gate; three honest signals beat one mixed signal.
- Admin sub-tab markup extracted from `static/index.html` (≈14k lines) into per-tab partials under `static/_partials/admin/<tab>.html`, inlined at request time by `_render_shell()`'s `<!-- INCLUDE: ... -->` marker expansion. Settings sub-sections similarly extracted to `static/_partials/settings/<section>.html`. Master template down to ≈8k lines (~44% smaller).
- Provider chip state taxonomy normalised — `failing` → `pill-error` (red), `paused` → `pill-muted` (grey), healthy → brand colour. `pill-warning` (amber) is no longer used for either state; visual conflation between "currently producing a recoverable warning" and "auto-paused after repeated failures" was confusing the at-a-glance triage.
- Backend post-merge percent recompute — `_merge_one_host` re-derives `host_mem_percent` and `host_disk_percent` from the final merged `host_*_used` / `host_*_total` after every provider has contributed, so SNMP's naive `total - free` no longer leaks past NE's per-OS-aware bytes accounting. SPA helpers `memPercentOf(h)` / `diskPercentOf(h)` consume the recomputed value first; `fmtPercentLabel(v)` renders 1 decimal across the full range.
- `/api/me` `client_config` now includes `hosts_idle_fill_seconds`, `notifications_page_size`, `stat_bar_warn_pct`, and the AI sidebar / port-scan tunables surface so the SPA reads through one canonical channel.
- Snapshot persistence — `_merge_one_host` writes to `host_snapshots` only when at least one snapshot-eligible field came from a LIVE provider, so the freshness banner ("Last live data Xm ago") matches the chart's "Last sample N ago" instead of resetting on every drawer poll.
- Beszel hub-tier picker — `_pick_stat_type(hours)` selects retention-aware aggregation tiers (`≤1h → 1m`, `≤12h → 10m`, `≤48h → 20m`, otherwise `120m`) so 24h windows no longer return only the last hour.
- node_exporter parser handles `node_memory_MemTotal_bytes` (camelCase) and ZFS multi-dataset pool dedup so disk totals don't multiply by N datasets.
- Webmin extractor suppresses `host_cpu_percent` when any other provider is active (its single-shot `/proc/stat` snapshot is coarser than Beszel / NE longer-window samples).
- Apprise notify dispatch + the new `app` medium fan out via `asyncio.gather(return_exceptions=True)` so a failure in one medium doesn't drop the others; retry-once-on-transient-overload (HTTP 429 / 502 / 503 / 504) gated by the `tuning_ai_retry_*` knobs for AI provider calls.
- Synology dark-theme icon updated to a single-colour white-on-transparent variant; Linux Mint, Apprise, Seerr brand icons added or refreshed.
- Python container base bumped from `python:3.12-slim` to `python:3.14-slim`. Existing call sites updated for `datetime.fromtimestamp(ts, tz=timezone.utc)` / `datetime.now(timezone.utc)` (replacing deprecated `utcfromtimestamp` / `utcnow`).
- CI workflow moved to Node.js 24 ahead of the September 2026 Node 20 removal.
- Drawer-chart range picker (1h / 6h / 24h / 7d) persists across refresh via `localStorage.hostHistoryRange`.
- Topbar refresh spinner now stops when the underlying gather completes (no longer spins forever after a transient error).
- `swarm_agent_unhealthy` notifications fire on TRANSITIONS only — single incident emits one alert + one matching `swarm_agent_recovered`, instead of every cycle.
- TOTP audit rows go through `assert_op_type` so the canonical `op_type` registry catches typos at insert time.
- Notifications retention dial moved from Admin → Process Tunables to Admin → Notifications where users editing notification policy will find it.
- Per-medium notification preferences moved into Profile → Notifications.
- `notify` template defaults now resolve via `NOTIFY_TEMPLATE_DEFAULTS[event][kind]` with DB overrides, and missing placeholders render verbatim (`{key}`) via `SafeDict` rather than raising `KeyError` mid-dispatch.
- UI sprite caching switched from `no-cache` to `public, max-age=31536000, immutable` with content-hash query string.

### Fixed

- Cool-down log lines no longer flag as ERROR — `_severity_for` regex was matching the literal `failed` in cool-down skip messages and turning Admin → Logs red on benign skips. Skip / cool-down / deferred messages now use verbs that don't match the ERROR regex; resolved probe target is included in every log line so back-off tracing doesn't require host_id → alias cross-reference.
- Drawer charts no longer bridge across multi-hour sampling gaps with one fake-smooth line — gap detection inserts a break when consecutive samples are >2× the expected interval apart.
- Stack-header `upd` count no longer inflated by offline / orphan containers carrying stale image digests.
- `_kick_background_gather` returns the in-flight task ref instead of bool, so cold-cache callers `await` the same task the bg-refresh path just spawned (single-flight invariant).
- `_BACKGROUND_TASKS` defensive cap on the strong-ref set fires a WARN log line at the cap so a future spawn-site leak is visible instead of silently growing the set.
- SSE `host:provider_*` events thread `client_id` for self-filter so the originating tab doesn't echo-paint on its own action.
- SSE `host:provider_done` events carry an `ok: bool` outcome hint so the SPA chip can settle into the right post-probe state without a second round-trip.
- Hosts row in-place reconcile — never wholesale-replace `this.hosts` array. Backend transient errors (single tick) preserve the previously-known marker through the blip via `Object.keys(host)`-only assignment in `refreshHostRow`.
- Sparkline post-probe history backfill + flat-zero hide on cold load.
- Port-scan "Scan ports" drawer button stays disabled + spinning until the scan COMPLETES on the backend (was prematurely re-enabling on the queued response).
- Port-scan previous-cycle results are surfaced even when the master toggle is off (read from the persistent table; running NEW scans is still gated).
- Webmin master-toggle disabled-state respected by the section's dirty-tracking helper.
- TrueNAS disk-aggregate over-count from multiple `node_filesystem_*` series sharing one underlying pool.
- AI request-timeout retry honours the fallback chain instead of returning the first 30s timeout to the user.
- AI sidebar typing lag (textarea is now an uncontrolled DOM input with a throttled vanilla JS listener; per-keystroke Alpine reactivity removed).
- Beszel hub-batch single-flight — concurrent /api/hosts/one/{id} callers reuse one hub probe instead of fanning out N.
- AI palette markdown rendering — fenced code blocks render as proper code panels with copy buttons.
- AI weather context — `/api/weather` response now uses compact field names (`temp_c` / `humidity` / `wind_kmh` / `condition` / `forecast`) the SPA actually reads, replacing the verbose Open-Meteo schema that the SPA's `_buildAiPaletteContext` was looking through but not finding.
- AI palette chart-kind dispatch — heuristic correctly picks `memory_history` / `cpu_history` / `disk_projection` from the prompt content; irrelevant disk-projection charts no longer render on non-disk queries.
- Drawer keyboard navigation (←/→ to step through the visible filtered list) no longer re-fires `openHostDrawer` on every press.
- Apprise brand icon — switched to homarr-labs `dashboard-icons/webp/apprise.webp` source with the `<image href="data:...">` SVG-wrap pattern so the resolver's `.svg` extension contract holds.
- Toast notifications — pause-on-hover/focus + explicit copy button (replaces the auto-dismiss-only contract that was eating in-flight reads).
- SweetAlert page-jump on confirm popups — when validation fails on a row that's not on the current admin-editor page, the page-jump runs BEFORE `focusFirstFieldError` so the focus actually lands.
- `<asset-api-host>/admin/api` cache clear on `Type.ShortName` casing variations — `shape_asset` walks every plausible casing so the host drawer's `[<TYPE>]` prefix renders consistently.
- SQLite LIKE-pattern wildcard leak in three host-id sites (timeline + bulk-resume) — host_ids carrying `%` / `_` no longer match unrelated rows.
- Path-traversal guard on persistent-log endpoints + node_modules path-traversal hardening.
- `py/clear-text-logging-sensitive-data` at `logic/events.py:178` — event payloads no longer log secret-bearing fields verbatim.
- Stack-row `_stale` indicator + `cache_refreshing` / `hub_probing` SPA hints surface during the cold-load instant-paint window.
- `host_pulse_sampler` no longer raises `unknown tunable` on every tick (same drift class as the earlier Webmin sampler fix).
- `audit_template_data` placeholder validation rejects unknown placeholders at save time; deprecated placeholders flagged via the `NOTIFY_DEPRECATED_PLACEHOLDERS` map.
- `_clean_host_snmp.walk_concurrency` per-host override accepts the same `(lo, hi)` range as the global `tuning_snmp_per_host_walk_concurrency` (was 1..32 vs 1..16 — admin-set per-host=24 validated fine but global=24 silently clamped to 16).

### Security

- CodeQL `py/full-ssrf` triage across the probe modules — defence-in-depth refactored into a shared `url_safety` helper that classifies hostnames before outbound HTTP fan-out.
- CodeQL `js/insecure-randomness` cleanup at `static/js/app.js` — `Math.random()` fallback replaced with `crypto.getRandomValues` for the per-tab `client_id` and the WebAuthn challenge nonce.
- CodeQL `py/url-redirection` at `logic/oidc.py` — `next` parameter validated against a same-origin allowlist before redirect.
- CodeQL `py/path-injection` at `_safe_avatar_path` — avatar-write path traversal hardened via Path.resolve() + parent-directory containment check.
- Test-before-Save gate on Portainer / OIDC / Asset Inventory admin tabs — Save unlocks only when the form snapshot stamped at last-successful-Test matches the live form snapshot.
- Step-up reauth gate (`POST /api/admin/reauth`) on bulk-destructive admin actions for local-password users.
- `omnigrid.*` container label namespace lockdown — only the curated set (`omnigrid.url` / `omnigrid.name` / `omnigrid.icon` / `omnigrid.hide`) is consumed; arbitrary labels do not flow into the SPA.

### Internal

- Provider-name set canonicalised — `logic/host_metrics_sampler.py:_PROVIDER_PREFIXES` is the single source of truth; `main.py` imports as `_PROVIDER_AUTO_PAUSE_NAMES`. SNMP vendor MIB key set similarly canonicalised at `logic/snmp.py:_VALID_VENDOR_KEYS`. Notification mediums at `logic/ops.py:NOTIFY_MEDIUMS`.
- `op_type` canonical-name registry — `logic/ops.py:OP_TYPES` is the single source of truth; `assert_op_type(op_type)` gates every raw `INSERT INTO history` site so typos can't silently land bad rows. Eight schedule-runner sites in `logic/schedules.py` (gather_refresh / backup / asset_inventory_refresh / prune_logs / prune_notifications / swarm_agent_health / port_scan_refresh / config_backup) and five `main.py` sites (ssh_run / ssh_terminal / port_scan UDP-error / port_scan main / the dynamic-op_type `_bulk_write_history_rows` helper) now stamp `assert_op_type(...)` immediately before the INSERT — previously bypassed the registry so a typo would silently land in the audit table. `config_backup` added to `OP_TYPES` (was the one literal that didn't have a peer in the registry).
- `logic/db.py` + `logic/backups.py` lint sweep — extracted `_walk_hosts_config()` shared helper across the four `curated_*_hosts` functions; narrowed broad `except Exception` clauses to specific exception tuples per failure mode; explicit DB_PATH None guards inside `db_conn()` + `_snapshot_db_to` for type narrowing; tightened settings-version bump exception handling; dropped redundant default-equals-argument noise.
- `logic/ops.py` + `logic/schedules.py` + `logic/telegram_listener.py` lint sweep — moved `_sqlite3` alias to module-top of `ops.py` (was lazy-imported inside 4 try-blocks → PyCharm flagged reference-before-assignment); narrowed broad excepts per failure mode (`(httpx.HTTPError, OSError, ValueError)` for Apprise HTTP, `(sqlite3.Error, OSError, RuntimeError, ValueError)` for sampler IO blocks, `(ZoneInfoNotFoundError, ValueError, OSError)` for timezone resolution, `(asyncio.CancelledError, …)` with re-raise for cancellation-sensitive sleeps); renamed shadowed `e` variables in nested try-blocks; uppercase scope-shadowed `GRACE` locals in `schedules.py` renamed to lowercase `grace` per PEP 8; unused `params` in three runners (gather_refresh / backup / config_backup) renamed to `_params`; redundant `return (duration, status)` parens stripped across 8 sites; redundant `target_stack=None` kwarg-equals-default dropped from `_ops.new_op(...)` call sites; missing docstrings added to
  `Operation` + `Operation.log` + `Operation.done` + `Operation.to_dict` + `new_op` + `persist_history` + every `do_*` handler + `list_schedules`; bare `match.group()` in place of `match.group(0)` in three Telegram regex-sub helpers; the `_MD_ITALIC_STAR` regex's redundant `\*` inside the character class corrected to bare `*`.
- `record_provider_outcome(host_id, provider, ok, ...)` is now the canonical helper at every per-(provider, host) probe boundary — both success AND failure branches stamp `host_provider_last_ok` so the chip's "Updated Xm ago" subtitle populates.
- Section-owned save pattern — every admin tab declares `_<name>SectionTuningKeys()` + `_<name>SectionPlainKeys()` + `<name>SectionDirty()` + `save<Name>Section()` so a Pulse Save no longer re-POSTs every Webmin / Beszel / NE field.
- Auto-pause sweep on lifespan startup — orphan `<provider>:<host_id>` rows in `host_failure_state` + `host_provider_last_ok` are deleted when the host has been removed from `hosts_config` OR no longer has the provider configured.
- Snapshot-first render in `/api/hosts/list` — pre-populates each row with last-known `host_*` fields from `host_snapshots` with `_stale_fields` / `_stale_ts` markers so repeat visits paint instantly.
- `_populate_detected_ports` shared helper — `api_hosts_list` AND `_merge_one_host` read from one helper to surface `host.detected_ports[]` from `host_port_scans`, with no toggle / provider-state gates.
- AI palette context build moved to a single `_buildAiPaletteContext()` method shared by the modal palette and the sidebar; rich records (host metrics, weather forecast, log context) replace bare IDs.
- `static/index.html` sub-8k lines via the per-partial split.
- `tmp/img_*` references stripped from every committed surface — local screenshots are ephemeral; descriptions in prose travel.
- `notes/note_todo.txt` LATER block ordering hardened to ascending `[#NNN]`; sub-section headings now show `(none)` placeholder when empty so "actually empty" is distinct from "section truncated by a bad edit".

### Removed

- `host:row_updated` SSE event retired — `/api/hosts/one/{id}` was the only publisher, and the SPA-side handler caused an SSE infinite loop (read endpoint published an event, handler called the read endpoint, which published another event). Per-host UI updates now flow exclusively through `host:failure_state_changed` (sampler-driven) and the existing 30s polling fallback.
- Admin → Version page removed — pre-fix the route at `GET/POST /api/admin/version` wrote to `/app/VERSION.txt` via a per-file bind mount that no longer exists under image-build deploys. The durable seed path is now: edit repo-root `VERSION.txt`, commit, push — `deploy.yml`'s source-B resolver reads it as the floor.

## [1.3.0] — 2026-05-02

Third MINOR cut on top of `1.2.0` — rolls up **316 closed issues** under the 1.3.0 milestone (232 enhancements, 84 bug fixes). Every entry shipped to the live deploy as a PATCH bump on the daily CI cadence; this MINOR bundles them under a single tag for rollback / changelog purposes.

### Highlights

- SNMP infrastructure (per-port throughput chart, utilization heatmap, total-throughput chart, opt-in per-host enable, tunables, uptime + reboot detection, Memory chart unit alignment).
- Ping host-stats provider end-to-end (per-host TCP/ICMP probes, drawer chart, hosts-table cells, cap_add NET_RAW for ICMP, cool-down skip semantics).
- APC UPS over-time charts (Output Load %, Battery %, Battery Temperature) in the host drawer.
- Drawer chart system polish (time-range picker disables-while-loading + spinner; `Updated Xs ago` freshness hint; first-position counters & state debug panel; full unified-cadence #232 timer + pushOnly gate).
- Provider chips + per-provider styling — chip class refactor, reactive colour application, mono SVG icons in provider tabs, paused-banner with debug-panel jump-link.
- Real-time / SSE polish — third "reconnecting" pill state with amber pulse, freshness-watchdog flips connection state on silent half-open sockets.
- Authentication tightening — passkeys WebAuthn QR-only on macOS root cause + fix (RP-ID), digest-mismatch follow-up, three-front fix shipped, OIDC cookie cleanup on every callback path.
- Body-scroll lock when any drawer is open — eliminates accidental background-page scroll while the operator interacts with the host / item / node drawer.
- Snapshot persistence timestamps now reflect the last LIVE probe (not the last save), so the host card's freshness banner agrees with the chart's "Last sample N ago" instead of refreshing on every drawer poll.
- Settings → Host stats refactor (tab strip with horizontal scrolling preserved + vertical scroll locked).
- Hardware card section gate now accepts snapshot-fallback hits so cached host_cpu_model / host_mem_total / host_disk_total / host_serial / host_model / host_firmware / host_vendor / host_swap_used stay rendered when every live provider is offline.

### Authentication, passkeys & 2FA

- `passkeys_allowed` now returned by `api_get_settings` next to the TOTP-policy fields (#218) [Bug]
- OIDC flow cookie now deleted on every callback path, not just the success branch (#222) [Bug]
- Spinner pattern brought to all Save buttons that were missing it (#293) [Bug]
- requirements.txt — bumped three floor-pinned deps to current PyPI latest (#295) [Enhancement]
- WebAuthn passkey QR-only on macOS — multi-pass investigation, root cause was RP-ID change (#330) [Enhancement]
- Authentication tab now has Enabled/Disabled pill + remaining width outliers across admin tabs unified (#342) [Enhancement]
- WebAuthn RP-ID mismatch detection (#359) [Enhancement]
- WebAuthn `verify_authentication` 0/0 sign-counter check comment rewritten to match actual code behaviour —... (#373) [Enhancement]
- Defensive `.get(key, default)` swap across every `_TOTP_POLICY_DEFAULTS[...]` and... (#492) [Enhancement]

### Real-time / SSE event stream

- Real-time event stream via SSE — replaces the SPA's polling-only "live feel" with a single push channel from... (#228) [Enhancement]
- UX batch — five UX-bugs and five UX-enhancements shipped together (#232) [Enhancement]
- SSE-push host history chart — `host_metrics_sampler.py:_probe_one` publishes `host:history_appended` event... (#234) [Enhancement]
- Live-mode tracing console.logs in `static/js/app.js` (#243) [Bug]
- docs-maintainer agent sweep — five files updated by the agent (api.md got a new "Client config" subsection +... (#249) [Enhancement]
- (CRITICAL) — removed `host:row_updated` SSE publish from `/api/hosts/one/{id}` (#251) [Bug]
- Wired `session:renewed` SSE listener in `static/js/app.js:_initSSE` (#257) [Enhancement]
- Fix — operator-visible amber toast on `:overflow` SSE event (#260) [Enhancement]
- Events-dropped counter chip alongside the SSE pill (#261) [Enhancement]
- Pass `force: true` to `refreshHostRow` from BOTH SSE handlers (`host:row_updated` listener kept for future... (#267) [Enhancement]
- Bounded the SSE per-subscriber `local: asyncio.Queue` (`asyncio.Queue(maxsize=256)`) so a paused/throttled... (#269) [Enhancement]
- SSE heartbeat cadence now operator-tunable via `tuning_sse_heartbeat_seconds` (default 25, range 5-300) (#272) [Enhancement]
- SSE connection lifetime cap now operator-tunable via `tuning_sse_max_lifetime_seconds` (default 21600 = 6h,... (#273) [Enhancement]
- SSE freshness-watchdog idle threshold now operator-tunable via `tuning_sse_idle_threshold_seconds` (default... (#276) [Enhancement]
- pollOps SSE-up keep-alive cadence now operator-tunable via `tuning_pollops_sse_keepalive_seconds` (default... (#277) [Enhancement]
- SSE freshness watchdog false-flip fixed (#294) [Bug]
- Debounced `host:history_appended` SSE handler (#494) [Enhancement]
- `X-OmniGrid-Client-Id` request-correlation header for SSE self-filter (#498) [Enhancement]

### SNMP

- SNMP host-stats provider (sixth in the family) (#361) [Enhancement]
- SNMP per-host enable checkbox persistence fixed (#363) [Enhancement]
- SNMP raw + normalized panels added to host-drawer "Show debug data" (#364) [Enhancement]
- Per-host "Enable SNMP for this host" checkbox flipped from default-on to OPT-IN (#365) [Enhancement]
- SNMP `tuning_snmp_probe_timeout_seconds` + `tuning_snmp_concurrency` are now actually consumed — operator... (#366) [Bug]
- SNMP tunables added to `SettingsIn` Pydantic model (#367) [Bug]
- SNMP per-host probe targeting hard-gated on alias OR `snmp_name` (#368) [Enhancement]
- SNMP `probe_snmp` `try/except asyncio.TimeoutError` now reachable (#369) [Enhancement]
- SNMP per-host cache TTL knobs separated from Webmin's (#370) [Enhancement]
- `_snmp_get` / `_snmp_walk` log exception type at WARNING + carve out cancellation (#376) [Bug]
- SNMP debug-panel raw payload expanded (#386) [Enhancement]
- Dedicated `tuning_snmp_unreachable_cooldown_seconds` knob (#389) [Enhancement]
- SNMP-aware `host_metrics_sampler` (#392) [Enhancement]
- UCD-SNMP-MIB OIDs (1.3.6.1.4.1.2021.x) for embedded Linux (#395) [Enhancement]
- SNMP walks no longer crash on pysnmp 7.x (#398) [Bug]
- APC UPS card in host drawer (#412) [Enhancement]
- Per-interface SNMP traffic chart in host drawer — oper-status dot, ↓rx · ↑tx mono span, stacked bar... (#415) [Enhancement]
- Hosts-page SNMP chip respects per-host opt-in flag (#422) [Enhancement]
- SNMP CPU/Load/Memory cards hidden when host also has Beszel or node-exporter (avoids redundant disagreeing... (#424) [Enhancement]
- SNMP chart cards upgraded to match Beszel/NE chart styling (420×120 viewBox, gridlines, legend strip +... (#425) [Enhancement]
- SNMP interface list capped at top 10 by traffic + per-host "Show {count} more" toggle (busy-by-traffic-desc... (#426) [Enhancement]
- SNMP Memory chart Y-axis no longer reads "0 B / 0 B / 0" while waiting on live probe — derives max from... (#429) [Bug]
- "No data from any enabled provider" banner lists SNMP + Ping (#430) [Enhancement]
- SNMP charts on freshly-enabled hosts show "Collecting first samples" hint (#432) [Enhancement]
- SNMP Memory chart unit alignment via `fmtBytesAt(value, refMax)` (#433) [Enhancement]
- SNMP uptime trend + reboot detection (#434) [Enhancement]
- SNMP total-throughput chart — cumulative ifHCInOctets / ifHCOutOctets sums persisted, in/out... (#438) [Enhancement]
- SNMP-only nodes no longer see the misleading "Time-series sourced from Beszel/NE" banner (#441) [Bug]
- Help-circle metric-source tooltip on every chart (Ping + SNMP CPU/Load/Memory/Throughput/Pages + per-port).... (#442) [Enhancement]
- Per-port SNMP throughput chart — new `host_snmp_iface_samples` table, sampler write per active... (#444) [Enhancement]
- SNMP Load chart legend zero-when-chart-non-zero — `snmpLoadLegendValue` falls back to `snmpStats(...).max` (#445) [Enhancement]
- SNMP Load chart renders as % of cores instead of raw load values — `snmpCoresFor` + `snmpLoadPctLive` helpers (#447) [Enhancement]
- Printer pages chart hidden on non-printer SNMP hosts (UPS / router false positives suppressed via... (#450) [Enhancement]
- SNMP freshness banner always renders in `--warning` orange (#452) [Enhancement]
- "Collecting data..." spinner pattern landed on EVERY chart card during warm-up — Beszel/NE side... (#468) [Enhancement]
- Dedicated SNMP sample interval — `tuning_snmp_sample_interval_seconds` (default 0 = inherit global) (#473) [Enhancement]
- SNMP throughput delta helpers emit `null` on out-of-bounds (counter wrap / reboot / gap) instead of... (#474) [Bug]
- Capped `/api/hosts/{id}/snmp/iface_history` SELECT with `LIMIT h * 60 * 64` (#484) [Enhancement]
- SNMP throughput / per-port throughput / per-port utilization charts render genuine null gaps as visual breaks... (#490) [Enhancement]
- Module-load INFO line in `logic/snmp.py` reports which pysnmp walk function the resolver picked... (#493) [Enhancement]
- Per-(provider, host) auto-pause + manual resume across EVERY provider (Beszel, Pulse, node-exporter, Webmin,... (#501) [Enhancement]
- SNMP charts now follow the drawer's 1h / 6h / 24h / 7d range picker (#504) [Bug]
- Time-range picker (1h / 6h / 24h / 7d) now renders on the host drawer for SNMP-only hosts (managed switches,... (#511) [Enhancement]
- Hardware card SNMP rows (model / serial / firmware) now render `—` placeholder when the snapshot saw the... (#512) [Bug]
- UPS info card now renders when ANY UPS field is present (live OR stale), not just `host_ups_status` (#518) [Bug]
- Per-port utilization chart now renders on hosts whose SNMP agent doesn't expose `ifHighSpeed` (printers /... (#520) [Enhancement]
- SNMP "Collecting first samples — chart will populate after the next sampler tick (~N min)" hint now reflects... (#526) [Enhancement]
- Printer info card now stays mounted with cached values when the SNMP provider is offline (#527) [Bug]

### Ping

- Added `icmplib==3.0.4` to `requirements.txt` so the Ping provider's "use ICMP" toggle becomes wired out of... (#296) [Enhancement]
- Ping-only hosts now register as "configured", get a provider chip, and surface accurate up/down status (#299) [Enhancement]
- Settings → Host stats TABS refactor shipped (#300) [Enhancement]
- Ping host-stats provider end-to-end (#301) [Enhancement]
- Settings → Host stats → Ping → Test target picker — fixed empty dropdown when the operator opens the Settings... (#302) [Bug]
- Hosts table — CPU / Memory / Disk bars no longer render on host ROWS for ping-only hosts (#303) [Bug]
- docker-compose.yml — added `cap_add: [NET_RAW]` to the `omnigrid` service so the Ping provider's optional... (#306) [Enhancement]
- Host-drawer Ping latency chart shipped (#308) [Enhancement]
- Drawer chart-grid wrapper now opens for `h.ping_enabled` too — pre-fix the gate was `(h.beszel_id ||... (#309) [Bug]
- Ping-only host CPU/Memory/Disk surfaces tightened across hosts table + drawer (#310) [Bug]
- /api/hosts/debug — `active_providers` now per-host filtered (#312) [Enhancement]
- Ping sampler hardening — robustness pass (#313) [Enhancement]
- Host drawer — dedicated Ping debug box (raw + normalized) added to the existing per-provider debug panel... (#314) [Enhancement]
- Settings → Host stats renamed to "Providers" — operator request that the section name reflect what it... (#315) [Enhancement]
- Ping chart range picker (1h / 6h / 24h / 7d) + cadence wiring complete (#317) [Enhancement]
- CURATED_FIELDS + CURATED_REFRESH_FIELDS extended for ping (#318) [Enhancement]
- Drawer second chart-grid wrapper now also opens for `h.ping_enabled` (#319) [Enhancement]
- Ping legend ms-formatting fix in host-drawer chart card (#320) [Bug]
- Ping chart x-axis labels were blank — fixed (#321) [Bug]
- Per-provider chip colour customisation in Settings → Providers (#326) [Enhancement]
- Hosts header provider-chip strip now includes ping (#328) [Bug]
- Host drawer Ping latency chart promoted to its own full-width row above CPU/Memory/Disk.. (#329) [Enhancement]
- Per-row provider chips on the Admin → Hosts EDITOR (the small `beszel`/`pulse`/`exporter`/`webmin`/`ping`... (#346) [Enhancement]
- Provider chips on the Hosts page header toolbar (top strip showing beszel/pulse/node_exporter/webmin/ping)... (#352) [Enhancement]
- SSH "Enable for this host" checkbox moved from RIGHT to LEFT of the SSH section, matching the Ping section's... (#355) [Enhancement]
- Ping port + transport per-host inputs now also disable when the host's main "enabled" is OFF — operator... (#379) [Enhancement]

### UPS / battery

- APC PowerNet-MIB OIDs (1.3.6.1.4.1.318.x) for Smart-UPS family (#394) [Enhancement]
- APC UPS card refinements in the host drawer (#515) [Enhancement]
- APC UPS over-time charts (Output Load %, Battery %, Battery Temperature) in the host drawer (#516) [Enhancement]

### Printer

- Printer-MIB walks added (#409) [Enhancement]
- Printer card supply bars now render in their mapped brand colour (cyan/magenta/yellow/black/waste-grey)... (#423) [Enhancement]
- Printer supply names render brand acronyms + SKU codes ALL CAPS — `titleCase()` rule extension (#436) [Enhancement]
- Printer pages-printed sparkline + lifetime headline (#439) [Enhancement]
- Lifetime page count repositioned inside Printer card body at 18px semibold mono (#449) [Enhancement]
- Printer card freshness banner — orange "Last sample Xm ago" via `snmpHistoryFreshness(h)` + snapshot-stale... (#467) [Bug]
- Printer card uses DB-backed history fast-path — `snmpLatestPageCount` walks history backwards (#469) [Enhancement]
- Pages printed chart REMOVED entirely per operator request (#470) [Enhancement]

### Beszel / Pulse / Webmin / Portainer

- Outer 45s timeout on `/api/hosts/one/{host_id}` to prevent NPM 504s (#241) [Enhancement]
- Mitigation — Beszel + Pulse hub probes inside `_do_host_provider_probe` now run in parallel via... (#259) [Enhancement]
- `_get_host_provider_state(force=True)` now also drops the per-host Webmin caches (#266) [Enhancement]
- Webmin probe outer budget unified across legacy `api_hosts` AND `_merge_one_host` via... (#274) [Enhancement]
- `_AUTH_COOLDOWN_SECONDS` duplicated across `logic/webmin.py:74` AND `logic/ssh.py:111` unified under one... (#280) [Enhancement]
- Move Webmin cache TTLs to Settings → Host stats → Webmin section (#285) [Enhancement]
- Settings → Host stats — unified Save (#289) [Enhancement]
- Admin Save button standardisation — in-flight + disabled state across Notifications / Portainer / OIDC + audit of ~10 other Save buttons + saveSchedule / saveRetention modal Saves + saveSshSettings label normalisation (#290) 
- Settings → Host stats tab labels simplified per operator request — three keys in `static/i18n/en.json`... (#298) [Enhancement]
- UI consistency — Apprise (Notifications) + SSH admin tabs now have an "Enabled" / "Disabled" pill next to the... (#338) [Enhancement]
- UX review batch — i18n hardcoded-string sweep, drawer/modal A11Y dialog roles, global focus-visible ring, prefers-reduced-motion expansion, skip-link utility, and /admin/hosts hard-href fix (#410) 
- Beszel + Pulse Test buttons pinned right via grid layout (`grid-cols-[1fr_auto]` + `justify-self-end`) (#414) [Enhancement]
- Hosts-toolbar Open Beszel / Open Pulse buttons floating to the trailing edge — three-pass fix landing on... (#440) [Bug]
- Beszel Load avg chart shows `load` unit chip in title (#455) [Enhancement]
- GPU chart cards (Power Draw / Usage / VRAM) for hosts with discrete GPUs via Beszel `stats.g` (#460) [Bug]
- Beszel Load avg chart renders as % of cores via `la*_pct` per-tick fields (#462) [Enhancement]
- README.md updated against current state — host telemetry charts list extended (Temperature, GPU Power / Usage... (#482) [Enhancement]
- Beszel history fetch now picks the right aggregation tier for the requested window (#513) [Bug]
- Pulse + Beszel probe failures now log to stdout (and therefore land in Admin → Logs) (#523) [Bug]
- Pulse and Beszel probes now hard-gate on explicit `pulse_name` / `beszel_name` aliases (#525) [Bug]

### Provider chips & icons

- Per-provider chip colours apply reactively in Hosts page + drawer (#327) [Enhancement]
- Provider icons (mono SVG) in Settings → Providers tab strip + Admin → Hosts collapsed-card chip strip —... (#362) [Enhancement]
- Hosts-page header provider chips became clickable filters (#391) [Enhancement]
- Provider tab strip dot now uses `.dot-on` / `.dot-off` utility classes (#407) [Enhancement]
- Per-port utilization heatmap. ifHighSpeed walk + `link_speed_mbps` persistence +... (#451) [Enhancement]
- `network_ifaces` added to `_BARE_SNAPSHOT_KEYS` so per-iface chip strip + per-port heatmap fall back to... (#476) [Enhancement]
- Per-iface 32-bit counter degraded badge on the host drawer's network-iface chip strip (#491) [Enhancement]
- "Last successful probe" timestamp on every provider chip (#497) [Enhancement]

### Drawer, charts & Node Exporter

- Admin → Process tunables — bounds rendered as three small icon chips (↓ min · ↑ max · ◎ default) instead of... (#248) [Enhancement]
- node-exporter per-host probe timeout unified across THREE consumers via... (#275) [Bug]
- Move "node-exporter probe timeout (seconds)" out of Process tunables to Settings → Host stats → Node-exporter... (#286) [Bug]
- Host drawer — dedicated "Enabled agents" card with colored pills, sitting just above the System card (#307) [Enhancement]
- Host drawer — dedicated "Enabled agents" card with colored pills + repositioned (#311) [Enhancement]
- History view's OP cell chip wraps `gather refresh` (and any multi-word op_type) onto two lines, looking... (#322) [Bug]
- Cloudflare brand icon shipped — `static/img/icons/cloudflare.svg` from homarr-labs/dashboard-icons (orange... (#324) [Enhancement]
- Tiny 9px package icon next to display name when sourced from asset inventory (operator-typed labels show no... (#357) [Enhancement]
- Stat-bar warn / crit thresholds operator-tunable (#406) [Enhancement]
- IDEA — Drawer focus-trap helper (`_focusTrap(el)`) (#417) [Enhancement]
- "+ Add URL" link in host drawer System card lands on the specific host's row in Admin → Hosts (#428) [Enhancement]
- Hardware inventory rows (host_model / host_serial / host_firmware) added to drawer Hardware card (#437) [Enhancement]
- Chart-source tooltip simplified — `metricSource()` returns only the active primary provider, no fallback... (#453) [Enhancement]
- Faded amber `⚠` triangle prefix on every stale text element via `.stale:not(.stat-bar)::before` (#454) [Bug]
- Permanently-flat chart cards hide after 1h soak via `hostChartIsPermanentlyFlat` (#456) [Enhancement]
- Chart title order unified `name → [unit] → tooltip`; dynamic unit chips via `unitForBytes()` (#458) [Enhancement]
- Network + Bandwidth chart-source tooltips simplified — `metricSource()` returns one active source (#459) [Enhancement]
- Temperature chart shows "Collecting data..." spinner during warm-up (#461) [Enhancement]
- Total throughput / per-port throughput legend + Y-axis share ONE unit family via `fmtBytesAt(v, max)` (#463) [Enhancement]
- "Edit" button added to host drawer header (admin-only) — close-drawer + openAdminTab('hosts') +... (#464) [Enhancement]
- Per-port throughput polylines now draw — rewrote as 10 fixed polylines indexed against... (#465) [Bug]
- Total Throughput chart static-rate headline (`↓ rx ↑ tx`) above chart line; per-port utilization heatmap... (#466) [Enhancement]
- Pages chart no longer stays in spinner forever for idle printers — gate dropped `snmpPagesPerDayMax > 0`... (#471) [Bug]
- Per-port utilization chart converted from chip-strip heatmap to a true LINE CHART (top-5 ifaces, Y-axis... (#472) [Enhancement]
- 32-bit ifInOctets wrap detection — `extract_interfaces` tags each iface row with `counter_width: 32 | 64` (#475) [Enhancement]
- "Updated Xs ago" freshness label suppressed on permanently-flat charts (#479) [Enhancement]
- Per-port throughput legend defensiveness — verified no fix needed (#480) [Bug]
- Compact stale display when ALL host_* fields are stale (#496) [Bug]
- Host-drawer charts on a unified time x-axis (#505) [Enhancement]
- Host-drawer pause-banner + Resume-button consistency pass (#506) [Enhancement]
- Top-of-drawer "{N} providers auto-paused" affordance (#509) [Enhancement]
- Disabled-host banner copy (#510) [Enhancement]
- Host-drawer debug panel now exposes per-host counters & state (#521) [Enhancement]
- Charts cropped from the right on initial drawer open (#524) [Enhancement]
- Body-scroll lock when any drawer is open (#530) [Bug]
- Time-range picker (1h / 6h / 24h / 7d) now disables its buttons while the underlying loaders are in flight,... (#531) [Enhancement]

### Hosts editor, Host groups & Hosts page

- Perf — short-TTL cache on `load_host_snapshots()` (default 5s, admin-tunable via... (#230) [Bug]
- Debounce on the Hosts-view filter input (#242) [Enhancement]
- Admin → Hosts collapsed-card layout fixes (#316) [Bug]
- Hosts page lazy-loaded probe fetch via IntersectionObserver (#331) [Enhancement]
- Hosts + Host_groups + Providers admin tabs aligned to the standardised pattern (#341) [Enhancement]
- SSH icon repositioned to RIGHT of the Admin → Hosts editor row header (was on the LEFT) (#345) [Enhancement]
- Per-host SSH flipped from opt-out (`ssh.disabled=true`) to opt-in (`ssh.enabled=true`) (#347) [Enhancement]
- Host display label now falls back to the asset-inventory's stored name when the operator has left the Admin →... (#350) [Enhancement]
- Admin → Hosts editor's collapsed row header — the small green/grey SSH-state dot replaced with an SSH... (#351) [Enhancement]
- Admin → Hosts editor's collapsed row header — when the operator clears the display label, the header now... (#358) [Bug]
- Host-level "enabled" checkbox now hard-gates every per-provider checkbox in Admin → Hosts editor — operator... (#371) [Enhancement]
- "Page X of Y" pagination labels in Admin → Hosts editor + Admin → Host Groups now use the existing... (#387) [Enhancement]
- Friendlier hosts_config save-side error messages (duplicate id / custom_number) (#431) [Bug]
- Orphan sweep on lifespan startup + per-provider orphan detection (#528) [Bug]

### Admin & Settings pages

- Ops poll cadence tunable — switched from milliseconds to seconds in the admin UI (#263) [Enhancement]
- Auth rate-limit policy now operator-tunable (#278) [Enhancement]
- `_WEBMIN_HOST_CACHE_TTL` (30s success) + `_WEBMIN_HOST_FAIL_CACHE_TTL` (5s failure) in... (#282) [Enhancement]
- `_HOST_PROVIDER_CACHE_TTL = 10.0` in `main.py:_get_host_provider_state` now operator-tunable via... (#283) [Enhancement]
- `_PROBE_CONCURRENCY = 8` in `logic/host_metrics_sampler.py` now operator-tunable via... (#284) [Enhancement]
- Settings → Host stats tab strip — horizontal scrolling preserved, vertical scrolling suppressed (#297) [Enhancement]

### Logs view & retention

- UI reorganization — moved two tunables out of the generic Process tunables form to their domain-specific... (#287) [Enhancement]

### Schedules & automation

- SnmpEngine module-level singleton (#382) [Bug]
- Warming-up banner reads configured sampler interval — three-pass fix landing on `snmpWarmingUpText()` helper... (#443) [Bug]

### Mobile / responsive UX

- `extract_storage` unit-normalisation heuristic for hrStorageType=RAM (#375) [Enhancement]
- Host mobile-card `.host-mobile-card-metric .name` font bumped 9.5px → 10.5px and letter-spacing 0.5px → 0.3px... (#405) [Bug]

### Topbar, login & branding

- Investigate "new version" blue topbar button not appearing (#227) [Bug]
- Single context-aware refresh button — replaced the topbar's icon-only refresh + the Hosts-toolbar "Refresh"... (#236) [Enhancement]
- Topbar refresh button restyled to match the previous Hosts-toolbar shape (#237) [Enhancement]
- Alpine `t` shadowing in the topbar nav — `<template x-for="t in navItems()">` declared the loop variable as... (#240) [Bug]
- Login error fix — disabled-user case now returns specific 403 "Account is disabled (#288) [Bug]
- Login UI — 403 detail now surfaced (#291) [Enhancement]
- Login UI — password field cleared on every failed login attempt (#292) [Bug]
- `get_credential_by_credential_id` SELECT in `logic/auth.py` now includes `rp_id` (#374) [Bug]
- Deploy workflow redirects `docker login` stderr to `/dev/null` (#385) [Enhancement]
- A11Y review LOW + NIT findings (#413) [Enhancement]

### Filters, badges & status pills

- Clickable `button.chip` chips meet `--touch-target-min` on phones (≤768px viewport) (#399) [Enhancement]
- IDEA — Provider filter chip "Solo" via Shift-click (#418) [Enhancement]
- IDEA — CHANGELOG "What's New" badge after deploy (#420) [Enhancement]

### Internationalisation & accessibility

- "Error: " prefix in host-debug error display now uses i18n via new `debug_panel.error_prefix` key — operator... (#388) [Bug]
- A11Y / IA broader retrofit (tablist roles, progressbar attrs, profile-modal avatar role) (#416) [Enhancement]
- i18n bundle JSON syntax fix (#500) [Bug]

### Database / migrations / data

- SHA-256 git migration — local working tree, push remote, runner-side checkout all converted from SHA-1 to... (#304) [Enhancement]
- Deploy migration to Dockerfile-based image build (Plan A — full image with static/ + node_modules/ baked) (#333) [Enhancement]
- Snapshot-first render in `/api/hosts/list` (#517) [Enhancement]
- Per-host probe path now writes to `host_snapshots` (#522) [Enhancement]

### API endpoints & backend helpers

- `api_hosts` docstring gained a deprecation note directing bearer-token scrapers to `/api/hosts/list` +... (#256) [Enhancement]

### Documentation

- Fix `CHANGELOG.md` release-page links on the public git host (#245) [Bug]
- Three stale references to `tuning_ops_poll_interval_ms` / `OPS_POLL_INTERVAL_MS` cleaned up in `README.md`,... (#253) [Bug]
- deploy.yml — replaced `actions/checkout@v4` with a manual SHA-256-compatible clone step (#305) [Bug]
- Hardened deploy.yml version-source resolution — code-complete (#334) [Enhancement]
- Extend deploy.yml to also push the built image to the container registry (#335) [Enhancement]
- Dockerfile OCI `image.source` label now carries a multi-line LABEL comment cross-referencing... (#383) [Enhancement]
- `_clean_host_snmp` now carries an explicit comment documenting that omission == disabled (#384) [Enhancement]

### Internal cleanup, refactor & bug sweeps

- Enhancements sweep — 17 enhancements shipped in one batch (3 of the original 20 — were already covered) (#231) [Enhancement]
- `_do_host_provider_probe(active:..., cache_key: tuple)` annotation corrected from `list` to `set[str]` (#254) [Enhancement]
- `loss_pct` format spec in `logic/ping_sampler.py` is now defensive — `(result.get('loss_pct') or 0):.0f` (#378) [Bug]
- `_ALLOWED_TRANSPORTS` (frozenset) and `_TRANSPORT_ORDER` (tuple) hoisted from per-credential loops to module... (#381) [Enhancement]
- `paused_at` SQL drift fixed — extended `_failure_state_for_host` SELECT + return dict to surface the column... (#477) [Enhancement]
- Defensive `.get("passkeys_allowed", True)` in `main.py` (two call sites) replaces the `[]` subscript on... (#486) [Enhancement]
- Cleaned unused `_default` destructure in `logic/schedules.py:_run_prune_logs` — switched to `_, _, _lo, _hi =... (#488) [Bug]
- Resume button defensive clear + visual prominence (#508) [Bug]

### Other improvements & fixes

- `prune_old_logs` cutoff math + filename-date parse now route through a new shared `_resolved_tz()` helper... (#219) [Enhancement]
- Legacy `/api/hosts` now calls `_shape_host_api_row(h, s, providers, any_provider_enabled=True)` per row... (#220) [Enhancement]
- `_validate_id_token._find_key` now rejects `kid is None` instead of silently picking `keys[0]` (#221) [Bug]
- `verify_authentication` now actually performs the sign-counter regression check the comment promised (#223) [Enhancement]
- `verify_registration` now whitelists client-supplied `transports` against the documented... (#224) [Bug]
- `/api/events` now caps each connection's wall-clock lifetime at `_SSE_MAX_LIFETIME_SECONDS = 6 * 3600` (6h,... (#225) [Enhancement]
- `auto_provision_authentik` username collision is now O(1) in expectation (#226) [Enhancement]
- Cursor:pointer fix — global `button { cursor: pointer; }` rule + `button:disabled { cursor: not-allowed }`... (#229) [Enhancement]
- Stale-data badges in the Hosts UI — three gaps closed end-to-end (#233) [Bug]
- Polling pill UX enhancement — pill mirrors the picker's chosen mode (Live / Off / Polling) with appropriate... (#235) [Enhancement]
- Loaders for Admin → Users / Sessions / Tokens (#238) [Enhancement]
- CSRF mismatch self-recovery in the global fetch wrapper (#239) [Bug]
- Fix fan-out 504s from `/api/hosts/one/<id>` saturating NPM's upstream pool (#244) [Enhancement]
- No-static-config rule + first knob converted (`PARALLEL` → `tuning_hosts_parallel_fetch`) (#246) [Enhancement]
- Admin → Process tunables — fixed hardcoded "six" subtitle + rewrote every help string with detailed use-case... (#247) [Bug]
- switched from relative paths to absolute git-host URLs (#250) [Bug]
- `_get_host_provider_state` re-computes `active` + `cred_blob` + `cache_key` INSIDE `_host_provider_lock` via... (#252) [Enhancement]
- `_webmin_host_cache.pop(h["id"], None)` also fires on failure-write branch (#255) [Bug]
- Fix — added `tuning_host_snapshots_cache_ttl_seconds` to the SPA's `tuningKeys` array (#258) [Enhancement]
- Version link references switched to ROOT-RELATIVE paths (#262) [Enhancement]
- Per-host probe wall-clock as hover-title on the host status dot (#264) [Enhancement]
- Removed env-var-name hint line from Admin → Process tunables rows (#265) [Enhancement]
- Prometheus histogram `omnigrid_host_provider_lock_wait_seconds` on `_host_provider_lock` acquire time (#268) [Enhancement]
- Request-correlation log line at every `events.publish` site (#270) [Enhancement]
- Hosts header label fixed — "polling off in Live" UX bug (#271) [Bug]
- Convention-violations housekeeping notes — closing for record (no work to ship) (#279) [Bug]
- Sorted Process tunables form alphabetically by translated label (#281) [Enhancement]
- Nodes-section source-count chip overcount — fixed both sides (#323) [Enhancement]
- Split `cloudflared` from `cloudflare` "solved now" (#325) [Bug]
- Three-front fix shipped (#332) [Bug]
- Flip Swarm to PULL from the container registry instead of using local-only tags (#336) [Enhancement]
- Removed Admin → Version page + GET/POST `/api/admin/version` endpoints (#337) [Bug]
- Title-row spacing unified across ALL admin tabs to the dominant `mb-2` pattern (#339) [Bug]
- Admin → Sessions tab spacing unified — `space-y-3` → `space-y-4` (matches Users / Tokens / Notifications... (#340) [Enhancement]
- Automated dep-bump PR config added for the public mirror (#343) [Enhancement]
- Fixed the digest-mismatch ✕ status on OmniGrid's own row (#344) [Bug]
- CRITICAL: cross-host SSH toggle bug — ticking row A's checkbox auto-enabled OTHER rows that didn't have an... (#348) [Enhancement]
- Digest-mismatch root cause + real fix (#344 follow-up; #117 investigation result) (#349) [Bug]
- Provider chips on the Hosts header toolbar now use `class="chip"` instead of `class="pill"` so the... (#353) [Bug]
- Long display labels now ellipsis-truncate with `min-w-0 max-w-[280px] truncate` instead of pushing the SSH... (#354) [Enhancement]
- SSH icon (and any other binding that reads from row data) was returning STALE state until a hard refresh... (#356) [Bug]
- Legacy `/api/hosts` refactored to compose `_get_host_provider_state` + `_merge_one_host` — operator... (#360) [Enhancement]
- Lazy IO observer fan-out now honours `tuning_hosts_parallel_fetch` concurrency cap (#372) [Enhancement]
- Renamed `for c in creds:` → `for cred in creds:` in `api_local_login_webauthn_start` (#377) [Enhancement]
- Host icon resolution now reads `assetForHost(h).name` / `type_short` / `vendor` / `model` as additional... (#380) [Bug]
- `probe_snmp` reads ENTITY-MIB physical-entry walks + sysContact / sysLocation (#390) [Enhancement]
- `probe_snmp` extended with Dell DELL-RAC-MIB (iDRAC) + Cisco CISCO-MEMORY-POOL-MIB / CISCO-PROCESS-MIB /... (#393) [Enhancement]
- SYNOLOGY-MIB OIDs (1.3.6.1.4.1.6574.x) for DSM-based NAS (#396) [Enhancement]
- Ubiquiti UniFi switch / AP sysDescr "MODEL, FIRMWARE" parser (#397) [Enhancement]
- `var(--provider-icon-size, 14px)` fallback literal removed from `.provider-icon` (#400) [Enhancement]
- `rgba(0, 0, 0, 0.18)` literal on `.log-sev-pill.is-active .log-sev-count` replaced with new... (#401) [Enhancement]
- `--r-pill: 999px` token added; all 7 `border-radius: 999px` literals migrated to `var(--r-pill)` — operator... (#402) [Enhancement]
- Typography token family declared on `:root` — `--fs-xs` (11px) / `--fs-sm` (12px) / `--fs-md` (13px) /... (#403) [Enhancement]
- Profile-modal avatar moved from inline `:style="'background: hsl(...)'"` to sanctioned `--avatar-hue`... (#404) [Enhancement]
- SweetAlert2 overrides token-ised — `13px` → `var(--fs-md)`, `12px` → `var(--fs-sm)`, `8px 18px` → `var(--s-3)... (#408) [Enhancement]
- Network card "idle interfaces" toggle for switches (#411) [Enhancement]
- IDEA — Density toggle (compact/comfortable/spacious) (#419) [Enhancement]
- Hosts-page CPU/Mem/Disk percentages now render as integers (`Math.round`) instead of `73.84579584587%` (#421) [Enhancement]
- Single-interface unhide — host with exactly 1 docker/internal iface (and no busy / idle ifaces) now renders... (#427) [Enhancement]
- Desktop Hosts-page CPU / Memory / Disk bars self-identify on hover via `:title` tooltips (#435) [Enhancement]
- No-data banner lists per-host enabled providers (was global `host_stats_source` CSV) —... (#446) [Enhancement]
- Per-field stale styling sharpened (opacity 0.55→0.45, saturate(0.6), dashed underline) (#448) [Bug]
- Per-port heatmap renders chips from live `network_ifaces[]` before iface_history accumulates (#457) [Enhancement]
- Dead `SettingsIn.show_header_clock` / `show_header_weather` fields removed (declared but never... (#478) [Enhancement]
- Provider icons + text labels in chips visually centered (#481) [Enhancement]
- Added `network_ifaces` to SPA `CURATED_REFRESH_FIELDS` (#483) [Bug]
- Unified host-refresh worker pool (#485) [Bug]
- Removed the legacy `_HOST_SNAPSHOT_KEYS` tuple in `logic/gather.py` (#487) [Enhancement]
- `_snmp_walk` connection-level errors now return whatever varBinds were already collected instead of... (#489) [Enhancement]
- Settings GET version int for cheap cross-tab change detection (#495) [Enhancement]
- Node column removed from the Stacks view (#499) [Enhancement]
- Resume-all button counted disabled providers (#502) [Enhancement]
- Per-provider admin-panel tuning-knob blocks share a centralised key list + disabled-gate helper (#503) [Enhancement]
- Services view's Node column now renders topology-style pills (host name + state-coloured dot, green for... (#507) [Enhancement]
- Stale banner now lists the actual stale field names so operators can identify what's counted as cached (#514) [Bug]
- host_swap_used now renders inline in the Hardware card (#519) [Bug]
- "Counters & state" debug-panel section moved to the FIRST position in the host-debug grid (#529) [Bug]
- Snapshot persistence timestamp (`_stale_ts`) now reflects "last LIVE probe" instead of "last save" (#532) [Enhancement]
- "No data from any enabled provider — OmniGrid could not match this host to <providers>" banner now suppresses... (#533) [Enhancement]

## [1.2.0] — 2026-04-28

Second MINOR cut on top of `1.1.0` — rolls up **118 closed issues** under the 1.2.0 milestone. Every entry shipped to the live deploy as a PATCH bump on the daily CI cadence; this MINOR bundles them under a single tag for rollback / changelog purposes.

### Highlights

- **FIDO2 passkeys as a 2FA factor** alongside TOTP — full enrolment flow, recovery codes, force-2FA toggle from Admin → Users, passkey transports rendered as inline chips.
- **OIDC / SSO end-to-end** — Google + Authentik + generic providers; secure cookie cleanup on every callback path; digest-mismatch + RP-ID hardening on macOS WebAuthn.
- **Real-time event stream** replacing the SPA's polling loops — new `/api/events` SSE endpoint backed by an in-process pub/sub bus; toolbar "Live" pill flips state on connection health; `op:created` / `op:updated` / `cache:invalidated` / `stats:refreshed` / `host:row_updated` and ~10 more events wired through.
- **Logs view + daily-rotated retention** — multi-level filter chips, copy-to-clipboard, configurable retention via Admin → Config, on-disk rotation honors level config at runtime.
- **Beszel / Pulse / Webmin / Portainer provider system** — per-provider chips, mono SVG icons, paused-banner state, drawer overlay surface, master enable toggles per provider.
- **Mobile / responsive overhaul** — no more horizontal page scroll on iPhone, mobile-first toolbars, Toolbar + Nodes header wrap cleanly, mobile topbar phase 1.
- **Notifications system** — 12+ event types wired through Apprise, per-event enable toggles in Admin, dedupe window, force-immediate test button.
- **Schedules & automation** maturity — schedule history view, master schedule enable, per-schedule run history.

### Authentication, passkeys & 2FA

- User force-2FA toggle from Admin → Users table (#114) [Bug]
- Enrolment QR was rendering at full container width (~600-800px on desktop). qrcode-generator SVG has no int... (#115) [Enhancement]
- FIDO2 passkeys as a 2FA factor alongside TOTP (#116) [Enhancement]
- QR rendering bug — TOTP enrolment QR was showing the raw `otpauth://...` URI instead of an actual QR code (#117) [Enhancement]
- The TOTP / 2FA policy section (master toggle + per-role required + lockout window) moves out of Admin → Con... (#119) [Enhancement]
- Button + dirty indicator on Admin → Authentication tab TOTP/2FA section (#121) [Enhancement]
- Profile section icons — About card heading gets `#icon-id-card`; Two-factor card heading swapped its inline... (#128) [Enhancement]
- Six text buttons (`→ readonly` / `Disable` / `Reset pw` / `Disable 2FA` / `Force 2FA` / `Delete`) replaced... (#133) [Enhancement]
- Users table status pills (Active / Disabled / admin / readonly / 2FA On / Off / Required) were rendering co... (#156) [Enhancement]
- Master toggle for passkey enrolment + login (`passkeys_allowed`, default true) (#158) [Enhancement]
- Wraps Duo's `webauthn>=2.0` package via `logic/webauthn_helper.py`; routes `GET /api/me/webauthn`, `POST /a... (#160) [Enhancement]
- Enrolment didn't reliably surface the password-manager save sheet (1Password / Bitwarden / iCloud Keychain) (#161) [Bug]
- Enrolment was failing with `SecurityError` behind NPM. `_request_rp_id` was reading `request.url.hostname`... (#162) [Enhancement]
- Profile → Passkeys card: "Add a passkey" button sat flush against the bottom of the enrolled-keys list with... (#164) [Enhancement]
- Sessions table now shows the 2FA method each session was authenticated with (Password / Password + TOTP / P... (#165) [Enhancement]
- `passkeys_allowed` now returned by `api_get_settings` next to the TOTP-policy fields (#196) [Enhancement]
- 17-enhancement sweep across OIDC / events / metrics / TOTP / Webmin / WebAuthn (#199) [Enhancement]
- Passkey transports rendered as inline chips (#213) [Enhancement]

### OIDC / SSO

- Style mono icons for Admin → Portainer + Admin → OIDC (Authentik) (#150) [Enhancement]
- `/api/oidc/test` now respects the in-flight `verify_tls` checkbox from the OIDC settings form instead of al... (#176) [Bug]
- `_validate_id_token` in `logic/oidc.py` was feeding the unverified id_token header's `alg` straight into Py... (#178) [Bug]
- `_validate_id_token` in `logic/oidc.py` now logs `[oidc] kid=... not in cached jwks (#185) [Enhancement]
- `_validate_id_token._find_key` now rejects `kid is None` instead of silently picking `keys[0]` (#200) [Bug]
- OIDC flow cookie now deleted on every callback failure path via `HTTPException(headers=...)` (#201) [Bug]
- `verify_authentication` now actually performs the sign-counter regression guard the docstring promised (#202) [Bug]

### Real-time / event stream

- SSE pill gains a third "reconnecting" state with amber pulse (#211) [Enhancement]
- Time event stream replacing the SPA's polling loops (#214) [Enhancement]

### Logs view & retention

- Logs view gained a severity multi-select filter (Error / Warning / Success / Info) (#146) [Enhancement]
- Logs on disk + configurable retention (#152) [Enhancement]
- Tab Admin → Logs viewer + new `prune_logs` scheduler kind (#153) [Enhancement]
- Logs → Files tab now renders log files with the same colourisation as the Live tab (#154) [Enhancement]
- `_run_prune_logs` schedule kind in `logic/schedules.py` accepted an admin-supplied `params.days` without a... (#179) [Enhancement]
- `_run_prune_logs` schedule history rows now record the resolved `days` value in `target_name` (`"42 log fil... (#188) [Enhancement]
- `prune_old_logs` cutoff math + filename-date parse now route through a shared `_resolved_tz()` helper s (#195) [Bug]

### Schedules & automation

- `/api/ops` poll cadence is now a tunable (Admin → Config → "Ops poll cadence (ms)"). Backed by `tuning_ops_... (#145) [Enhancement]
- Schedules ("Prune <docker-host>", "Refresh fleet cache") were re-seeding on every container boot even afte... (#159) [Enhancement]
- Rows could get stuck "running" forever after a lifespan cancel mid-run. `fire_schedule()` records `(last_op... (#175) [Bug]
- **Unified topbar refresh cadence** (#206). Replaced the separate SYNC + STATS pickers with ONE control offe... (#206) [Enhancement]

### Notifications

- Notifications admin tab into Notifications + General; per-event notification toggles. `logic/ops.py:notify(... (#107) [Enhancement]
- Notification (#108) [Enhancement]
- When a notification fires for a specific user (per-event opt-in path with `actor_username`), the configured... (#138) [Enhancement]
- Host_paused" notification event fires when `host_metrics_sampler` auto-pauses a host after the configured f... (#142) [Enhancement]
- Success notification title now includes the new version number (#143) [Enhancement]
- `notify(event=...)` was hardcoding the per-event admin-gate default to `True`, but `_NOTIFY_EVENT_DEFAULTS`... (#169) [Enhancement]

### Hosts editor & Host groups

- Subgroup in Admin → Host Groups now scrolls the new row into view + focuses the name input (#113) [Enhancement]
- View: parent group labels now render in `--text-dim` (slightly faded) so sub-group labels stand out as the... (#197) [Enhancement]
- Stale-data badges in the Hosts UI (#216) [Enhancement]

### Drawer, charts & Node Exporter

- `loadHostHistory` now stamps `loadedAt = Date.now()` on every successful HTTP 2xx, regardless of whethe (#100) [Enhancement]
- Chart "?" data-source icons in host drawer (#129) [Enhancement]
- Chart `?` icons now resolve a definitive per-host label instead of a generic "Beszel OR node-exporter" string (#130) [Enhancement]
- Host metric-source tooltip now correctly resolves `cpu` and `load_avg` to node-exporter for NE-only hosts (... (#137) [Bug]
- Tooltip cropped at the host-drawer start edge on left-column metric cards (#141) [Bug]
- Chart in the host drawer for hosts whose Beszel agent emits thermal sensors (e.g (#166) [Enhancement]
- Chart upgraded to multi-line + Y-axis scale (#167) [Enhancement]
- Chart polylines were invisible AND y-axis labels rendered out of bounds (#172) [Enhancement]
- `refreshHostRow` in the SPA leaked stale fields when `/api/hosts/one/{id}` omitted a key (#177). The origin... (#177) [Bug]
- Card legend chips overflowed the chart's right edge on hosts with many thermal sensors (8 cores) (#182) [Enhancement]
- Host cards reported memory as 1024× the real value on Webmin module variants whose `mem_total` / `memory_to... (#190) [Bug]
- Host drawer "Updated Xs ago" label gains absolute-ISO tooltip for Grafana correlation (#212) [Enhancement]

### Stats sampler & metrics infra

- `host_net_sampler` was ignoring the permanent-fail auto-pause. The metrics sampler already skipped paused h... (#151) [Bug]
- `stats_samples` was gaining duplicate rows for the most-recent sample of each item after every container re... (#168) [Enhancement]
- `_HOST_SNAPSHOT_KEYS` whitelist in `logic/gather.py` was dropping real provider-emitted fields, so when a p... (#170) [Bug]
- `_record_failure` in `logic/host_metrics_sampler.py` was sync but reached for `asyncio.get_event_loop()` to... (#180) [Enhancement]
- `resumeHostSampling` force-refreshes immediately after the operator un-pauses a host so the first post- (#181) [Enhancement]
- `_get_host_provider_state` cache key in `main.py` now includes the active-sources tuple. Previously a setti... (#183) [Enhancement]
- `_get_failure_state` (`logic/host_metrics_sampler.py`) docstring cleaned up (#189) [Enhancement]
- Host-snapshots read-side cache (#192) [Bug]
- `_get_failure_state` in `logic/host_metrics_sampler.py` lagged the schema after #189 added `host_failure_st... (#193) [Enhancement]
- _warned_no_mounts` set replaced with a 1024-entry FIFO-evicting `OrderedDict` (#205) [Enhancement]
- StaleAge guard for missing `_stale_ts` (#207) [Enhancement]

### Beszel / Pulse / Webmin / Portainer

- `_flatten_temperatures` was being called THREE times per point in `logic/beszel.py:fetch_system_history` (o... (#184) [Enhancement]
- Fetch_system_history` in `logic/beszel.py` was building the PocketBase filter via f-string interpolation (#191) [Bug]

### Admin & Settings pages

- Admin env-vars-still-set warning banner (#104) [Enhancement]
- Admin → Version copy + the deploy.yml bump-step note. `patch_label` drops the "(CI-managed)" suffix; `patch... (#105) [Enhancement]
- Two-layer scoping (admin global + per-user) (#110) [Enhancement]
- Becomes a settings-sidebar peer of Profile / Ignore list / Language. `settingsSections` gets `{id:'notifica... (#118) [Enhancement]
- Settings-sidebar peer of Profile / Notifications / Ignore list / Language (#120) [Enhancement]
- Save-button copy across admin tabs (#123) [Enhancement]
- Header icons on Admin + Settings views (#124) [Enhancement]
- Intro paragraph ("User accounts, active sessions, and API tokens (#127) [Enhancement]
- Every admin tab's primary heading now renders its matching `adminSections[i].icon` next to the title using... (#131) [Enhancement]
- Users / Sessions / Tokens intro paragraph moved from above the section boxes to below them, so the start of... (#147) [Enhancement]
- Log (Admin → History) now uses server-side paging instead of fetching the whole filtered set up to a 500-ro... (#173) [Enhancement]
- Admin → Config tuning fields client-side integer + bounds validation (#210) [Enhancement]

### Topbar, login & branding

- Topbar widgets card always showed "Unsaved" indicator on page open. `_headerPrefsBaseline` was initialised... (#112) [Bug]
- Logo inside the source chip on the Profile page (#125) [Enhancement]
- Schedules (Scheduled + Queue), and Create-User / Create-Token card headers now carry matching icons consist... (#136) [Enhancement]
- Reload" banner was appending `_v=` to the URL on every click instead of replacing it (URL grew as `?_v=1.1.... (#149) [Enhancement]
- Assertion verifier rejected with "Unexpected client data origin" when NPM rewrites the `Host` header to its... (#163) [Enhancement]
- Every hardcoded English string flagged on the SPA + login page now flows through `t('key.path')` (#174) [Enhancement]

### Vendor icons

- Three returns in `iconUrlFor` plus `hostIconUrl`'s explicit-override path AND keyword-scan path (stack/item... (#215) [Bug]

### Filters, badges & status pills

- Symbol>` dedup on `static/index.html`. 15 unique icons (copy / chevron-right / chevron-down / chevron-up /... (#111) [Enhancement]
- "Unsaved" pill text now flashes subtly on a 2s opacity cycle (1 → 0.55 → 1, ease-in-out) (#122) [Enhancement]
- Both flipped from `pill-success` (bright green) to `pill-ok` (subtle muted-green) (#134) [Enhancement]
- Fail marker for chronically-down hosts (#135) [Enhancement]
- `is_meaningful(False)` returned False because Python's `bool ⊂ int` made `isinstance(False, int)` true and... (#194) [Bug]
- Updates badge on the Stacks nav button (#217) [Enhancement]

### Mobile / responsive UX

- Pinch-zoom is now actually disabled on iOS Safari, not just on Android. iOS Safari deliberately ignores the... (#132) [Enhancement]

### API endpoints & backend helpers

- `/api/hosts/one/{host_id}` now accepts `?force=true` to bypass the 10s provider-state cache, mirroring the... (#101) [Enhancement]
- Test endpoints surface human-readable failure summaries instead of raw upstream stack traces (#103) [Enhancement]
- Version page now edits every component (MAJOR / MINOR / PATCH) and writes the values straight to `VERSION.txt` (#106) [Enhancement]
- Timezone fallback now surfaces in `/api/me`'s `client_config.scheduler_tz` (`{configured, resolved, fallbac... (#186) [Enhancement]
- Passkeys_allowed in api_get_settings (#198) [Enhancement]

### Documentation

- Documentation refresh pass — 5 docs files modified to match the recently-shipped feature waves: PII leak in... (#126) [Bug]

### Internal cleanup, refactor & bug sweeps

- Field error on a filtered-out row no longer silently no-ops. `focusFirstFieldError` in `static/js/app.js` e... (#102) [Bug]
- Startup robustness pass. (a): `seed_stats_cache_from_db` and `seed_nodes_info_from_snapshots` moved into a... (#140) [Bug]
- Tab primary action buttons unified (#157) [Enhancement]
- Dead-code cleanup from (#171) [Bug]
- `_HOST_SNAPSHOT_KEYS` is no longer a hand-maintained tuple drift class. Replaced with an `_is_snapshot_key(... (#187) [Enhancement]
- 10-bug sweep shipped in one batch (#203) [Enhancement]
- Five UX-bugs and five UX-enhancements shipped together (#207–#215). was already fixed via #198 (passkeys_al... (#208) [Enhancement]

### Other improvements & fixes

- Editor: typing `custom_number` into a row + tabbing out no longer reorders the row mid-edit. cn `@input` no... (#109) [Enhancement]
- `host_permanent_fail_window_seconds` was kept as a SettingsIn field, GET-side resolver row, and POST-side v... (#139) [Enhancement]
- Dot flicker on the Hosts view's 15s poll cycle (#144) [Enhancement]
- Notifications event grid: "Host sampling auto-paused" split out of the Security events group into its own "... (#148) [Enhancement]
- Edit modal: `kind` + `cadence_mode` dropdowns weren't preselecting the saved value (the documented Alpine s... (#155) [Bug]
- Pointer on every clickable button (#204) [Enhancement]
- SSH terminal close-code toasts (4400/4401/4402/4403) with origin-mismatch path showing NPM-debug guidance (#209) [Enhancement]

## [1.1.0] — 2026-04-26

First MINOR cut after the `1.0.0` baseline — rolls up **97 closed issues** under the 1.1.0 milestone. Every entry shipped to the live deploy as a PATCH bump on the daily CI cadence; this MINOR bundles them under a single tag for rollback / changelog purposes.

### Highlights

- **Drawer-based host UX**: row-expansion converted to a slide-out drawer with explicit 12-col grid + slide animation; host details, debug panel and SSH-run toggles all live in the new drawer surface.
- **Host historical charts from node-exporter** — Prometheus/Grafana-lite path for NE-only hosts. New chart card grid: CPU/Memory/Disk + Bandwidth + Disk I/O + Load Average (1m/5m/15m).
- **Live xterm.js SSH terminal** in Admin → Hosts (admin-only WSS to a backend asyncssh PTY).
- **Asset API integration** on host rows — model/serial/location autofill button + dirty-state tracking.
- **Schedules infrastructure** — daily / weekly / monthly schedules now actually fire (grace window added).
- **Vendor icons batch** — ~30 new vendor icons (Aqara, ASUS, Alienware, Amazon Fire TV, Bose, Chromecast, HDHomeRun, Humax, J-Tech Digital, Kaonmedia, Nixplay, Samsung family rationalisation, +14-icon brand batch).
- **Admin master toggles** for Apprise / Open-Meteo / Portainer / SSH; child controls disable when the master is off; unified Save + dirty-pill pattern across every Admin tab.
- **Multi-database scaffolding** (laying the groundwork for non-SQLite backends).
- **i18n infrastructure** — `actions.close` and friends, every shipped string now flows through `t()`.

### Hosts editor & Host groups

- Host rows joined against an external asset API for model / serial / location, with autofill button + dirty-... (#3)
- Toggle for host-drawer Debug data panel (#4)
- The first character into a host row's ID collapses the panel (#8)
- Range pre-fill on +Add host group (#11)
- "Collapse all" button visual fix (#14)
- "+ Add sub-group" quick button on parent host groups (#15)
- `+ Add sub-group` parent dropdown didn't reflect the chosen parent (#19)
- Group range error message wasn't showing (#23)
- `Show children` parent dropdown adjustment + Expand all / Collapse all bulk buttons (#27)
- Host drawer polish — explicit 12-col grid with `col-span-6` cards, slide animation switched from Alpine x-t... (#34)
- Hosts count badge on the "Hide hosts without agents" filter (#40)
- Groups + sub-groups now HIDDEN when "Hide hosts without agents" filter is on (preference reversed from #45) (#45)
- Service summary in HOST DRAWER (#64)
- Range filter on host drawer charts now triggers refetch (#66)
- Usage chart in host drawer (Beszel) (#68)
- For the Admin → Hosts editor (122 hosts → 200+ projected) (#72)
- Hosts editor page across reloads / tab nav (#79)
- Only host drawer: Disk I/O + Network charts now distinguish "no activity in window" from "node-exporter doe... (#85)
- Pagination + sticky action bar mirroring the Hosts editor (#86)
- Debug + SSH-run toggles in the host drawer now scroll the just-expanded body to the top of the drawer viewport (#96)
- Hosts / Host groups action bar now matches the editor section's width AND pins correctly to the viewport bo... (#97)

### Drawer, charts & Node Exporter

- View: CPU sparkline invisible on idle nodes (#21)
- Row expansion converted to slide-out drawer (#22)
- Sparks self-diagnostic + `app().statsDebug()` console helper (#35)
- Host historical graphs from node-exporter (Prometheus/Grafana-lite path for NE-only hosts) (#41)
- Mem/Disk chart Alpine errors fixed (SVG <template> doesn't work) (#47)
- Subtitle reflects actual stats picker + polling cadence honors it (#52)
- In + Net Out combined into one chart shipped (#61)
- Disk I/O chart shipped (#62)
- Average chart shipped (1m / 5m / 15m) (#63)
- Bandwidth chart shipped (#65)
- Line chart legend values no longer all-red (#67)
- Theme + hotkeys pushed down by stats picker (#73)
- I/O chart hidden for NE-only hosts (#77)
- Only host Disk I/O chart now populates from `node_disk_{read,written}_bytes_total` counters (#78)
- Drawer "No NIC activity" hint now branches on whether node-exporter is in play, not on whether Beszel is ma... (#80)
- Only host Disk I/O chart was stuck on perpetual `0 B/s` for NAS / RAID boxes (Synology, TrueNAS, OPNsense).... (#82)
- Disk I/O support for NE-only hosts (#89). `parse_disk_counters` now falls back to `node_devstat_bytes_tota... (#89)
- Drawer charts now show a subtle `Updated Xs/m/h ago` freshness hint beside the time-range picker (#95)

### Admin pages: Apprise / Open-Meteo / Portainer / SSH / Debug / Sessions

- Admin-only xterm.js viewport over WSS to a backend asyncssh PTY (#2)
- Debug-panel toggle removed from Admin → Hosts (#10)
- Service "enabled" master switches for Apprise, Open-Meteo, Portainer, SSH (#13)
- Admin → all tabs — master-toggle treatment unified: child controls disable when the master is off; Apprise... (#18)
- Admin tabs use Save button + show "Unsaved" indicator (Apprise / Open-Meteo / Portainer / SSH) (#20)
- Api/items` 500 scope bug (#37)
- Inventory dirty pill unified with other admin tabs (#49)
- 4 admin-tab dirty flags unified to smart-getter pattern (#50)
- Meteo Save button moved below the URL input (#51)
- Admin → Config tab — UI override for the 6 process-level tunables (#76)
- Admin → Debug tab: smart-getter dirty pattern + Save button (#81)
- _format_provider_test_summary()` in `main.py` keeps the Pulse + Beszel test-connection response shape ident... (#91)

### Schedules

- Daily / weekly / monthly schedules now actually fire (grace window added (#16)
- weekly npm audit + node_modules served via allowlist (was wildcard mount) (#55)

### Topbar, login & branding

- Topbar split into two rows (Option A) (#7)
- Clock + weather repositioned LEFT of the user avatar (#9)
- Brand icons batch — 14 new icons + keyword wiring (#32)
- Humax brand icon added (#42)
- Clean wordmark for `samsung`, corporate mark to `samsung-electronics` (#43)
- Kaonmedia brand icon added (#44)
- Header "Update stack" button hides when stack is expanded (#46)
- Mobile topbar phase 1 — no more horizontal page scroll on iPhone (#56)
- Toolbar + Nodes header wrap cleanly on mobile (#57)
- Topbar widgets prefs follow the dirty-pattern (no auto-save on toggle) (#59)
- Avatar lifts up to row 1, clock+weather take their own row (#60)
- Utility belt merged into header flow + language above SYNC (#70)
- Page logo no longer shows a white halo at the rounded corners (#93). `static/login.html` swapped from the... (#93)

### Vendor icons

- ~30 new vendor icons added across multiple batches (Aqara, ASUS, Alienware, Amazon Fire TV, Bose, Chromecas... (#6)
- HDHomeRun + J-Tech Digital + Nixplay (#71)

### Documentation

- `CHANGELOG.md` (this file) at the repo root, in Keep-a-Changelog format, with `[Unreleased]` + `[1.0.0]` ba... (#88)
- `README.md` ref updated from `notes/note_authentik.txt` to `notes/guidelines/authentik.md` (#92)
- Operator-private hostnames in shipped docs and code comments with example.com placeholders (#94)

### Filters, badges & status pills

- Paused"` status now correctly maps to `"down"` (#38)
- Colour cleanly + always show "0 failed" (#74)
- Filter bar (Stacks / Services / Nodes views): the divider between the health and status filter groups no lo... (#84)

### Internationalisation & translations

- `actions.close` i18n key (#29)

### Database / migrations / data

- Type ShortName field name confirmed + backend exposes `type_short` (#26)
- schema-migration infrastructure (logic/migrations.py) (#54)
- User UI prefs sync (cross-device) (#58)
- Scaffolding for multi-database support (#75)

### Internal / refactor / code review

- Host Groups editor — collapsible children, NUMBER input moved to the natural Tab-order column, group headin... (#5)
- Signature-based dedupe (#12)
- Short detection widened + diagnostic added (#17)
- Reverts/cleanup follow-ups from this session (#24)
- Fix turn from the code-review report (#31)
- **Code-review compliance batches** (closed all of (#33)
- surface SESSION_SECRET-auto-generated warning to admins (#53)
- Fresh full-code-review pass (#83)
- Model switched back to SemVer `MAJOR.MINOR.PATCH` after a brief stint with the `MAJOR.MINOR`-only model (#87)

### Other improvements & fixes

- `hostStatsSourceEnabled()` field name typo (#1)
- Provider outages no longer blank the page (#25)
- The `_deriveTypeShort` JS acronym fallback (#28)
- Block agent-memory paths under `static/` (#30)
- Text-compaction fix (img_3.png) (#36)
- Filter Docker / k8s / Proxmox internal interfaces behind a toggle (#39)
- Password field is not contained in a form" warning silenced (#48)
- Paginate + add per-system match diagnostic (#69)
- _load_curated_hosts` between the two NE samplers (#90)

## [1.0.0] — 2026-03-21

Baseline release — first version under the SemVer + `CHANGELOG.md`
cadence (see `docs/RELEASE_PROCESS.md`). The changelog story starts
here.

<!-- Version link references — point to in-tree release-notes files.
     Each `docs/releases/_v<MAJORMINORPATCH>_release_notes.md` is the
     frozen per-MINOR release notes companion (operator-facing,
     vendor-neutral). Pointing the markdown link references at these
     in-tree paths means BOTH the IDE's static link checker AND the
     git-host's markdown renderer resolve the targets correctly, with
     no operator-private hostname / URL leaked. `[Unreleased]` points
     at `docs/RELEASE_PROCESS.md` — the operator runbook for cutting
     the next release. `[1.0.0]` has no entry because v1.0.0 is the
     baseline release with no companion notes file. -->

<!-- The link-ref block below uses blank-line separators between each
     definition (one definition per visual paragraph) as a deliberate
     stylistic choice — it reads more cleanly in a long file. The
     JetBrains markdown formatter would consolidate them into one
     contiguous block (no blank lines between), triggering the
     `IncorrectFormatting` weak-warning on every flagged line. The
     suppress pragma below scopes the noinspection to just this
     block — not a blanket file-wide suppression. -->
<!--suppress IncorrectFormatting -->

[Unreleased]: docs/RELEASE_PROCESS.md

[1.1.0]: docs/releases/_v110_release_notes.md

[1.2.0]: docs/releases/_v120_release_notes.md

[1.3.0]: docs/releases/_v130_release_notes.md

[1.4.0]: docs/releases/_v140_release_notes.md

[1.5.0]: docs/releases/_v150_release_notes.md

[1.6.0]: docs/releases/_v160_release_notes.md
