"""
Django settings for dwarikasbackend — Dwarikas Central API Engine.

Loads all runtime secrets from environment variables that are mounted
from Google Cloud Secret Manager at Cloud Run startup. Do not hard-code
any credential here.

References:
  - context/architecture.md — system boundaries and storage model
  - context/feature-spec/spec02-Database_and_appconfiguration.md
"""

import os
from pathlib import Path

import dj_database_url

# ---------------------------------------------------------------------------
# Path resolution
# ---------------------------------------------------------------------------

BASE_DIR = Path(__file__).resolve().parent.parent


# ---------------------------------------------------------------------------
# Security configurations
# ---------------------------------------------------------------------------

# SECRET_KEY is sourced from the environment. The fallback value is only used
# in local development; it must be replaced by a proper secret in production.
SECRET_KEY = os.environ.get(
    'DJANGO_SECRET_KEY',
    'fallback-development-key-32-chars-min'
)

DEBUG = os.environ.get('DEBUG', 'False') == 'True'

ALLOWED_HOSTS = os.environ.get('ALLOWED_HOSTS', '*').split(',')


# ---------------------------------------------------------------------------
# Core application configuration
#
# Django admin, sessions, and staticfiles are intentionally excluded:
#   - Admin:      Not used; all privileged operations go through DRF endpoints.
#   - Sessions:   Replaced by stateless Supabase JWT authentication.
#   - Staticfiles: No static assets served by this API container.
# ---------------------------------------------------------------------------

INSTALLED_APPS = [
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'rest_framework',
    'django_filters',                  # For django-filter queries
    'corsheaders',
    'api.apps.ApiConfig',
    'inventory.apps.InventoryConfig',   # Spec #04 — unmanaged ORM mirrors of Supabase schema
    'tasks.apps.TasksConfig',
    'ondc.apps.OndcConfig',
    'whatsapp.apps.WhatsappConfig',
]


MIDDLEWARE = [
    'corsheaders.middleware.CorsMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]

ROOT_URLCONF = 'dwarikasbackend.urls'
WSGI_APPLICATION = 'dwarikasbackend.wsgi.application'


# ---------------------------------------------------------------------------
# Database — Direct Supabase connection pooling integration
#
# DATABASE_URL is expected in the form:
#   postgresql://user:password@host:port/dbname
#
# conn_max_age=600 keeps TCP connections alive across the stateless Cloud Run
# execution bursts, avoiding the overhead of a new connection per request.
# conn_health_checks=True ensures stale pooled connections are evicted before
# being handed to a query.
# ---------------------------------------------------------------------------

DATABASE_URL = os.environ.get('DATABASE_URL')
DATABASES = {
    'default': dj_database_url.config(
        default=DATABASE_URL,
        conn_max_age=600,       # Persist connection pooling across stateless execution bursts
        conn_health_checks=True,
    )
}


# ---------------------------------------------------------------------------
# Django REST Framework — Authentication and permissions
#
# All endpoints default to requiring a valid Supabase JWT. Public read-only
# endpoints override this at the view level with AllowAny.
# Full SupabaseJWTAuthentication implementation lands in Spec #03.
# ---------------------------------------------------------------------------

REST_FRAMEWORK = {
    'DEFAULT_AUTHENTICATION_CLASSES': [
        'api.authentication.SupabaseJWTAuthentication',
        'api.external_auth.ExternalApiKeyAuthentication',   # Add after JWT auth
    ],
    'DEFAULT_PERMISSION_CLASSES': [
        'rest_framework.permissions.IsAuthenticated',
    ],
    'DEFAULT_FILTER_BACKENDS': [
        'django_filters.rest_framework.DjangoFilterBackend',
        'rest_framework.filters.SearchFilter',
        'rest_framework.filters.OrderingFilter',
    ],
    'DEFAULT_PAGINATION_CLASS': 'rest_framework.pagination.PageNumberPagination',
    'PAGE_SIZE': 25,
}



# ---------------------------------------------------------------------------
# Local JWT decryption — Supabase project JWKS
#
# The raw JWT secret (HS256) or the JWKS endpoint is fetched from the
# environment so Django middleware can validate tokens locally without a
# network round-trip to Supabase Auth servers.
# ---------------------------------------------------------------------------

SUPABASE_JWT_SECRET = os.environ.get('SUPABASE_JWT_SECRET')


# ---------------------------------------------------------------------------
# CORS framework boundaries
#
# CORS_ALLOW_ALL_ORIGINS is permissive during development. Tighten this in
# production to an explicit allow-list of client origins (Next.js web portal,
# mobile app host, Admin POS UI).
# ---------------------------------------------------------------------------

CORS_ALLOW_ALL_ORIGINS = True  # TODO: restrict to explicit origins in production

CORS_ALLOW_HEADERS = [
    'accept',
    'accept-encoding',
    'authorization',
    'content-type',
    'dnt',
    'origin',
    'user-agent',
    'x-csrftoken',
    'x-requested-with',
]


# ---------------------------------------------------------------------------
# Internationalisation
# ---------------------------------------------------------------------------

LANGUAGE_CODE = 'en-us'
TIME_ZONE = 'UTC'
USE_I18N = True
USE_TZ = True


# ---------------------------------------------------------------------------
# Default primary key type
# ---------------------------------------------------------------------------

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

# Custom test runner to enable database creation for unmanaged models
TEST_RUNNER = 'inventory.tests.runner.ManagedModelTestRunner'

