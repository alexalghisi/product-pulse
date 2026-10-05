"""Django settings. Everything that differs between machines comes from the environment."""

from __future__ import annotations

import os
from pathlib import Path
from urllib.parse import unquote, urlparse

BASE_DIR = Path(__file__).resolve().parent


def _env(name: str, default: str) -> str:
    return os.environ.get(name, default)


def _database_from_url(url: str) -> dict[str, object]:
    parsed = urlparse(url)
    if parsed.scheme not in {"postgres", "postgresql"}:
        raise ValueError(f"PULSE_DATABASE_URL must be a postgres:// URL, got {parsed.scheme!r}")
    return {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": parsed.path.lstrip("/"),
        "USER": unquote(parsed.username or ""),
        "PASSWORD": unquote(parsed.password or ""),
        "HOST": parsed.hostname or "localhost",
        "PORT": str(parsed.port or 5432),
        "CONN_MAX_AGE": 60,
        "TEST": {"NAME": "test_" + parsed.path.lstrip("/")},
    }


SECRET_KEY = _env("PULSE_SECRET_KEY", "dev-only-not-secret")
DEBUG = _env("PULSE_DEBUG", "1") == "1"
ALLOWED_HOSTS = _env("PULSE_ALLOWED_HOSTS", "localhost,127.0.0.1,testserver").split(",")

INSTALLED_APPS = [
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "rest_framework",
    "product_pulse.core",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
]

ROOT_URLCONF = "product_pulse.urls"
WSGI_APPLICATION = "product_pulse.wsgi.application"
TEMPLATES: list[dict[str, object]] = []

DATABASES = {
    "default": _database_from_url(
        _env("PULSE_DATABASE_URL", "postgres://synckit:synckit@localhost:5432/ph_pulse")
    )
}
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

USE_TZ = True
TIME_ZONE = "UTC"

CSRF_TRUSTED_ORIGINS = _env(
    "PULSE_CSRF_TRUSTED_ORIGINS", "http://localhost:5280,http://127.0.0.1:5280"
).split(",")

REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": [
        "product_pulse.api.auth.PersonalAPIKeyAuthentication",
        "rest_framework.authentication.SessionAuthentication",
    ],
    "DEFAULT_PERMISSION_CLASSES": ["rest_framework.permissions.IsAuthenticated"],
    "DEFAULT_RENDERER_CLASSES": ["rest_framework.renderers.JSONRenderer"],
    "UNAUTHENTICATED_USER": None,
}

PULSE_CLICKHOUSE_URL = _env("PULSE_CLICKHOUSE_URL", "http://127.0.0.1:8130")
PULSE_CLICKHOUSE_DATABASE = _env("PULSE_CLICKHOUSE_DATABASE", "pulse")
PULSE_REDIS_URL = _env("PULSE_REDIS_URL", "redis://localhost:6379/0")
PULSE_STREAM = _env("PULSE_STREAM", "pulse:events")
# Capture answers 503 above this many buffered batches, so clients retry instead of the
# buffer growing until Redis runs out of memory.
PULSE_STREAM_HIGH_WATERMARK = int(_env("PULSE_STREAM_HIGH_WATERMARK", "200000"))

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "handlers": {"console": {"class": "logging.StreamHandler"}},
    "root": {"handlers": ["console"], "level": _env("PULSE_LOG_LEVEL", "INFO")},
    "loggers": {"httpx": {"level": "WARNING"}, "httpcore": {"level": "WARNING"}},
}
