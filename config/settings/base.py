"""Base settings shared by every environment.

Values that differ per environment (secrets, hosts, URLs) are read from the
environment / ``.env`` file via django-environ.
"""

from pathlib import Path

import environ
from django.utils.translation import gettext_lazy as _

BASE_DIR = Path(__file__).resolve().parent.parent.parent

env = environ.Env(
    DJANGO_DEBUG=(bool, False),
    DJANGO_ALLOWED_HOSTS=(list, []),
)
environ.Env.read_env(BASE_DIR / ".env")

SECRET_KEY = env("DJANGO_SECRET_KEY")
DEBUG = env("DJANGO_DEBUG")
ALLOWED_HOSTS = env("DJANGO_ALLOWED_HOSTS")

DJANGO_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "django.contrib.postgres",
]
THIRD_PARTY_APPS = [
    "rest_framework",
]
LOCAL_APPS = [
    "apps.core",
    "apps.accounts",
    "apps.salons",
    "apps.clients",
    "apps.bookings",
    "apps.notifications",
    "apps.dashboard",
    "apps.assistant",
]
INSTALLED_APPS = DJANGO_APPS + THIRD_PARTY_APPS + LOCAL_APPS

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.locale.LocaleMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"
WSGI_APPLICATION = "config.wsgi.application"
ASGI_APPLICATION = "config.asgi.application"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "django.template.context_processors.i18n",
            ],
        },
    },
]

# Database — PostgreSQL only (exclusion constraints, pgvector).
DATABASES = {
    "default": env.db(
        "DATABASE_URL",
        default="postgres://salonflow:salonflow@localhost:5432/salonflow",
    ),
}
DATABASES["default"]["ATOMIC_REQUESTS"] = True
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

AUTH_USER_MODEL = "accounts.User"
LOGIN_URL = "login"
LOGIN_REDIRECT_URL = "dashboard:home"
LOGOUT_REDIRECT_URL = "login"
AUTH_PASSWORD_VALIDATORS = [
    {
        "NAME": "django.contrib.auth.password_validation."
        "UserAttributeSimilarityValidator"
    },
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

# Internationalisation — Georgian (default, no URL prefix) and English (/en/).
LANGUAGE_CODE = "ka"
LANGUAGES = [
    ("ka", _("Georgian")),
    ("en", _("English")),
]
LOCALE_PATHS = [BASE_DIR / "locale"]
TIME_ZONE = "Asia/Tbilisi"
USE_I18N = True
USE_TZ = True

# Static & media
STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STATICFILES_DIRS = [BASE_DIR / "static"]
MEDIA_URL = "media/"
MEDIA_ROOT = BASE_DIR / "media"
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {
        "BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage"
    },
}

# Django REST Framework
REST_FRAMEWORK = {
    "DEFAULT_PERMISSION_CLASSES": [
        "rest_framework.permissions.IsAuthenticatedOrReadOnly",
    ],
    "DEFAULT_PAGINATION_CLASS": "rest_framework.pagination.PageNumberPagination",
    "PAGE_SIZE": 20,
}

# Celery
CELERY_BROKER_URL = env("REDIS_URL", default="redis://localhost:6379/0")
CELERY_RESULT_BACKEND = CELERY_BROKER_URL
CELERY_TIMEZONE = TIME_ZONE
CELERY_TASK_TRACK_STARTED = True
CELERY_TASK_TIME_LIMIT = 5 * 60
CELERY_TASK_ACKS_LATE = True
CELERY_BEAT_SCHEDULE = {
    "dispatch-due-reminders": {
        "task": "notifications.dispatch_due_reminders",
        "schedule": env.int("REMINDER_DISPATCH_INTERVAL_SECONDS", default=300),
    },
}

# Notifications
#: Reminder kind -> hours before the appointment it is sent.
BOOKING_REMINDER_HOURS = {"reminder_24h": 24, "reminder_2h": 2}
SMS_BACKEND = env(
    "SMS_BACKEND", default="apps.notifications.sms.backends.console.ConsoleBackend"
)
SMS_SENDER = env("SMS_SENDER", default="SalonFlow")
TWILIO_ACCOUNT_SID = env("TWILIO_ACCOUNT_SID", default="")
TWILIO_AUTH_TOKEN = env("TWILIO_AUTH_TOKEN", default="")
TWILIO_FROM_NUMBER = env("TWILIO_FROM_NUMBER", default="")

#: Reverse proxies in front of Django that append to X-Forwarded-For (see
#: apps.core.net.client_ip). 0 locally; production sets 1 (Caddy).
TRUSTED_PROXY_HOPS = env.int("TRUSTED_PROXY_HOPS", default=0)

# Cache (rate limits) — Redis, shared by all web workers.
CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.redis.RedisCache",
        "LOCATION": env("CACHE_URL", default=CELERY_BROKER_URL),
    }
}

# AI assistant
ASSISTANT_LLM_BACKEND = env(
    "ASSISTANT_LLM_BACKEND", default="apps.assistant.providers.fake.FakeLLM"
)
ASSISTANT_EMBEDDING_BACKEND = env(
    "ASSISTANT_EMBEDDING_BACKEND",
    default="apps.assistant.providers.fake.FakeEmbeddings",
)
ASSISTANT_LLM_MODEL = env("ASSISTANT_LLM_MODEL", default="claude-opus-5-5")
ASSISTANT_EMBEDDING_MODEL = env("ASSISTANT_EMBEDDING_MODEL", default="voyage-3.5")
VOYAGE_API_KEY = env("VOYAGE_API_KEY", default="")
# ANTHROPIC_API_KEY is read by the anthropic SDK from the environment.
ASSISTANT_TOP_K = 5
#: Cosine distance cut-off for retrieved chunks (0 = identical, 2 = opposite).
ASSISTANT_MAX_DISTANCE = env.float("ASSISTANT_MAX_DISTANCE", default=0.85)
ASSISTANT_RATE_LIMITS = {
    "client": {"minute": 6, "day": 60},
    "salon": {"day": env.int("ASSISTANT_SALON_DAILY_LIMIT", default=1000)},
}

# Email
EMAIL_HOST = env("EMAIL_HOST", default="")
EMAIL_PORT = env.int("EMAIL_PORT", default=587)
EMAIL_HOST_USER = env("EMAIL_HOST_USER", default="")
EMAIL_HOST_PASSWORD = env("EMAIL_HOST_PASSWORD", default="")
EMAIL_USE_TLS = env.bool("EMAIL_USE_TLS", default=True)
DEFAULT_FROM_EMAIL = env("DEFAULT_FROM_EMAIL", default="SalonFlow <noreply@localhost>")

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "handlers": {"console": {"class": "logging.StreamHandler"}},
    "root": {"handlers": ["console"], "level": "INFO"},
}
