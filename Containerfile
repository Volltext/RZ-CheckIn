# Ein einzelner Container für die gesamte Anwendung (FastAPI + SQLite-Datei auf einem
# gemounteten Volume) -- siehe README.md Abschnitt "Deployment" und
# deploy/rz-checkin.container für die Podman-Quadlet-Unit.

FROM python:3.12-slim

RUN useradd --create-home --uid 10001 rzcheckin
WORKDIR /app

# Erst nur die Metadaten kopieren, damit der Dependency-Layer bei reinen Code-Änderungen
# aus dem Cache kommt.
COPY pyproject.toml ./
COPY app ./app
RUN pip install --no-cache-dir .

# SQLite-Datei lebt auf einem gemounteten Volume, damit sie Updates/Neustarts übersteht.
RUN mkdir -p /data && chown rzcheckin:rzcheckin /data
VOLUME ["/data"]
ENV RZ_DATABASE_PATH=/data/rz-checkin.db

USER rzcheckin
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request as u; u.urlopen('http://127.0.0.1:8000/health', timeout=3)" || exit 1

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
