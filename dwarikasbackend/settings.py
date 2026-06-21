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
    'axes',                            # django-axes brute force protection
    'api.apps.ApiConfig',
    'inventory.apps.InventoryConfig',   # Spec #04 — unmanaged ORM mirrors of Supabase schema
    'tasks.apps.TasksConfig',
    'ondc.apps.OndcConfig',
    'whatsapp.apps.WhatsappConfig',
    'payments.apps.PaymentsConfig',   # Spec #17 — Razorpay Payment Gateway
    'pos.apps.PosConfig',             # Spec #18 — POS Cash Sales & In-Store Bill Generation
    'promotions.apps.PromotionsConfig', # Spec #19 — Promotions & Discounts
    'coupons.apps.CouponsConfig',     # Spec #20 — Coupon Code Creation & Application
    'gaming.apps.GamingConfig',       # Spec #21 — Gaming Engine Integration & Coupon Rewards
    'amazon.apps.AmazonConfig',       # Spec #23 — Amazon SP-API One-Click Product Listing
    'quickcommerce.apps.QuickcommerceConfig', # Spec #24 — Blinkit & JioMart One-Click Product Listing
]


MIDDLEWARE = [
    'corsheaders.middleware.CorsMiddleware',
    'api.middleware.AxesLockoutMiddleware',          # Block locked out IPs early
    'api.middleware.JTIBlocklistMiddleware',        # Check revocation first
    'api.middleware.UserAgentValidationMiddleware', # Then device binding
    'django.middleware.common.CommonMiddleware',
    'axes.middleware.AxesMiddleware',               # After CommonMiddleware
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
    'DEFAULT_THROTTLE_CLASSES': [
        'rest_framework.throttling.AnonRateThrottle',
        'rest_framework.throttling.UserRateThrottle',
    ],
    'DEFAULT_THROTTLE_RATES': {
        'anon': '60/minute',
        'user': '300/minute',
    },
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
# In production, CORS_ALLOWED_ORIGINS must be set as an environment variable
# (via Secret Manager) containing a comma-separated list of allowed frontend
# origins, e.g.:
#   https://app.dwarikas.com,https://admin.dwarikas.com,https://www.dwarikas.com
#
# When CORS_ALLOWED_ORIGINS is not set (local development), all origins are
# allowed as a convenience fallback — NEVER leave this unset in production.
# ---------------------------------------------------------------------------

_cors_origins_str = os.environ.get('CORS_ALLOWED_ORIGINS', '')
if _cors_origins_str:
    CORS_ALLOWED_ORIGINS = [o.strip() for o in _cors_origins_str.split(',') if o.strip()]
    CORS_ALLOW_ALL_ORIGINS = False
else:
    # Local development fallback — tighten via CORS_ALLOWED_ORIGINS in production
    CORS_ALLOW_ALL_ORIGINS = True

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
# Cloud Run service self-reference URL
#
# Cloud Tasks callbacks target the running Django service itself. In production
# this must be set to the Cloud Run service URL via --set-env-vars in gcloud.
# The localhost fallback is used only during local development.
# ---------------------------------------------------------------------------

CLOUD_RUN_SERVICE_URL = os.environ.get(
    'CLOUD_RUN_SERVICE_URL',
    'http://localhost:8080',  # local dev fallback — override in Cloud Run deployment
)


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

# ── Cache Backend (Redis with LocMem fallback for tests) ────────────────────
import sys
IS_TESTING = 'test' in sys.argv or any('test' in arg for arg in sys.argv)

if IS_TESTING:
    CACHES = {
        'default': {
            'BACKEND': 'django.core.cache.backends.locmem.LocMemCache',
            'LOCATION': 'dwarikas-testing-cache',
        }
    }
else:
    REDIS_URL = os.environ.get('REDIS_URL', 'redis://localhost:6379/0')
    CACHES = {
        'default': {
            'BACKEND': 'django_redis.cache.RedisCache',
            'LOCATION': REDIS_URL,
            'OPTIONS': {
                'CLIENT_CLASS': 'django_redis.client.DefaultClient',
            },
        }
    }

# ── django-axes brute force protection ─────────────────────────────────────
AXES_ENABLED = True
AXES_FAILURE_LIMIT = 5            # Lock after 5 consecutive failures
AXES_COOLOFF_TIME = 2             # Hours (2-hour sliding ban)
AXES_LOCKOUT_PARAMETERS = [['ip_address']]   # Lock by IP
AXES_CACHE = 'default'            # Use cache (Redis or LocMem) for axes state
AXES_LOCKOUT_CALLABLE = 'api.middleware.custom_lockout_view'

AUTHENTICATION_BACKENDS = [
    'axes.backends.AxesStandaloneBackend',
    'django.contrib.auth.backends.ModelBackend',
]

