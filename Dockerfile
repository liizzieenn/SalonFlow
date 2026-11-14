# syntax=docker/dockerfile:1

# --- 1. Tailwind CSS -----------------------------------------------------------------
FROM node:22-alpine AS assets
WORKDIR /build
COPY package.json package-lock.json ./
RUN npm ci --no-audit --no-fund
# Tailwind scans templates and Python files for class names.
COPY static/src static/src
COPY templates templates
COPY apps apps
RUN npm run build

# --- 2. Django app -------------------------------------------------------------------
FROM python:3.12-slim AS app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

ARG REQUIREMENTS=requirements/prod.txt
COPY requirements/ requirements/
RUN pip install -r ${REQUIREMENTS}

COPY . .
COPY --from=assets /build/static/css/app.css static/css/app.css

# Hashed, compressed static files (whitenoise). Settings need a key to import.
RUN DJANGO_SETTINGS_MODULE=config.settings.prod DJANGO_SECRET_KEY=build-only \
    python manage.py collectstatic --noinput -v0 --ignore "src/*"

RUN useradd --create-home --uid 1000 app && chown -R app:app /app
USER app

EXPOSE 8000

ENTRYPOINT ["/app/docker/entrypoint.sh"]
CMD ["gunicorn", "config.wsgi:application", "-c", "/app/docker/gunicorn.conf.py"]
