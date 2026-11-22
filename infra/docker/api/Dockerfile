# syntax=docker/dockerfile:1.7
# TabFree API image (BUILD_PLAN P7.5): the API, the portal and the dashboard runtime in one image.
# The same image runs the job worker: `python -m tabfree_api.jobs worker --scheduler`.
#
# Build from the repository root:
#   docker build -f infra/docker/api/Dockerfile -t tabfree/api:$(git rev-parse --short HEAD) .
#   docker build -f infra/docker/api/Dockerfile --build-arg EXTRAS="aws ai render" ...   # with Chromium
#
# - Runs as uid 10001; writes only to /tmp, so the task definition can set readonlyRootFilesystem.
# - Extras: aws (Athena, Redshift, S3, SES), ai (Claude API / Bedrock), render (Playwright: PNG/PDF
#   exports, subscriptions). The render extra also installs Chromium and its system libraries.
# - Not built yet: written without Docker available. Pin the base images by digest on the first build;
#   the release workflow signs the image and attaches an SBOM (P7.3).

ARG PYTHON_IMAGE=python:3.13.9-slim-bookworm
ARG NODE_IMAGE=node:24.11.1-bookworm-slim

FROM ${NODE_IMAGE} AS web
WORKDIR /src
COPY package.json package-lock.json ./
COPY packages/tablecalc/package.json packages/tablecalc/
COPY packages/runtime/package.json packages/runtime/
COPY apps/portal/package.json apps/portal/
RUN npm ci --ignore-scripts
COPY tsconfig*.json ./
COPY packages/tablecalc packages/tablecalc
COPY packages/runtime packages/runtime
COPY apps/portal apps/portal
RUN npm run build -w @tabfree/runtime && npm run build -w @tabfree/portal

FROM ghcr.io/astral-sh/uv:0.9.5 AS uv

FROM ${PYTHON_IMAGE} AS build
ARG EXTRAS="aws ai"
COPY --from=uv /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never UV_PROJECT_ENVIRONMENT=/opt/venv
WORKDIR /src
COPY pyproject.toml uv.lock ./
COPY packages ./packages
COPY apps ./apps
RUN uv sync --frozen --no-dev --no-editable --package tabfree-api $(for e in ${EXTRAS}; do printf -- '--extra %s ' "$e"; done) \
 && /opt/venv/bin/python -c "import tabfree_api.main"

FROM ${PYTHON_IMAGE}
ARG EXTRAS="aws ai"
RUN groupadd --system --gid 10001 tabfree \
 && useradd --system --uid 10001 --gid tabfree --home-dir /tmp --no-create-home --shell /usr/sbin/nologin tabfree
COPY --from=build /opt/venv /opt/venv
COPY --from=web /src/packages/runtime/dist/bundle /opt/tabfree/runtime
COPY --from=web /src/apps/portal/dist /opt/tabfree/portal
ENV PATH=/opt/venv/bin:$PATH \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    HOME=/tmp \
    PLAYWRIGHT_BROWSERS_PATH=/opt/playwright \
    TABFREE_RUNTIME_DIR=/opt/tabfree/runtime \
    TABFREE_PORTAL_DIR=/opt/tabfree/portal \
    TABFREE_SESSION_DB=/tmp/sessions.db
RUN case " ${EXTRAS} " in *" render "*) playwright install --with-deps chromium && rm -rf /var/lib/apt/lists/* ;; esac
USER 10001:10001
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=4)"]
CMD ["uvicorn", "tabfree_api.main:app_factory", "--factory", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--forwarded-allow-ips", "*"]
