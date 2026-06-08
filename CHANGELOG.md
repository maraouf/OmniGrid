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

- **Apps — Apprise integration (notification gateway at a glance + send notifications from the AI).** Apprise (caronc/apprise-api — the notification fan-out OmniGrid itself uses) instances pinned via Admin → Apps get an expanded card showing how many **notification endpoints** are configured, which **services** they route to (Telegram / Discord / Email / …), and the **routing tags** you can target — read from `GET /json/urls/<key>` with secrets masked, so no credential is ever shown. No API key needed (point the instance URL at the apprise-api root, or paste the full `.../notify/<key>` URL). The AI assistant and Telegram bot get a skill set: **Apprise status**, **Send a test notification**, and **Send a notification** — ask the assistant to *"send a notification saying the backup finished"* or target a tag with *"send a notification to \[admins] the disk is full"* and it fires it through Apprise.
- **Apps — richer media-list skill results (posters, progress, quick actions).** Skills that list media now render each title with a small poster thumbnail + date instead of a plain text list — a richer at-a-glance view. Live across the media app family — the *arr **Upcoming** lists and **Download queues** (Radarr / Sonarr / Lidarr / Readarr), Seerr **List requests** (now also a one-click drawer button), Bazarr **missing subtitles**, and Tautulli / Plex **Recently added**. A **Download queue** row also shows a progress bar and a per-row **Remove from queue** button (removes the download from the client too). Posters load through the in-app image proxy; titles without a poster show a placeholder.

- **Admin → Apps — "has extra features" indicator.** Each instance in the Apps list now shows a small lightning icon when that app has a built-in integration (live card data + AI / Telegram skills) — alongside the existing Docker-link icon — so it's clear at a glance which pinned apps offer the extras.

- **Apps — Tautulli integration (Plex activity + statistics).** Tautulli instances pinned via Admin → Apps gain a per-instance **API key** field (Tautulli → Settings → Web Interface → API key) with a Test-connection button, following the same drawer / loading / error UX as the rest of the app family. The card shows active **Streams**, **Transcodes**, total **Bandwidth**, and **Libraries**, plus a Tautulli version + total-items footnote. The AI assistant and Telegram bot get a Tautulli skill set: **Tautulli status**, **Who's watching now** (current Plex streams), **List Plex libraries**, **Recently added**, and **Watch history**.

- **Apps dashboard — tiles now fill the row cleanly (no wasted right-edge gap).** The custom-dashboard grid moved to a fixed 12-column layout so the four tile widths always tile flush — e.g. three 3-wide tiles fill the row edge-to-edge instead of leaving an empty strip on the right. Narrow screens collapse to 2-up, then a single stacked column. The Prayer Times and Public IP widgets adapt to the now-wider 2x1 tile: prayers show as a side list focused on the next prayer, and the public IP shrinks so the ISP / ASN / location details stay visible.

- **Apps — qBittorrent integration (live transfer view + torrent actions, multi-instance).** qBittorrent instances pinned via Admin → Apps get a per-instance **username + password** (qBittorrent → Tools → Options → Web UI) with a Test-connection button — the password is stored encrypted-at-rest. The card shows what the client is doing right now: **Downloading**, **Seeding**, **Paused**, and **Total** torrent counts plus a live **⬇ download / ⬆ upload** speed row and the qBittorrent version. You can run **several** qBittorrent instances (e.g. a seedbox and a local box) — each gets its own card, and the AI assistant and Telegram bot target a **specific** instance (give each chip a distinct name so you can say "pause all on the seedbox"; over Telegram, an ambiguous command lists the hosts and asks which one). Skills: **qBittorrent status**, **List torrents** (optionally filtered by state — downloading / seeding / paused / completed / all), **Add a torrent** (by magnet link or .torrent
  URL), **Resume all torrents**, and **Pause all torrents** (destructive-confirm). (Auth uses qBittorrent's WebUI session login; works with v4 and v5. Leave the credentials blank only if the WebUI bypasses auth for the OmniGrid host.)
- **Apps — share your custom dashboards (private or public, read-only or editable).** The named custom dashboards on the Apps tab can now be shared with other users. Each dashboard has a **visibility** setting — **Private** (only you can see it) or **Public** (everyone signed in can see it) — and, when public, an **edit permission** — **Read-only** (others can view it, only you can change it) or **Editable** (anyone except read-only users can rearrange it). Only the owner can delete a dashboard or change its sharing settings; the picker shows a **"Shared by …"** badge on dashboards you don't own and a **Public · Editable / Read-only** badge on your shared ones, and a read-only board hides its edit controls (duplicate it to make your own editable copy). Existing dashboards are automatically migrated to private on first load — nothing changes until you choose to share. (Dashboards now live server-side so they can be shared across accounts.)
- **Apps — Plex integration (library at a glance + now-playing + AI actions).** Plex Media Server instances pinned via Admin → Apps gain a per-instance **Plex token** field (any `X-Plex-Token`) with a Test-connection button, following the same drawer / loading / error UX as the rest of the app family. The card shows **Movies**, **Shows**, **Music**, and **Now playing** (active streams, highlighted when someone's watching), plus a libraries count + Plex version footnote. The AI assistant and Telegram bot get a Plex skill set: **Plex status** (library summary), **What's playing on Plex** (who's streaming what, with progress), **Recently added to Plex**, **Search Plex** (find a title across your whole library), and **Scan Plex libraries** (re-index every section for new media). (Counts come from the PMS HTTP API — library sections + per-section totals + the active-sessions endpoint — cached server-side with a refresh interval configurable per instance.) **Sign in to Plex (no token to
  copy):** the editor has a **"Sign in to Plex"** button that runs Plex's OAuth flow — click it, authorise in the plex.tv popup, and your token is fetched and filled in automatically (the same seamless flow Tautulli / Overseerr use). Pasting a token by hand still works as a fallback.
- **Stats dashboard — six quick-summary cards.** The dashboard now surfaces the headline number from each of the deeper Stats pages so you get a one-glance read without opening them: **Database size**, **Total samples** (rows across every sample table), **Incidents (30d)**, **Network (30d)** (combined RX + TX), **AI cost (30d)**, and **AI jobs (30d)**. Each card clicks through to its full page. (The total-samples figure is a heavier count, so it's cached briefly.) Every dashboard card also now has a **distinct icon** — a few were sharing the same glyph before. **The whole grid now paints instantly** — every card shows its icon + title immediately with a shimmer placeholder inside, then fills in its number when its data lands, so a slow figure (like the sample count) never holds up the rest of the page.
- **More keyboard shortcuts + a tidier shortcuts cheat sheet.** Press `?` (or open it from the user menu) to see the full list. **Profile** now has a shortcut for everyone (Cmd/Ctrl+6); admins also get **Stats** (Cmd/Ctrl+7) and **Admin** (Cmd/Ctrl+8). The cheat sheet's page/view jumps (Stacks, Services, Nodes, Hosts, History + the new ones) moved into their own "Go to" column so the two columns are balanced and easier to scan.
- **Telegram — new `/skills` command.** Lists your per-app skill roster on its own — one tappable entry per pinned app; tap `/<app>` to see that app's commands, or just ask in plain text and the assistant runs them. (Same roster the `/help` menu shows, on its own command.)

- **AI assistant — "what was I looking at on the other tab?"** The AI palette and sidebar can now answer questions about your OTHER open OmniGrid tabs / devices. When you have OmniGrid open in more than one tab (or on your laptop AND phone), the assistant sees each other tab's current view, active filters, selection, and which device it's on — so you can ask "what was I looking at on my desktop?" from your phone instead of re-navigating. (Builds on the existing tab-activity popover and its "Reproduce here" handoff.) (Also answerable from the Telegram bot.)

- **Apps — Kavita integration (digital-library summary + search + scan).** Kavita instances pinned via Admin → Apps gain a per-instance **API key** field (Kavita → Settings → Account → API Key) with a Test-connection button, following the same drawer / loading / error UX as the rest of the app family. The card shows **Libraries**, **Series**, **Volumes**, and total **Size**, plus a Kavita version + chapter-count footnote. The AI assistant and Telegram bot get a Kavita skill set: **Kavita status**, **List libraries** (with each one's type), **Search the library** (find a title across your whole collection), and **Scan libraries** (queue a rescan of every library). (Series / Volumes / Size come from Kavita's admin-only server stats, so they populate when the API key belongs to an admin; the Libraries count always shows.)
- **Apps — Prowlarr integration (indexer manager status + sync + search).** Prowlarr instances pinned via Admin → Apps gain a per-instance **API key** field (Prowlarr → Settings → General → API Key) with a Test-connection button, following the same drawer / loading / error UX as the rest of the *arr family. The card shows **Indexers** (enabled / total), **Apps synced** (connected *arr applications), lifetime **Queries** and **Grabs**, plus a Prowlarr version + health footnote. The AI assistant and Telegram bot get a Prowlarr-appropriate skill set: **Prowlarr status**, **List indexers** (with each one's enabled / disabled state), **Sync indexers to apps** (pushes your indexers to every connected app), and **Search indexers** (manual search a term across every indexer and return the top results). (Prowlarr manages indexers rather than a media library, so there's no calendar, download queue, or storage section.) The AI / Telegram bot can also now **find indexers to add** ("what indexers
  can I add matching X" — searches Prowlarr's full indexer catalogue) and **add an indexer** ("add &lt;name&gt; on prowlarr", optionally "with flaresolverr" to route it through your FlareSolverr proxy); for an indexer that needs extra config the add surfaces Prowlarr's validation message so you finish it in the web UI. The **find indexers** search is facet-aware — asking for "english indexers" matches Prowlarr's `en-US` / `en-GB` / `enAU` locale codes (not just a literal word), you can filter by privacy ("public" / "private" / "semi-private"), and you can combine the two ("english public indexers" lists only the public English-language ones). **Follow-up patch:** a new **Add indexers in bulk** skill adds them all at once instead of one at a time — "add all the public English indexers" adds every matching indexer that isn't already configured (it skips the ones you already have), and any indexer Prowlarr blocks behind a Cloudflare challenge is **auto-retried through your FlareSolverr
  proxy** so it lands without you doing anything. You get a summary back — how many were added (and how many needed FlareSolverr), how many were already there, and any that failed (with the reason). Use the same filter words as the find skill ("public", "english", "public english", an indexer-name fragment, or "all"). **Follow-up patch — FlareSolverr auto-tagging:** some indexers add cleanly but Prowlarr still flags them with "this site may use Cloudflare DDoS Protection, therefore Prowlarr requires FlareSolverr" — these now get the FlareSolverr proxy **tag** applied automatically so they actually link to it. The bulk add does this for the indexers it just added, and a new **Fix FlareSolverr tags** action ("fix flaresolverr" / "tag the cloudflare indexers") sweeps **every** configured indexer (handy for ones you added before FlareSolverr was set up) — testing each via Prowlarr's own test and tagging the ones that need it. **Follow-up patch:** the **Apps synced** line on the Prowlarr
  card now renders each connected app as a small brand-icon chip (icon + name) instead of a raw comma-separated text string.
- **Apps — ddns-updater integration (DNS-sync status + one-click update).** ddns-updater (qdm12/ddns-updater) instances pinned via Admin → Apps get a card showing how many DNS **Records** it manages, how many are **Up to date**, how many are **Failing** (with the failing domains), and your current **Public IP**. ddns-updater has no API key or login, so the editor just needs the instance URL. The AI assistant and Telegram bot get two skills: **DNS records status** and **Update DNS now** (triggers an immediate refresh of every record). (ddns-updater exposes no JSON API, so the data is read from its web UI.)
- **Apps — Readarr integration (book / audiobook library at a glance + AI actions).** Readarr instances pinned via Admin → Apps gain a per-instance **API key** field (Readarr → Settings → General → API Key) with a Test-connection button, following the same drawer / loading / error UX as Radarr / Sonarr / Lidarr. The card shows total **Authors**, **Missing books**, **Downloading**, and **Monitored**, plus a **Storage** section (every mount, free / total, usage bar) and a Readarr version + health footnote. The AI assistant and Telegram bot get a full skill set: **Readarr status**, **Upcoming books** (next 30 days), **Download queue**, **Look up an author** ("do I have *X*?"), **Add an author** (resolves a quality + metadata profile and the most-free root folder, then starts a search), **Remove an author** (keeps the files; destructive-confirm gated), **Search for missing books**, and **Refresh book library**. (Like Radarr/Sonarr/Lidarr, its Storage section is hidden at the 3×1 and 4×1
  card
  size.)
- **Container / service drawer shows the running app version.** When an image sets the standard OCI version label (`org.opencontainers.image.version`), the item drawer now shows that version (e.g. "v1.7.0") as a field between **Stack** and **Replicas** — handy when the image tag itself is just `:latest`. Read from data already gathered, so no extra calls.
- **Apps — Lidarr integration (music library at a glance + AI actions).** Lidarr instances pinned via Admin → Apps gain a per-instance **API key** field (Lidarr → Settings → General → API Key) with a Test-connection button, following the same drawer / loading / error UX as Radarr / Sonarr. The card shows total **Artists**, **Missing albums**, **Downloading**, and **Monitored**, plus a **Storage** section (every mount, free / total, usage bar) and a Lidarr version + health footnote. The AI assistant and Telegram bot get a full skill set: **Lidarr status**, **Upcoming albums** (next 30 days), **Download queue**, **Look up an artist** ("do I have *X*?"), **Add an artist** (resolves a quality + metadata profile and the most-free root folder, then starts a search), **Remove an artist** (keeps the files; destructive-confirm gated), **Search for missing albums**, and **Refresh music library**.
- **"Extras only" filter on both the Apps view and Admin → Apps → Instances.** A new toggle (next to the status filters on the Apps page, and in the Instances search bar) shows only the apps / instances that have a rich expanded card or AI skills (AdGuard Home Sync, Radarr, Sonarr, Seerr, Bazarr, APC, Speedtest, AdGuard, Pi-hole), so you can quickly narrow a large list to the ones with extras.
- **Apps — AdGuard Home Sync integration (sync health at a glance + AI actions).** AdGuard Home Sync instances (bakito/adguardhome-sync) pinned via Admin → Apps gain an expanded card showing whether a **sync is running**, the **origin** status, how many **replicas** are in sync (and which are failing), following the same drawer / loading / error UX as Radarr / Sonarr. HTTP Basic auth is optional — the editor offers an optional **username + password** (with a Test-connection button) for instances that require it, and works against an open instance when left blank. The AI assistant and Telegram bot get four skills: **AdGuard sync status**, **Sync now** (trigger a sync), **Recent sync logs**, and **Clear sync logs**. The app's icon now uses a dedicated AdGuard-Home-Sync mark — the stack and service rows pick it up too (previously they showed the plain AdGuard Home icon).
- **Stats — "Apps with extras" card.** The Stats dashboard has a new card (next to Apps) showing how many of your pinned apps have a rich expanded card / AI skills (Radarr, Sonarr, Seerr, Bazarr, APC, Speedtest, AdGuard, Pi-hole) and how many of those are currently active (at least one instance up). Clicking it opens the Apps view.
- **Apps — Sonarr integration (TV library at a glance + AI actions).** Sonarr instances pinned via Admin → Apps gain a per-instance **API key** field (Sonarr → Settings → General → API Key) with a Test-connection button, following the same drawer / loading / error UX as Radarr. The Apps card shows total **Series**, **Missing episodes**, **Downloading** (the active queue), and **Monitored**, plus a **Storage** section listing every mount with free / total space and a usage bar, and a Sonarr version + health footnote. The AI assistant and Telegram bot get a full skill set: **Sonarr status**, **Upcoming episodes** (next 14 days), **Download queue**, **Look up a series** ("do I have *X*?"), **Add a series** (by title or TVDB id — resolves a quality profile, the most-free root folder, and a language profile on older Sonarr, then starts a search), **Remove a series** (removes the library entry, keeps the files; destructive-confirm gated), **Search for missing episodes**, and **Refresh series
  library**. The API key is stored encrypted-style (never returned in the clear — only an `api_key_set` flag), keep-current-if-blank like every other secret.
- **App drawer — top refresh button.** The app detail drawer now has a refresh button in its header (next to close), matching the host drawer. One click re-probes every instance of the open app for reachability AND force-refreshes its per-app card data (Radarr / Seerr / Bazarr / … expanded panels), bypassing the caches; the button spins until both finish.
- **Apps — Radarr integration (movie library at a glance + AI actions).** Radarr instances pinned via Admin → Apps gain a per-instance **API key** field (Radarr → Settings → General → API Key) with a Test-connection button, following the same drawer / loading / error UX as Bazarr and Seerr. Once the key is set, the Apps card expands to show the metrics that matter for a movie manager — total **Movies**, **Monitored**, **Missing** (no file — amber when any), and **Downloading** (the active queue), plus a **Storage** section listing **every mount Radarr reports** (root / library / remote shares) with free / total space and a usage bar, and a Radarr version + health-issue footnote. The data comes from a small set of v3 endpoints (library list + queue / disk / health), cached server-side with a refresh interval configurable per instance. The AI assistant and Telegram bot get a full set of skills: **Radarr status** (library summary), **Upcoming movies** (next 14 days), **Download queue** (
  what's downloading + progress), **Look up a movie** ("do I have *X*?"), **Add a movie** (by title or TMDB id — resolves a quality profile + the most-free root folder and starts a search), **Remove a movie** (removes the library entry, keeps the files on disk; gated by the destructive-confirm flow), plus **Search for missing movies** and **Refresh movie library**. The API key is stored encrypted-style (never returned in the clear — only an `api_key_set` flag), keep-current-if-blank like every other secret.
- **Apps — Seerr (Overseerr / Jellyseerr) integration + request & suggest movies from the AI.** Seerr instances pinned via Admin → Apps gain a per-instance **API key** field (Seerr → Settings → General → API Key) with a Test-connection button. Once set, the Apps card expands to show the request queue at a glance — **pending**, **processing**, **available**, and **total** requests, plus open-issue and version footnotes (from one cheap `GET /api/v1/request/count`, cached server-side). The headline feature is in the AI: ask the assistant or the Telegram bot to **request a movie by title** ("request Inception", "can you get The Matrix?") and it searches Seerr, picks the top movie, and submits the request — no confirmation friction. Ask it to **suggest a movie** ("what should I watch?") and it pulls a random popular title **you don't already have** (it skips anything already requested or in your Seerr library), shown with its **rating, year, genre, country, and top-billed cast**, a poster
  preview, plus a
  one-click **Request on Seerr** button — right in the AI sidebar AND as a native tap-button on Telegram — or just say "request it". You can also set **per-user suggestion filters** that the AI saves to your account — "don't suggest French movies", "only movies rated 7 or higher", "no horror" — and ask it to show or clear them; every suggestion honours your filters. Three skills back this — **Seerr status** (read-only), **Request a movie** (takes the title), and **Suggest a movie**. Movie suggestions use **TMDB**: add an optional per-instance **TMDB API key** + base URLs (defaults `https://api.themoviedb.org/3` and `https://image.tmdb.org/t/p`, tolerant of either base form) under the app's editor; without a TMDB key, suggestions fall back to Seerr's own discover list. Both API keys are stored encrypted-style (never returned in the clear — only `*_set` flags), keep-current-if-blank like every other secret. **Follow-up patch:** a new **List requests** skill names the actual titles (with
  year) in your queue instead of just the counts — ask "what movies are processing on Seerr?" / "what's pending approval?" and it lists each one (🎬 movies / 📺 shows). It defaults to the in-progress queue (processing + pending); add a word to narrow or widen it ("approved", "available", "all"). **Follow-up patch:** the Seerr card now shows more of the dashboard at a glance — **declined** and **available** counts, the **Movies** vs **TV** request split, **total**, and a **Top requesters** list (each user's avatar, name, and request count). The extra figures come from the same `GET /api/v1/request/count` plus a tolerated `GET /api/v1/user?sort=requests`; a failure on the users call never breaks the card.
- **Apps — Bazarr integration (missing-subtitle counts at a glance).** Bazarr instances pinned via Admin → Apps gain a per-instance **API key** field (Bazarr → Settings → General → API key) with a Test-connection button. Once the key is set, the Apps card expands to show the four metrics that actually matter for a subtitle manager — **episodes missing subtitles**, **movies missing subtitles**, **throttled providers** (amber when any), and **health issues** (green at 0, red otherwise) — plus the Bazarr version. The data comes from one cheap `GET /api/badges` call (cached server-side, refresh interval configurable per instance). The AI assistant and Telegram bot get three skills: **Bazarr status** ("how many subtitles are missing?"), **Search for missing subtitles** (kicks off Bazarr's wanted-subtitle search for movies + series now), and **List missing subtitles** (shows what's still missing) — all also available as one-click buttons in the app drawer. The API key is stored
  encrypted-style (never returned in the clear — only an `api_key_set` flag), keep-current-if-blank like every other secret.
- **Prayer Times — reminders before each prayer.** OmniGrid can now send a notification a few minutes before every daily prayer. It's a normal notification **event** — an admin enables **Prayer reminder** under **Admin → Notifications** (in the new *Information* category, off by default like the sign-in event), and then **each user who wants reminders opts in** and picks their channels (in-app / Telegram / Apprise) in the same **Profile → Notifications** table. Opt-in is explicit and per-user — because reminders are personal and location-specific, a user only receives them if they turned the event on themselves (so a shared channel like Telegram never gets a copy for every account on the system). An admin sets the global lead time (default **10 minutes** before each prayer) under **Admin → Prayer Times**; set it to 0 to turn reminders off entirely. Reminders use your saved location (falling back to the admin default), respect each channel's global on/off switch, and fire exactly once
  per prayer per day (no duplicates across restarts). Requires Prayer Times to be enabled.
- **Prayer Times — "Recent samples" history panel (Admin → Prayer Times).** A background sampler now records the day's five prayers + Sunrise + the Hijri date once per day per location into a small history table, and Admin → Prayer Times gains a collapsible "Recent samples" panel (matching Weather / Public IP) showing the most recent days with their prayer times and Hijri date. Two new tunables control it: a sampler cadence (default every 6 hours — prayer times are daily-static) and a retention window (default 90 days; 0 keeps every row). Disabling Prayer Times stops the sampler; the on-demand widget / AI / Telegram lookups are unaffected either way.
- **Port scan — broader default coverage of common TCP and UDP service ports.** The default port-scan list now probes a much wider set of well-known service ports out of the box (TCP grew from 217 to 282 — adding Kerberos, Modbus, IPMI, Docker Swarm, etcd, Oracle, iSCSI, LDAP global-catalog, Ceph, Kafka, Cassandra, Solr, kubelet, RabbitMQ management, WinRM-HTTPS, RADIUS, Java RMI and more; UDP grew from 20 to 39 — adding Kerberos, rpcbind, LDAP CLDAP/LDAPS, QUIC, DHCPv6, RADIUS, NFS, STUN, VXLAN, LLMNR, Geneve, traceroute, WS-Discovery and more). Every default port now carries a service-name hint for the scan results. No ports were removed, and UDP scan timing/concurrency defaults are unchanged.
- **Prayer Times — dashboard card + AI / Telegram prayer-time & Hijri-calendar answers.** A new custom-dashboard widget card shows today's five daily prayers (Fajr / Dhuhr / Asr / Maghrib / Isha) each with its own icon, the **next prayer highlighted with a live countdown**, and today's **Hijri (Islamic) calendar date** — all from one call to the free AlAdhan API (no API key). Add it from a custom dashboard's **+ Widget** menu; it uses your saved Weather location automatically. Configure it under **Admin → Prayer Times**: a master enable toggle, the **calculation method** (full AlAdhan method list — defaults to Egyptian General Authority), the **Asr school** (Standard/Shafi'i or Hanafi), an optional fallback location, and a Test-connection button. The AI assistant and the Telegram bot can both answer prayer-time and Hijri questions from real data — ask "when is the next prayer?", "what time is Maghrib?", or "what's the Hijri date today?" in either surface, or use the Telegram `/prayer`
  and `/hijri` commands. Disabled by default (enabling authorises outbound calls to api.aladhan.com); all settings are stored in the database.
- **Apps — AdGuard Home integration (aggregated stats + fleet actions, via the REST control API).** AdGuard Home instances pinned via Admin → Apps gain per-host **username + password** fields (HTTP Basic auth) with a Test-connection button. Because the Apps view aggregates the same app across hosts, AdGuard's expanded card shows **one combined block for the whole fleet** (not a row per host): blocked today (count + %), queries today, blocklist domains (max across hosts), active clients, average processing time, and the top blocked domain — with a protection on/off indicator and a per-host footnote if a host is unreachable. Fleet-wide actions are available right on the card (admin only): **Enable**, **Disable** (indefinitely), **Disable for** a preset window (1m / 5m / 10m / 30m / 1h / 2h / 24h), **Refresh blocklists**, and **Re-enable** (cancel a timed disable) — every action applies to all AdGuard hosts at once. The same actions plus a read-only **AdGuard status** summary are exposed
  to the AI assistant and the Telegram bot, so you can ask "how much did AdGuard block today?" or "disable AdGuard for 10 minutes" in either surface; disabling protection asks for confirmation first. The password is stored encrypted-at-rest (never returned to the browser — only an `api_key_set` flag), keep-current-if-blank like every other secret.
- **Apps — Speedtest Tracker app with expanded latest/average/graph card.** Speedtest Tracker (`speedtest-tracker.dev`) instances pinned via Admin → Apps gain a per-instance API key field plus a Test-connection button (Bearer-auth probe against the upstream `/api/v1/speedtests/latest`). Once the key is set, the Apps view's Speedtest card expands to two columns and renders the latest download / upload / ping plus the 10-test rolling average plus a 3-line SVG sparkline (download green, upload blue, ping amber-dashed) over the most recent 30 tests. Data refreshes every 60 seconds (cached server-side) with a manual reload button on the chart card. The api_key is stored encrypted-style (never returned in the clear — only an `api_key_set: bool` flag flows to the SPA) using the same keep-current-if-blank contract every other secret in OmniGrid uses.
- **Apps — Per-instance and per-template "Show extras" toggle.** APC (and now Speedtest Tracker) cards render rich per-host extras (battery / load / runtime / temperature for APC; latest / average / graph for Speedtest). Both the per-template (Admin → Apps → edit Template) and per-instance (Admin → Apps → edit Instance) editors now carry a "Show extras" checkbox so you can hide the panel without unpinning the chip. Per-instance is tri-state: indeterminate (inherit template default) → checked (always show) → unchecked (always hide regardless of template).
- **Apps — Per-app encapsulation architecture.** Each app's custom backend logic, frontend helpers, and HTML markup now live in their own dedicated trio of files under `logic/apps/<slug>.py`, `static/js/apps/<slug>.js`, and `static/_partials/_components/apps/<slug>_editor.html` + `<slug>_extras.html`. Adding a new app with custom logic (auth-required data fetch, expanded card, brand-specific extras) is one file per layer plus one registration entry — generic files (`apps_routes.py`, `app-apps.js`, `apps-card.html`, `admin/apps.html`) stay app-agnostic. Slug-keyed dispatchers (`POST /api/services/{host_id}/{service_idx}/test-credential`, `GET /api/services/{host_id}/{service_idx}/app-data`) resolve the chip's catalog template, look up the per-app module via the registry, and delegate. The APC UPS extras (Battery / Output load / Runtime / Battery temperature / Battery state panel) and the Speedtest Tracker extras (latest / 10-test average / 3-line SVG sparkline) both ship under this
  architecture as the reference implementations.
- **Apps — richer Speedtest results in the AI assistant (Telegram + web sidebar).** Asking the AI to "show me the latest speed test" now replies with emoji-formatted stats — ⬇️ download, ⬆️ upload, 🏓 ping, 🕒 time — and reports BOTH the latest values AND the rolling average download / upload / ping. The timestamp renders in your own date/time format (Settings → Profile → Formats) instead of a raw ISO string. When the result carries a Speedtest share image, the AI includes it; on Telegram the bot sends the result image as a photo so it displays inline (text replies disable link previews). The averages and result-image link are surfaced from the cached last result, so "show latest" never triggers a new test.
- **Slow-query log line — caller-site identification.** When `tuning_slow_query_threshold_ms` is set above 0, each `[slow_query] warning:` line now includes a `site=<path>:<function>:<lineno>` field identifying the user-meaningful caller (the route handler / sampler / scheduler runner that ran the query) instead of just the SQL. Stack walk fires only on queries past the threshold so the steady-state cost stays zero. Diagnosing "why is this PRAGMA slow?" no longer requires reverse-engineering the SQL — the caller path is right there.
- **Stats → Samplers sub-tab.** New admin Stats dashboard sub-page surfacing per-sampler live state: running flag, last-tick timestamp, last-tick row count, last-prune row count, effective interval. Lets admins verify each sampler is actually writing rows AND pruning to retention at a glance — distinct from the existing Stats → Samples (which counts the resulting rows but doesn't surface whether the writer is alive). Backed by `GET /api/admin/stats/samplers`.
- **Telegram `/reboot` alias for `/restart`.** Same handler, same destructive-confirm gate. Adds the muscle-memory alternate verb users reach for when they want the OS-level restart vs the Docker service restart — both verbs work, the canonical name stays `/restart`.
- **Telegram `/moon` command + AI moon-phase grounding.** Replies with today's moon phase + illumination percentage. Requires the WeatherAPI.com provider (Open-Meteo is moon-blind); when Open-Meteo is active the bot honestly says "I don't have moon data" instead of hallucinating.
- **Stats → Samplers sub-tab — distinct icon.** Was sharing the `activity` icon with the Network sub-tab; switched to `loader` so the two sibling sub-tabs read distinctly in the sidebar pill and the section header.
- **Weather: dual-provider support with moon-phase data.** Admin → Weather is now a full provider tab (alongside Portainer / OIDC / Asset Inventory) letting you pick between Open-Meteo (free, no key, no moon data) and WeatherAPI.com (free key from weatherapi.com, full moon astronomy — phase / illumination / moonrise / moonset). The moon-phase Apps widget + moon-related AI palette / Telegram answers auto-disable when Open-Meteo is selected (cleanly — the AI honestly says "I don't have moon data" instead of hallucinating phases). Test-before-Save gate validates the WeatherAPI key against the live endpoint before allowing a save; Open-Meteo Save is unconditional (no key to validate). A "Use my profile location" button stamps lat/lon/label from your saved profile weather location.
- **Weather: historical sampling for AI / Telegram retrospective questions.** A lifespan-managed sampler writes one row per hour (default — tunable) to a new `weather_samples` table covering temperature / humidity / wind / condition / moon phase / illumination. The AI palette and Telegram bot now answer "what was the weather yesterday afternoon" / "when was the last full moon" / "rainiest day this week" from cached historical samples instead of refusing or re-querying. Retention defaults to 90 days; set to 0 to keep every sample forever for long-term climate trends.
- **Apps Custom — Moon-phase widget.** New `moon` widget kind for the Custom layout — illumination ring (SVG donut, lit-fraction drives the fill), full phase name hero ("Waxing Gibbous" / "Waning Crescent" / "Full Moon" / "New Moon" etc.), illumination percentage subtitle, and moonrise/moonset detail row. Hides automatically with a "Switch to WeatherAPI.com" hint when the active weather provider can't supply moon data.
- **Apps Custom — Per-widget refresh button + freshness label.** Every refreshable widget tile (weather / moon / public_ip) now has a refresh-cw icon button that force-bypasses the cache to re-query the backend, and a subtle "Updated Ns/Nm/Nh ago" footer chip that self-ticks every second so you can see how fresh the data is at a glance. Client-side widgets (clock / system stats) skip the refresh button cleanly — refreshing them would be a no-op.
- **Apps Custom — Bookmark optional icon URL or slug.** Add-bookmark form now has a third input for an optional icon — paste a full URL (svg / png / favicon / data: URI all work) OR a brand slug like `plex` / `github` / `adguard` to reuse the existing brand-icon resolver. Blank falls back to brand-resolution on the bookmark's name (existing behaviour). Persists in your `ui_prefs` like the rest of the Custom layout, so your bookmark icon survives across machines and browsers.
- Apps view now explains why an instance is degraded or down: each non-healthy instance shows the probe failure reason (timeout, connection refused, unexpected status, …) inline, and multi-port services render a per-port status pill so you can see exactly which port is failing without leaving the view.
- App detail drawer: clicking an app card opens a slide-out panel showing its catalog binding and every host instance, with a per-instance debug panel that reveals the exact probe target(s), per-port outcomes, and — when an app can't be probed — a plain-language reason (e.g. "chip has no URL and the host has no Address set", "probe is disabled"). Includes a per-instance "Probe now" action.
- Built-in Apps catalog templates added in a release now appear automatically on the next deploy (a seeded-slug ledger picks up new built-ins while leaving ones you deleted on purpose gone) — AdGuard Home is included.
- The empty Apps view now offers admins a "Run discovery on a host" shortcut into the discovery wizard, and the app status pill announces status changes to screen readers.
- Admin → Apps → Instances is now editable: edit a pinned app's name / URL / icon / ports / probe inline (with a per-port editor), or remove it, via a per-row editor and an Edit button in the app detail drawer (chips were previously create-only).
- The AI command palette can now open the Apps discovery wizard for a host (e.g. "discover apps on web01").
- Admin → Apps → Instances can now be grouped by host or by service (with collapsible groups) to tame a long list of pinned apps.
- App instances can be linked to a Portainer container or stack (in the instance editor), adding inline Restart and Update actions in the app detail drawer.
- Added Syncthing (8384/tcp Web UI) and a Plex DLNA port (32469/tcp) to the built-in Apps catalog templates.
- Added MariaDB (3306), PostgreSQL (5432), MongoDB (27017), and InfluxDB (8086 HTTP API + 8088 RPC) to the built-in Apps catalog templates, with a bundled MariaDB brand icon. All four ports are already covered by the default port-scan list, so an open database port is matched to its app in the discovery wizard.
- Added Dockge (Docker stack manager) to the built-in Apps catalog templates with a bundled brand icon.
- Added Splunk (8080), Beszel Hub (8090, distinct from the Beszel agent), and RustDesk (relay ports 21114–21119) to the built-in Apps catalog templates, with a bundled Splunk brand icon and the RustDesk ports added to the default port-scan list.
- The Apps "Pin to host" picker is now a searchable, type-to-filter dropdown (matching the discovery wizard) instead of a long plain list of every host.
- Added Proxmox VE (8006 HTTPS Web UI + 8443), MySQL (3306), and UniFi OS Server (8080/8443/11443) to the built-in Apps catalog templates. Proxmox's 8006 and the Windows Update Delivery Optimization port (7680) were added to the default port-scan list so an open port is matched to its app in the discovery wizard.
- **GitHub and Google Cloud Source Repositories brand icons.** Pinning a GitHub app (or naming a host `github`) now renders the GitHub mark, which flips to its white variant automatically on the dark theme. Google Cloud Source Repositories (slug `gcsr`) renders its own brand mark.

### Changed

- **AdGuard Home Sync "Recent sync logs" now reads as a clean log list instead of raw JSON.** Running the skill from the app drawer used to dump the raw log buffer (a wall of JSON) into the result box. Each entry is now shown as a tidy `‹level icon› HH:MM:SS  message` line — info / warning / error stand out at a glance — with the relevant context (the error detail, which instance / host) shown in parentheses.
- **"Show extras" is now per-instance for normal apps (and clearly labelled for aggregate apps).** For apps that show **one combined card across all instances** — Pi-hole and AdGuard Home — toggling "Show extras" on any instance still applies to them all (their card is a single aggregated block), and the Admin → Apps instance editor now says so. For every other app (qBittorrent, the *arr family, …), which renders **one card per instance**, "Show extras" is now saved **per-instance** — toggling it on one instance no longer changes the others — and the editor notes that the setting applies to that instance only.
- **Radarr / Sonarr / Lidarr / Readarr cards hide Storage at the 3×1 and 4×1 sizes.** On the custom dashboard, a wide single-row card (3-wide or 4-wide) didn't have room for the per-mount Storage list (it got cut). Storage is now hidden for just those four apps at just those two widest short sizes; every other size and app is unchanged.
- **Per-app instance "Test connection" shows when it was last tested.** After a successful Test on an app instance (Radarr, Sonarr, Lidarr, Seerr, Bazarr, AdGuard, AdGuard Home Sync, Pi-hole, Speedtest), a "✓ Last tested Xm ago" chip appears next to the button and survives a reload — matching the Portainer / OIDC panels.
- **Per-app "Test connection" now works before you save the instance URL.** The Test button forwards the URL currently typed in the editor, so a brand-new or just-edited app instance can be tested immediately instead of reporting "no upstream URL configured" until you save first.
- **Every app editor's data-cache TTL field shows the same min / max / default chips.** The AdGuard Home, Pi-hole, and Speedtest Tracker editors were missing the little bounds chips (5 / 3600 / app default) that Radarr / Sonarr / Bazarr / Seerr already showed — they now match, so every app's cache-TTL field reads consistently.
- **App cards show a small spinner while their extra stats load.** The "Loading … data" placeholder on every app card with expanded stats (AdGuard Home, Pi-hole, Radarr, Sonarr, Seerr, Bazarr, APC UPS, Speedtest Tracker) now has a subtle spinner to the left of the text, so a card mid-fetch reads as busy rather than stalled. Seerr and Bazarr cards previously showed nothing at all until their first fetch landed — they now show the same spinner-and-text placeholder as the others.
- **Telegram `/cleanup` now names the containers it's removing.** When you run cleanup (after confirm, or directly when destructive actions are allowed), the reply lists every stopped/orphan container grouped by stack — so you can see exactly *what* was removed, not just a count.
- **Public-IP changes are detected and shown much faster.** OmniGrid used to lag well behind a dedicated DDNS updater on a WAN IP change — the background change-sampler only ran every 5 minutes, and the topbar widget had its own 10-minute refresh cache on top, so a new IP could take many minutes to appear. The sampler now defaults to every **2 minutes** (dial it to 30–60s under Admin → Config for near-instant detection), and the moment a change is detected it's **pushed live to the topbar widget** (a new `public_ip:changed` event) instead of waiting out the widget's refresh cache — so the displayed IP updates almost as soon as it changes.
- **AdGuard Home and Pi-hole app cards now show the running app version.** A small version footnote (e.g. "AdGuard Home v0.107.x", "Pi-hole v6.x") appears under the stats on each app card, matching what Seerr and Bazarr already show. (Speedtest Tracker isn't included — its API doesn't expose an app version.)
- **The topbar "Weather location" is now "Your location" — one shared setting.** The coordinates you set in Settings → Profile are used by the weather widget, Prayer Times, AND prayer reminders, so the field is renamed to "Your location" and moved out from under the weather-widget toggle — it stays available even when the weather widget is off (so prayer features work without it). Your existing saved location carries over automatically; nothing to re-enter.
- **Prayer Times widget fills the empty space in wide, short tiles.** In the 3×1 and 4×1 (wide + short) sizes the prayer card now lays the next-prayer countdown + Hijri date on the left and the full five-prayer list on the right — mirroring the Weather widget's hero/forecast split — instead of leaving the right side blank. Narrow and tall prayer tiles, and every other widget, are unchanged. **Follow-up:** on those wide+short tiles, which only fit three rows, the list now shows a rolling three-prayer window with the next upcoming prayer always in the **middle** (previous / upcoming / next). It re-centres live on its own as each prayer time passes — no page refresh needed — and wraps cleanly across the end of the day (after Isha it rolls to tomorrow's Fajr).
- **Seerr "suggest a movie" digs deeper before giving up.** If you exclude a lot of countries (or have a big library), the suggester used to run out of candidates quickly and reply "I couldn't find a fresh movie." It now draws from a roughly 2× larger and much deeper slice of the catalogue by default, and two new per-instance dials in the Seerr app editor (Admin → Apps → edit the Seerr instance) let you push it further: **page attempts** (how many random discover pages to try) and **catalogue depth** (how deep into the popularity-ranked catalogue to reach).
- **Telegram `/help` no longer lists every app-skill command.** Apps with many skills (AdGuard Home and Pi-hole each have a dozen disable-timer commands) used to dump the whole list into `/help`. Now each app shows one tappable entry — tap it (e.g. `/adguardhome`, `/seerr`) and the bot replies with that app's commands, keeping the menu compact.
- **AdGuard Home / Pi-hole: the Apps-page card now shows stats only — actions moved to the app drawer.** The combined-fleet card on the Apps grid no longer carries the Enable / Disable / Disable-for / Refresh buttons; it shows the aggregated stats at a glance, and the fleet actions (plus per-host detail) live in the app's drawer. Click the card to open the drawer and act.
- **App drawer skill results can be dismissed.** Boxes opened by a skill button (e.g. "latest speed test", Seerr suggestion filters / status) now have a small ✕ in the corner that clears the box and its data until you run the skill again.
- **App skill buttons share one consistent style across every app.** The per-app skill buttons (Speedtest, Bazarr, Seerr, …) now use the same subtle chip style as the AdGuard Home / Pi-hole action buttons, and the Seerr "Request on Seerr" follow-up button is a matching primary-accent chip — so the app drawers no longer mix solid-fill and chip buttons.
- **Admin → Schedules "Create schedule" Kind dropdown is sorted alphabetically** by its displayed name, instead of definition order, so the kind you want is easier to find.
- **Custom Apps dashboard edit mode is more consistent and readable.** The remove (trash) button on bookmark and widget tiles is now a rounded pill matching the settings/refresh pills on app cards, instead of a small square icon button. In the Unsectioned staging area, app tiles are now double width so the app name reads in full instead of truncating (height unchanged).
- **App-skill runs (e.g. Speedtest "Run speed test") are now fully traced in Admin → Logs.** Every step — the request (web or Telegram, with actor), each gate decision (no skill / unknown skill / api_key not set / unresolved instance), the dispatch, and the upstream result including each GET/POST attempt's status — emits an `[app_skill]` / `[speedtest]` line at the right severity (INFO / warning / error). A skill that doesn't fire is no longer silent: the log shows exactly where it stopped (e.g. the on-demand trigger endpoint returning 404 on a Speedtest Tracker build that doesn't expose it). The api key is never logged.
- **Retention prunes no longer hold the database writer lock for seconds.** The hourly sample-table cleanups (service-probe samples + the host-metrics / SNMP / SNMP-interface / SNMP-temperature / incident tables) now delete in bounded chunks, committing between each, instead of one big `DELETE` per table inside a single transaction. Under SQLite's single-writer model a multi-second delete used to queue every other writer behind it — which is why a *tiny* prune (e.g. the once-a-day database-size sample) could show up as a 2-second slow-query warning: it was 2 seconds of waiting for the lock, not 2 seconds of work. Chunking caps how long the lock is held so writers interleave and the warning cascade clears.
- **Another "latest per port" fleet query optimized.** The bulk per-(host, chip, port) latest-sample read used a self-join against a `MAX(ts)` subquery (co-routine + per-row join + a temp sort); rewritten as a single `GROUP BY … MAX(ts)` index scan — same results, no sort. Companion to the earlier per-port fix.
- **Service-probe "latest per port" fleet query no longer triggers multi-second slow-query warnings.** The per-port and per-chip "latest sample for the whole fleet" reads (on the Hosts/Apps list path) were `ROW_NUMBER()` window queries — SQLite always sorts those, even with a matching index, so on a large `service_samples` table they took several seconds. Rewritten as `GROUP BY … MAX(ts)`, which uses the existing covering index as a single ordered index scan (no sort); same results, sub-millisecond on the same data.
- Millisecond values in log lines (e.g. the `[slow_query]` warning, AI retry backoff) now print with thousands separators — `4,674.6ms` instead of `4674.6ms` — so large timings are readable at a glance.
- The Hosts view is snappier on large fleets: the host filter/sort/group computation is now memoized per render pass, so it runs once instead of several times per update (it was recomputed by both the desktop and mobile layouts plus the count badges every time a single host's stats refreshed). No change to filtering, sorting, grouping, or the live in-place row updates.
- **Apps view performance.** Apps grid memoizes derived per-card helpers + the cards now cap visible per-host instances to 3 with a "+ N more" expand affordance (state persists in `ui_prefs`). Aggregate `/api/apps` + `/api/apps/instances` queries now run via `asyncio.to_thread` so a long-running SQL pass can't stall every concurrent request. Net result: opening the Apps view + scrolling a long pinned-instances list is noticeably snappier on a multi-host deploy.
- **Backend hot-path latency.** Hourly sampler `_prune_old_*` calls now route through `asyncio.to_thread` (10 sample-table prunes — `host_metrics`, `host_net`, `host_snmp`, `host_snmp_iface`, `host_snmp_temp`, `host_pulse`, `host_webmin`, `host_beszel`, `host_http`, `service`, `ping` — plus the original `stats_samples`); plain `(ts)` indexes were added to every sample table so the prune SELECT can seek instead of scan; the `_gather_impl` Portainer fan-out parallelises 5 reads via `asyncio.gather` instead of running serial; `compute_baselines` runs in a worker thread. The cumulative wins close the `/api/healthz` 502-flap window on busy gather + sampler ticks.
- **Sampler query rewrite — `ROW_NUMBER()` over correlated subqueries.** Per-host "latest sample per provider" queries previously used a correlated subquery (`WHERE ts = (SELECT MAX(ts) WHERE host_id=...)`) that scaled O(N²) on the sample-table count; rewritten as `ROW_NUMBER() OVER (PARTITION BY host_id ORDER BY ts DESC)` window-function pass which is O(N log N) and avoids the per-row inner SELECT. Wins compound on the per-host fan-out path (`/api/hosts/one/{id}` reads the latest sample per provider).
- **Settings read-through cache + parsed-JSON memo.** `get_setting` / `get_setting_bool` now serve from a process-local 3s read-through cache so the hundreds-per-tick settings reads collapse to one SELECT per row per 3 s. Large JSON-valued settings (e.g. `hosts_config`) additionally memoize the `json.loads()` parse keyed on the raw-string identity so curated-host helpers (`iter_curated_hosts` / `curated_ne_hosts` / `curated_snmp_hosts` / etc.) skip the parse when the source string is unchanged.
- **Backend-unreachable threshold widened to 75 s with hysteresis.** The SPA's "Backend unreachable" banner only fires after 75 s of consecutive failures (was 30 s) AND resets the timer on tab visibility change so a laptop waking from sleep doesn't false-positive. Cuts the visible-error noise from transient 10-15 s network blips.
- **Healthcheck timeouts + log file-handle reuse.** Docker `HEALTHCHECK` timeout raised + the persistent-log writer now reuses one file handle per day (was: open-write-close per line) so high-frequency logging doesn't stall the event loop long enough to trip the healthcheck. Fewer false-positive SIGKILL container restarts under load.
- **tracemalloc default OFF in production.** `tracemalloc.start(N)` adds ~2-3× overhead per Python allocation; default-ON wedged the event loop past the 20 s `/api/healthz` Docker healthcheck on busy gather + sampler cycles → SIGKILL crash-loop. New `OG_TRACEMALLOC_FRAMES` env var opts in for one diagnostic session (warning log line fires on startup so it's not forgotten). The asyncio exception handler stays always-on (zero overhead in the no-exception path).
- **Per-host failure-streak log dedup.** Samplers that probe N hosts per tick and M of them are persistently failing now emit verbose ERROR on the FIRST failure per signature, then condensed one-line WARN lines with `streak=N, first_failure {age}s ago: <short>` on subsequent identical failures. Recovery resets the streak. Stops the wall-of-red log flood that made the system look unstable when it was just N dead hosts being silently quiet.
- The Apps discovery wizard now proposes every catalog app applicable to an open port — including single-port apps on shared ports like 8080 (Dozzle / qBittorrent) or 3000 (Forgejo / Grafana / AdGuard Home) that were previously suppressed — so you can pick the right one. A port already mapped to an app on the host is still never offered again (no over-mapping); best/exact matches rank first.
- The Apps discovery wizard's host picker is now a searchable, type-to-filter input instead of a long scrolling dropdown — find a host by typing part of its name, ID, or address, with arrow-key and Enter selection.
- Apps latency now displays with a thousands separator and a space before the unit (e.g. "1,234 ms"), and the App detail drawer's Probe-now / Show-debug buttons now have icons matching the host drawer.
- Removed Vaultwarden from the built-in app-catalog templates; added the missing Nextcloud brand icon.
- The Apps discovery wizard no longer proposes a second app for a port another app on the same host already claims — with AdGuard Home pinned on 80/443, Pi-hole and Nextcloud are no longer suggested purely on those shared ports, while an app on a still-free port is unaffected.

### Fixed

- **Destructive app skills asked for through the AI now require confirmation.** When the AI assistant or Telegram bot was asked to run a destructive per-app skill (e.g. remove a movie/series/artist/author from an *arr, or disable AdGuard/Pi-hole blocking), it could fire immediately with no confirmation. The web assistant now shows an inline Yes/Cancel chip (or runs straight away only in autonomous mode); the Telegram bot runs a destructive skill from free text only when "Allow destructive commands" is enabled, otherwise it points you at the explicit `/command` (which asks you to confirm). The server now also refuses a destructive skill that arrives without confirmation.
- **App skill output now shows in the AI chat.** Running a per-app skill from the assistant (e.g. "radarr status") showed only a green "Ran" chip with no result; the skill's actual output (status summary, etc.) now renders inline in the reply.
- **Service drawer no longer shows phantom "pending" placements.** A multi-replica service could list extra "? — pending" rows in its Placement section (e.g. cloudflared showed its 3 real nodes plus 3 empty pending rows). Those were Swarm tasks not yet assigned to any node; the Placement list now shows only tasks actually placed on a node (genuinely failed tasks still appear so real errors stay visible).
- **AdGuard Home Sync shows its own actions, with no empty box.** A sync app could show no action buttons and an empty box below its stats — because the app-detection matched the *plain AdGuard Home* fleet card on any name containing "adguard" (including "AdGuard Home Sync"), which hid the sync app's own actions and drew an empty aggregated-stats box. App detection now prefers the exact catalog template, so each app shows only its own card and actions.
- **App actions show for apps with optional auth (AdGuard Home Sync).** The app drawer hid the action buttons for any app instance without a saved API key — but AdGuard Home Sync's API is often open (auth optional), so its Sync now / status / logs / clear-logs actions never appeared. Actions now show when an app's auth is optional, even without a key (the server still enforces auth on the actual call).
- **No more empty box in the app drawer.** When an app's extras were toggled off, the drawer still drew an empty bordered box where the rich card would go. The box now appears only when there's content to show.
- **AdGuard Home Sync now shows its version.** The sync tool doesn't expose its version through its API, so OmniGrid reads it (best-effort) from the tool's own web page — the version now appears on the card and in the AI/Telegram "status" reply, like the other apps. (If it can't be read, the line is simply omitted.)
- **AdGuard Home Sync no longer borrows the plain AdGuard Home card/actions.** App detection for AdGuard Home matched any name containing "adguard" — including "AdGuard Home Sync" — which could overlay the AdGuard fleet card ("No AdGuard host reachable") and its enable/disable/refresh actions onto a sync app. The two are now matched distinctly (AdGuard excludes names with "sync"; the sync app requires both "adguard" and "sync"), so each shows only its own card and actions. (If a sync instance was linked to the wrong catalog template, re-pick "AdGuard Home Sync" in Admin → Apps so its icon, card, and skills resolve correctly.)
- **The AI no longer under-counts cleanup candidates.** Asking the assistant about cleanup could report fewer stopped/orphan containers than the `/cleanup` command actually removes (e.g. "1 orphan" when there were 3), because it was inferring from a capped sample of items. The AI now gets the authoritative removable set (the exact list `/cleanup` uses) plus a `removable_total` count, so its answer matches `/cleanup`.
- **Telegram host status (the `/hosts` command and the AI) now matches the dashboard.** They could report hosts as "Down" (e.g. routers, switches, printers) that the web dashboard showed as up. Two causes, both fixed: the `/hosts` command classified "down" as *any host with a failure marker* — even a single failed provider on an otherwise-reachable host — and the AI read the fast snapshot status, which lags the dashboard's live per-host re-probe. Both now resolve host status through one shared path that takes the snapshot, reconciles a stale down/unknown to up when a provider's latest probe succeeded, and **re-probes any still-flagged host using the same per-host check the dashboard runs** (bounded + time-budgeted so it never stalls a reply). `/hosts` now groups by real status (Active / Down / Unknown / Paused / Disabled) so its counts line up with the Hosts page.
- **Telegram `/prayer`, `/hijri`, and `/moon` now honour your date/time format.** These commands rendered dates and clock times in a fixed format regardless of your **Settings → Profile → Formats** preference — prayer times showed in 24-hour even if you'd chosen 12-hour, the `/hijri` Gregorian line showed an ISO `2026-06-06` date, and `/moon` showed ISO outlook dates. They now follow your chosen format: prayer + moonrise/moonset times render in your 12h/24h convention (without a meaningless `:00` seconds), and the Gregorian + moon-outlook dates use your date format. The Hijri (Islamic-calendar) date is deliberately left as-is, since a Hijri month number must never be rendered with a Gregorian month name.
- **Hosts "Problem" filter no longer shows a count that disagrees with the table.** With the Problem filter active, when a host changed into a problem state (down / paused / unknown) the toolbar count and the "Problem" pill updated immediately, but the table kept showing the old set of rows until you toggled the filter off and on again. The table now re-renders the moment a host's status flips, so the rows and the count always agree.
- **Fajr prayer reminder no longer goes missing.** For locations east of UTC, the Fajr reminder window opens shortly after local midnight — a time when the prayer-times source's own UTC clock can still be on the previous calendar day. The lookup was being made without a date, so during that window it returned the previous day's times and the Fajr reminder was silently skipped while the daytime prayers (which are well clear of the boundary) kept firing. The lookup now pins the request to the current moment so the prayer-times provider always resolves the correct local date, and the in-memory cache is refreshed across local midnight so it can't serve a stale day either.
- **APC UPS app card now shows a "Loading…" placeholder while its data is fetched.** The APC tile on the Apps page rendered nothing until its first SNMP sample landed, so it looked blank or static for a moment. It now shows "Loading UPS data…" immediately (matching the Speedtest card), then fills in the battery / output-load / runtime / temperature / state grid — with a clear error or "no UPS data reported" message if the host can't be read or isn't actually a UPS.
- **Probe-failure alerts now honour your per-channel notification routing.** HTTP-probe and service-probe failure alerts fire from a background monitor with no acting user, so the per-channel choices you set in Profile → Notifications were ignored and the alert went to every enabled channel — including Telegram even when you'd routed the event to in-app only. These fleet alerts now follow the admins' Profile routing: a channel is used only when at least one admin still has it enabled for that event, and is suppressed when every admin who made an explicit choice routed the event away from it. In a single-admin setup this means your Profile settings govern exactly; deployments where no admin set a per-channel choice keep firing to every enabled channel as before.
- **Notification events can be enabled without Apprise.** The per-event enable toggles in Admin → Notifications (and the Enable-all / Disable-all / Errors-only buttons) were greyed out unless Apprise was switched on — so an operator using only in-app or Telegram couldn't enable any event (including the new prayer reminders), which then showed as disabled on the Profile → Notifications page. Events deliver to whichever channels are enabled, so these toggles now work regardless of Apprise.
- **Long resource names on the Stacks / Services tables no longer run under the status pills.** A bare image digest (or any long name) now truncates with an ellipsis — hover to see the full name — instead of overflowing into the Health / Status columns.
- **Public-IP widget no longer shows "no data" right after you enable it.** Enabling Public IP in Admin and saving left the apps-page widget empty until a full page reload, because the save cleared the cached value without re-fetching. It now force-probes immediately after the save so the widget populates without a reload.
- **App card description sat too far below the title (worst in the 4×1 size).** The grey meta/description line now sits tight under the app title with a small gap instead of being pushed down by the app logo's vertical slack.
- **Web AI sidebar actions that carry structured data now actually run.** Asking the AI to "run a speed test" / "show the latest speed test" (or to create/update a schedule, send a notification) failed silently in the web sidebar with "ACTION_DATA needs host_id + service_idx + skill_id" and produced no server log — the structured `ACTION_DATA` payload was dropped during response parsing (the action trailer was stripped before the payload was read). It's now parsed from the full reply, so these actions dispatch correctly and appear in the logs. (Telegram was unaffected.)
- **Release notes ("What's new") render cleanly for git-cliff / conventional-commit changelogs.** Projects that publish that style (every entry suffixed with a ` - (commit hash)` and using `*(scope)*` markers) no longer show the trailing commit-hash noise on every line, and `*(scope)*` / `*italic*` now render as emphasis instead of literal asterisks. GitHub-native release notes are unaffected, and parenthesised PR references are preserved.
- Asking the AI to "show me the latest speed test" now queries the Speedtest Tracker app **live** instead of refusing with "no cached results" when nothing has been fetched yet (e.g. a fresh restart or a Telegram-first request). A new read-only **Show latest speed test** skill pulls the current result straight from the app; the cache is only used as an immediate hint. The on-demand **Run speed test** trigger now tries `POST` first (the method current Speedtest Tracker builds require — a `GET` returns 405), so a working trigger no longer logs a misleading warning. **Follow-up:** the Telegram bot now actually runs these skills when asked — previously it replied "I cannot trigger the interactive skill on Telegram, use the SPA" even though the bot can run them server-side. It now runs the skill and posts the real result (and any result image) back into the chat.
- Running an app skill from the App detail drawer (e.g. **Show latest speed test**) now shows its result inline under the button — including the result image when the app provides one — instead of only flashing it in a toast you couldn't fully read.
- The AI assistant (sidebar, Cmd-K response, and AI history detail) now renders a standalone image URL in its reply as an inline image preview — e.g. the Speedtest result PNG the assistant appends — instead of showing it as a bare text link.
- Notification `{time}` placeholder now renders in your configured timezone (the scheduler timezone) instead of UTC, so a notification's time matches every other timestamp in the UI.
- Schedules that got stuck "in flight" — a fire was recorded but never completed because the op hung or the container was restarted mid-run — now self-heal and re-fire automatically on the next tick instead of staying skipped until the next restart. A new tunable (default 1 hour) sets how long a run may stall before it is treated as wedged.
- The Service-probe provider chip now appears on hosts that have app probes enabled (it was silently never rendered on the Hosts page), and both the HTTP-probe and Service-probe providers now show their "Updated X ago" and sample-count stats reliably — previously the HTTP-probe periodic sampler could stay dormant, and a failed probe could wrongly clear the last-success timestamp, freezing the chip on a stale time.
- Saving, editing, deleting, and re-seeding Apps service-catalog templates no longer fail with an internal error. The discovery bulk-apply and provider-resume cross-tab live refreshes now propagate over the event stream as intended.
- Apps pinned from a catalog template (no explicit URL) are now actually probed against the host's configured Address instead of being silently skipped — this was why catalog-pinned apps showed "degraded" while only URL-based ones reported. The manual "Probe now" action works on them too.
- The Apps view now loads automatically on page load / refresh instead of requiring a manual Reload click.
- HTTP service probes now fall back to a GET request when a service rejects HEAD (many, including NetData's `/api/v1/info`, reply 400/403/501 to HEAD), so those apps no longer show a false "unexpected status" failure.
- Renaming or re-iconing a catalog template now propagates to pinned app instances that haven't set their own override — instances no longer snapshot the template's name/icon at pin time. The instance URL is also visible at narrower window widths.
- Editing an app instance (URL/link, ports, icon) now reflects immediately in the Apps view and detail drawer, and removing a port no longer leaves a stale per-port indicator.
- The per-app "Probe now" refresh in the host drawer's Apps card now works for apps that have ports configured even when continuous probing is turned off — an explicit click probes the configured ports regardless of the continuous-sampler toggle, and the refresh button now appears whenever there are port pills to refresh.
- In the host drawer's Port Scan section, a configured-but-not-yet-probed port now shows a grey "unknown" dot (matching the Apps card) instead of a red "down" dot — the two surfaces no longer disagree on a pending port's status.
- The Apps detail drawer's debug panel now shows whether continuous probing is actually running for a chip — surfacing the global Service-probe provider toggle and a plain-language list of why an app's port pills are grey (provider globally off and/or the chip's probe disabled). The per-app "Probe now" button still works regardless.
- The host drawer's asset port section now also lists open ports found by the port scan that aren't in the asset record (under "Open in scan — not in asset"), so you can see which scanned ports to add to the asset — the mirror of the existing marker that flags asset ports missing from the scan.
- Each Hosts-view row now shows an apps-count badge ("N apps") next to its app chips, coloured green when all the host's apps are up and amber when one or more are down — an at-a-glance app-health signal that's separate from the host's own reachability dot.
- Admin → Apps → Templates can now Export the whole app catalog as a portable JSON pack and Import one back (upserting templates by slug), for backing up or sharing catalog packs between installs.
- The App detail drawer now has a Logs action for app chips linked to a Docker container — it opens a modal that tails the container's logs (selectable line count) straight from Portainer, including containers on worker nodes.
- The App detail drawer's debug panel gained a Copy button that copies the full diagnostic JSON to the clipboard.
- Fixed the built-in Authentik app template's health check: it now accepts Authentik's 200 response (current versions) as well as 204 (older), instead of false-reporting the app as down on a 200.
- The host drawer's Apps card now has a clearly-labelled "Probe all (N)" button that probes every app on the host in one click (the per-app refresh icons remain for individual probes).
- **Cross-tab Cleanup refresh — switched to visibility listener.** A hidden tab running a background timer is throttled by every modern browser, so the post-Cleanup cross-tab refresh sometimes landed minutes after the bulk delete actually completed. Replaced the timer-based debounce with a `visibilitychange` listener so the tab refreshes the moment it comes back to the foreground.
- **OIDC error handling + SPA error toasts.** `/api/oidc/test` + the SPA's OIDC admin tab surface concrete error messages instead of raw HTML walls (handled via the canonical `fmtResponseError(r)` helper for both Pydantic-array validation errors AND nginx / openresty 502 / 504 interstitials).
- **DNS-failure skip cache + shared unreachable probe timeouts.** All host-stats samplers consult a process-local cache that skips a DNS-failing host for `tuning_dns_failed_skip_seconds` (default 300 s) instead of retrying on every tick → cuts the wall-clock cost of "one un-resolvable hostname blocks the whole tick" by orders of magnitude. Per-provider `*_PROBE_TIMEOUT_UNREACHABLE_SECONDS` knobs let the FIRST probe to an unreachable host time out fast (3 s) instead of waiting the full probe window. Concrete `host:port` targeting in timeout-diagnostic log lines so the diagnostic command is copy-paste-ready.
- **Telegram "Test connection" no longer messages everyone in your chat list.** When the destination is configured with more than one chat (e.g. a group plus individual operator DMs), the Test-connection button now pings only the primary (first) chat instead of broadcasting the test message to every recipient — including people who'd merely messaged the bot. Real notifications still go to every configured chat; only the manual test is scoped, and the result note tells you how many other chats were intentionally skipped.
- **Telegram AI replies make `/commands` tappable.** Per-app skill commands and commands like `/whoami` that the assistant mentions are now clickable bot commands instead of copy-only monospace text — tap to run.
- **Telegram AI now excludes every country you name.** "Don't suggest movies from Spain and Denmark" (or "Spain, Denmark & France") now excludes all of them, not just the first — the same fix applies to allowing countries back. Multi-word countries like "Trinidad and Tobago" are handled correctly.
- **AI movie suggestions stop repeating the same films.** Each movie the assistant suggests (Telegram or the command palette) is now remembered per user and skipped for a cooldown window, so asking again gives you something new instead of cycling through the same titles. The window is operator-tunable (default 12 hours, set to 0 to disable) in Admin → Notifications → Telegram.
- **"Suggest a movie" now renders correctly in the App detail drawer.** Running the Seerr suggestion from a host's App card no longer pushes the movie description off-screen, and the poster image and the one-click **Request** button now appear properly — previously the long description text overflowed the narrow drawer, which also hid the poster and the Request button. **Follow-up:** the **Request** button is no longer stuck disabled — it now enables correctly after a suggestion and shows a "Working…" → result transition when tapped (the drawer button's state updates reactively now, matching the AI sidebar).
- **Prayer reminder notifications show the time in your preferred format.** The "in X minutes" prayer reminder used to print the prayer time in 24-hour form regardless of your settings; it now respects your date/time format (Settings → Profile → Formats), including 12-hour AM/PM, without changing the actual time.
- **Telegram skill commands now show a clear "🤖 Thinking…" message on slow commands.** Long-running Telegram skill commands (e.g. `/seerr_suggest_movie`, which queries your library and TMDB across several pages) used to show only the easy-to-miss typing indicator — and Telegram clears that after ~5 seconds, so the chat looked idle. The bot now posts a visible "🤖 Thinking…" bubble immediately (and keeps the typing indicator alive), then replaces it in place with the result when ready.

### Security

- **Replaced fast unkeyed hashing of secrets with keyed HMAC for internal cache keys.** Two internal discriminators that told cached entries apart by a secret — the Pi-hole session cache (keyed by the Pi-hole password) and the Telegram command-registration de-duplicator (keyed by the bot token) — hashed the secret with a bare fast hash. They now use a keyed HMAC with a per-process random secret, which is non-reversible and not offline-brute-forceable. These were never credential storage (just in-memory cache keys), so there is no migration and no behaviour change.
- **Release-notes HTML stripper hardened against reconstructed tags.** The stack-update "What's new" popup strips layout HTML from upstream release notes before rendering; the strip now loops until stable so a reconstructed tag can't survive a single pass. Defence-in-depth only — the renderer already fully escapes everything before re-introducing a small formatting whitelist, so no raw tag was ever executed.

### Removed

- **`host:row_updated` SSE event was already retired in 1.2.x — the SPA-side stub listener has now been removed too.** The publisher was deleted because it caused an infinite loop (the read endpoint published the event, the SPA handler called the read endpoint, which published another event); per-host UI updates have been flowing exclusively through `host:failure_state_changed` (sampler-driven) + the 30 s polling fallback since 1.2.x. The leftover stub handler is gone too, avoiding dead-code confusion.

### Internal

- **`_TimedConnection` SQLite subclass for connection timing.** Wraps `sqlite3.Connection.execute` / `executemany` with a `perf_counter` pair so the slow-query log line can stamp the actual wall-clock cost without per-call wrapping at every consumer site.
- **`UNKNOWN_ACTOR` constant for "unknown" actor fallback.** Centralised across admin routes (was scattered as literal `"unknown"` strings) so attribution in `history` rows stays consistent and a future rename (e.g. to `"system"` for scheduler-driven rows) is a one-line edit.
- **`ALLOWED_PALETTE_ACTIONS` audit pass.** Removed 8 entries without matching SPA descriptors — those would have been advertised to the AI as available actions while being silently un-dispatchable. Audit ensures the action-roster surface accurately reflects what the SPA can actually execute.

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
