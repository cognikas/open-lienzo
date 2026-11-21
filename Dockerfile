# syntax=docker/dockerfile:1
# open-lienzo: local viewer + sync scripts for claude.ai artifacts.
#   docker build --target test .                    run the unit tests (Linux)
#   docker build --target runner -t open-lienzo .   the image that compose.yaml runs
# The image carries no artifacts and none of Anthropic's code (the Design runtime, dc-runtime.js):
# both live in the mounted /data folder, which each user fills from their own claude.ai account.

# ---- base: python:3.13-slim pinned by its multi-arch index digest (amd64 + arm64) ----------------------
# Written as a plain FROM (not an ARG) so Dependabot can bump the digest.
FROM python:3.13-slim@sha256:e48f59cdba69a3cbd9ed2cf0f348d90d45a8d350d98134cf1070bc106ee9ed90 AS base

# ---- build: sources + bytecode -------------------------------------------------------------------
FROM base AS build
WORKDIR /app
COPY LICENSE README.md ./
COPY viewer/ viewer/
COPY sync/ sync/
COPY docker/ docker/
COPY hooks/ hooks/
COPY tests/ tests/
RUN python -m compileall -q /app

# ---- test: the unit tests, only when targeted ------------------------------------------------------
FROM build AS test
RUN PYTHONUTF8=1 python -m unittest discover -s /app/tests

# ---- runner: what ships ---------------------------------------------------------------------------
FROM base AS runner
ARG VERSION=dev
ARG REVISION=unknown
LABEL org.opencontainers.image.title="open-lienzo" \
      org.opencontainers.image.description="Local viewer and sync for claude.ai artifacts (Design canvas, Slides, pages with a local db)" \
      org.opencontainers.image.source="https://github.com/cognikas/open-lienzo" \
      org.opencontainers.image.vendor="Cognikas" \
      org.opencontainers.image.licenses="MIT" \
      org.opencontainers.image.version="${VERSION}" \
      org.opencontainers.image.revision="${REVISION}"

# git: sync.py history and dedupe.py read the artifacts repo; ripgrep: entities.py looks for links in a workspace.
# pip goes: open-lienzo is stdlib only, and pip's vendored libraries (urllib3, msgpack…) are what scanners flag.
# upgrade: Debian security fixes land before the next base digest bump (Trivy's HIGH gate stays green).
RUN apt-get update \
 && apt-get upgrade -y --no-install-recommends \
 && apt-get install -y --no-install-recommends git ripgrep \
 && rm -rf /var/lib/apt/lists/* \
 && python -m pip uninstall -y -q pip \
 && rm -rf /usr/local/lib/python3.13/ensurepip /usr/local/bin/pip* \
 && git config --system safe.directory '*' \
 && groupadd --gid 10001 lienzo \
 && useradd --uid 10001 --gid 10001 --no-create-home --home-dir /nonexistent --shell /usr/sbin/nologin lienzo \
 && mkdir -p /data && chown 10001:10001 /data

COPY --from=build /app /app

ENV PYTHONUTF8=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    LIENZO_HOST=0.0.0.0 \
    LIENZO_ROOT=/data \
    LIENZO_ACCOUNTS=/data/accounts.json

USER 10001:10001
WORKDIR /data
VOLUME ["/data"]
EXPOSE 8765

HEALTHCHECK --interval=30s --timeout=3s --start-period=10s --retries=3 \
  CMD ["python", "-c", "import sys, urllib.request; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8765/_health', timeout=2).status == 200 else 1)"]

ENTRYPOINT ["python", "/app/docker/entrypoint.py"]
CMD ["serve"]
