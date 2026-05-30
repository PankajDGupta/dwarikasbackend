# Step 2: Database and App Configuration (core/settings.py)

Configure settings.py to leverage environment variables (which will be safely mounted from Google Cloud Secret Manager at runtime). This step disables local sessions and configures DRF to treat Supabase Auth tokens as the single source of truth.

import os
from pathlib import Path
import dj_database_url

BASE_DIR = Path(__file__).resolve().parent.parent

# Security Configurations
SECRET_KEY = os.environ.get('DJANGO_SECRET_KEY', 'fallback-development-key-32-chars-min')
DEBUG = os.environ.get('DEBUG', 'False') == 'True'
ALLOWED_HOSTS = os.environ.get('ALLOWED_HOSTS', '*').split(',')

# Core Applications Configuration
INSTALLED_APPS = [
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'rest_framework',
    'corsheaders',
    'api.apps.ApiConfig',
]

MIDDLEWARE = [
    'corsheaders.middleware.CorsMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]

ROOT_URLCONF = 'core.urls'
WSGI_APPLICATION = 'core.wsgi.application'

# Direct Supabase Connection Pooling Integration
DATABASE_URL = os.environ.get('DATABASE_URL')
DATABASES = {
    'default': dj_database_url.config(
        default=DATABASE_URL,
        conn_max_age=600,  # Persist connection pooling across stateless execution bursts
        conn_health_checks=True,
    )
}

# Framework Target Optimization Matrices
REST_FRAMEWORK = {
    'DEFAULT_AUTHENTICATION_CLASSES': [
        'api.authentication.SupabaseJWTAuthentication',
    ],
    'DEFAULT_PERMISSION_CLASSES': [
        'rest_framework.permissions.IsAuthenticated',
    ],
}

# Local JWT Decryption Keys Extraction
SUPABASE_JWT_SECRET = os.environ.get('SUPABASE_JWT_SECRET')

# CORS Framework Boundaries
CORS_ALLOW_ALL_ORIGINS = True  # Tighten in production to explicit origin domains
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