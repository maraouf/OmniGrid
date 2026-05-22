// noinspection NestedFunctionJS,FunctionContainsLoopsJS,FunctionWithMultipleLoopsJS,OverlyComplexFunctionJS,OverlyLongFunctionJS,OverlyLargeFunctionJS
// noinspection DuplicatedCodeFragmentJS,DuplicatedCode,ChainedFunctionCallJS,ChainedMethodCallJS,ConditionalExpressionJS,NestedConditionalExpressionJS
// noinspection RedundantConditionalExpressionJS,MagicNumberJS,JSMagicNumber,FunctionWithMultipleReturnPointsJS,IfStatementWithTooManyBranchesJS,JSForIIterationOverNonNumericKeyJS
// noinspection NestedTemplateLiteralJS
/* global Alpine, Swal, I18N, t, OG_VERSION, Terminal, FitAddon, WebLinksAddon, qrcode */
/* jshint esversion: 11, browser: true, devel: true, strict: implied, curly: false, bitwise: false, laxbreak: true, eqeqeq: false, forin: false, -W069 */
// Icon registry for the SPA's brand-icon resolver.

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
