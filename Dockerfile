FROM python:3.12-slim AS runtime
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
WORKDIR /app
RUN addgroup --system iagis && adduser --system --ingroup iagis --home /app iagis \
    && mkdir -p /data /reports && chown -R iagis:iagis /app /data /reports
COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install --no-cache-dir .
USER iagis
VOLUME ["/data", "/reports"]
HEALTHCHECK --interval=30s --timeout=10s --start-period=20s --retries=3 \
  CMD python -m iagis.cli check >/dev/null || exit 1
ENTRYPOINT ["python", "-m", "iagis.cli"]
CMD ["worker", "--entity-id", "0"]
