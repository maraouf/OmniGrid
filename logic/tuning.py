"""Three-tier tunable resolver — DB setting > env var > code default.

Each tunable currently shipped as an env-only constant gets a parallel
DB setting (``tuning_<lowercase_env_var>``); the resolver picks the
non-blank DB value first, falls back to the env var, then to the
hardcoded default. The DB value can be edited live from Admin → Config
without a restart — every consumer calls ``tuning_int(...)`` at the
point of use rather than caching it at module import.
"""
import os

from logic.db import get_setting


# Authoritative table of (db_key, env_var, default, min, max). The UI
# editor + the validator + the resolver all reference this single
# source of truth — adding a new knob means one edit here + extending
# the SettingsIn model + one row in the Admin → Config form.
TUNABLES: dict[str, tuple[str, int, int, int]] = {
    "tuning_cache_ttl_seconds":             ("CACHE_TTL_SECONDS",            900, 30,  86400),
    "tuning_stats_cache_ttl_seconds":       ("STATS_CACHE_TTL_SECONDS",       30,  5,  3600),
    "tuning_registry_concurrency":          ("REGISTRY_CONCURRENCY",           8,  1,  64),
    "tuning_stats_concurrency":             ("STATS_CONCURRENCY",             16,  1,  128),
    "tuning_stats_history_days":            ("STATS_HISTORY_DAYS",             7,  1,  365),
    "tuning_stats_sample_interval_seconds": ("STATS_SAMPLE_INTERVAL_SECONDS", 300, 30,  3600),
}


def tuning_int(db_key: str) -> int:
    """Return the effective value for one tunable. Three-tier: DB > env > default."""
    if db_key not in TUNABLES:
        raise KeyError(f"unknown tunable: {db_key}")
    env_var, default, _lo, _hi = TUNABLES[db_key]
    try:
        raw = (get_setting(db_key, "") or "").strip()
    except Exception:
        # DB unreachable (config-error boot path) — skip straight to env.
        raw = ""
    if raw:
        try:
            return int(raw)
        except ValueError:
            pass
    env_raw = os.getenv(env_var, "")
    if env_raw:
        try:
            return int(env_raw)
        except ValueError:
            pass
    return default


def effective_state() -> dict:
    """Return current effective values + which tier each came from. Used by
    the GET endpoint so the UI can render placeholders showing the env
    fallback and the code default behind the DB override.
    """
    out: dict = {}
    for k, (env_var, default, lo, hi) in TUNABLES.items():
        try:
            raw_db = (get_setting(k, "") or "").strip()
        except Exception:
            raw_db = ""
        env_raw = os.getenv(env_var, "")
        out[k] = {
            "db":        raw_db,
            "env":       env_raw,
            "default":   default,
            "effective": tuning_int(k),
            "min":       lo,
            "max":       hi,
            "env_var":   env_var,
        }
    return out


if __name__ == "__main__":
    # Smoke: tuning_int returns env value when DB is blank, code default
    # when both are blank. DB lookup is best-effort — when DB_PATH is
    # unset in the dev shell, the resolver still falls through to env /
    # default cleanly.
    assert tuning_int("tuning_cache_ttl_seconds") > 0
    assert tuning_int("tuning_stats_concurrency") > 0
    print("tuning smoke passed")
