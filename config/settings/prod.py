"""Production settings: secure defaults, everything sensitive from env.

Designed for docker-compose.prod.yml: Caddy terminates HTTPS and proxies to
gunicorn, so the public hostname comes from one ``DOMAIN`` variable.
"""

from .base import *  # noqa: F403
from .base import env

DEBUG = False

DOMAIN = env("DOMAIN", default="")
ALLOWED_HOSTS = env.list("DJANGO_ALLOWED_HOSTS", default=[DOMAIN] if DOMAIN else [])
CSRF_TRUSTED_ORIGINS = env.list(
    "DJANGO_CSRF_TRUSTED_ORIGINS", default=[f"https://{DOMAIN}"] if DOMAIN else []
)

# Caddy sets X-Forwarded-Proto and appends the client IP to X-Forwarded-For.
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
TRUSTED_PROXY_HOPS = env.int("TRUSTED_PROXY_HOPS", default=1)
SECURE_SSL_REDIRECT = env.bool("DJANGO_SECURE_SSL_REDIRECT", default=True)
# Container health checks call /health/ over plain HTTP from inside the network.
SECURE_REDIRECT_EXEMPT = [r"^health/$"]
SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True
SECURE_HSTS_SECONDS = env.int("DJANGO_SECURE_HSTS_SECONDS", default=60 * 60 * 24 * 30)
SECURE_HSTS_INCLUDE_SUBDOMAINS = env.bool(
    "DJANGO_SECURE_HSTS_SUBDOMAINS", default=False
)
SECURE_HSTS_PRELOAD = False
SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_REFERRER_POLICY = "same-origin"

DATABASES["default"]["CONN_MAX_AGE"] = env.int(  # noqa: F405
    "DJANGO_CONN_MAX_AGE", default=60
)
DATABASES["default"]["CONN_HEALTH_CHECKS"] = True  # noqa: F405

EMAIL_BACKEND = env(
    "EMAIL_BACKEND", default="django.core.mail.backends.smtp.EmailBackend"
)

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "plain": {"format": "%(asctime)s %(levelname)s %(name)s %(message)s"}
    },
    "handlers": {"console": {"class": "logging.StreamHandler", "formatter": "plain"}},
    "root": {"handlers": ["console"], "level": env("LOG_LEVEL", default="INFO")},
    "loggers": {"django.security": {"level": "WARNING"}},
}
