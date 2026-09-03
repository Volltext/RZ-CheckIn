# Ein einzelner Container für die gesamte Anwendung (FastAPI + SQLite-Datei auf einem
# gemounteten Volume) -- siehe README.md Abschnitt "Super-easy Deployment" und
# deploy/rz-checkin.container für die Podman-Quadlet-Unit.
#
# Bewusst so gebaut, dass NACH "podman load" nichts weiter konfiguriert werden muss:
# Session-Secret und ein erster Admin-Zugang werden beim allerersten Start automatisch
# erzeugt (siehe docker-entrypoint.sh und app/main.py::_bootstrap_admin) und landen in
# "podman logs". `podman run -d -p 8000:8000 -v rz_checkin_data:/data <image>` reicht.

FROM python:3.11-slim

RUN useradd --create-home --uid 10001 rzcheckin
WORKDIR /app

# Erst nur die Metadaten kopieren, damit der Dependency-Layer bei reinen Code-Änderungen
# aus dem Cache kommt.
COPY pyproject.toml ./
COPY app ./app
RUN pip install --no-cache-dir .

COPY docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh
RUN chmod +x /usr/local/bin/docker-entrypoint.sh

# SQLite-Datei UND das automatisch erzeugte Session-Secret leben auf einem gemounteten
# Volume, damit sie Updates/Neustarts überstehen.
RUN mkdir -p /data && chown rzcheckin:rzcheckin /data
VOLUME ["/data"]
ENV RZ_DATABASE_PATH=/data/rz-checkin.db

USER rzcheckin
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request as u; u.urlopen('http://127.0.0.1:8000/health', timeout=3)" || exit 1

ENTRYPOINT ["docker-entrypoint.sh"]
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
