"""Apps feature — reusable service catalog (templates) + cross-host aggregation.

The catalog is a small library of named recipes operators can bind to a
host's ``hosts_config[].services[]`` entry. Each template carries:

    {
        "id":           numeric primary key,
        "name":         display label (e.g. "Plex"),
        "slug":         stable identifier (e.g. "plex"),
        "icon":         icon slug fed to iconUrlFor() (defaults to slug),
        "description":  one-liner shown in the Apps view,
        "default_ports": [
            {"port": 32400, "protocol": "tcp", "label": "Web UI",
             "probe_path": "/web/", "probe_status": 0},
            ...
        ],
        "source":       "builtin" | "operator"
    }

A per-host chip references a template via ``services[i].catalog_id``;
multi-port chips override / extend the catalog's ports via
``services[i].probe.ports[]``.

This module is the single source of truth for catalog CRUD + the
cross-host aggregate view consumed by ``GET /api/apps``. Built-in
templates seed on boot via ``seed_builtins``: each builtin slug is
seeded at most once (tracked in a ledger setting) so a builtin newly
added to ``_BUILTIN`` in a release appears automatically on the next
deploy, while a builtin the operator deleted on purpose is NOT
re-added. Operator edits to a builtin already in the table are never
overwritten; the Admin → Apps "Re-seed built-ins" button (``force=True``)
restores any builtin missing from the table regardless of the ledger.
"""
from __future__ import annotations

import json
import re
import sqlite3
import time
from typing import Any, Iterable, Optional
from urllib.parse import urlparse

from logic.db import db_conn, iter_curated_hosts

# --------------------------------------------------------------------------
# Built-in catalog seed — ~15 essential templates the MVP ships with.
# Ports are the upstream defaults; operator overrides per host. Probe paths
# are the canonical health endpoints when one exists, "/" otherwise.
# --------------------------------------------------------------------------
_BUILTIN: list[dict[str, Any]] = [
    {
        "name": "Plex", "slug": "plex", "icon": "plex",
        "description": "Media streaming server (Plex Media Server)",
        "default_ports": [
            {"port": 32400, "protocol": "tcp", "label": "Web UI",
             "probe_path": "/web/", "probe_status": 0},
            {"port": 32469, "protocol": "tcp", "label": "DLNA",
             "probe_path": "", "probe_status": 0},
        ],
    },
    {
        "name": "Syncthing", "slug": "syncthing", "icon": "syncthing",
        "description": "Continuous peer-to-peer file synchronisation",
        "default_ports": [
            {"port": 8384, "protocol": "tcp", "label": "Web UI",
             "probe_path": "/", "probe_status": 0},
        ],
    },
    {
        "name": "ManageEngine", "slug": "manageengine", "icon": "manageengine",
        "description": "ManageEngine IT management suite",
        "default_ports": [
            {"port": 8020, "protocol": "tcp", "label": "8020",
             "probe_path": "", "probe_status": 0},
            {"port": 8027, "protocol": "tcp", "label": "8027",
             "probe_path": "", "probe_status": 0},
            {"port": 8746, "protocol": "tcp", "label": "8746",
             "probe_path": "", "probe_status": 0},
            {"port": 49669, "protocol": "tcp", "label": "49669",
             "probe_path": "", "probe_status": 0},
        ],
    },
    {
        "name": "Veeam Server", "slug": "veeam-server", "icon": "veeam",
        "description": "Veeam Backup & Replication server",
        "default_ports": [
            {"port": 9392, "protocol": "tcp", "label": "Backup",
             "probe_path": "", "probe_status": 0},
            {"port": 9393, "protocol": "tcp", "label": "Catalog",
             "probe_path": "", "probe_status": 0},
            {"port": 9401, "protocol": "tcp", "label": "Secure",
             "probe_path": "", "probe_status": 0},
            {"port": 9419, "protocol": "tcp", "label": "REST",
             "probe_path": "", "probe_status": 0},
        ],
    },
    {
        "name": "Rundeck", "slug": "rundeck", "icon": "rundeck",
        "description": "Runbook automation / job scheduler",
        "default_ports": [
            {"port": 4440, "protocol": "tcp", "label": "Web UI",
             "probe_path": "/", "probe_status": 0},
        ],
    },
    {
        "name": "Email (SMTP)", "slug": "smtp", "icon": "smtp",
        "description": "Mail transfer agent (SMTP / SMTPS / Submission)",
        "default_ports": [
            {"port": 25, "protocol": "tcp", "label": "SMTP",
             "probe_path": "", "probe_status": 0},
            {"port": 465, "protocol": "tcp", "label": "SMTPS",
             "probe_path": "", "probe_status": 0},
            {"port": 587, "protocol": "tcp", "label": "Submission",
             "probe_path": "", "probe_status": 0},
        ],
    },
    {
        "name": "Forgejo", "slug": "forgejo", "icon": "forgejo",
        "description": "Self-hosted Git service",
        "default_ports": [
            {"port": 3000, "protocol": "tcp", "label": "Web UI",
             "probe_path": "/", "probe_status": 0},
        ],
    },
    {
        "name": "Uptime Kuma", "slug": "uptime-kuma", "icon": "uptime-kuma",
        "description": "Self-hosted uptime monitoring",
        "default_ports": [
            {"port": 3001, "protocol": "tcp", "label": "Web UI",
             "probe_path": "/", "probe_status": 0},
        ],
    },
    {
        "name": "Tracearr", "slug": "tracearr", "icon": "tracearr",
        "description": "Media-request tracing dashboard",
        "default_ports": [
            {"port": 3003, "protocol": "tcp", "label": "Web UI",
             "probe_path": "/", "probe_status": 0},
        ],
    },
    {
        "name": "netboot.xyz", "slug": "netboot-xyz", "icon": "netboot-xyz",
        "description": "Network boot menu + assets",
        "default_ports": [
            {"port": 3050, "protocol": "tcp", "label": "Web UI",
             "probe_path": "/", "probe_status": 0},
            {"port": 3060, "protocol": "tcp", "label": "Assets",
             "probe_path": "/", "probe_status": 0},
        ],
    },
    {
        "name": "Kavita", "slug": "kavita", "icon": "kavita",
        "description": "Self-hosted digital library / reader",
        "default_ports": [
            {"port": 5002, "protocol": "tcp", "label": "Web UI",
             "probe_path": "/", "probe_status": 0},
        ],
    },
    {
        "name": "Speedtest Tracker", "slug": "speedtest-tracker", "icon": "speedtest-tracker",
        "description": "Scheduled internet-speed tracking dashboard",
        "default_ports": [
            {"port": 5050, "protocol": "tcp", "label": "Web UI",
             "probe_path": "/", "probe_status": 0},
            {"port": 5051, "protocol": "tcp", "label": "Web UI (alt)",
             "probe_path": "/", "probe_status": 0},
        ],
    },
    {
        "name": "Seerr", "slug": "seerr", "icon": "seerr",
        "description": "Media-request management",
        "default_ports": [
            {"port": 5055, "protocol": "tcp", "label": "Web UI",
             "probe_path": "/", "probe_status": 0},
        ],
    },
    {
        "name": "Bazarr", "slug": "bazarr", "icon": "bazarr",
        "description": "Subtitle management for Sonarr / Radarr",
        "default_ports": [
            {"port": 6767, "protocol": "tcp", "label": "Web UI",
             "probe_path": "/", "probe_status": 0},
        ],
    },
    {
        "name": "Homarr", "slug": "homarr", "icon": "homarr",
        "description": "Self-hosted dashboard / homepage",
        "default_ports": [
            {"port": 7575, "protocol": "tcp", "label": "Web UI",
             "probe_path": "/", "probe_status": 0},
        ],
    },
    {
        "name": "Pulse", "slug": "pulse-server", "icon": "pulse",
        "description": "Proxmox Pulse monitoring server (distinct from the agent)",
        "default_ports": [
            {"port": 7655, "protocol": "tcp", "label": "Web UI",
             "probe_path": "/", "probe_status": 0},
        ],
    },
    {
        "name": "Apprise", "slug": "apprise", "icon": "apprise",
        "description": "Notification fan-out API",
        "default_ports": [
            {"port": 8005, "protocol": "tcp", "label": "API",
             "probe_path": "/", "probe_status": 0},
        ],
    },
    {
        "name": "ddns-updater", "slug": "ddns-updater", "icon": "ddns-updater",
        "description": "Dynamic-DNS record updater",
        "default_ports": [
            {"port": 8010, "protocol": "tcp", "label": "Web UI",
             "probe_path": "/", "probe_status": 0},
        ],
    },
    {
        "name": "Dozzle", "slug": "dozzle", "icon": "dozzle",
        "description": "Real-time Docker container log viewer",
        "default_ports": [
            {"port": 8080, "protocol": "tcp", "label": "Web UI",
             "probe_path": "/", "probe_status": 0},
        ],
    },
    {
        "name": "Dockge", "slug": "dockge", "icon": "dockge",
        "description": "Docker compose stack manager",
        "default_ports": [
            {"port": 8085, "protocol": "tcp", "label": "Web UI",
             "probe_path": "/", "probe_status": 0},
        ],
    },
    {
        "name": "MariaDB", "slug": "mariadb", "icon": "mariadb",
        "description": "MariaDB / MySQL relational database server",
        "default_ports": [
            {"port": 3306, "protocol": "tcp", "label": "MySQL",
             "probe_path": "", "probe_status": 0},
        ],
    },
    {
        "name": "PostgreSQL", "slug": "postgresql", "icon": "postgresql",
        "description": "PostgreSQL relational database server",
        "default_ports": [
            {"port": 5432, "protocol": "tcp", "label": "PostgreSQL",
             "probe_path": "", "probe_status": 0},
        ],
    },
    {
        "name": "MongoDB", "slug": "mongodb", "icon": "mongodb",
        "description": "MongoDB document database server",
        "default_ports": [
            {"port": 27017, "protocol": "tcp", "label": "MongoDB",
             "probe_path": "", "probe_status": 0},
        ],
    },
    {
        "name": "InfluxDB", "slug": "influxdb", "icon": "influxdb",
        "description": "InfluxDB time-series database (HTTP API + RPC)",
        "default_ports": [
            {"port": 8086, "protocol": "tcp", "label": "HTTP API",
             "probe_path": "/ping", "probe_status": 0},
            {"port": 8088, "protocol": "tcp", "label": "RPC",
             "probe_path": "", "probe_status": 0},
        ],
    },
    {
        "name": "Splunk", "slug": "splunk", "icon": "splunk",
        "description": "Splunk log analytics / SIEM web UI",
        "default_ports": [
            {"port": 8080, "protocol": "http", "label": "Web UI",
             "probe_path": "/", "probe_status": 0},
            {"port": 8089, "protocol": "tcp", "label": "Management / REST API",
             "probe_path": "", "probe_status": 0},
            {"port": 8191, "protocol": "tcp", "label": "Embedded Mongo (KV store)",
             "probe_path": "", "probe_status": 0},
        ],
    },
    {
        "name": "Beszel Hub", "slug": "beszel-hub", "icon": "beszel",
        "description": "Beszel monitoring hub web UI (distinct from the agent)",
        "default_ports": [
            {"port": 8090, "protocol": "http", "label": "Web UI",
             "probe_path": "/", "probe_status": 0},
        ],
    },
    {
        "name": "RustDesk", "slug": "rustdesk", "icon": "rustdesk",
        "description": "Self-hosted RustDesk remote-desktop server (hbbs/hbbr)",
        "default_ports": [
            {"port": 21114, "protocol": "tcp", "label": "hbbs Web",
             "probe_path": "", "probe_status": 0},
            {"port": 21115, "protocol": "tcp", "label": "hbbs NAT",
             "probe_path": "", "probe_status": 0},
            {"port": 21116, "protocol": "tcp", "label": "hbbs ID/Reg",
             "probe_path": "", "probe_status": 0},
            {"port": 21117, "protocol": "tcp", "label": "hbbr Relay",
             "probe_path": "", "probe_status": 0},
            {"port": 21118, "protocol": "tcp", "label": "hbbs WS",
             "probe_path": "", "probe_status": 0},
            {"port": 21119, "protocol": "tcp", "label": "hbbr WS",
             "probe_path": "", "probe_status": 0},
        ],
    },
    {
        "name": "AdGuardHome Sync", "slug": "adguardhome-sync", "icon": "adguard-home",
        "description": "Syncs config between AdGuard Home instances",
        "default_ports": [
            {"port": 8091, "protocol": "tcp", "label": "Web UI",
             "probe_path": "/", "probe_status": 0},
        ],
    },
    {
        "name": "LubeLogger", "slug": "lubelogger", "icon": "lubelogger",
        "description": "Vehicle maintenance + fuel tracking",
        "default_ports": [
            {"port": 8095, "protocol": "tcp", "label": "Web UI",
             "probe_path": "/", "probe_status": 0},
        ],
    },
    {
        "name": "Tautulli", "slug": "tautulli", "icon": "tautulli",
        "description": "Plex monitoring + statistics",
        "default_ports": [
            {"port": 8181, "protocol": "tcp", "label": "Web UI",
             "probe_path": "/", "probe_status": 0},
        ],
    },
    {
        "name": "FlareSolverr", "slug": "flaresolverr", "icon": "flaresolverr",
        "description": "Cloudflare-challenge solver proxy",
        "default_ports": [
            {"port": 8191, "protocol": "tcp", "label": "API",
             "probe_path": "/", "probe_status": 0},
        ],
    },
    {
        "name": "Homebridge", "slug": "homebridge", "icon": "homebridge",
        "description": "HomeKit bridge for non-native accessories",
        "default_ports": [
            {"port": 8581, "protocol": "tcp", "label": "Web UI",
             "probe_path": "/", "probe_status": 0},
        ],
    },
    {
        "name": "Komodo", "slug": "komodo", "icon": "komodo",
        "description": "Build + deployment / container orchestration UI",
        "default_ports": [
            {"port": 9120, "protocol": "tcp", "label": "Web UI",
             "probe_path": "/", "probe_status": 0},
        ],
    },
    {
        "name": "OmniGrid", "slug": "omnigrid", "icon": "omnigrid",
        "description": "OmniGrid update + management dashboard",
        "default_ports": [
            {"port": 9500, "protocol": "tcp", "label": "Web UI",
             "probe_path": "/api/healthz", "probe_status": 200},
        ],
    },
    {
        "name": "Fing Agent", "slug": "fing-agent", "icon": "fing-agent",
        "description": "Network device discovery agent",
        "default_ports": [
            {"port": 44444, "protocol": "tcp", "label": "Agent",
             "probe_path": "", "probe_status": 0},
        ],
    },
    {
        "name": "Jellyfin", "slug": "jellyfin", "icon": "jellyfin",
        "description": "Open-source media streaming server",
        "default_ports": [
            {"port": 8096, "protocol": "tcp", "label": "HTTP",
             "probe_path": "/health", "probe_status": 200},
            {"port": 8920, "protocol": "tcp", "label": "HTTPS",
             "probe_path": "/health", "probe_status": 200},
        ],
    },
    {
        "name": "Radarr", "slug": "radarr", "icon": "radarr",
        "description": "Movie collection manager (*arr stack)",
        "default_ports": [
            {"port": 7878, "protocol": "tcp", "label": "Web UI",
             "probe_path": "/ping", "probe_status": 200},
        ],
    },
    {
        "name": "Sonarr", "slug": "sonarr", "icon": "sonarr",
        "description": "TV series collection manager (*arr stack)",
        "default_ports": [
            {"port": 8989, "protocol": "tcp", "label": "Web UI",
             "probe_path": "/ping", "probe_status": 200},
        ],
    },
    {
        "name": "Prowlarr", "slug": "prowlarr", "icon": "prowlarr",
        "description": "Indexer manager (*arr stack)",
        "default_ports": [
            {"port": 7886, "protocol": "tcp", "label": "Web UI",
             "probe_path": "/ping", "probe_status": 200},
        ],
    },
    {
        "name": "qBittorrent", "slug": "qbittorrent", "icon": "qbittorrent",
        "description": "BitTorrent client with Web UI",
        "default_ports": [
            {"port": 8080, "protocol": "tcp", "label": "Web UI",
             "probe_path": "/", "probe_status": 0},
        ],
    },
    {
        "name": "Portainer", "slug": "portainer", "icon": "portainer",
        "description": "Docker container management UI",
        "default_ports": [
            {"port": 8000, "protocol": "tcp", "label": "Edge tunnel",
             "probe_path": "", "probe_status": 0},
            {"port": 8443, "protocol": "tcp", "label": "HTTPS (alt)",
             "probe_path": "/api/system/status", "probe_status": 200},
            {"port": 9000, "protocol": "tcp", "label": "HTTP",
             "probe_path": "/api/system/status", "probe_status": 200},
            {"port": 9443, "protocol": "tcp", "label": "HTTPS",
             "probe_path": "/api/system/status", "probe_status": 200},
        ],
    },
    {
        "name": "Authentik", "slug": "authentik", "icon": "authentik",
        "description": "Identity provider / SSO (Authentik)",
        "default_ports": [
            # HTTP health check on /-/health/live/. probe_status 0 = accept
            # any 2xx/3xx: Authentik returns 200 on current versions and 204
            # on older ones, so a fixed 204 false-fails the 200 case.
            {"port": 9000, "protocol": "http", "label": "HTTP",
             "probe_path": "/-/health/live/", "probe_status": 0},
            {"port": 9443, "protocol": "https", "label": "HTTPS",
             "probe_path": "/-/health/live/", "probe_status": 0},
        ],
    },
    {
        # Authentik's LDAP outpost — distinct from the SSO web template
        # above. Bare TCP-connect: LDAP / LDAPS speak their own protocol,
        # not HTTP, so an HTTP probe would never validate them.
        "name": "Authentik LDAP", "slug": "authentik-ldap", "icon": "authentik",
        "description": "Authentik LDAP provider / outpost",
        "default_ports": [
            {"port": 389, "protocol": "tcp", "label": "LDAP",
             "probe_path": "", "probe_status": 0},
            {"port": 636, "protocol": "tcp", "label": "LDAPS",
             "probe_path": "", "probe_status": 0},
        ],
    },
    {
        "name": "Home Assistant", "slug": "home-assistant",
        "icon": "home-assistant",
        "description": "Home automation hub",
        "default_ports": [
            {"port": 8123, "protocol": "tcp", "label": "Web UI",
             "probe_path": "/", "probe_status": 0},
        ],
    },
    {
        "name": "Homepage", "slug": "homepage", "icon": "homepage",
        "description": "Self-hosted services dashboard (gethomepage.dev)",
        "default_ports": [
            {"port": 5000, "protocol": "http", "label": "Web UI",
             "probe_path": "/", "probe_status": 0},
        ],
    },
    {
        "name": "Pi-hole", "slug": "pi-hole", "icon": "pi-hole",
        "description": "DNS-level ad-blocker",
        "default_ports": [
            {"port": 80, "protocol": "tcp", "label": "Admin UI",
             "probe_path": "/admin/", "probe_status": 0},
            {"port": 53, "protocol": "tcp", "label": "DNS",
             "probe_path": "", "probe_status": 0},
        ],
    },
    {
        "name": "Grafana", "slug": "grafana", "icon": "grafana",
        "description": "Metrics + observability dashboard",
        "default_ports": [
            {"port": 3000, "protocol": "tcp", "label": "Web UI",
             "probe_path": "/api/health", "probe_status": 200},
        ],
    },
    {
        "name": "Prometheus", "slug": "prometheus", "icon": "prometheus",
        "description": "Time-series metrics scraper",
        "default_ports": [
            {"port": 9090, "protocol": "tcp", "label": "Web UI",
             "probe_path": "/-/healthy", "probe_status": 200},
        ],
    },
    {
        "name": "NetData", "slug": "netdata", "icon": "netdata",
        "description": "Real-time host metrics agent",
        "default_ports": [
            {"port": 19999, "protocol": "tcp", "label": "Web UI",
             "probe_path": "/api/v1/info", "probe_status": 200},
        ],
    },
    {
        "name": "Webmin", "slug": "webmin", "icon": "webmin",
        "description": "Unix system administration UI",
        "default_ports": [
            {"port": 10000, "protocol": "tcp", "label": "HTTPS",
             "probe_path": "/", "probe_status": 0},
        ],
    },
    {
        "name": "Nextcloud", "slug": "nextcloud", "icon": "nextcloud",
        "description": "Self-hosted file sync + collaboration",
        "default_ports": [
            {"port": 80, "protocol": "tcp", "label": "HTTP",
             "probe_path": "/status.php", "probe_status": 200},
            {"port": 443, "protocol": "tcp", "label": "HTTPS",
             "probe_path": "/status.php", "probe_status": 200},
        ],
    },
    {
        "name": "node-exporter", "slug": "node-exporter", "icon": "node-exporter",
        "description": "Prometheus host-metrics exporter (Linux / FreeBSD)",
        "default_ports": [
            {"port": 9100, "protocol": "tcp", "label": "Metrics",
             "probe_path": "/metrics", "probe_status": 200},
        ],
    },
    {
        "name": "Nginx Proxy Manager", "slug": "nginx-proxy-manager",
        "icon": "nginx-proxy-manager",
        "description": "Reverse-proxy admin UI (NPM) with LetsEncrypt + SQLite",
        "default_ports": [
            {"port": 81, "protocol": "tcp", "label": "Admin UI",
             "probe_path": "/", "probe_status": 0},
        ],
    },
    {
        "name": "AdGuard Home", "slug": "adguard-home", "icon": "adguard-home",
        "description": "Network-wide DNS-level ad / tracker blocker + DHCP",
        "default_ports": [
            {"port": 3000, "protocol": "tcp", "label": "Admin UI",
             "probe_path": "/", "probe_status": 0},
            {"port": 80, "protocol": "tcp", "label": "Admin UI (post-setup)",
             "probe_path": "/", "probe_status": 0},
            {"port": 53, "protocol": "tcp", "label": "DNS (TCP)",
             "probe_path": "", "probe_status": 0},
            {"port": 853, "protocol": "tcp", "label": "DNS-over-TLS",
             "probe_path": "", "probe_status": 0},
        ],
    },
    {
        # Monitoring / backup agents — bare TCP listeners (no HTTP UI), so
        # every port uses an empty probe_path + probe_status 0, which the
        # sampler treats as a plain TCP-connect liveness check.
        "name": "Zabbix Agent", "slug": "zabbix", "icon": "zabbix",
        "description": "Zabbix monitoring agent (passive checks listener)",
        "default_ports": [
            {"port": 10050, "protocol": "tcp", "label": "Agent (passive)",
             "probe_path": "", "probe_status": 0},
        ],
    },
    {
        "name": "Zabbix Web", "slug": "zabbix-web", "icon": "zabbix",
        "description": "Zabbix server web frontend",
        "default_ports": [
            {"port": 80, "protocol": "tcp", "label": "HTTP",
             "probe_path": "/", "probe_status": 0},
            {"port": 443, "protocol": "tcp", "label": "HTTPS",
             "probe_path": "/", "probe_status": 0},
        ],
    },
    {
        "name": "Veeam Agent", "slug": "veeam", "icon": "veeam",
        "description": "Veeam backup management + transport agent",
        "default_ports": [
            {"port": 6160, "protocol": "tcp", "label": "Management Agent",
             "probe_path": "", "probe_status": 0},
            {"port": 6162, "protocol": "tcp", "label": "Transport",
             "probe_path": "", "probe_status": 0},
        ],
    },
    {
        "name": "Pulse Agent", "slug": "pulse", "icon": "pulse",
        "description": "Pulse monitoring agent (Proxmox / host metrics)",
        "default_ports": [
            {"port": 9191, "protocol": "tcp", "label": "Agent",
             "probe_path": "", "probe_status": 0},
        ],
    },
    {
        "name": "Beszel Agent", "slug": "beszel", "icon": "beszel",
        "description": "Beszel monitoring agent (reports to the Beszel hub)",
        "default_ports": [
            {"port": 45876, "protocol": "tcp", "label": "Agent",
             "probe_path": "", "probe_status": 0},
        ],
    },
    {
        "name": "Lidarr", "slug": "lidarr", "icon": "lidarr",
        "description": "Music collection manager (*arr stack)",
        "default_ports": [
            {"port": 7882, "protocol": "tcp", "label": "Web UI",
             "probe_path": "/ping", "probe_status": 200},
        ],
    },
    {
        "name": "Readarr", "slug": "readarr", "icon": "readarr",
        "description": "Book / audiobook collection manager (*arr stack)",
        "default_ports": [
            {"port": 7888, "protocol": "tcp", "label": "Web UI",
             "probe_path": "/ping", "probe_status": 200},
        ],
    },
    {
        # VPN tunnels — bare connectivity, no HTTP UI, so empty
        # probe_path + probe_status 0 (TCP-connect liveness for
        # Tailscale; UDP for OpenVPN, which the TCP-connect probe
        # can't verify — the port metadata is still useful for
        # port-scan mapping + inventory).
        "name": "Tailscale", "slug": "tailscale", "icon": "tailscale",
        "description": "Mesh VPN (WireGuard-based)",
        "default_ports": [
            {"port": 57221, "protocol": "tcp", "label": "Tailscale",
             "probe_path": "", "probe_status": 0},
        ],
    },
    {
        "name": "OpenVPN", "slug": "openvpn", "icon": "openvpn",
        "description": "OpenVPN tunnel server",
        "default_ports": [
            {"port": 1194, "protocol": "udp", "label": "OpenVPN",
             "probe_path": "", "probe_status": 0},
        ],
    },
    {
        # Portainer EDGE agent — the agent endpoint on 9001 is a gRPC/
        # TLS tunnel, not a browsable HTTP UI, so it's a bare TCP-connect
        # liveness check (distinct from the Portainer template's 9000/9443
        # admin UI). Reuses the portainer brand icon.
        "name": "Portainer Agent", "slug": "portainer-agent", "icon": "portainer",
        "description": "Portainer Edge agent endpoint",
        "default_ports": [
            {"port": 9001, "protocol": "tcp", "label": "Agent",
             "probe_path": "", "probe_status": 0},
        ],
    },
    {
        # Squid forward proxy — the 3128 listener speaks HTTP-proxy, not
        # a browsable UI, so it's a bare TCP-connect liveness check.
        "name": "Squid Proxy", "slug": "squid", "icon": "squid",
        "description": "Squid caching forward proxy",
        "default_ports": [
            {"port": 3128, "protocol": "tcp", "label": "Proxy",
             "probe_path": "", "probe_status": 0},
        ],
    },
    {
        "name": "Whisparr", "slug": "whisparr", "icon": "whisparr",
        "description": "Adult media collection manager (*arr stack)",
        "default_ports": [
            {"port": 6969, "protocol": "tcp", "label": "Web UI",
             "probe_path": "/ping", "probe_status": 200},
        ],
    },
    {
        "name": "Tdarr", "slug": "tdarr", "icon": "tdarr",
        "description": "Distributed media transcode automation",
        "default_ports": [
            {"port": 8265, "protocol": "tcp", "label": "Web UI",
             "probe_path": "/", "probe_status": 0},
            {"port": 8266, "protocol": "tcp", "label": "Server",
             "probe_path": "", "probe_status": 0},
        ],
    },
]

_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9\-]{0,63}$")


def _coerce_int(v: Any) -> Optional[int]:
    """Narrow an ``Any | None`` cell into ``Optional[int]``.

    Used at every JSON / dict cell boundary so static analysis can
    see the type narrow before the value reaches downstream consumers
    that demand a concrete ``int``. Mirrors the same-shape helper in
    ``logic.service_sampler`` — duplicated rather than imported to
    keep the module dependency graph one-way (sampler depends on
    catalog, not the other way around).
    """
    if v is None or v == "":
        return None
    if isinstance(v, bool):
        # bool is a subclass of int but should not be coerced — most
        # boolean-looking entries in JSON payloads are protocol flags
        # we'd reject downstream anyway.
        return None
    if isinstance(v, (int, float)):
        return int(v)
    if isinstance(v, str):
        try:
            return int(v)
        except ValueError:
            return None
    return None


def _coerce_ports(raw: Any) -> list[dict]:
    """Normalise a ``default_ports`` / ``probe.ports`` list. Drops malformed
    entries; clamps int fields to sensible ranges. Returns ``[]`` on
    non-list input.
    """
    if not isinstance(raw, list):
        return []
    out: list[dict] = []
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        port = _coerce_int(entry.get("port"))
        if port is None or not (1 <= port <= 65535):
            continue
        proto = (entry.get("protocol") or "tcp")
        proto = proto.strip().lower() if isinstance(proto, str) else "tcp"
        if proto not in ("tcp", "udp", "http", "https"):
            proto = "tcp"
        label = entry.get("label")
        label = label.strip()[:64] if isinstance(label, str) else ""
        probe_path = entry.get("probe_path")
        probe_path = probe_path.strip()[:256] if isinstance(probe_path, str) else ""
        probe_status = _coerce_int(entry.get("probe_status")) or 0
        if not (0 <= probe_status <= 599):
            probe_status = 0
        out.append({
            "port": port,
            "protocol": proto,
            "label": label,
            "probe_path": probe_path,
            "probe_status": probe_status,
        })
    return out


def _row_to_dict(row: sqlite3.Row | tuple) -> dict[str, Any]:
    """Materialise a `service_catalog` row into the API shape."""
    try:
        ports_raw = json.loads(row[5] or "[]")
    except (TypeError, ValueError):
        ports_raw = []
    return {
        "id": int(row[0]),
        "name": row[1] or "",
        "slug": row[2] or "",
        "icon": row[3] or "",
        "description": row[4] or "",
        "default_ports": _coerce_ports(ports_raw),
        "source": row[6] or "operator",
        "created_ts": int(row[7] or 0),
        "updated_ts": int(row[8] or 0),
    }


def _select_columns() -> str:
    return ("id, name, slug, icon, description, default_ports_json, source, "
            "created_ts, updated_ts")


def list_catalog() -> list[dict[str, Any]]:
    """All templates (builtin + operator) ordered by name."""
    try:
        with db_conn() as c:
            rows = c.execute(
                f"SELECT {_select_columns()} FROM service_catalog ORDER BY name COLLATE NOCASE"
            ).fetchall()
    except (sqlite3.Error, OSError) as e:
        print(f"[service_catalog] list_catalog skipped: {e}")
        return []
    return [_row_to_dict(r) for r in rows]


def get_catalog_by_id(cid: int) -> Optional[dict[str, Any]]:
    """Single template by primary key. ``None`` when not found."""
    try:
        cid_int = int(cid)
    except (TypeError, ValueError):
        return None
    try:
        with db_conn() as c:
            row = c.execute(
                f"SELECT {_select_columns()} FROM service_catalog WHERE id = ?",
                (cid_int,),
            ).fetchone()
    except (sqlite3.Error, OSError) as e:
        print(f"[service_catalog] get_catalog_by_id({cid!r}) skipped: {e}")
        return None
    return _row_to_dict(row) if row else None


def get_catalog_by_slug(slug: str) -> Optional[dict[str, Any]]:
    """Single template by slug. ``None`` when not found."""
    if not isinstance(slug, str) or not slug.strip():
        return None
    try:
        with db_conn() as c:
            row = c.execute(
                f"SELECT {_select_columns()} FROM service_catalog WHERE slug = ?",
                (slug.strip().lower(),),
            ).fetchone()
    except (sqlite3.Error, OSError) as e:
        print(f"[service_catalog] get_catalog_by_slug({slug!r}) skipped: {e}")
        return None
    return _row_to_dict(row) if row else None


def create_catalog_entry(*, name: str, slug: str = "", icon: str = "",
                         description: str = "",
                         default_ports: Optional[list[dict]] = None,
                         source: str = "operator") -> dict[str, Any]:
    """Insert a new template. Raises ``ValueError`` on validation errors;
    raises ``sqlite3.IntegrityError`` on duplicate slug.

    `slug` is auto-derived from `name` when blank.
    """
    if not isinstance(name, str) or not name.strip():
        raise ValueError("name is required")
    name_clean = name.strip()[:128]
    slug_clean = (slug or _auto_slug(name_clean)).strip().lower()
    if not _SLUG_RE.match(slug_clean):
        raise ValueError(f"invalid slug: {slug!r} (must be lowercase letters, digits, hyphens)")
    icon_clean = (icon or "").strip()[:64] or slug_clean
    desc_clean = (description or "").strip()[:512]
    ports_clean = _coerce_ports(default_ports or [])
    src_clean = source if source in ("builtin", "operator") else "operator"
    now = int(time.time())
    with db_conn() as c:
        cur = c.execute(
            "INSERT INTO service_catalog "
            "(name, slug, icon, description, default_ports_json, source, created_ts, updated_ts) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (name_clean, slug_clean, icon_clean, desc_clean,
             json.dumps(ports_clean), src_clean, now, now),
        )
        cid = int(cur.lastrowid or 0)
    return get_catalog_by_id(cid) or {}


def update_catalog_entry(cid: int, *,
                         name: Optional[str] = None,
                         slug: Optional[str] = None,
                         icon: Optional[str] = None,
                         description: Optional[str] = None,
                         default_ports: Optional[list[dict]] = None) -> Optional[dict[str, Any]]:
    """Partial update — only fields with non-None values are written.
    Returns the post-update row or ``None`` if the id didn't exist.
    """
    existing = get_catalog_by_id(cid)
    if existing is None:
        return None
    sets: list[str] = []
    params: list[Any] = []
    if name is not None:
        if not isinstance(name, str) or not name.strip():
            raise ValueError("name cannot be empty")
        sets.append("name = ?")
        params.append(name.strip()[:128])
    if slug is not None:
        slug_clean = slug.strip().lower()
        if not _SLUG_RE.match(slug_clean):
            raise ValueError(f"invalid slug: {slug!r}")
        sets.append("slug = ?")
        params.append(slug_clean)
    if icon is not None:
        sets.append("icon = ?")
        params.append(icon.strip()[:64])
    if description is not None:
        sets.append("description = ?")
        params.append(description.strip()[:512])
    if default_ports is not None:
        sets.append("default_ports_json = ?")
        params.append(json.dumps(_coerce_ports(default_ports)))
    if not sets:
        return existing
    sets.append("updated_ts = ?")
    params.append(int(time.time()))
    params.append(cid)
    with db_conn() as c:
        c.execute(
            f"UPDATE service_catalog SET {', '.join(sets)} WHERE id = ?",
            tuple(params),
        )
    return get_catalog_by_id(cid)


def delete_catalog_entry(cid: int) -> bool:
    """Delete a template. Returns True when a row was removed.

    Builtin templates ARE deletable — the operator owns their catalog.
    If they delete a builtin and want it back, they can re-seed via the
    admin UI (future) or just re-create it manually.
    """
    try:
        cid_int = int(cid)
    except (TypeError, ValueError):
        return False
    with db_conn() as c:
        cur = c.execute("DELETE FROM service_catalog WHERE id = ?", (cid_int,))
        return (cur.rowcount or 0) > 0


def _auto_slug(name: str) -> str:
    """Derive a slug from a display name. Lowercase, hyphens replace
    runs of non-alphanumeric chars."""
    s = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return s[:64] or "service"


# Bumped if the export pack shape ever changes incompatibly.
CATALOG_PACK_VERSION = 1


def export_catalog() -> dict[str, Any]:
    """Portable catalog pack — every template projected to the fields an
    import on another install can reconstruct. Drops install-specific
    columns (id / created_ts / updated_ts) so a pack imports cleanly
    anywhere; keys on ``slug``. Shape:

        {"omnigrid_catalog_pack": 1, "exported_ts": <epoch>,
         "count": N, "entries": [{name, slug, icon, description,
         default_ports, source}, ...]}
    """
    entries: list[dict[str, Any]] = []
    for e in list_catalog():
        entries.append({
            "name": e.get("name") or "",
            "slug": e.get("slug") or "",
            "icon": e.get("icon") or "",
            "description": e.get("description") or "",
            "default_ports": e.get("default_ports") or [],
            "source": e.get("source") or "operator",
        })
    return {
        "omnigrid_catalog_pack": CATALOG_PACK_VERSION,
        "exported_ts": int(time.time()),
        "count": len(entries),
        "entries": entries,
    }


def import_catalog_entries(entries: Any) -> dict[str, Any]:
    """Upsert a list of template dicts (an export pack's ``entries``).

    Match is by ``slug``: an existing slug is UPDATED (name / icon /
    description / default_ports), a new slug is CREATED. Imported
    templates are always stamped ``source="operator"`` regardless of the
    pack's source field — a pack can't inject a "builtin" the seed ledger
    would then think it owns. Per-entry errors are collected, not fatal,
    so one malformed row doesn't abort the whole pack.

    Returns ``{created, updated, errors: [{slug, error}, ...]}``.
    """
    created = 0
    updated = 0
    errors: list[dict[str, str]] = []
    if not isinstance(entries, list):
        return {"created": 0, "updated": 0, "errors": [{"slug": "", "error": "entries must be a list"}]}
    for raw in entries:
        if not isinstance(raw, dict):
            errors.append({"slug": "", "error": "entry is not an object"})
            continue
        name = (raw.get("name") or "").strip()
        slug = (raw.get("slug") or "").strip().lower()
        # Derive the slug from the name when the pack omitted it, mirroring
        # create_catalog_entry's own fallback.
        if not slug and name:
            slug = _auto_slug(name)
        if not name:
            errors.append({"slug": slug, "error": "name is required"})
            continue
        try:
            existing = get_catalog_by_slug(slug) if slug else None
            if existing is not None:
                update_catalog_entry(
                    _coerce_int(existing.get("id")) or 0,
                    name=name,
                    icon=(raw.get("icon") or "").strip(),
                    description=(raw.get("description") or "").strip(),
                    default_ports=raw.get("default_ports") or [],
                )
                updated += 1
            else:
                # source defaults to "operator" in create_catalog_entry — an
                # imported pack can't inject a "builtin" the seed ledger owns.
                create_catalog_entry(
                    name=name,
                    slug=slug,
                    icon=(raw.get("icon") or "").strip(),
                    description=(raw.get("description") or "").strip(),
                    default_ports=raw.get("default_ports") or [],
                )
                created += 1
        except (ValueError, sqlite3.IntegrityError, sqlite3.Error) as e:
            errors.append({"slug": slug, "error": str(e)})
    return {"created": created, "updated": updated, "errors": errors}


def catalog_builtin_ports() -> set[int]:
    """Set of every port declared across the built-in catalog templates
    (``_BUILTIN``). Backs the port-scan coverage audit
    (``scripts/audit_catalog_ports.py``): every shipped app template's
    ports MUST be in ``port_scanner.DEFAULT_PORTS`` so a default scan
    always covers an app OmniGrid ships a template for — adding a
    template then means adding its port(s) to DEFAULT_PORTS too, and the
    audit catches any miss."""
    ports: set[int] = set()
    for tpl in _BUILTIN:
        for p in (tpl.get("default_ports") or []):
            pi = _coerce_int(p.get("port")) if isinstance(p, dict) else None
            if pi:
                ports.add(pi)
    return ports


def seed_builtins(force: bool = False) -> int:
    """Insert built-in templates, picking up genuinely-new builtins on
    every boot while respecting operator deletions.

    Two paths:

    * Boot path (``force=False``, called from lifespan): inserts any
      builtin slug that is NOT already in the table AND has never been
      auto-seeded before (tracked in the
      ``Settings.SERVICE_CATALOG_SEEDED_SLUGS`` ledger). A builtin newly
      ADDED to ``_BUILTIN`` in a release therefore appears automatically
      on the next deploy — no manual re-seed — while a builtin the
      operator DELETED on purpose stays gone (its slug is in the ledger).
    * Force path (``force=True``, the Admin → Apps "Re-seed built-ins"
      button): inserts every builtin slug missing from the table,
      ignoring the ledger, so the operator can restore a deleted builtin.

    Either way the ledger is reconciled to cover every current builtin
    slug, so the next boot only acts on slugs added to ``_BUILTIN`` after
    this point. An existing pre-ledger deploy adopts its whole builtin
    set into the ledger on the first boot with this code — and in the
    same pass picks up any builtin that landed in ``_BUILTIN`` since it
    was first seeded (e.g. AdGuard Home).

    Returns the number of rows inserted.
    """
    try:
        from logic.settings_keys import Settings
        ledger_key = Settings.SERVICE_CATALOG_SEEDED_SLUGS.value
        with db_conn() as c:
            # Read the ever-seeded ledger via THIS connection — never via
            # set_setting() (it opens a second connection and would lock
            # against our still-open transaction).
            seeded_ledger: set[str] = set()
            row = c.execute(
                "SELECT value FROM settings WHERE key=?", (ledger_key,)
            ).fetchone()
            if row and row[0]:
                try:
                    parsed = json.loads(row[0])
                    if isinstance(parsed, list):
                        seeded_ledger = {str(s) for s in parsed}
                except (ValueError, TypeError):
                    seeded_ledger = set()
            existing_slugs = {
                r[0] for r in c.execute("SELECT slug FROM service_catalog").fetchall()
            }
            now = int(time.time())
            inserted = 0
            for tpl in _BUILTIN:
                slug = tpl["slug"]
                if slug in existing_slugs:
                    continue  # already present — never duplicate
                if not force and slug in seeded_ledger:
                    continue  # boot path: operator deleted it on purpose
                try:
                    c.execute(
                        "INSERT INTO service_catalog "
                        "(name, slug, icon, description, default_ports_json, source, created_ts, updated_ts) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                        (
                            tpl["name"],
                            tpl["slug"],
                            tpl.get("icon") or tpl["slug"],
                            tpl.get("description") or "",
                            json.dumps(_coerce_ports(tpl.get("default_ports") or [])),
                            "builtin",
                            now,
                            now,
                        ),
                    )
                    inserted += 1
                except sqlite3.IntegrityError:
                    # Race / duplicate slug — skip silently.
                    pass
            # Reconcile the ledger to cover every current builtin so the
            # next boot only considers slugs added to _BUILTIN later. The
            # write rides THIS connection's transaction (same reason as
            # the read above).
            new_ledger = seeded_ledger | {tpl["slug"] for tpl in _BUILTIN}
            ledger_changed = new_ledger != seeded_ledger
            if ledger_changed:
                c.execute(
                    "INSERT OR REPLACE INTO settings(key,value) VALUES (?,?)",
                    (ledger_key, json.dumps(sorted(new_ledger))),
                )
            # Bump `_settings_version` when we wrote anything (inserts OR a
            # ledger change) so the SSE `settings:updated` event fires +
            # open Admin → Apps tabs auto-refresh without a manual reload.
            # Direct service_catalog writes bypass `set_setting` (the
            # canonical bump trigger), so the bump fires explicitly here on
            # the same connection that rides the outer commit transaction.
            if inserted > 0 or ledger_changed:
                try:
                    from logic.db import _bump_settings_version_in
                    _bump_settings_version_in(c)
                except (sqlite3.Error, ImportError):
                    # Defence-in-depth: a bump failure must NOT roll
                    # back the seed inserts. SPA misses one cross-tab
                    # notification — recoverable on next poll.
                    pass
            return inserted
    except (sqlite3.Error, OSError) as e:
        print(f"[service_catalog] seed_builtins skipped: {e}")
        return 0


# --------------------------------------------------------------------------
# Cross-host aggregate — the data source for the top-level Apps view.
# --------------------------------------------------------------------------
def _service_signature(svc: dict, catalog_by_id: dict) -> tuple[str, str, str]:
    """Group key for cross-host aggregation. Returns (group_id, name, icon).

    Priority order:
    1. catalog_id (numeric FK) when set and resolvable → use catalog row
    2. name (case-insensitive) when set → use name
    3. URL hostname as a last resort → "url:<host>"
    """
    cid_int = _coerce_int(svc.get("catalog_id"))
    if cid_int and cid_int in catalog_by_id:
        tpl = catalog_by_id[cid_int]
        return f"cat:{cid_int}", tpl["name"], tpl.get("icon") or tpl.get("slug") or ""
    name = (svc.get("name") or "").strip()
    if name:
        return f"name:{name.lower()}", name, (svc.get("icon") or "").strip()
    url = (svc.get("url") or "").strip()
    if url:
        return f"url:{url.lower()}", url, (svc.get("icon") or "").strip()
    return "unknown", "Unknown service", ""


def _instance_status(svc: dict) -> str:
    """Derive an instance's status from its `last_probe` field stamped
    by populate_host_service_merge. ``up`` / ``down`` / ``unknown``."""
    last = svc.get("last_probe")
    if not isinstance(last, dict):
        return "unknown"
    if last.get("alive"):
        return "up"
    return "down"


def merge_port_results(probe_ports: Any, sample_rows: Any) -> list[dict]:
    """Per-port display list = the chip's CONFIGURED ports merged with
    probe-sample HISTORY.

    Every port in ``probe_ports`` (the chip's ``probe.ports[]``) appears
    in config order. A port that has a sample row carries its
    ``alive`` / ``rtt_ms`` / ``error``; a port not yet probed (e.g. the
    operator just added it) is emitted as PENDING (``alive=None``,
    ``pending=True``) so it shows immediately instead of waiting for the
    next sampler tick. Sample rows for ports no longer in the config are
    dropped (stale) — same end as the prior filter, but now the reverse
    case (configured-but-unprobed) is also covered. The frontend maps
    ``alive`` true/false/None onto up/down/unknown pill states.
    """
    by_port: dict[int, dict] = {}
    for pr in (sample_rows or []):
        if isinstance(pr, dict):
            pv = _coerce_int(pr.get("port"))
            if pv:
                by_port[pv] = pr
    out: list[dict] = []
    seen: set[int] = set()
    for cp in (probe_ports or []):
        if not isinstance(cp, dict):
            continue
        pv = _coerce_int(cp.get("port"))
        if not pv or pv in seen:
            continue
        seen.add(pv)
        sample = by_port.get(pv)
        if sample is not None:
            out.append(sample)
        else:
            out.append({
                "port": pv,
                "protocol": (cp.get("protocol") or "tcp"),
                "alive": None,
                "rtt_ms": None,
                "error": None,
                "pending": True,
            })
    return out


# noinspection DuplicatedCode
def list_apps() -> list[dict[str, Any]]:
    """Cross-host aggregate. Each returned row is one APP (group) with
    every host that runs an instance:

        {
            "group_id":   "cat:5" | "name:plex" | "url:...",
            "name":       "Plex",
            "icon":       "plex",
            "catalog":    { ... full template dict ... } | None,
            "instances":  [
                {
                    "host_id":      str,
                    "host_label":   str,
                    "service_idx":  int,
                    "url":          str,
                    "status":       "up" | "down" | "unknown",
                    "last_probe":   { alive, rtt_ms, ts, error } | None,
                    "probe_enabled": bool,
                    "ports":         [...]  # operator-set, may be empty
                },
                ...
            ],
            "instance_count": int,
            "up_count":       int,
            "down_count":     int,
            "unknown_count":  int,
            "status":         "up" | "degraded" | "down" | "unknown"
        }

    Sorted by name ASC.
    """
    catalog_rows = list_catalog()
    catalog_by_id: dict[int, dict[str, Any]] = {int(r["id"]): r for r in catalog_rows}
    # Cache latest probe per (host, idx) so we hit the DB once not N times.
    # `latest_per_port_all_for_host` is the batched per-port reader — one
    # query per host (same profile as latest_for_host) so multi-port chips
    # can surface which specific port failed in the Apps card's diagnosis
    # row without an extra DB hit per instance.
    from logic.service_sampler import latest_for_host, latest_per_port_all_for_host
    groups: dict[str, dict[str, Any]] = {}
    for host_row in iter_curated_hosts():
        hid = (host_row.get("id") or "").strip()
        if not hid:
            continue
        host_label = (host_row.get("label") or hid).strip()
        # `address` is the curated "Hostname or IP" probe target from
        # Admin → Hosts — surface it on every Apps instance so the
        # SPA can display a stable canonical hostname regardless of
        # what the row's `label` is. Falls back to the id when blank.
        host_address = (host_row.get("address") or hid).strip()
        services = host_row.get("services")
        if not isinstance(services, list) or not services:
            continue
        latest = latest_for_host(hid)
        per_port = latest_per_port_all_for_host(hid)
        for idx, svc in enumerate(services):
            if not isinstance(svc, dict):
                continue
            # Stamp last_probe so _instance_status can derive
            sample = latest.get(idx)
            if sample:
                svc = dict(svc)
                svc["last_probe"] = sample
            gid, gname, gicon = _service_signature(svc, catalog_by_id)
            grp = groups.get(gid)
            if grp is None:
                # Explicit dict[str, Any] annotation so the per-key
                # value types stay heterogeneous (group_id/name/icon
                # → str, catalog → Optional[dict], instances → list).
                # Without this the type checker infers a narrower
                # union from the literal and rejects the catalog
                # assignment below.
                grp: dict[str, Any] = {
                    "group_id": gid,
                    "name": gname,
                    "icon": gicon,
                    "catalog": None,
                    "instances": [],
                }
                # Resolve catalog block if group is catalog-linked.
                # `gid[4:]` is the catalog id we stamped into the
                # signature; _coerce_int narrows it from str → int
                # explicitly so static analysis can see the cast
                # before the dict.get() lookup.
                if gid.startswith("cat:"):
                    cid_lookup = _coerce_int(gid[4:])
                    if cid_lookup is not None:
                        grp["catalog"] = catalog_by_id.get(cid_lookup)
                groups[gid] = grp
            probe_cfg = svc.get("probe") or {}
            inst_status = _instance_status(svc)
            # Per-port display = the chip's CONFIGURED ports merged with
            # probe-sample HISTORY: every configured port shows (a just-
            # added one as PENDING until the sampler probes it), and a
            # sample for a port no longer in config is dropped (stale).
            port_results = merge_port_results(probe_cfg.get("ports"), per_port.get(idx))
            grp["instances"].append({
                "host_id": hid,
                "host_label": host_label,
                "host_address": host_address,
                "service_idx": idx,
                "url": (svc.get("url") or "").strip(),
                "status": inst_status,
                "last_probe": sample,
                "probe_enabled": bool(probe_cfg.get("enabled")),
                "ports": probe_cfg.get("ports") or [],
                # Optional Docker linkage — drives the App drawer's inline
                # Restart action when the operator linked this chip to a
                # Portainer container / stack.
                "docker_stack": (svc.get("docker_stack") or "").strip(),
                "docker_container": (svc.get("docker_container") or "").strip(),
                "docker_host": (svc.get("docker_host") or "").strip(),
                # Per-port latest outcomes (multi-port chips only) so the
                # Apps card can show WHICH port failed + its error reason,
                # not just the rolled-up chip status. Filtered to current
                # config ports above.
                "port_results": port_results,
            })
    # Tally + roll-up status
    out: list[dict] = []
    for grp in groups.values():
        up = sum(1 for i in grp["instances"] if i["status"] == "up")
        down = sum(1 for i in grp["instances"] if i["status"] == "down")
        unknown = sum(1 for i in grp["instances"] if i["status"] == "unknown")
        total = len(grp["instances"])
        grp["instance_count"] = total
        grp["up_count"] = up
        grp["down_count"] = down
        grp["unknown_count"] = unknown
        if total == 0:
            grp["status"] = "unknown"
        elif down == 0 and unknown == 0:
            grp["status"] = "up"
        elif up == 0 and down > 0:
            grp["status"] = "down"
        else:
            grp["status"] = "degraded"
        out.append(grp)
    out.sort(key=lambda g: g["name"].lower())
    return out


# noinspection DuplicatedCode
def iter_instances() -> Iterable[dict[str, Any]]:
    """Flat per-instance iterator — every chip across every host.

    Used by the Admin → Apps tab's "all instances" view and by future
    discovery wizards. Same shape as `list_apps()` instances + adds
    `host_id` / `host_label` / `name` / `icon` / `catalog_id` /
    `catalog_name` so callers can render without re-joining.

    The catalog-by-id init mirrors `list_apps` — same shape so the
    helpers can lookup catalog templates without re-querying. The
    duplicated-fragment warning at the equivalent block in `list_apps`
    is suppressed because extracting the 6 lines into a helper would
    introduce an extra dict copy on every call without a meaningful
    DRY benefit (the two functions yield different shapes from the
    same source data).
    """
    catalog_rows = list_catalog()
    catalog_by_id: dict[int, dict[str, Any]] = {int(r["id"]): r for r in catalog_rows}
    from logic.service_sampler import latest_for_host
    for host_row in iter_curated_hosts():
        hid = (host_row.get("id") or "").strip()
        if not hid:
            continue
        host_label = (host_row.get("label") or hid).strip()
        # `address` (curated "Hostname or IP" from Admin → Hosts) is
        # surfaced so SPA consumers can display the canonical reachable
        # hostname regardless of operator label. Falls back to hid.
        host_address = (host_row.get("address") or hid).strip()
        services = host_row.get("services")
        if not isinstance(services, list) or not services:
            continue
        latest = latest_for_host(hid)
        for idx, svc in enumerate(services):
            if not isinstance(svc, dict):
                continue
            cid_int = _coerce_int(svc.get("catalog_id"))
            tpl: Optional[dict[str, Any]] = catalog_by_id.get(cid_int) if cid_int is not None else None
            tpl_name = tpl.get("name", "") if tpl else ""
            tpl_slug = tpl.get("slug", "") if tpl else ""
            tpl_icon = tpl.get("icon", "") if tpl else ""
            name = (svc.get("name") or "").strip() or tpl_name
            icon = (svc.get("icon") or "").strip() or (tpl_icon or tpl_slug)
            sample = latest.get(idx)
            probe_cfg = svc.get("probe") or {}
            sample_alive = bool(sample and isinstance(sample, dict) and sample.get("alive"))
            yield {
                "host_id": hid,
                "host_label": host_label,
                "host_address": host_address,
                "service_idx": idx,
                "catalog_id": cid_int,
                "catalog_name": tpl_name or None,
                "catalog_slug": tpl_slug or None,
                "name": name,
                "icon": icon or tpl_slug,
                "url": (svc.get("url") or "").strip(),
                "ports": probe_cfg.get("ports") or [],
                "probe_enabled": bool(probe_cfg.get("enabled")),
                "probe_type": (probe_cfg.get("type") or "tcp").strip().lower(),
                "docker_stack": (svc.get("docker_stack") or "").strip(),
                "docker_container": (svc.get("docker_container") or "").strip(),
                "docker_host": (svc.get("docker_host") or "").strip(),
                "last_probe": sample,
                "status": "up" if sample_alive else ("down" if sample else "unknown"),
            }


# Public aliases — main.py's Apps endpoints reuse these helpers so the
# per-host chip's `probe.ports[]` array is normalised identically to a
# catalog template's `default_ports`, and the Any|None → Optional[int]
# narrow at every JSON / dict-cell boundary uses the same canonical
# implementation. The underscore-prefixed originals remain for
# in-module callers; the aliases are the documented entry points for
# cross-module callers so static analysis doesn't flag protected-
# member access on the import line.
coerce_ports = _coerce_ports
coerce_int = _coerce_int


# --------------------------------------------------------------------------
# Discovery wizard — match a host's known ports against catalog templates
# and propose bindings the operator can one-click adopt.
# --------------------------------------------------------------------------
def host_claimed_ports(host_row: dict, *, exclude_idx: Optional[int] = None) -> set[int]:
    """Set of ports already claimed by a host's existing service chips.

    Walks every chip's ``probe.ports[]`` + legacy ``probe.port`` +
    URL-derived port (explicit ``:port``, or the scheme default 80 / 443
    when an http(s) URL carries no port). The discovery wizard subtracts
    this set from the ports a NEW catalog template is allowed to match on,
    so it never proposes a second app for a port another app already owns
    (e.g. don't suggest Pi-hole / Nextcloud on 80 / 443 when AdGuard Home
    is already pinned and claims them).

    ``exclude_idx`` — skip the chip at this ``services[]`` index, so a chip
    can't suppress proposals for its own ports (unused by the current
    discover flow, but lets a future re-discovery path reason about one
    chip without self-collision).
    """
    claimed: set[int] = set()
    chips = host_row.get("services") if isinstance(host_row, dict) else None
    if not isinstance(chips, list):
        return claimed
    for idx, chip in enumerate(chips):
        if exclude_idx is not None and idx == exclude_idx:
            continue
        if not isinstance(chip, dict):
            continue
        probe_raw = chip.get("probe")
        probe = probe_raw if isinstance(probe_raw, dict) else {}
        for pp in (probe.get("ports") or []):
            if isinstance(pp, dict):
                ppi = _coerce_int(pp.get("port"))
                if ppi:
                    claimed.add(ppi)
        legacy = _coerce_int(probe.get("port"))
        if legacy:
            claimed.add(legacy)
        url = (chip.get("url") or "").strip()
        if url:
            try:
                pu = urlparse(url if "://" in url else "tcp://" + url)
                port_num = pu.port
                if port_num:
                    claimed.add(port_num)
                elif url.lower().startswith("https://"):
                    claimed.add(443)
                elif url.lower().startswith("http://"):
                    claimed.add(80)
            except (ValueError, AttributeError):
                pass
    return claimed


def propose_bindings(host_id: str, *,
                     detected_ports: Optional[list[int]] = None,
                     host_label: str = "",
                     existing_catalog_ids: Optional[set[int]] = None,
                     claimed_ports: Optional[set[int]] = None,
                     min_confidence: float = 0.0) -> list[dict[str, Any]]:
    """Match a host's detected ports against catalog templates.

    Returns a list of proposal dicts ordered by confidence DESC:

        {
            "catalog":     { ...full template dict... },
            "matched_ports": [80, 443],     # ports in template that the host has open
            "unmatched_ports": [22],         # template ports not seen on the host
            "confidence":  0.85,             # 0.0-1.0
            "match_reasons": ["all template ports detected",
                              "host label 'plex-server' contains 'plex'"]
        }

    ``detected_ports`` — list of TCP/UDP port numbers known to be open on
    the host (typically from the latest ``host_port_scans`` entry). When
    None, the function returns an empty list (no signal to match against).
    ``host_label`` — display label or host id for name-match boosting.
    ``existing_catalog_ids`` — catalog_ids already bound to this host's
    chips; the corresponding templates are SKIPPED so the wizard only
    suggests new bindings.
    ``claimed_ports`` — ports already owned by this host's existing chips
    (from ``host_claimed_ports``). A candidate template may only match on
    ports NOT in this set, so the wizard won't propose a second app for a
    port another app already owns: with AdGuard claiming 80 / 443, a
    Nextcloud template (80 / 443) scores zero matched ports and drops out,
    and a Pi-hole template (80 / 443 / 53) only matches on the still-free
    53. Pass an empty set / None to disable the filter.
    ``min_confidence`` — proposals below this threshold are dropped from
    the output. Default 0.0: the wizard shows EVERY template with at
    least one detected + unclaimed matched port so the operator can pick
    among all applicable apps for an open port (incl. generic/ambiguous
    ports like 8080 = Dozzle vs qBittorrent, or 3000 = Forgejo vs Grafana
    vs AdGuard). Confidence is still computed and used to SORT so exact /
    name-matched candidates rank first; it no longer GATES.

    Scoring (used for ranking, max 1.0):
      - port-overlap base: matched_ports / total_template_ports
      - name-match bonus: +0.3 if host_label contains template.slug OR
        any whole word of template.name (lowercased)
      - single-port templates (Plex etc.) need EXACT match to score
      - hard floor at 1.0
    """
    if not host_id:
        return []
    if not detected_ports:
        return []
    detected_set = {int(p) for p in detected_ports if p}
    if not detected_set:
        return []
    existing = existing_catalog_ids or set()
    claimed = {int(p) for p in (claimed_ports or set()) if p}
    host_haystack = (host_label or host_id or "").lower()
    templates = list_catalog()
    proposals: list[dict[str, Any]] = []
    for tpl in templates:
        if tpl["id"] in existing:
            continue
        tpl_ports = [int(p["port"]) for p in (tpl.get("default_ports") or [])
                     if isinstance(p, dict) and p.get("port")]
        if not tpl_ports:
            continue
        # A template may only match on ports NOT already owned by another
        # app on this host — so a port already claimed elsewhere gives this
        # candidate no credit (and a template whose ports are ALL claimed
        # scores zero matched ports and drops out below).
        matched = sorted(p for p in tpl_ports if p in detected_set and p not in claimed)
        unmatched = sorted(p for p in tpl_ports if p not in detected_set)
        if not matched:
            continue
        # Port-overlap base score.
        coverage = len(matched) / len(tpl_ports)
        # Name-match bonus — slug match, name word match, or description
        # word match. Word-boundary check so "plex" doesn't match "duplex".
        slug = (tpl.get("slug") or "").lower()
        name_words = {w for w in re.split(r"[\s\-_]+", (tpl.get("name") or "").lower()) if w}
        name_match = False
        match_reasons: list[str] = []
        if slug and re.search(rf"\b{re.escape(slug)}\b", host_haystack):
            name_match = True
            match_reasons.append(f"host label contains '{slug}'")
        else:
            for w in name_words:
                if len(w) < 3:
                    continue
                if re.search(rf"\b{re.escape(w)}\b", host_haystack):
                    name_match = True
                    match_reasons.append(f"host label contains '{w}'")
                    break
        # Coverage reason.
        if coverage >= 1.0:
            match_reasons.insert(0, "all template ports detected")
        elif coverage >= 0.5:
            match_reasons.insert(0, f"{len(matched)} of {len(tpl_ports)} template ports detected")
        else:
            match_reasons.insert(0, f"{len(matched)} of {len(tpl_ports)} ports detected (partial)")
        # Confidence — RANKING signal only (does not gate; see
        # min_confidence default 0.0). Every template with >=1 detected +
        # unclaimed matched port is proposed so the operator can pick
        # among all applicable apps for an open port; the generic-port
        # single-port penalty was removed on operator request (it hid
        # legitimate single-port apps like Dozzle/Forgejo on shared ports
        # such as 8080/3000). Over-mapping is still prevented downstream
        # by the claimed_ports filter — once a port is bound to an app on
        # the host it stops being offered for any other template.
        confidence = coverage
        if name_match:
            confidence = min(1.0, confidence + 0.3)
        if confidence < min_confidence:
            continue
        proposals.append({
            "catalog": tpl,
            "matched_ports": matched,
            "unmatched_ports": unmatched,
            "confidence": round(confidence, 3),
            "match_reasons": match_reasons,
            "name_match": name_match,
        })
    proposals.sort(key=lambda p: (-p["confidence"], p["catalog"]["name"].lower()))
    return proposals
