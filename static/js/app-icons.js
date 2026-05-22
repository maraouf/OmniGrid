// noinspection ElementNotExported,JSUnusedGlobalSymbols,JSUnusedLocalSymbols,JSUnresolvedReference,JSUnresolvedFunction,JSUnresolvedVariable,JSIgnoredPromiseFromCall,CheckTagEmptyBody,HtmlUnknownTag,HtmlExtraClosingTag,MagicNumberJS,UnusedCatchParameterJS,OverlyComplexBooleanExpressionJS,FunctionWithMultipleReturnPointsJS,FunctionWithMoreThanThreeNegationsJS,OverlyNestedFunctionJS,OverlyLongFunctionJS,OverlyComplexFunctionJS,FunctionWithInconsistentReturnsJS,ChainedFunctionCallJS,NestedFunctionCallJS,NestedAssignmentJS,JSVariableNamingConventionJS,FunctionNamingConventionJS,JSStringConcatenationToES6Template,JSPotentiallyInvalidUsageOfThis,ContinueStatementJS,BreakStatementJS,AssignmentToFunctionParameterJS,IfStatementWithoutBlockJS,IfStatementWithIdenticalBranchesJS,AnonymousFunctionJS,AnonymousCapturingGroupJS,AnonymousFunctionRegExpJS,NamedFunctionExpressionJS,ConditionalExpressionJS,NestedConditionalExpressionJS,ConstantOnRightSideOfComparisonJS,ConstantOnLeftSideOfComparisonJS,EmptyCatchBlockJS,StatementWithEmptyBodyJS,RedundantConditionalExpressionJS,RedundantLocalVariableJS,JSValidateTypes,JSCheckFunctionSignatures,JSPrimitiveTypeWrapperUsage,JSDuplicatedDeclaration,TooManyFunctionParametersJS,NestedTemplateLiteralJS,AssignmentToForLoopParameterJS,AssignmentResultUsedJS,ConditionalCanBeReplacedWithEarlyExitJS
/* global Alpine, Swal, I18N, t, OG_VERSION, Terminal, FitAddon, WebLinksAddon, qrcode */
/* jshint esversion: 11, browser: true, devel: true, strict: implied, curly: false, bitwise: false, laxbreak: true, eqeqeq: false, forin: false, -W069 */
// Icon registry for the SPA's brand-icon resolver.
//
// `KNOWN_ICONS` is the registry of every icon file that actually exists
// under `static/img/icons/`. `iconUrlFor()` consults this before
// returning a /img/icons/<slug>.svg URL so unknown stack / host names
// don't trigger 404 noise in the browser console — operators flagged
// "Failed to load resource: website-monitoring.svg" for a stack that
// simply doesn't have a brand mark. With the registry, the resolver
// returns '' for unrecognized slugs and the SPA's existing
// `x-show="iconUrl"` gates hide the <img> entirely.
//
// Auto-built from `ls static/img/icons/*.svg | sed 's/\.svg$//'`. Re-run
// that pipeline (or `scripts/sync_icon_registry.sh` if/when added) after
// adding or removing icons.
//
// `KNOWN_DARK_ICONS` lists slugs that ship a `<slug>-dark.svg` variant
// alongside the default `<slug>.svg`. The icon resolver consults
// `_themeIcon(url)` at every emit point and auto-swaps to the
// `-dark.svg` URL when the document is in dark theme. Slugs NOT in this
// set get the same URL on both themes — most brand icons render fine on
// both backgrounds and don't need the second file. Adding a new dark
// variant: drop the `<slug>-dark.svg` under `static/img/icons/` AND add
// the slug here. Operators who set `h.icon = '<slug>-dark'` explicitly
// bypass the auto-swap (the `-dark` suffix is detected and
// short-circuits).

export const KNOWN_DARK_ICONS = new Set([
  // Pre-fix manual variants — also listed in KNOWN_ICONS as separate
  // slugs (`glinet-dark`, `portainer-dark`) for explicit-override
  // compatibility, but operators using the bare slug get the auto-swap
  // here.
  'glinet',
  'portainer',
  // Apple's bare logo is jet-black on the default file (homarr-labs
  // upstream `apple.svg`). The dark-theme variant `apple-dark.svg`
  // carries the white logo (sourced from upstream `apple-light.svg`,
  // re-saved under our standardised `-dark.svg` filename so the
  // resolver convention stays uniform: `<slug>-dark.svg` is always
  // "use this on dark theme" regardless of which side the upstream
  // calls "light" or "dark").
  'apple',
  // Apple TV+ — upstream naming matches our convention: their
  // `apple-tv-plus.svg` is the light-theme variant, their
  // `apple-tv-plus-light.svg` is the dark-theme variant (lighter
  // colours visible on dark bg). Saved locally as `apple-tv-plus.svg`
  // and `apple-tv-plus-dark.svg` respectively.
  'apple-tv-plus',
  // Synology — homarr-labs' upstream `synology.svg` is the dark-bg
  // variant (light-coloured logo); their `synology-light.svg` is the
  // light-bg variant. Saved locally as `synology.svg` (light-theme
  // default) and `synology-dark.svg` (dark-theme variant) so our
  // standard `<slug>-dark.svg` convention holds.
  'synology',
  // Dell — same upstream-`-light`-means-dark-colour-variant pattern as
  // Apple / Apple TV+. Local `dell.svg` is upstream `dell.svg`,
  // local `dell-dark.svg` is upstream `dell-light.svg`.
  'dell',
  // Amazon — same `-light`-means-dark-colour pattern. Local
  // `amazon.svg` from upstream `amazon.svg`; local `amazon-dark.svg`
  // from upstream `amazon-light.svg`.
  'amazon',
]);

export const KNOWN_ICONS = new Set([
  '5g', 'adguard-home', 'alexa', 'alienware', 'amazon', 'amazon-dark', 'ansible',
  'apache', 'apc', 'apc-ups', 'apple', 'apple-dark', 'apple-light', 'apple-tv-plus',
  'apple-tv-plus-dark', 'apple-tv-plus-light', 'apprise', 'aqara', 'asus', 'authentik', 'bazarr',
  'beszel', 'bose', 'caddy', 'chromecast', 'cisco', 'cloudflare', 'cloudflared', 'database',
  'ddns-updater', 'debian', 'dell', 'dell-dark', 'deluge', 'docker', 'dovecot',
  'dozzle', 'esxi', 'fing', 'firetv', 'flaresolverr', 'forgejo',
  'freenas', 'ftth', 'gigabyte', 'gitsync', 'glinet', 'glinet-dark', 'google',
  'google-home', 'grafana', 'hisense', 'homarr', 'home-assistant', 'homebridge',
  'hdhomerun', 'homepage', 'hp', 'huawei', 'humax', 'idrac', 'ikea', 'ilo',
  'influxdb', 'jellyfin', 'jellyseerr', 'seerr', 'jtech', 'kali', 'kaonmedia', 'kavita', 'keycloak',
  'komodo', 'kubernetes', 'lenovo', 'linuxmint', 'lubelogger', 'mail',
  'mailcow', 'meta', 'microsoft', 'mikrotik', 'mongodb', 'motorola',
  'myspeed', 'n8n', 'nest', 'netboot-xyz', 'netdata', 'nginx', 'nixplay',
  'nginx-proxy-manager', 'nintendo-switch', 'nzbget', 'oculus', 'openvpn', 'opnsense',
  'pfsense', 'pi-hole', 'pihole', 'pivpn', 'playstation', 'plex',
  'portainer', 'portainer-dark', 'postgresql', 'poweredge', 'proliant', 'prometheus',
  'prowlarr', 'proxmox', 'pulse', 'qbittorrent', 'rachio', 'radarr',
  'reolink', 'roku', 'roundcube', 'rundeck', 'rustdesk', 'sabnzbd',
  'samsung', 'samsung-electronics', 'sandisk', 'seeedstudio', 'sensibo', 'smtp', 'somfy', 'sonarr',
  'speedtest-tracker', 'squid', 'stalwart', 'synology', 'synology-dark', 'tailscale', 'tautulli',
  'tracearr', 'traefik', 'transmission', 'truenas', 'truenas-core', 'truenas-scale',
  'ubiquiti', 'ubuntu', 'ui', 'unifi', 'ups', 'uptime-kuma',
  'vcenter', 'vdsl', 'veeam', 'vmware', 'vsphere', 'wd',
  'webmin', 'windows', 'windows-10', 'windows-server', 'wireguard', 'xiaomi',
  'zabbix',
]);
