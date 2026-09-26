# megane-builder-tools as a Streamable HTTP service (App Runner: deploy/terraform).
# Build for x86_64, which is what App Runner runs:
#   docker build --platform linux/amd64 -t megane-builder-tools .

FROM python:3.12-slim AS build
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never
RUN pip install --no-cache-dir "uv>=0.8,<0.9"
WORKDIR /app
COPY pyproject.toml uv.lock README.md LICENSE ./
RUN uv sync --frozen --no-dev --no-install-project
COPY src ./src
RUN uv sync --frozen --no-dev --no-editable

FROM python:3.12-slim
# RDKit's wheel links libXrender / libXext for its drawing code.
RUN apt-get update \
    && apt-get install -y --no-install-recommends libxrender1 libxext6 \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --create-home --uid 10001 app
COPY --from=build --chown=app:app /app/.venv /app/.venv
USER app
ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PORT=8080 \
    MEGANE_BUILDER_TOOLS_TRANSPORT=http \
    MEGANE_BUILDER_TOOLS_HOST=0.0.0.0 \
    MEGANE_BUILDER_TOOLS_STATELESS=1 \
    MEGANE_BUILDER_TOOLS_CALL_TIMEOUT=100 \
    MEGANE_BUILDER_TOOLS_MAX_CONCURRENCY=2
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=5s CMD python -c "import urllib.request,os; urllib.request.urlopen(f'http://127.0.0.1:{os.environ[\"PORT\"]}/health')"
CMD ["megane-builder-tools"]
