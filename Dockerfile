FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

COPY pyproject.toml README.md ./
COPY src ./src

RUN pip install --no-cache-dir . \
    && groupadd --gid 10001 tpby \
    && useradd --uid 10001 --gid tpby --create-home --shell /usr/sbin/nologin tpby \
    && mkdir -p /app/data/media /app/sessions \
    && chown -R tpby:tpby /app/data /app/sessions

USER tpby

VOLUME ["/app/data", "/app/sessions"]

ENTRYPOINT ["tpby"]
