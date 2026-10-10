# syntax=docker/dockerfile:1
# Reviewed digest-pinned official images must be supplied; this file does not download weights.
ARG NODE_IMAGE
ARG PYTHON_IMAGE
FROM ${NODE_IMAGE} AS frontend
WORKDIR /build
RUN npm install --global pnpm@11.19.0
COPY frontend/package.json frontend/pnpm-lock.yaml ./
RUN pnpm install --frozen-lockfile
COPY frontend/index.html frontend/tsconfig.json frontend/vite.config.ts ./
COPY frontend/src ./src
RUN pnpm build

FROM ${PYTHON_IMAGE} AS console
WORKDIR /app
ENV PYTHONUTF8=1 PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PYTHONPATH=/app/backend TMP=/app/tmp TEMP=/app/tmp
COPY requirements.runtime.lock.txt ./
RUN pip install --no-cache-dir -r requirements.runtime.lock.txt
COPY pyproject.toml ./
COPY backend ./backend
RUN pip install --no-cache-dir --no-deps . && useradd --uid 10001 --create-home console \
    && mkdir -p state tmp reports && chown -R console:console state tmp reports
COPY profiles ./profiles
COPY data ./data
COPY schemas ./schemas
COPY docs/licenses ./docs/licenses
COPY --from=frontend /build/dist ./frontend/dist
USER 10001:10001
EXPOSE 8120
HEALTHCHECK --interval=10s --timeout=3s --start-period=20s --retries=5 \
    CMD python -c "import json,urllib.request; r=urllib.request.urlopen('http://127.0.0.1:8120/api/setup',timeout=2); assert r.status==200 and json.load(r)['version']=='0.3.0'"
CMD ["python", "-m", "uvicorn", "console_app.app:factory", "--factory", "--host", "0.0.0.0", "--port", "8120", "--no-access-log"]
