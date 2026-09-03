#!/bin/sh
# Erzeugt beim allerersten Start ein zufälliges Session-Secret und legt es dauerhaft auf
# dem /data-Volume ab, falls RZ_SESSION_SECRET nicht explizit gesetzt wurde. So bleiben
# Admin-Logins auch über Container-Neustarts hinweg gültig, ohne dass beim Deployment
# irgendetwas von Hand konfiguriert werden muss.
set -eu

SECRET_FILE="/data/.session_secret"

if [ -z "${RZ_SESSION_SECRET:-}" ]; then
    if [ ! -s "$SECRET_FILE" ]; then
        python -c "import secrets; print(secrets.token_hex(32))" > "$SECRET_FILE"
        chmod 600 "$SECRET_FILE"
    fi
    RZ_SESSION_SECRET="$(cat "$SECRET_FILE")"
    export RZ_SESSION_SECRET
fi

exec "$@"
