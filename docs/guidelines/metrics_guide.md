# Metrics guide — OmniGrid Prometheus / Grafana setup

Companion to research proposal C1 (see `notes/notes_agent_research.txt`, "Research round —
2026-04-22"). Describes how to expose, scrape, and visualise OmniGrid's own fleet metrics.

## Status

The `/metrics` endpoint is **implemented** (see `main.py`). It serves Prometheus-formatted
metrics at:

```
http://docker.home.lan:9500/metrics
```

Backend wiring shipped with this endpoint:

- `prometheus-client` added to `requirements.txt`.
- Metric registry + collectors defined in `logic/metrics.py` (not inline in `main.py` anymore —
  the module was extracted during the `logic/` split).
- `_gather()` records `GATHER_DURATION` and calls `metrics.populate_from_cache()` at the end.
- `_get_remote_digest()` records `REGISTRY_LATENCY` / `REGISTRY_ERRORS` per host.
- `persist_history()` increments `OPS_TOTAL{op_type, status}` for every op.
- Custom collector reports `cache_age_seconds` fresh on every scrape via
  `metrics.register_cache_age_collector(...)` in `_lifespan`.
- `@app.get("/metrics")` route handler registered **above** the `StaticFiles` catch-all at `/`
  (NOT `app.mount` — Starlette's Mount only matches `/metrics/` with a trailing slash, which
  breaks default Prometheus scrapers; see [Common pitfalls](#common-pitfalls)).

The dashboard JSON (`notes/grafana_dashboard_omnigrid.json`) is ready to import; panels light up
as soon as Prometheus starts scraping.

## Port & URL matrix

| Environment            | Host port | Container port | Path       | Notes                                                        |
| ---------------------- | --------- | -------------- | ---------- | ------------------------------------------------------------ |
| Local dev (uvicorn)    | `8088`    | `8088`         | `/metrics` | `uvicorn main:app --port 8088 --reload`                      |
| Production (Swarm)     | `9500`    | `8088`         | `/metrics` | Port mapping from `docker-compose.yml` (`ports: "9500:8088"`) |

Full URLs you will actually type:

- Local dev → `http://localhost:8088/metrics`
- Inside Swarm → `http://omnigrid:8088/metrics` (service-name DNS)
- From the host → `http://docker.home.lan:9500/metrics`
- Through NPM → `https://omnigrid.home.lan/metrics` (if exposed publicly)

Pick ONE of these for Prometheus — whichever shares a network with your Prometheus instance. If
Prometheus runs on the same Swarm, prefer the service-name form (port 8088); otherwise use the
9500 form on the host.

## Metrics exported

Target schema — match these names exactly when implementing, because the Grafana dashboard
PromQL expressions are hardcoded to them. All definitions live in `logic/metrics.py`.

### Point-in-time gauges (set from within `_gather()`)

| Metric                          | Labels            | Description                                                                                                                    |
| ------------------------------- | ----------------- | ------------------------------------------------------------------------------------------------------------------------------ |
| `omnigrid_items_total`          | `status`, `type`  | Count of items per status/type. `status ∈ up-to-date|update|error|unknown|ignored`; `type ∈ service|container|orphan`. Pre-seeded to 0 for every known combination so Grafana stat panels render 0 instead of "No data". |
| `omnigrid_stack_outdated`       | `stack`           | Updates available per stack.                                                                                                   |
| `omnigrid_stack_offline`        | `stack`           | Offline items per stack.                                                                                                       |
| `omnigrid_cache_age_seconds`    | (none)            | Seconds since `_cache` was last populated. Should stay below `CACHE_TTL_SECONDS` (900). Computed at scrape time by a custom collector. |

### Counters (monotonically increasing)

| Metric                              | Labels              | Description                                                                                                          |
| ----------------------------------- | ------------------- | -------------------------------------------------------------------------------------------------------------------- |
| `omnigrid_ops_total`                | `op_type`, `status` | One-click ops performed. `op_type ∈ update_stack|update_container|restart_service|restart_container|remove_container`; `status ∈ success|error`. |
| `omnigrid_registry_errors_total`    | `registry`          | Registry probe failures per registry host.                                                                           |

### Histograms

| Metric                                  | Labels     | Description                                                                             |
| --------------------------------------- | ---------- | --------------------------------------------------------------------------------------- |
| `omnigrid_registry_latency_seconds`     | `registry` | Per-request registry HEAD/GET latency. Buckets `0.05, 0.1, 0.25, 0.5, 1, 2, 5, 10`.     |
| `omnigrid_gather_duration_seconds`      | (none)     | End-to-end `_gather()` runtime. Buckets `0.5, 1, 2, 5, 10, 30, 60, 120`.                |

## Backend wiring (for the implementer)

If you're extending the metric set or reviewing the existing wiring, these are the integration
points.

### 1. `requirements.txt`

```
prometheus-client>=0.20
```

Pure-Python, no compile step.

### 2. `logic/metrics.py` — registry + metric objects

```python
from prometheus_client import (
    CollectorRegistry, Gauge, Counter, Histogram,
    generate_latest, CONTENT_TYPE_LATEST,
)

REGISTRY = CollectorRegistry()

ITEMS_TOTAL          = Gauge('omnigrid_items_total',
                             'Items by status and type',
                             ['status', 'type'], registry=REGISTRY)
STACK_OUTDATED       = Gauge('omnigrid_stack_outdated',
                             'Outdated items per stack',
                             ['stack'], registry=REGISTRY)
STACK_OFFLINE        = Gauge('omnigrid_stack_offline',
                             'Offline items per stack',
                             ['stack'], registry=REGISTRY)
OPS_TOTAL            = Counter('omnigrid_ops_total',
                               'One-click operations performed',
                               ['op_type', 'status'], registry=REGISTRY)
REGISTRY_ERRORS      = Counter('omnigrid_registry_errors_total',
                               'Remote-registry probe failures (per registry host)',
                               ['registry'], registry=REGISTRY)
REGISTRY_LATENCY     = Histogram('omnigrid_registry_latency_seconds',
                                 'Remote-registry HEAD/GET latency',
                                 ['registry'], registry=REGISTRY,
                                 buckets=(0.05, 0.1, 0.25, 0.5, 1, 2, 5, 10))
GATHER_DURATION      = Histogram('omnigrid_gather_duration_seconds',
                                 'End-to-end _gather() duration',
                                 registry=REGISTRY,
                                 buckets=(0.5, 1, 2, 5, 10, 30, 60, 120))
```

`omnigrid_cache_age_seconds` is NOT a `Gauge` — it's a custom Collector (`_CacheAgeCollector` in
`logic/metrics.py`) that computes `time.time() - cache["ts"]` on every scrape. This avoids
stale-gauge problems between `_gather()` calls. Wire it via
`metrics.register_cache_age_collector(cache_getter)` once at startup from `_lifespan`.

### 3. Populate gauges at the end of `_gather()`

Call `metrics.populate_from_cache(cache)`. The helper clears labels first so stacks that
disappeared don't linger as stale metrics — Prometheus gauges never decay on their own. It also
pre-seeds every known `(status, type)` combination to 0 so queries always have a matching series
even when the fleet has nothing in that bucket.

### 4. Wrap `_get_remote_digest()` with timing + error counting

```python
start = time.time()
try:
    # ... existing body ...
    REGISTRY_LATENCY.labels(registry=host).observe(time.time() - start)
    return digest
except Exception:
    REGISTRY_ERRORS.labels(registry=host).inc()
    raise
```

### 5. Wrap `_gather()`

```python
with GATHER_DURATION.time():
    # ... existing body ...
```

### 6. Increment `OPS_TOTAL` in each `_do_*` handler

In the `finally` block, with `status='success'` or `status='error'`.

### 7. Use `@app.get("/metrics")`, NOT `app.mount("/metrics", ...)`

This was the very first bug in the real wiring:

```python
# WRONG — Starlette's Mount requires a trailing slash. Prometheus
# scrapers hit `/metrics` without one, fall through to the StaticFiles
# catch-all, and get FastAPI's {"detail":"Not Found"} back.
app.mount("/metrics", make_asgi_app(registry=REG))   # DON'T
```

```python
# RIGHT — a plain route handler responds to bare /metrics.
from fastapi.responses import Response
from logic import metrics

@app.get("/metrics")
async def prometheus_metrics():
    return Response(
        content=metrics.generate_latest(metrics.REGISTRY),
        media_type=metrics.CONTENT_TYPE_LATEST,
    )
```

It still needs to be registered **before** the `StaticFiles` catch-all at the bottom of `main.py`,
or it never gets reached. See `CLAUDE.md` "Conventions worth knowing" for both rules.

## Prometheus scrape config

Add this job to your `prometheus.yml`. Pick the target form that matches where Prometheus runs
relative to OmniGrid.

```yaml
scrape_configs:
  - job_name: omnigrid
    metrics_path: /metrics
    scrape_interval: 30s
    scrape_timeout: 10s
    static_configs:
      - targets:
          # Same Swarm overlay network:
          - omnigrid:8088
          # OR — Prometheus outside the Swarm, host-port form:
          # - docker.home.lan:9500
          # OR — behind NPM (HTTPS):
          # - omnigrid.home.lan
        labels:
          env: home-lab
          app: omnigrid
```

Alternate job name used in the homelab deploy:

```yaml
  - job_name: '42_omnigrid'
    metrics_path: /metrics
    scrape_interval: 30s
    scrape_timeout: 10s
    static_configs:
      - targets:
          - docker.home.lan:9500
        labels:
          env: home-lab
          app: omnigrid
```

Then reload:

```bash
sudo systemctl restart prometheus
```

If going through NPM / HTTPS, add:

```yaml
scheme: https
tls_config:
  insecure_skip_verify: true   # only if your NPM uses a self-signed cert
```

Reload Prometheus (`curl -X POST http://prometheus:9090/-/reload` or SIGHUP) and confirm the
target is UP at:

```
http://<prometheus-host>:9090/targets
```

Also sanity-check the metrics parse:

```bash
curl http://docker.home.lan:9500/metrics | head -40
```

You should see `HELP` / `TYPE` lines for the metrics listed above.

## Grafana — data source + dashboard import

1. Grafana → Connections → Data sources → Add data source → Prometheus. Set URL to
   `http://prometheus:9090` (or wherever Prometheus lives). Save & Test.
2. Grafana → Dashboards → New → Import → Upload JSON file. Use
   `notes/grafana_dashboard_omnigrid.json` (already in this repo). When prompted:
   - Name: leave as `OmniGrid — Fleet Status`.
   - UID: leave as `omnigrid-fleet` (or change to avoid collision).
   - DS_PROMETHEUS: pick the data source you just created.
3. First panels you should see light up (in order of how fast their metrics appear):
   - "Up-to-date / Updates / Errors / Unknown" stat panels — as soon as the first `_gather()`
     completes.
   - "Cache age" — second tick after that.
   - Histograms (Gather duration, Registry latency) — after ~5m of scrapes so
     `histogram_quantile()` has enough buckets.
   - "Operations rate" — only after you trigger update/restart/remove actions.

## Validation checklist

- [ ] `curl http://<host>:<port>/metrics` returns `text/plain` and contains
      `omnigrid_items_total`.
- [ ] Prometheus `/targets` shows the `omnigrid` job as UP.
- [ ] Grafana panel "Up-to-date" is not N/A.
- [ ] `omnigrid_cache_age_seconds` drops to ~0 right after a manual refresh and climbs back up
      afterwards.
- [ ] Trigger a bulk restart in the UI — the "Operations rate" panel gains a bar within the
      scrape interval.

## Common pitfalls

1. **"No data" in every panel, `/metrics` returns HTML.** The `/metrics` mount is AFTER
   `app.mount("/", StaticFiles(...))`. Move it **before** that line in `main.py`. See step 7 in
   [Backend wiring](#7-use-appgetmetrics-not-appmountmetrics-).
2. **Histograms empty even after many requests.** Check that the handler wraps the whole call.
   `GATHER_DURATION.time()` must be used as a context manager, not a decorator on the async
   function (unless wrapping with a sync helper).
3. **Labels like `stack="foo"` disappear.** The `.clear()` call at the top of the populate block
   resets ALL labels; make sure it runs BEFORE the re-population loop, not after. Without
   `.clear()`, removed stacks linger in Prometheus as stale metrics (gauges never decay on their
   own).
4. **`docker stats`-style numbers don't match Grafana.** Expected. `/metrics` exports OmniGrid's
   VIEW of the fleet; the live stats are in `_gather_stats()` and are NOT exported (yet). They
   may be added later as `omnigrid_container_cpu_percent` / `omnigrid_container_mem_bytes` — see
   a future research round.
5. **Prometheus complains about cardinality explosion.** The only metrics with unbounded labels
   are `*{stack}` and `*{registry}`. If you have hundreds of stacks, consider dropping the
   `stack` label from the gauges and exposing stack-level data only via a separate
   `/api/stacks.csv` endpoint.

## Related files

- `notes/notes_agent_research.txt` — proposal C1 (this metric set).
- `notes/grafana_dashboard_omnigrid.json` — ready-to-import dashboard.
- `logic/metrics.py` — registry + metric objects + `populate_from_cache()` +
  `register_cache_age_collector()`.
- `main.py` — `@app.get("/metrics")` handler + `_lifespan` wiring.
- `CLAUDE.md` → "Conventions worth knowing" — mount-order rule.
