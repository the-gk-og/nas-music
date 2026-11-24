# nas-music server image, including the web player. Build from the REPO ROOT (BuildKit required):
#   docker build -f server/Dockerfile -t nasmusic:latest .
#   docker build -f server/Dockerfile --build-arg WITH_ANALYSIS=1 -t nasmusic:latest .   # + Essentia extra
#   docker build -f server/Dockerfile --build-arg WITH_DJVOICE=0 -t nasmusic:latest .    # without the radio DJ voice
# The build context is filtered by server/Dockerfile.dockerignore.
# Runs as uid/gid 568 (TrueNAS "apps"). Expects /data (rw: config.toml, db, cert, caches) and /music (ro).

ARG PYTHON_IMAGE=python:3.12-slim-bookworm
ARG NODE_IMAGE=node:22-bookworm-slim

# ---------- web player: web/ -> /out (empty when the repo has no web/ yet; the server then shows a placeholder)
FROM ${NODE_IMAGE} AS web
WORKDIR /web
RUN --mount=type=bind,source=.,target=/ctx \
    --mount=type=cache,target=/root/.npm \
    mkdir -p /out; \
    if [ -f /ctx/web/package.json ]; then \
      cp -a /ctx/web/. /web/ && npm ci --no-audit --no-fund && npm run build && cp -a dist/. /out/; \
    else \
      echo "web/package.json not found: building the server without the web player"; \
    fi

# ---------- build: resolve the locked deps into /opt/venv ----------
FROM ${PYTHON_IMAGE} AS build
ARG WITH_ANALYSIS=0
# Radio DJ voice (Piper TTS, ~50 MB). The voice model itself downloads into /data on first use.
ARG WITH_DJVOICE=1
COPY --from=ghcr.io/astral-sh/uv:0.13.8 /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    UV_PROJECT_ENVIRONMENT=/opt/venv
WORKDIR /src
# Dependencies first (cached layer), then the project itself.
COPY server/pyproject.toml server/uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    EXTRA=""; if [ "$WITH_ANALYSIS" = "1" ]; then EXTRA="--extra analysis"; fi; \
    if [ "$WITH_DJVOICE" = "1" ]; then EXTRA="$EXTRA --extra djvoice"; fi; \
    uv sync --frozen --no-dev --no-install-project $EXTRA
COPY server/nasmusic ./nasmusic
RUN --mount=type=cache,target=/root/.cache/uv \
    EXTRA=""; if [ "$WITH_ANALYSIS" = "1" ]; then EXTRA="--extra analysis"; fi; \
    if [ "$WITH_DJVOICE" = "1" ]; then EXTRA="$EXTRA --extra djvoice"; fi; \
    uv sync --frozen --no-dev --no-editable $EXTRA

# ---------- runtime ----------
# libchromaprint-tools = fpcalc (Chromaprint, LGPL-2.1+), run as a separate process for automatic tag fixing.
FROM ${PYTHON_IMAGE}
RUN apt-get update \
 && apt-get install -y --no-install-recommends ffmpeg libchromaprint-tools tzdata \
 && rm -rf /var/lib/apt/lists/* \
 && groupadd --gid 568 apps \
 && useradd --uid 568 --gid 568 --no-create-home --home-dir /data --shell /usr/sbin/nologin apps \
 && mkdir -p /data /music \
 && chown 568:568 /data
COPY --from=build /opt/venv /opt/venv
COPY server/config.example.toml /opt/nasmusic/config.example.toml
COPY --from=web /out /opt/nasmusic/web
ENV PATH=/opt/venv/bin:$PATH \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    NASMUSIC_CONFIG=/data/config.toml
USER 568:568
WORKDIR /data
EXPOSE 8443 8080
HEALTHCHECK --interval=60s --timeout=5s --start-period=30s --retries=3 \
    CMD ["python", "-c", "import ssl,urllib.request; urllib.request.urlopen('https://127.0.0.1:8443/api/v1/health', timeout=4, context=ssl._create_unverified_context())"]
CMD ["nasmusic", "serve"]
