# syntax=docker/dockerfile:1.7
# TabFree extract converter (BUILD_PLAN P2.8): `tabfree-migrate convert-batch` on ECS Fargate.
#
# Build from the repository root (the build context must contain the uv workspace):
#   docker build --platform linux/amd64 -f infra/docker/converter/Dockerfile -t tabfree/converter:$(git rev-parse --short HEAD) .
#
# - linux/amd64 only: tableauhyperapi ships no Linux arm64 wheel.
# - tableauhyperapi is pinned by uv.lock (version and sha256), installed with the `hyper` and `aws` extras.
# - Runs as uid 10001 and writes only to /scratch (task ephemeral storage), so the task definition sets
#   readonlyRootFilesystem = true. hyperd writes its log into the job's work directory under /scratch.
# - Not built yet: written without Docker available. Pin the base images by digest on the first build.

ARG PYTHON_IMAGE=python:3.13.9-slim-bookworm

FROM --platform=linux/amd64 ghcr.io/astral-sh/uv:0.9.5 AS uv

FROM --platform=linux/amd64 ${PYTHON_IMAGE} AS build
COPY --from=uv /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    UV_PROJECT_ENVIRONMENT=/opt/venv
WORKDIR /src
COPY pyproject.toml uv.lock ./
COPY packages ./packages
COPY apps ./apps
RUN uv sync --frozen --no-dev --no-editable --package tabfree-migrate --extra hyper --extra aws \
 && /opt/venv/bin/python -c "import tableauhyperapi, boto3, tabfree_migrate.convert_batch"

FROM --platform=linux/amd64 ${PYTHON_IMAGE}
RUN groupadd --system --gid 10001 tabfree \
 && useradd --system --uid 10001 --gid tabfree --home-dir /scratch --no-create-home --shell /usr/sbin/nologin tabfree \
 && mkdir -p /scratch && chown 10001:10001 /scratch
COPY --from=build /opt/venv /opt/venv
ENV PATH=/opt/venv/bin:$PATH \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    HOME=/scratch \
    TMPDIR=/scratch \
    TABFREE_WORK_DIR=/scratch/work
VOLUME ["/scratch"]
WORKDIR /scratch
USER 10001:10001
ENTRYPOINT ["tabfree-migrate"]
CMD ["convert-batch", "--help"]
