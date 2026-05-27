"""Typed enum of every env var OmniGrid reads at the bare ``os.getenv``
boundary — i.e. env vars that are NOT routed through TUNABLES.

Third leg of the typed-key family:

  * :class:`logic.settings_keys.Settings` — plain ``settings`` table keys.
  * :class:`logic.tuning.Tunable` — TUNABLES keys (each TUNABLES entry
    carries its own env-var name in the second axis of the tuple; those
    env-var names are owned by ``TUNABLES`` and DO NOT appear here).
  * :class:`EnvKey` (this module) — env-only values with no TUNABLES row:
    secrets (``SESSION_SECRET``), bootstrap seeds (``BOOTSTRAP_ADMIN_*``,
    the legacy ``PORTAINER_*`` first-boot seeds), build-time paths
    (``DB_PATH``, ``ENV_FILE_PATH``, ``LOG_DIR``), and registry creds
    (``GITHUB_TOKEN``, ``DOCKERHUB_USER``, ``DOCKERHUB_TOKEN``).

Why a typed enum: pre-fix every call site wrote the literal env-var name
inline (``os.getenv("DOCKERHUB_TOKEN", "")``). A typo
(``"DOCKHERUB_TOKEN"``) silently returns the default with no exception
at runtime. Editing ``EnvKey.DOCKERHUB_TOKEN`` triggers ``NameError`` at
import time, not a silent miss at runtime. Same drift-detection
guarantee as the ``Settings`` + ``Tunable`` enums.

Members inherit ``str`` so call sites can pass either the enum or its
string value transparently:
``EnvKey.GITHUB_TOKEN == "GITHUB_TOKEN"`` is true, ``hash()`` agrees,
and ``os.getenv(EnvKey.X.value)`` works without explicit conversion.

Naming: matches the literal env-var name exactly. So
``os.getenv("GITHUB_TOKEN", "")`` becomes
``env_get(EnvKey.GITHUB_TOKEN)``.

Adding a new env var: declare a member here (alphabetical-by-value sort
for tidy diffs), route every ``os.getenv`` site through ``env_get``,
add a corresponding entry to ``docs/guidelines/env_example.md`` so the
operator-facing reference stays current.

What does NOT belong here: env vars OmniGrid does NOT itself read
(``TZ`` is consumed by libc, not by OmniGrid code); env vars covered by
TUNABLES (every ``tuning_*`` knob's bootstrap env-var name lives in
``TUNABLES[key][0]`` — duplicating them here would be a drift class).
"""

import os
from enum import Enum


class EnvKey(str, Enum):
    """Typed env-key registry — see module docstring.

    Members sorted alphabetically by string value for stable diffs.
    """
    BOOTSTRAP_ADMIN_PASSWORD = "BOOTSTRAP_ADMIN_PASSWORD"
    BOOTSTRAP_ADMIN_USER = "BOOTSTRAP_ADMIN_USER"
    DB_BUSY_TIMEOUT_MS = "DB_BUSY_TIMEOUT_MS"
    DB_PATH = "DB_PATH"
    DB_TYPE = "DB_TYPE"
    DB_WAL_ENABLED = "DB_WAL_ENABLED"
    DOCKERHUB_TOKEN = "DOCKERHUB_TOKEN"
    DOCKERHUB_USER = "DOCKERHUB_USER"
    ENV_FILE_PATH = "ENV_FILE_PATH"
    GITHUB_TOKEN = "GITHUB_TOKEN"
    LOG_DIR = "LOG_DIR"
    PORTAINER_API_KEY = "PORTAINER_API_KEY"
    PORTAINER_ENDPOINT_ID = "PORTAINER_ENDPOINT_ID"
    PORTAINER_URL = "PORTAINER_URL"
    SESSION_LAST_SEEN_THROTTLE_SECONDS = "SESSION_LAST_SEEN_THROTTLE_SECONDS"
    SESSION_SECRET = "SESSION_SECRET"
    VERIFY_TLS = "VERIFY_TLS"


def env_get(key: EnvKey, default: str = "") -> str:
    """Typed env-var reader. Wraps ``os.getenv`` with the :class:`EnvKey`
    enum so a typo'd literal can't silently read empty at runtime.

    Returns ``default`` (empty string by default) when the env var is
    unset. Identical semantics to ``os.getenv(name, default)`` — an
    empty-string-VALUED env var still returns the empty string (NOT the
    default); callers wanting "treat empty as missing" pair this with
    ``or fallback`` at the call site (see e.g. ``logic.db.DB_TYPE``).
    """
    return os.getenv(key.value, default)
