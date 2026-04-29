# OmniGrid container image — single-stage, baked-in deps + source.
#
# Build context expects: requirements.txt + main.py + logic/ + static/ +
# node_modules/ at the repo root. .dockerignore strips dev-only files
# (CLAUDE.md, notes/, tests/, .claude/, .git, etc.) so they never enter
# the image.
#
# Build:
#   docker build --build-arg VERSION=1.2.3 -t omnigrid:1.2.3 -t omnigrid:latest .
#
# The pipeline reads the previous version from /api/version, increments
# PATCH, and passes the result via --build-arg VERSION. Local builds
# without the arg fall back to "0.0.0-dev" (visible signal in the UI).

FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Python deps in their own layer so source-only changes hit the cache.
# python:3.12-slim ships pre-built wheels for every dep we currently
# pin (cryptography / bcrypt / asyncssh / icmplib all have arm64 +
# x86_64 wheels on PyPI). If a future dep needs to compile from source,
# add a transient build-deps block here:
#   RUN apt-get update && apt-get install -y --no-install-recommends \
#         build-essential libffi-dev libssl-dev \
#       && pip install -r requirements.txt \
#       && apt-get purge -y build-essential libffi-dev libssl-dev \
#       && apt-get autoremove -y && rm -rf /var/lib/apt/lists/*
COPY requirements.txt /app/requirements.txt
RUN pip install --upgrade pip \
 && pip install -r /app/requirements.txt

# Source. .dockerignore filters dev-only files BEFORE this COPY runs.
COPY . /app

# Version baked at build time. Pipeline reads previous version from
# /api/version, increments PATCH, passes here as --build-arg VERSION=$NEW.
# Falls back to "0.0.0-dev" for local / unversioned builds.
ARG VERSION=0.0.0-dev
RUN echo "$VERSION" > /app/VERSION.txt

LABEL org.opencontainers.image.title="OmniGrid" \
      org.opencontainers.image.version="$VERSION" \
      org.opencontainers.image.source="https://git.www.home.lan/m.a.raouf/OmniGrid" \
      org.opencontainers.image.description="Portainer-native update + management dashboard for Docker Swarm"

EXPOSE 8088

# Container-level healthcheck. Swarm reads the compose-level healthcheck
# in production (which is identical to this), but keeping this here means
# `docker run` outside Swarm gets the same liveness contract.
HEALTHCHECK --interval=30s --timeout=5s --start-period=60s --retries=3 \
  CMD python -c "import urllib.request;urllib.request.urlopen('http://localhost:8088/api/healthz',timeout=3)" || exit 1

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8088", "--workers", "1"]
