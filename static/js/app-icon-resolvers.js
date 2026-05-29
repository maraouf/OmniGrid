// noinspection NestedFunctionJS,FunctionContainsLoopsJS,FunctionWithMultipleLoopsJS,OverlyComplexFunctionJS,OverlyLongFunctionJS,OverlyLargeFunctionJS
// noinspection DuplicatedCodeFragmentJS,DuplicatedCode,ChainedFunctionCallJS,ChainedMethodCallJS,ConditionalExpressionJS,NestedConditionalExpressionJS
// noinspection RedundantConditionalExpressionJS,MagicNumberJS,JSMagicNumber,FunctionWithMultipleReturnPointsJS,IfStatementWithTooManyBranchesJS,JSForIIterationOverNonNumericKeyJS
// noinspection NestedTemplateLiteralJS
// noinspection ConstantOnRightSideOfComparisonJS,JSConstantOnRightSideOfComparison,JSVariableNamingConventionJS,LocalVariableNamingConventionJS,BadName,BadVariableName,RegExpAnonymousGroup
/* global Alpine, Swal, I18N, t, OG_VERSION, Terminal, FitAddon, WebLinksAddon, qrcode */
/* jshint esversion: 11, browser: true, devel: true, strict: implied, curly: false, bitwise: false, laxbreak: true, eqeqeq: false, forin: false, -W069 */
// SPA brand-icon resolvers.

import {KNOWN_DARK_ICONS, KNOWN_ICONS} from './app-icons.js?v=__APP_VERSION__';

// Module-scope memo caches — these helpers are pure functions of
// their string inputs (no reactive deps beyond `this.themePref`
// which is captured into the cache key), so a process-long cache
// is safe and dramatically cheaper than re-walking the ~100-token
// keyword table on every reactive flush. The Hosts view alone has
// `hostIconUrl(h)` in several spots per row × 200 hosts; pre-cache
// that fired thousands of times per second under load.
//
// `_ICON_PRE_THEME_CACHE` — cache the slug-resolution layer keyed
// by raw input name. Returns the bare URL BEFORE `_themeIcon`
// applies the theme swap; that lets `_themeIcon` do its own caching
// keyed on `url|theme` so a theme flip doesn't bloat this cache.
const _ICON_PRE_THEME_CACHE = new Map();
// `_ICON_THEMED_CACHE` — cache the themed-URL layer keyed by
// `url|theme`. Theme is binary (dark / not-dark) so the cardinality
// is at most 2 × |distinct urls|. Cleared by `_iconCacheClear()`
// on themePref change to keep memory bounded over hours of use.
const _ICON_THEMED_CACHE = new Map();

export default {
  // Diagnostic counter — exposed via `window.__ogPerf.iconResolveCount`
  // when the perf dev tool is wired (best-practice item from the perf
  // review). Increment on every cache MISS (the work that matters);
  // hits are cheap dict lookups.
  _iconCacheMissCount: 0,
  _iconCacheClear() {
    _ICON_PRE_THEME_CACHE.clear();
    _ICON_THEMED_CACHE.clear();
  },
  // Theme-aware icon swap. Wraps every icon-URL emit point so
  // brands that ship a `<slug>-dark.svg` variant (KNOWN_DARK_ICONS)
  // get the dark URL when the document is in dark theme. Reads
  // `this.themePref` reactively so cycling theme via the toolbar
  // re-evaluates every Alpine `:src` binding without a page reload.
  // Idempotent — already-`-dark` URLs short-circuit, external / non-
  // /img/icons/ URLs pass through untouched.
  _themeIcon(url) {
    if (!url) {
      return url;
    }
    // Read themePref so Alpine tracks this as a dependency. The
    // resolution mirrors `applyTheme()` exactly (auto → matchMedia,
    // explicit → that value).
    const pref = this.themePref;
    let dark;
    if (pref === 'auto') {
      const sysLight = window.matchMedia && window.matchMedia('(prefers-color-scheme: light)').matches;
      dark = !sysLight;
    } else {
      dark = pref !== 'light';
    }
    // Cache lookup — key on `url|dark` (binary theme). Hit fast
    // path returns immediately; miss path falls through to the
    // existing logic + caches the result. Pure function of (url,
    // dark) so the process-long cache is safe.
    const cacheKey = `${url}|${dark ? '1' : '0'}`;
    const cached = _ICON_THEMED_CACHE.get(cacheKey);
    if (cached !== undefined) {
      return cached;
    }
    let final = url;
    if (dark) {
      const m = /^\/img\/icons\/([a-z0-9_-]+)\.svg$/i.exec(url);
      if (m) {
        const slug = m[1].toLowerCase();
        // Already a -dark / -light explicit variant — operator picked
        // this file deliberately, leave it alone.
        if (!slug.endsWith('-dark') && !slug.endsWith('-light') && KNOWN_DARK_ICONS.has(slug)) {
          final = `/img/icons/${slug}-dark.svg`;
        }
      }
    }
    // Cache-bust local icon URLs with `?v=APP_VERSION` so a deploy
    // that ships a corrected SVG (e.g. the cloudflare.svg "alwa"
    // corruption recovery) is guaranteed to be re-fetched —
    // unlike the bare `/img/icons/<slug>.svg` URL which the browser
    // can keep serving from disk cache for hours via heuristic
    // freshness on a Last-Modified header, even when the file on the
    // server has been updated. The version marker (`window.OG_VERSION`)
    // is set inline in `static/index.html` and substituted server-side
    // at HTML serve time, so it bumps with every PATCH deploy. The
    // global is named OG_VERSION (not __APP_VERSION__) because the
    // server-side substitution replaces every occurrence of the
    // placeholder string — including the LHS identifier — which would
    // otherwise produce `window.1.3.66 = "1.3.66"` ("Unexpected
    // number" at the dot) when the version is numeric. External /
    // non-/img/icons/ URLs pass through unchanged.
    if (/^\/img\/icons\//.test(final)) {
      const v = (typeof window !== 'undefined' && window.OG_VERSION) || '';
      if (v && v !== '__APP_VERSION__' && !final.includes('?')) {
        final = `${final}?v=${encodeURIComponent(v)}`;
      }
    }
    // Stash in cache before returning so the next call with the
    // same (url, theme) short-circuits.
    _ICON_THEMED_CACHE.set(cacheKey, final);
    return final;
  },
  iconUrlFor(name) {
    // Fast path — full themed-URL cache keyed by `name|themePref`.
    // The keyword table is ~100 entries and walked from declaration
    // order on every miss; this short-circuits hot bindings (200+
    // hosts × multiple per-row consumers) to a dict lookup. Pure
    // function of (name, theme) so the process-long cache is safe;
    // the `_themeIcon` layer's own cache keyed on (url, theme)
    // catches the secondary path (`hostIconUrl` returning a URL
    // not produced by this function).
    if (name) {
      const themeKey = (this.themePref === 'auto')
        ? ((typeof window !== 'undefined' && window.matchMedia
          && window.matchMedia('(prefers-color-scheme: light)').matches) ? 'L' : 'D')
        : (this.themePref === 'light' ? 'L' : 'D');
      const cacheKey = `${name}|${themeKey}`;
      const cached = _ICON_PRE_THEME_CACHE.get(cacheKey);
      if (cached !== undefined) {
        return cached;
      }
      const fresh = this._iconUrlForUncached(name);
      _ICON_PRE_THEME_CACHE.set(cacheKey, fresh);
      this._iconCacheMissCount += 1;
      return fresh;
    }
    return this._iconUrlForUncached(name);
  },
  // Uncached body — the previous `iconUrlFor`. Caller is `iconUrlFor`
  // ABOVE which memoizes the return value. Split into a separate
  // method so the existing N return paths (each calling
  // `this._themeIcon(...)`) didn't need to be rewritten to also
  // populate the cache; the wrapper handles it once.
  _iconUrlForUncached(name) {
    // Resolve an app name to an icon URL. Every icon is local (in
    // static/img/icons/) so the dashboard works offline. Override values
    // can either be:
    // - a bare canonical slug (resolved to /img/icons/<slug>.svg), or
    // - a full URL or absolute path ending in .svg/.png/.webp (used verbatim).
    //
    // URLs MUST be absolute (leading "/") — the SPA runs under deep-link
    // routes like /nodes, /settings/oidc, /admin/users, and a relative
    // "img/icons/..." would resolve against those paths (→ 404). Any
    // override that looks like "img/..." is auto-prefixed with "/".
    //
    // Theme-aware swap: every return point routes through
    // `_themeIcon(url)` so brands with a `-dark.svg` variant
    // auto-resolve to the dark URL when in dark theme.
    if (!name) {
      return '';
    }
    // Exact / whole-name overrides (checked first).
    const overrides = {
      // 'seerr' is its own brand (https://github.com/Fallenbagel/seerr)
      // — distinct from 'jellyseerr' / 'overseerr'. Use the dedicated
      // seerr.svg from homarr-labs dashboard-icons. The keyword scan
      // below MUST list 'jellyseerr' before 'seerr' so a name like
      // 'jellyseerr-redis' matches the jellyseerr icon first
      // (substring 'seerr' is contained in both).
      'seerr': 'seerr',
      'docker-prune': 'docker',
      'standalone': 'docker',
      'nebula-sync': 'pi-hole',
      'adguardhome-sync': 'adguard-home',
      'adguard-exporter': 'adguard-home',
      'blackbox-exporter': 'prometheus',
      'fing-agent': '/img/icons/fing.svg',
      'fing': '/img/icons/fing.svg',
      'lubelogger': '/img/icons/lubelogger.svg',
      'myspeed': '/img/icons/myspeed.svg',
      'squid-proxy': '/img/icons/squid.svg',
      'squid': '/img/icons/squid.svg',
      'tracearr': '/img/icons/tracearr.svg',
      'portainer': '/img/icons/portainer.svg',
      'portainer-agent': '/img/icons/portainer.svg',
      // Somfy typos / product-line synonyms — keep these in sync
      // with the `hostIconUrl` alias map so item / stack contexts
      // (not just curated host rows) accept the same misspellings.
      'smofy': 'somfy',
      'somphy': 'somfy',
      'tahoma': 'somfy',
      'connexoon': 'somfy',
      // Cloudflared (the tunnel daemon) has its OWN file
      // (cloudflared.svg) — same orange Cloudflare cloud bytes as
      // cloudflare.svg, but a distinct URL so the operator's edge
      // cache (Cloudflare's own CDN, fitting given they ARE running
      // cloudflared) doesn't keep serving a stale broken response on
      // the cloudflare.svg URL. The `cloudflared` slug resolves
      // naturally via KNOWN_ICONS, no alias needed; keeping the
      // other Cloudflare-family aliases pointed at the parent
      // cloudflare.svg brand mark.
      'cloudflared-tunnel': 'cloudflared',
      'cloudflare-tunnel': 'cloudflare',
      'cloudflare-warp': 'cloudflare',
      'cloudflare-zero-trust': 'cloudflare',
      // Operator's custom GitSync Connector container (stack name
      // `gitsync-connector`, service name `gitsync-connector_connector`).
      // Both the stack-namespaced and bare-name forms map to the
      // gitsync brand mark. Deliberately NOT aliasing bare `connector`
      // (too generic — would collide with Kafka Connect, MQTT bridges,
      // etc.); operators wanting a different `*-connector` icon stay
      // unaffected.
      'gitsync-connector': 'gitsync',
      'gitsync-connector_connector': 'gitsync',
      'gitsync_connector': 'gitsync',
      // Linux Mint short forms — bare slug AND hyphenated alias both
      // resolve to the canonical linuxmint.svg. Mirrors the
      // hostIconUrl alias map per the project's "BOTH alias maps" rule
      // so item / stack contexts get the same forgiveness.
      'mint': 'linuxmint',
      'linux-mint': 'linuxmint',
    };
    // Prefix patterns — one entry covers all siblings of a product
    // (authentik outposts: ak-outpost-authentik-ldap-outpost, etc.).
    const prefixes = [
      ['ak-outpost-', 'authentik'],
      ['komodo-', 'komodo'],
    ];
    const raw = String(name).toLowerCase().trim();
    const natural = raw.replace(/[\s_]+/g, '-').replace(/[^a-z0-9-]/g, '');
    const mapped = overrides[raw] || overrides[natural];
    // If the override looks like a URL or path, return it (guaranteeing
    // a leading "/" so it stays absolute under deep-link routes).
    if (mapped && /[/.]/.test(mapped)) {
      const url = mapped.startsWith('/') || /^https?:/i.test(mapped) ? mapped : '/' + mapped;
      return this._themeIcon(url);
    }
    if (mapped) {
      return this._themeIcon(`/img/icons/${mapped}.svg`);
    }
    for (const [prefix, slug] of prefixes) {
      if (natural.startsWith(prefix)) {
        return this._themeIcon(`/img/icons/${slug}.svg`);
      }
    }
    if (!natural) {
      return '';
    }
    // Only return a URL when the slug actually exists on disk —
    // otherwise the browser fires a 404 for every stack/host name
    // that doesn't happen to match a brand. Operator complaint:
    // "this is a stack without an image, why system looking for
    // image" → fixed by gating on KNOWN_ICONS.
    if (KNOWN_ICONS.has(natural)) {
      return this._themeIcon(`/img/icons/${natural}.svg`);
    }
    return '';
  },
  stackIconUrl(stack) {
    return stack ? this.iconUrlFor(stack.name) : '';
  },
  itemIconUrl(item) {
    // Use the parent stack's name for items inside a stack; otherwise the
    // item's own name (for standalone containers / services without stack).
    if (!item) {
      return '';
    }
    return this.iconUrlFor(item.stack || item.name);
  },
  // Resolve a curated host to an icon URL. Priority:
  // 1. explicit ``h.icon`` override (admin-supplied).
  // 2. ``iconUrlFor()`` on the raw id / label / provider names
  //    — finds a hit when one of those matches an icon slug
  //    verbatim (e.g. id "opnsense" → opnsense.svg).
  // 3. KEYWORD scan of the label + id for known brand tokens
  //    (e.g. "(Apache)" → apache.svg, "(NGINX)" → nginx.svg).
  //    Lets labels like "[VM] Debian OS 13 (WebServer 01) (Apache)"
  //    auto-match without the operator setting an icon manually.
  hostIconUrl(h) {
    if (!h) {
      return '';
    }
    // Sentinel "no icon" values — operator sets one of these when the
    // auto keyword-scan picks the WRONG brand icon and they want to
    // render no icon at all rather than the wrong one (e.g. a host
    // whose label happens to contain "syno" matched the Synology
    // icon by accident). Returning empty hides the `<img>` via every
    // consumer's `x-show="hostIconUrl(h)"` gate. Anything else falls
    // through to the existing override → exact-slug → keyword-scan
    // resolver chain. Case-insensitive, whitespace-tolerant.
    const _icon = (h.icon || '').trim().toLowerCase();
    if (['none', '-', 'off', 'false', 'no', 'disabled', 'hidden'].includes(_icon)) {
      return '';
    }
    if (h.icon) {
      // Normalise: bare slug → absolute /img/icons/<slug>.svg.
      // Slug aliases cover the common "wrong name" cases where the
      // icon file is stored under a different slug than the brand's
      // common name (e.g. "adguard" → adguard-home.svg).
      if (/^https?:/i.test(h.icon) || h.icon.startsWith('/')) {
        return h.icon;
      }
      const aliases = {
        'adguard': 'adguard-home',
        'ad-guard': 'adguard-home',
        'npm': 'nginx-proxy-manager',
        'nginxproxymanager': 'nginx-proxy-manager',
        'homeassistant': 'home-assistant',
        'pihole': 'pi-hole',
        'k8s': 'kubernetes',
        'pve': 'proxmox',
        'pi-vpn': 'pivpn',
        'ts': 'tailscale',
        'ovpn': 'openvpn',
        'wg': 'wireguard',
        'wireguard-vpn': 'wireguard',
        'qbit': 'qbittorrent',
        'qb': 'qbittorrent',
        'freenas-mini': 'freenas',
        'nas': 'truenas',
        'win': 'windows',
        'win11': 'windows',
        'win10': 'windows-10',
        'win-server': 'windows-server',
        'windowsserver': 'windows-server',
        'mailserver': 'mail',
        'smtprelay': 'smtp',
        'smtp-relay': 'smtp',
        'postfix': 'mail',
        'mailu': 'mail',
        'maddy': 'mail',
        // Egyptian carriers — e& (formerly Etisalat) + WE (formerly
        // Telecom Egypt / TE Data) share canonical brand identities
        // post-rebrand / post-merger. All legacy slugs alias to the
        // single canonical icon so an operator's stored `icon: "e-and"`
        // or `icon: "telecom-egypt"` from before the consolidation
        // still resolves to the live SVG.
        'e-and': 'etisalat',
        'eand': 'etisalat',
        'e&': 'etisalat',
        'telecom-egypt': 'we',
        'te-data': 'we',
        'vodafone-egypt': 'vodafone',
        'vodafone-eg': 'vodafone',
        'vodafone-dsl': 'vodafone',
        'orange-egypt': 'orange',
        'orange-eg': 'orange',
        'asus-router': 'asus',
        'asus-vpn': 'asus',
        'asuswrt': 'asus',
        'rt-ax': 'asus',
        'rt-ac': 'asus',
        'western-digital': 'wd',
        'seeed': 'seeedstudio',
        'seeed-studio': 'seeedstudio',
        'western digital': 'wd',
        'wdc': 'wd',
        'mycloud': 'wd',
        'my-cloud': 'wd',
        'mybooklive': 'wd',
        'my-book-live': 'wd',
        'syno': 'synology',
        'dsm': 'synology',
        'mint': 'linuxmint',
        'linux-mint': 'linuxmint',
        'synology-dsm': 'synology',
        'meraki': 'cisco',
        'cisco-asa': 'cisco',
        'asa': 'cisco',
        'ios-xe': 'cisco',
        'iosxe': 'cisco',
        'catalyst': 'cisco',
        'nexus': 'cisco',
        // Ubiquiti family — `ubiquiti.svg` is the parent brand mark,
        // `ui.svg` is the short-form UI badge, `unifi.svg` (unchanged)
        // stays for the UniFi product line specifically. An operator
        // tagging a host `ubnt` / `edgerouter` / `airmax` / `airfiber`
        // / `unifi-os` lands on the parent Ubiquiti mark.
        'ubnt': 'ubiquiti',
        'ui.com': 'ui',
        'unifi-os': 'ubiquiti',
        'edgerouter': 'ubiquiti',
        'edge-router': 'ubiquiti',
        'edgeswitch': 'ubiquiti',
        'edge-switch': 'ubiquiti',
        'airmax': 'ubiquiti',
        'airfiber': 'ubiquiti',
        'uisp': 'ubiquiti',
        'amplifi': 'ubiquiti',
        // Reolink — IP cameras / NVRs. Aliases cover the product
        // lines operators commonly tag hosts with.
        'reolink-nvr': 'reolink',
        'reolink-cam': 'reolink',
        'reolink-camera': 'reolink',
        'rlc': 'reolink',
        'rln': 'reolink',
        // Xiaomi family — phones, routers (Mi Router), smart-home
        // hubs, vacuums. Aliases cover the product names operators
        // commonly tag hosts with.
        'mi': 'xiaomi',
        'mi-router': 'xiaomi',
        'mi-home': 'xiaomi',
        'redmi': 'xiaomi',
        'poco': 'xiaomi',
        'mihome': 'xiaomi',
        // Hisense — TVs, smart-home hubs, white goods.
        'hisense-tv': 'hisense',
        'vidaa': 'hisense',
        // Sensibo — smart-AC controllers (sky / air / pure / pod).
        'sensibo-sky': 'sensibo',
        'sensibo-air': 'sensibo',
        'sensibo-pod': 'sensibo',
        'sensibo-pure': 'sensibo',
        // HP family — short / common synonyms route to the canonical
        // hp.svg brand mark. ProLiant / iLO keep their existing
        // dedicated icons (proliant.svg / ilo.svg) since those are
        // distinct product-line marks rather than the parent HP logo.
        'hpe': 'hp',
        'hewlett-packard': 'hp',
        'hewlettpackard': 'hp',
        // Samsung — `samsung` is the consumer wordmark; the corporate
        // "Samsung Electronics" mark lives at `samsung-electronics`.
        'samsung_electronics': 'samsung-electronics',
        'samsungelectronics': 'samsung-electronics',
        // Common typo + product-line synonyms for Somfy (motorised
        // blinds / smart-home hubs). Operators have typed "smofy" /
        // "somphy" repeatedly — alias them all to the canonical
        // somfy.svg so the icon picker is forgiving.
        'smofy': 'somfy',
        'somphy': 'somfy',
        'tahoma': 'somfy',
        'connexoon': 'somfy',
        // Amazon Fire TV product line.
        'fire-tv': 'firetv',
        'fire_tv': 'firetv',
        'firestick': 'firetv',
        // Amazon Echo / Alexa — Echo product variants all use
        // alexa.svg (the canonical Alexa-blue swirl mark).
        'echo': 'alexa',
        'echo-dot': 'alexa',
        'echo-show': 'alexa',
        'echo-studio': 'alexa',
        'amazon-echo': 'alexa',
        // Generic monitoring labels — operator-typed slugs that
        // don't match a concrete brand fall through to uptime-kuma
        // (the canonical free-software uptime monitor mark) so the
        // resolver returns a real file instead of a 404.
        'website-monitoring': 'uptime-kuma',
        'website_monitoring': 'uptime-kuma',
        'uptime-monitor': 'uptime-kuma',
        'monitoring': 'uptime-kuma',
        // Cloudflared has its own file (cloudflared.svg) — see
        // iconUrlFor for the rationale. Other Cloudflare-family
        // products use the parent cloudflare.svg brand mark.
        'cloudflared-tunnel': 'cloudflared',
        'cloudflare-tunnel': 'cloudflare',
        'cloudflare-warp': 'cloudflare',
        'cloudflare-zero-trust': 'cloudflare',
        // GitSync Connector — operator's custom service.
        'gitsync-connector': 'gitsync',
        'gitsync-connector_connector': 'gitsync',
        'gitsync_connector': 'gitsync',
      };
      const slug = aliases[h.icon.toLowerCase()] || h.icon;
      return this._themeIcon('/img/icons/' + slug + '.svg');
    }
    // when the operator has cleared the display label
    // (which falls back to assetForHost(h).name per), the icon
    // resolver loses its primary "this is a Synology / Dell / ..."
    // signal because h.label is empty. Fold the asset's name +
    // type_short + vendor + model into the candidate pool AND the
    // keyword-scan hay so cleared-label hosts inherit the asset's
    // brand hint. Cheap lookup — assetForHost is a Map.get().
    const _asset = (typeof this.assetForHost === 'function')
      ? (this.assetForHost(h) || null)
      : null;
    const _assetName = _asset ? String(_asset.name || '').trim() : '';
    const _assetTypeS = _asset ? String(_asset.type_short || '').trim() : '';
    const _assetVendor = _asset ? String(_asset.vendor || '').trim() : '';
    const _assetModel = _asset ? String(_asset.model || '').trim() : '';
    // Step 2 — exact-slug match on any field.
    const candidates = [
      h.id, h.label, h.host, h.beszel_name, h.pulse_name,
      _assetName, _assetTypeS, _assetVendor, _assetModel,
    ].filter(Boolean);
    for (const c of candidates) {
      const url = this.iconUrlFor(c);
      if (url) {
        return url;
      }
    }
    // Step 3 — keyword scan. Lowercase hay from label + id, then
    // test each known token. Order matters: longer / more specific
    // tokens win first so "nginx-proxy-manager" beats "nginx".
    const hay = [
      h.label, h.id, h.host,
      _assetName, _assetVendor, _assetModel, _assetTypeS,
    ].filter(Boolean).join(' ').toLowerCase();
    // Longest / most specific phrases first so "nginx proxy
    // manager" wins over "nginx" and "home assistant" wins over
    // "home". Every target slug must correspond to a file that
    // actually exists in static/img/icons/ — otherwise the @error
    // handler on the <img> hides the broken image.
    const tokens = [
      // reverse-proxy family
      ['nginx proxy manager', 'nginx-proxy-manager'],
      ['nginxproxymanager', 'nginx-proxy-manager'],
      ['proxy manager', 'nginx-proxy-manager'],
      [' npm', 'nginx-proxy-manager'],
      ['(npm)', 'nginx-proxy-manager'],
      ['traefik', 'traefik'],
      ['caddy', 'caddy'],
      // webservers
      ['nginx', 'nginx'],
      ['apache', 'apache'],
      // server hardware / lights-out management — checked BEFORE
      // hypervisors so "Dell PowerEdge … (iDRAC)" hits idrac and
      // "VMware vCenter Server" hits vcenter rather than the
      // generic vmware fallback.
      ['idrac', 'idrac'],
      ['ilo', 'ilo'],
      ['poweredge', 'poweredge'],
      ['power edge', 'poweredge'],
      ['dell server', 'poweredge'],
      ['proliant', 'proliant'],
      ['dell', 'dell'],
      // virtualisation suite — most-specific labels first.
      ['vcenter', 'vcenter'],
      ['v-center', 'vcenter'],
      ['vsphere', 'vsphere'],
      ['esxi', 'esxi'],
      ['esx', 'esxi'],
      ['vmware', 'vmware'],
      // power / UPS
      ['apc ups', 'apc-ups'],
      ['apc-ups', 'apc-ups'],
      ['apc', 'apc'],
      [' ups', 'ups'],
      // firewalls / routers / gateways
      ['opnsense', 'opnsense'],
      ['pfsense', 'pfsense'],
      ['mikrotik', 'mikrotik'],
      // UniFi-product-line phrases win over the bare "ubiquiti"
      // match — more-specific wins when the operator labelled a
      // host with its UniFi flavour.
      ['unifi', 'unifi'],
      // Reolink — IP cameras / NVRs. Common host-name shapes:
      // "reolink-nvr-01", "cam-reolink-front", "rlc-810a". Placed
      // before the generic "camera" / "nvr" fallbacks below so
      // brand wins over category.
      ['reolink nvr', 'reolink'],
      ['reolink cam', 'reolink'],
      ['reolink camera', 'reolink'],
      ['reolink', 'reolink'],
      ['rlc-', 'reolink'],
      ['rln-', 'reolink'],
      // Xiaomi family — Mi Router / Redmi / POCO / Mi Home hubs.
      // Most-specific phrases first so "mi router" wins over
      // bare "mi" (which would also match "mint" etc.).
      ['mi router', 'xiaomi'],
      ['mi-router', 'xiaomi'],
      ['mi home', 'xiaomi'],
      ['mi-home', 'xiaomi'],
      ['mihome', 'xiaomi'],
      ['xiaomi', 'xiaomi'],
      ['redmi', 'xiaomi'],
      ['poco', 'xiaomi'],
      // Hisense — TVs (VIDAA OS) + appliances.
      ['hisense', 'hisense'],
      ['vidaa', 'hisense'],
      // Sensibo — AC controller pucks. Models: Sky, Air, Pod, Pure.
      ['sensibo', 'sensibo'],
      // Ubiquiti family — parent brand mark. Specific product
      // phrases first so "edgerouter" / "airmax" etc. hit even
      // when "ubiquiti" also appears in the label.
      ['edgerouter', 'ubiquiti'],
      ['edge-router', 'ubiquiti'],
      ['edgeswitch', 'ubiquiti'],
      ['edge-switch', 'ubiquiti'],
      ['airfiber', 'ubiquiti'],
      ['airmax', 'ubiquiti'],
      ['amplifi', 'ubiquiti'],
      ['uisp', 'ubiquiti'],
      ['unifi-os', 'ubiquiti'],
      ['ubnt', 'ubiquiti'],
      ['ubiquiti', 'ubiquiti'],
      // ASUS routers — typical model strings: "RT-AX88U",
      // "RT-AC68U", "GT-AX11000", "ZenWiFi". "asuswrt" / "merlin"
      // are the firmware names operators sometimes label hosts
      // with. Phrases ordered before the generic "router" fallback
      // above (firewalls/routers/gateways block) so the brand wins.
      ['asus router', 'asus'],
      ['asus vpn', 'asus'],
      ['asuswrt', 'asus'],
      ['merlin', 'asus'],
      ['zenwifi', 'asus'],
      ['rt-ax', 'asus'],
      ['rt-ac', 'asus'],
      ['gt-ax', 'asus'],
      ['asus', 'asus'],
      // Cisco — enterprise switching / routing / firewall / wireless.
      // Covers the big product families: Meraki (cloud-managed
      // dashboards), ASA (firewalls), Catalyst + Nexus (switches),
      // IOS-XE / IOS-XR (operating systems operators often tag
      // hosts with). Placed before the generic firewall / router
      // fallbacks below so brand wins over category.
      ['cisco meraki', 'cisco'],
      ['meraki', 'cisco'],
      ['cisco asa', 'cisco'],
      ['catalyst', 'cisco'],
      ['nexus', 'cisco'],
      ['ios-xe', 'cisco'],
      ['ios-xr', 'cisco'],
      ['iosxe', 'cisco'],
      ['cisco', 'cisco'],
      // NAS / storage — Synology DSM + Western Digital (DS / RS
      // models for Synology, MyCloud / MyBook / WD Red / WD Blue
      // for Western Digital). Longer phrases first so
      // "western digital" wins over "wd".
      ['synology', 'synology'],
      ['dsm ', 'synology'],
      ['ds ', 'synology'],
      ['rs ', 'synology'],
      ['syno', 'synology'],
      ['western digital', 'wd'],
      ['western-digital', 'wd'],
      ['mycloud', 'wd'],
      ['my cloud', 'wd'],
      ['mybooklive', 'wd'],
      ['my book', 'wd'],
      ['wdc', 'wd'],
      [' wd ', 'wd'],
      // ISP / access-technology routers — longer phrases first so
      // "ftth router" hits ftth (not the bare "router" fallback).
      ['ftth', 'ftth'],
      ['fiber', 'ftth'],
      ['fibre', 'ftth'],
      ['gpon', 'ftth'],
      ['vdsl', 'vdsl'],
      ['adsl', 'vdsl'],
      ['dsl modem', 'vdsl'],
      [' dsl', 'vdsl'],
      ['5g router', '5g'],
      ['5g modem', '5g'],
      ['5g cpe', '5g'],
      ['cellular', '5g'],
      [' lte', '5g'],
      [' 5g', '5g'],
      ['gateway', 'opnsense'],
      ['firewall', 'opnsense'],
      ['router', 'opnsense'],
      // media / entertainment
      ['plex', 'plex'],
      ['jellyfin', 'jellyfin'],
      ['jellyseerr', 'jellyseerr'],
      ['overseerr', 'jellyseerr'],
      // 'seerr' (Fallenbagel/seerr) is a distinct brand; the icon
      // resolver MUST list it AFTER 'jellyseerr' and 'overseerr' so
      // 'jellyseerr-app' / 'overseerr-bg' match the longer phrase
      // first (the substring 'seerr' is contained in all three names).
      ['seerr', 'seerr'],
      ['tautulli', 'tautulli'],
      ['bazarr', 'bazarr'],
      ['sonarr', 'sonarr'],
      ['radarr', 'radarr'],
      ['prowlarr', 'prowlarr'],
      // smart home
      ['home assistant', 'home-assistant'],
      ['homeassistant', 'home-assistant'],
      ['homebridge', 'homebridge'],
      // ad-blocking / DNS
      ['pi-hole', 'pi-hole'],
      ['pihole', 'pi-hole'],
      ['adguard home', 'adguard-home'],
      ['adguardhome', 'adguard-home'],
      ['adguard', 'adguard-home'],
      ['nebula', 'pi-hole'],
      // identity
      ['authentik', 'authentik'],
      ['keycloak', 'authentik'],
      // orchestration / container tooling
      ['portainer', 'portainer'],
      ['komodo', 'komodo'],
      ['dozzle', 'dozzle'],
      ['homarr', 'homarr'],
      ['homepage', 'homepage'],
      // operating systems — checked BEFORE brand names so
      // "windows server" beats bare "windows".
      ['windows server', 'windows-server'],
      ['windows-server', 'windows-server'],
      ['win server', 'windows-server'],
      ['winsrv', 'windows-server'],
      ['windows 11', 'windows'],
      ['windows 10', 'windows-10'],
      ['windows', 'windows'],
      ['win11', 'windows'],
      ['win10', 'windows-10'],
      ['win2019', 'windows-server'],
      ['win2022', 'windows-server'],
      ['win2025', 'windows-server'],
      // hypervisors / storage / platforms
      ['proxmox', 'proxmox'],
      ['pve', 'proxmox'],
      ['truenas scale', 'truenas-scale'],
      ['truenas-scale', 'truenas-scale'],
      ['truenas core', 'truenas-core'],
      ['truenas-core', 'truenas-core'],
      ['truenas', 'truenas'],
      ['freenas', 'freenas'],
      ['docker', 'docker'],
      ['kubernetes', 'kubernetes'],
      ['k8s', 'kubernetes'],
      // observability
      ['grafana', 'grafana'],
      ['prometheus', 'prometheus'],
      ['uptime kuma', 'uptime-kuma'],
      ['uptimekuma', 'uptime-kuma'],
      ['netdata', 'netdata'],
      ['beszel', 'beszel'],
      ['pulse', 'pulse'],
      // job runners / automation
      ['rundeck', 'rundeck'],
      ['n8n', 'n8n'],
      ['ansible', 'ansible'],
      // git forges
      ['forgejo', 'forgejo'],
      ['gitea', 'forgejo'],
      // databases — brand-specific first, generic last.
      ['mongodb', 'mongodb'],
      ['mongo', 'mongodb'],
      ['postgresql', 'postgresql'],
      ['postgres', 'postgresql'],
      ['influxdb', 'influxdb'],
      ['influx', 'influxdb'],
      ['mariadb', 'database'],
      ['mysql', 'database'],
      ['redis', 'database'],
      ['sqlite', 'database'],
      ['database', 'database'],
      [' db ', 'database'],
      // systems management / monitoring
      ['webmin', 'webmin'],
      ['zabbix', 'zabbix'],
      // remote access / desktop
      ['rustdesk', 'rustdesk'],
      // mail — brand-specific first, generic last.
      ['mailcow', 'mailcow'],
      ['stalwart', 'stalwart'],
      ['roundcube', 'roundcube'],
      ['dovecot', 'dovecot'],
      ['smtp relay', 'smtp'],
      ['smtp gateway', 'smtp'],
      ['smtp', 'smtp'],
      ['mail server', 'mail'],
      ['mailserver', 'mail'],
      ['mail relay', 'mail'],
      ['webmail', 'roundcube'],
      ['imap', 'mail'],
      [' mail', 'mail'],
      ['postfix', 'mail'],
      ['mailu', 'mail'],
      ['maddy', 'mail'],
      // VPN / tunnelling — checked BEFORE "openvpn" alone so
      // "pivpn" isn't shadowed by the openvpn token.
      ['pivpn', 'pivpn'],
      ['pi-vpn', 'pivpn'],
      ['tailscale', 'tailscale'],
      ['headscale', 'tailscale'],
      ['openvpn', 'openvpn'],
      ['wireguard', 'wireguard'],
      ['wg-easy', 'wireguard'],
      // Cloudflare family — `cloudflared` (the tunnel daemon) has
      // its own file (cloudflared.svg, same artwork as
      // cloudflare.svg but a distinct URL so the operator's edge
      // cache can't get stuck on a broken response — fitting
      // given they ARE running cloudflared tunnel). Other
      // Cloudflare-family products share the parent
      // cloudflare.svg brand mark. Long-form phrases first so
      // "cloudflare zero trust" wins over bare "cloudflare".
      ['cloudflare zero trust', 'cloudflare'],
      ['cloudflare-zero-trust', 'cloudflare'],
      ['cloudflare tunnel', 'cloudflare'],
      ['cloudflare-tunnel', 'cloudflare'],
      ['cloudflared', 'cloudflared'],
      ['cloudflare warp', 'cloudflare'],
      ['cloudflare-warp', 'cloudflare'],
      ['cloudflare', 'cloudflare'],
      // GitSync Connector — operator's custom container. Long-form
      // phrases first; bare `gitsync` is also a meaningful brand
      // match in case a future stack drops the `-connector` suffix.
      ['gitsync-connector', 'gitsync'],
      ['gitsync_connector', 'gitsync'],
      ['gitsync connector', 'gitsync'],
      ['gitsync', 'gitsync'],
      // download clients
      ['qbittorrent', 'qbittorrent'],
      ['qbit', 'qbittorrent'],
      ['transmission', 'transmission'],
      ['deluge', 'deluge'],
      ['sabnzbd', 'sabnzbd'],
      ['nzbget', 'nzbget'],
      // notifications / networking
      ['apprise', 'apprise'],
      ['fing', 'fing'],
      ['myspeed', 'myspeed'],
      ['speedtest', 'speedtest-tracker'],
      ['kavita', 'kavita'],
      ['squid', 'squid'],
      ['lubelogger', 'lubelogger'],
      // Rachio — smart sprinkler controllers.
      ['rachio', 'rachio'],
      // GL.iNet — travel routers / mini-routers (GL-MT, GL-AR,
      // GL-AXT, Slate, Brume, Beryl model lines).
      ['gl.inet', 'glinet'],
      ['gl-inet', 'glinet'],
      ['glinet', 'glinet'],
      ['gl-mt', 'glinet'],
      ['gl-ar', 'glinet'],
      ['gl-axt', 'glinet'],
      ['gl-b', 'glinet'],
      ['slate-ax', 'glinet'],
      ['brume', 'glinet'],
      ['beryl-ax', 'glinet'],
      // Somfy — smart-home / motorised-blind hubs (TaHoma, Connexoon).
      ['somfy', 'somfy'],
      ['tahoma', 'somfy'],
      ['connexoon', 'somfy'],
      // HP / HPE family. Longest phrases first so "HPE ProLiant"
      // hits the existing `proliant` icon rather than falling
      // through to the generic HP wordmark, and "HP printer" /
      // "HP laptop" routes to the HP brand mark. The bare ` hp `
      // (with surrounding whitespace) avoids matching unrelated
      // "https" / "wp" / "shop" substrings inside hostnames.
      ['hewlett-packard', 'hp'],
      ['hewlett packard', 'hp'],
      [' hpe ', 'hp'],
      ['hpe-', 'hp'],
      [' hp ', 'hp'],
      ['hp-', 'hp'],
      // SanDisk — flash storage / SSDs / SD cards. Common host
      // labels: "SanDisk Extreme", "SD-Pro" model strings.
      ['sandisk', 'sandisk'],
      [' sd-pro', 'sandisk'],
      // Amazon Fire TV — streaming sticks / cubes / TVs running Fire OS.
      // Common host labels: "Fire TV Stick", "Fire TV Cube", "Fire TV 4K".
      ['fire tv', 'firetv'],
      ['fire-tv', 'firetv'],
      ['firetv', 'firetv'],
      ['firestick', 'firetv'],
      ['fire stick', 'firetv'],
      // Amazon Echo / Alexa — smart speakers, Echo Dot / Show / Studio.
      // The dashboard-icons repo's `alexa.svg` is the canonical
      // Alexa-blue swirl mark that Echo devices ship with, so all
      // Echo product variants resolve to it.
      ['amazon echo', 'alexa'],
      ['echo dot', 'alexa'],
      ['echo show', 'alexa'],
      ['echo studio', 'alexa'],
      [' echo ', 'alexa'],
      ['alexa', 'alexa'],
      // Amazon parent brand — distinct from Alexa/Echo. Order
      // matters: matches AFTER the more-specific Alexa/Echo /
      // Fire-TV phrases so a host labelled "Amazon Echo Dot"
      // resolves to alexa.svg, not amazon.svg.
      ['amazon', 'amazon'],
      // Apple-family devices and OS marks. Apple TV / Apple TV 4K /
      // Apple TV HD all resolve to the apple-tv-plus mark (the
      // canonical curved-edge "tv" Apple uses across hardware +
      // streaming service). Generic Apple phrases fall through to
      // the apple wordmark.
      ['apple tv', 'apple-tv-plus'],
      ['apple-tv', 'apple-tv-plus'],
      ['appletv', 'apple-tv-plus'],
      ['apple homepod', 'apple'],
      ['homepod mini', 'apple'],
      ['homepod', 'apple'],
      ['apple watch', 'apple'],
      ['apple ', 'apple'],
      ['imac', 'apple'],
      ['macbook', 'apple'],
      ['ipad', 'apple'],
      ['iphone', 'apple'],
      // Google smart-home line — Nest Hub / Hub Max use the
      // dedicated `nest.svg` mark (Wikimedia Commons "Google Nest
      // logo"), Chromecast uses `chromecast.svg` (Wikimedia
      // Commons "Google Chromecast wordmark"), and the broader
      // Google Home / Home Hub lineup falls back to `google-home`
      // (homarr-labs dashboard-icons). Most-specific phrases first
      // so "Google Nest Hub Max" hits nest, not google-home.
      ['nest hub max', 'nest'],
      ['google nest hub', 'nest'],
      ['nest hub', 'nest'],
      ['google nest', 'nest'],
      ['google chromecast', 'chromecast'],
      ['chromecast', 'chromecast'],
      ['google home hub', 'google-home'],
      ['google home', 'google-home'],
      ['google pixel', 'google'],
      ['pixel ', 'google'],
      // Console gaming
      ['playstation', 'playstation'],
      ['ps4', 'playstation'],
      ['ps5', 'playstation'],
      ['nintendo switch', 'nintendo-switch'],
      ['switch 2', 'nintendo-switch'],
      ['nintendo', 'nintendo-switch'],
      // Microsoft + family. Surface lands on microsoft.svg (no
      // dedicated Surface mark in dashboard-icons).
      ['microsoft surface', 'microsoft'],
      ['surface pro', 'microsoft'],
      ['microsoft', 'microsoft'],
      // Hardware brands
      ['lenovo', 'lenovo'],
      ['veeam', 'veeam'],
      // Linux distros. Longer / more-specific phrases first per the
      // load-bearing keyword-ordering rule, else `mint` would match
      // before `linux mint`. The whitespace-padded ` mint ` short
      // form (added below) protects against substring false-matches
      // inside hostnames like `webmint`, `intermint`, etc.
      ['debian', 'debian'],
      ['ubuntu', 'ubuntu'],
      ['linux mint', 'linuxmint'],
      ['linux-mint', 'linuxmint'],
      ['linuxmint', 'linuxmint'],
      [' mint ', 'linuxmint'],
      ['mint os', 'linuxmint'],
      ['kali linux', 'kali'],
      ['kali', 'kali'],
      // Meta / Oculus VR
      ['oculus', 'oculus'],
      ['meta quest', 'meta'],
      // Huawei phones / tablets
      ['huawei', 'huawei'],
      // Humax — UK / EU set-top box manufacturer (Freesat, Aura, etc).
      ['humax', 'humax'],
      // Kaonmedia — Korean set-top box / cable modem maker.
      ['kaonmedia', 'kaonmedia'],
      ['kaon media', 'kaonmedia'],
      ['kaon', 'kaonmedia'],
      // HDHomeRun — SiliconDust network TV tuner.
      ['hdhomerun', 'hdhomerun'],
      ['hd homerun', 'hdhomerun'],
      ['hd home run', 'hdhomerun'],
      ['silicondust', 'hdhomerun'],
      // J-Tech Digital — HDMI matrix / video distribution gear.
      ['jtech digital', 'jtech'],
      ['jtech', 'jtech'],
      ['j-tech', 'jtech'],
      ['j tech', 'jtech'],
      // Nixplay — digital photo frames.
      ['nixplay', 'nixplay'],
      // Seeed Studio — open-source hardware (Raspberry Pi accessories,
      // ReSpeaker, ReComputer, ReTerminal, XIAO boards). Long-form
      // first so "seeedstudio" matches before the bare "seeed" pad.
      ['seeed studio', 'seeedstudio'],
      ['seeedstudio', 'seeedstudio'],
      [' seeed ', 'seeedstudio'],
      // Samsung — separate slugs for the parent brand (`samsung`,
      // clean wordmark) vs. the corporate / B2B entity (`samsung-
      // electronics`, the older "Samsung Electronics" mark with the
      // ellipse). Most-specific phrase wins so "samsung electronics"
      // matches the corporate slug while "samsung galaxy" / "samsung tv"
      // land on the consumer wordmark. Order matters here.
      ['samsung electronics', 'samsung-electronics'],
      ['samsungelectronics', 'samsung-electronics'],
      ['samsung galaxy', 'samsung'],
      ['galaxy s', 'samsung'],
      ['galaxy a', 'samsung'],
      ['galaxy m', 'samsung'],
      ['galaxy tab', 'samsung'],
      ['samsung', 'samsung'],
      // Bose audio — SoundTouch / Home Speaker / Wave / QC family.
      ['bose soundtouch', 'bose'],
      ['bose home speaker', 'bose'],
      ['bose ', 'bose'],
      ['soundtouch', 'bose'],
      // Gigabyte motherboards / desktops / Aorus brand
      ['gigabyte', 'gigabyte'],
      ['aorus', 'gigabyte'],
      ['b550 aorus', 'gigabyte'],
      // Roku — streaming sticks. simple-icons.org source.
      ['roku', 'roku'],
      // Alienware (Dell sub-brand) — gaming laptops / desktops.
      ['alienware', 'alienware'],
      // Amazon Kindle (e-reader) — no dedicated icon in either
      // dashboard-icons or simple-icons; falls back to amazon.svg
      // via the parent-brand keyword above.
      ['kindle', 'amazon'],
      // WD TV Live Hub — Western Digital's media-streamer line;
      // reuses the existing wd.svg parent brand mark.
      ['wd tv', 'wd'],
      ['wd-tv', 'wd'],
    ];
    for (const [needle, slug] of tokens) {
      if (hay.includes(needle)) {
        return this._themeIcon('/img/icons/' + slug + '.svg');
      }
    }
    return '';
  },
};
