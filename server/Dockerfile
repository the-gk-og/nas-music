# nas-music server image.
#   docker build -t nasmusic:latest server/
#   docker build --build-arg WITH_ANALYSIS=1 -t nasmusic:latest server/   # + Essentia analysis extra
# Runs as uid/gid 568 (TrueNAS "apps"). Expects /data (rw: config.toml, db, cert, caches) and /music (ro).

ARG PYTHON_IMAGE=python:3.12-slim-bookworm

# ---------- build: resolve the locked deps into /opt/venv ----------
FROM ${PYTHON_IMAGE} AS build
ARG WITH_ANALYSIS=0
COPY --from=ghcr.io/astral-sh/uv:0.13.8 /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    UV_PROJECT_ENVIRONMENT=/opt/venv
WORKDIR /src
# Dependencies first (cached layer), then the project itself.
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    EXTRA=""; if [ "$WITH_ANALYSIS" = "1" ]; then EXTRA="--extra analysis"; fi; \
    uv sync --frozen --no-dev --no-install-project $EXTRA
COPY nasmusic ./nasmusic
RUN --mount=type=cache,target=/root/.cache/uv \
    EXTRA=""; if [ "$WITH_ANALYSIS" = "1" ]; then EXTRA="--extra analysis"; fi; \
    uv sync --frozen --no-dev --no-editable $EXTRA

# ---------- runtime ----------
FROM ${PYTHON_IMAGE}
RUN apt-get update \
 && apt-get install -y --no-install-recommends ffmpeg tzdata \
 && rm -rf /var/lib/apt/lists/* \
 && groupadd --gid 568 apps \
 && useradd --uid 568 --gid 568 --no-create-home --home-dir /data --shell /usr/sbin/nologin apps \
 && mkdir -p /data /music \
 && chown 568:568 /data
COPY --from=build /opt/venv /opt/venv
COPY config.example.toml /opt/nasmusic/config.example.toml
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
