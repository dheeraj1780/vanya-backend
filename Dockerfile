# Deliberately does NOT COPY secrets/ or .env — those stay out of the
# image entirely. All configuration (including the Firebase service
# account) is supplied as environment variables at deploy time; see
# app/core/config.py and app/utils/firebase_auth.py's module docstring.
FROM python:3.12-slim

WORKDIR /app

# Layer-cached separately from app code so a code-only change doesn't
# reinstall every dependency.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app

# STORAGE_BACKEND=local writes here — on most hosts this directory does
# NOT persist across redeploys/restarts unless the platform's persistent
# disk is attached to this exact path. Fine for a short beta; swap to
# real object storage (see storage.py's docstring) before anything
# longer-lived.
RUN mkdir -p /app/uploads

EXPOSE 8000

# Shell form so ${PORT} expands — Render/Railway/most PaaS inject PORT
# and expect the app to bind to it rather than a hardcoded value.
CMD uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}
