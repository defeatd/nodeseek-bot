# syntax=docker/dockerfile:1

# Alpine runtime image (no Playwright). This image supports HTTP fulltext fetching.
# Browser fallback is NOT included; set ALLOW_BROWSER_FALLBACK=false.

ARG PYTHON_VERSION=3.11

FROM python:${PYTHON_VERSION}-alpine AS builder

# Build deps for wheels (selectolax has native extensions)
RUN apk add --no-cache \
    build-base \
    python3-dev \
    musl-dev \
    libffi-dev \
    openssl-dev

WORKDIR /wheels

COPY requirements.txt ./
RUN python -m pip install --upgrade pip \
  && pip wheel --no-cache-dir --wheel-dir /wheels -r requirements.txt


FROM python:${PYTHON_VERSION}-alpine AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# Runtime deps for compiled wheels
RUN apk add --no-cache \
    libstdc++ \
    libgcc \
    libffi \
    openssl \
  && addgroup -S app \
  && adduser -S -G app app

WORKDIR /app

COPY --from=builder /wheels /wheels
COPY requirements.txt ./
RUN python -m pip install --no-cache-dir --no-index --find-links=/wheels -r requirements.txt \
  && rm -rf /wheels

COPY nodeseek_bot ./nodeseek_bot
COPY rules ./rules
COPY README.md ./README.md

# Create writable dirs (can be bind-mounted)
RUN mkdir -p /app/data /app/logs \
  && chown -R app:app /app

USER app

# Default metrics bind for containers (can be overridden by env)
ENV METRICS_BIND=0.0.0.0 \
    ALLOW_BROWSER_FALLBACK=false

EXPOSE 9108

CMD ["python", "-m", "nodeseek_bot"]
