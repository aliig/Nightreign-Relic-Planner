# ---------------------------------------------------------------------------
# Stage 1: build the Rust solver wheel.
#
# Kept in its own stage, before any Python source is copied, so a change to
# nrplanner or the backend never re-runs cargo.  abi3-py312 means one wheel
# serves every Python >= 3.12.
# ---------------------------------------------------------------------------
FROM ghcr.io/pyo3/maturin:v1.9.1 AS rust-builder

WORKDIR /build
COPY ./crates/nrplanner_core /build/crates/nrplanner_core

RUN --mount=type=cache,target=/root/.cargo/registry \
    --mount=type=cache,target=/build/crates/nrplanner_core/target \
    maturin build --release --manylinux 2_28 --interpreter python3.12 \
        -m crates/nrplanner_core/Cargo.toml -o /wheels

# ---------------------------------------------------------------------------
# Stage 2: the application image.
# ---------------------------------------------------------------------------
FROM python:3.12

ENV PYTHONUNBUFFERED=1

# Install uv
# Ref: https://docs.astral.sh/uv/guides/integration/docker/#installing-uv
COPY --from=ghcr.io/astral-sh/uv:0.9.26 /uv /uvx /bin/

# Compile bytecode
# Ref: https://docs.astral.sh/uv/guides/integration/docker/#compiling-bytecode
ENV UV_COMPILE_BYTECODE=1

# uv Cache
# Ref: https://docs.astral.sh/uv/guides/integration/docker/#caching
ENV UV_LINK_MODE=copy

WORKDIR /app/

# Place executables in the environment at the front of the path
# Ref: https://docs.astral.sh/uv/guides/integration/docker/#using-the-environment
ENV PATH="/app/.venv/bin:$PATH"

# Install dependencies
# Ref: https://docs.astral.sh/uv/guides/integration/docker/#intermediate-layers
RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=uv.lock,target=uv.lock \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    uv sync --frozen --no-install-workspace --package app

COPY ./backend/scripts /app/backend/scripts

COPY ./backend/pyproject.toml ./backend/alembic.ini /app/backend/

COPY ./backend/app /app/backend/app

COPY ./nrplanner /app/nrplanner

# Sync the project
# Ref: https://docs.astral.sh/uv/guides/integration/docker/#intermediate-layers
RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=uv.lock,target=uv.lock \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    uv sync --frozen --package app --no-install-package nrplanner-core

# Install the prebuilt solver wheel.  --no-deps because uv already resolved
# and installed everything it needs.
COPY --from=rust-builder /wheels /wheels
RUN --mount=type=cache,target=/root/.cache/uv \
    uv pip install --no-deps /wheels/nrplanner_core-*.whl

WORKDIR /app/backend/

ENV PYTHONPATH=/app

CMD ["fastapi", "run", "--workers", "4", "app/main.py"]
