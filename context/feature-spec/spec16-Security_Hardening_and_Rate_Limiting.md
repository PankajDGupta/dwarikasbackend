# Spec 16 — Security Hardening & Rate Limiting

## Goal
Implement production-grade API security hardening: rate limiting per IP and per user, brute-force protection, device-binding middleware, and Redis-backed session revocation (JTI blocklist). This spec brings the security architecture described in `system_design.md` section 6 to life.

---

## Scope
- Django middleware: `UserAgentValidationMiddleware` (device binding)
- DRF throttling: per-IP and per-user rate limits via Redis
- `django-axes` brute-force lockout (5 failures → 2-hour ban)
- Redis JTI blocklist for global cross-device logout
- Health-check endpoint: `GET /api/v1/health/`
- Add `django-axes`, `redis`, `django-redis` to dependencies

---

## Files to Create / Modify

### `pyproject.toml` — Add dependencies

```toml
"django-axes>=6.4.0",
"django-redis>=5.4.0",
"redis>=5.0.0",
```

---

### `dwarikasbackend/settings.py` — Redis cache + throttling + axes

```python
# ── Redis Cache Backend ─────────────────────────────────────────────────────
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

# ── DRF Throttling ──────────────────────────────────────────────────────────
REST_FRAMEWORK = {
    ...
    'DEFAULT_THROTTLE_CLASSES': [
        'rest_framework.throttling.AnonRateThrottle',
        'rest_framework.throttling.UserRateThrottle',
    ],
    'DEFAULT_THROTTLE_RATES': {
        'anon': '60/minute',      # 60 requests/min per IP for unauthenticated
        'user': '300/minute',     # 300 requests/min per authenticated user
    },
}

# ── django-axes brute force protection ─────────────────────────────────────
AXES_ENABLED = True
AXES_FAILURE_LIMIT = 5            # Lock after 5 consecutive failures
AXES_COOLOFF_TIME = 2             # Hours (2-hour sliding ban)
AXES_LOCKOUT_PARAMETERS = [['ip_address']]   # Lock by IP
AXES_CACHE = 'default'            # Use Redis cache for axes state

INSTALLED_APPS += ['axes']

AUTHENTICATION_BACKENDS = [
    'axes.backends.AxesStandaloneBackend',
    'django.contrib.auth.backends.ModelBackend',
]

MIDDLEWARE += [
    'axes.middleware.AxesMiddleware',
]
```

---

### `api/middleware.py` — New file

```python
"""
Custom Django middleware for advanced security hardening.
"""
import json
import hashlib
from django.http import JsonResponse
from django.conf import settings


class UserAgentValidationMiddleware:
    """
    Validates that the HTTP User-Agent header matches the device claim
    embedded inside the authenticated Supabase JWT `app_metadata.user_agent`.
    
    This mitigates token theft: a stolen JWT cannot be replayed from a
    different browser/device because the User-Agent won't match.
    
    Only applied when:
    - An Authorization: Bearer <token> header is present
    - The JWT payload contains a `user_agent` claim
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        auth_header = request.headers.get('Authorization', '')
        if not auth_header.startswith('Bearer '):
            return self.get_response(request)

        raw_token = auth_header.split(' ', 1)[1]

        try:
            import jwt as pyjwt
            # Decode without verification here (signature was already verified by DRF auth)
            payload = pyjwt.decode(raw_token, options={"verify_signature": False})
            token_user_agent = payload.get('app_metadata', {}).get('user_agent')
        except Exception:
            return self.get_response(request)

        if token_user_agent:
            current_user_agent = request.headers.get('User-Agent', '')
            if token_user_agent != current_user_agent:
                return JsonResponse(
                    {'error': 'Session token is bound to a different device. Please log in again.'},
                    status=403,
                )

        return self.get_response(request)


class JTIBlocklistMiddleware:
    """
    Checks the JWT `jti` (token ID) claim against a Redis blocklist.
    Revoked JTIs (e.g., after logout or password change) are rejected here
    before the request reaches any view, enabling instant cross-device logout.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        auth_header = request.headers.get('Authorization', '')
        if not auth_header.startswith('Bearer '):
            return self.get_response(request)

        raw_token = auth_header.split(' ', 1)[1]

        try:
            import jwt as pyjwt
            payload = pyjwt.decode(raw_token, options={"verify_signature": False})
            jti = payload.get('jti')
        except Exception:
            return self.get_response(request)

        if jti and _is_jti_revoked(jti):
            return JsonResponse(
                {'error': 'This session has been revoked. Please log in again.'},
                status=401,
            )

        return self.get_response(request)


def _is_jti_revoked(jti: str) -> bool:
    """Check Redis for revoked JTI."""
    from django.core.cache import cache
    return bool(cache.get(f'revoked_jti:{jti}'))


def revoke_jti(jti: str, ttl_seconds: int = 86400):
    """
    Adds a JTI to the Redis blocklist with a TTL matching token expiry.
    Call this from the logout endpoint.
    """
    from django.core.cache import cache
    cache.set(f'revoked_jti:{jti}', True, timeout=ttl_seconds)
```

---

### `dwarikasbackend/settings.py` — Register middleware (order matters)

```python
MIDDLEWARE = [
    'corsheaders.middleware.CorsMiddleware',
    'api.middleware.JTIBlocklistMiddleware',        # Check revocation first
    'api.middleware.UserAgentValidationMiddleware', # Then device binding
    'django.middleware.common.CommonMiddleware',
    'axes.middleware.AxesMiddleware',               # After CommonMiddleware
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]
```

---

### `api/logout_view.py` — New file

```python
"""
Logout endpoint: adds the caller's JWT jti to the Redis revocation blocklist.
"""
import jwt as pyjwt
from django.conf import settings
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView
from api.middleware import revoke_jti


class LogoutView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        auth_header = request.headers.get('Authorization', '')
        if not auth_header.startswith('Bearer '):
            return Response({'error': 'No token provided.'}, status=status.HTTP_400_BAD_REQUEST)

        raw_token = auth_header.split(' ', 1)[1]
        try:
            payload = pyjwt.decode(
                raw_token,
                settings.SUPABASE_JWT_SECRET,
                algorithms=['HS256'],
                audience='authenticated',
            )
            jti = payload.get('jti')
            exp = payload.get('exp', 0)
        except pyjwt.PyJWTError:
            return Response({'error': 'Invalid token.'}, status=status.HTTP_400_BAD_REQUEST)

        if jti:
            import time
            ttl = max(0, int(exp - time.time()))
            revoke_jti(jti, ttl_seconds=ttl or 3600)

        return Response({'message': 'Logged out successfully.'}, status=status.HTTP_200_OK)
```

---

### `api/health_view.py` — New file

```python
"""
Health-check endpoint for Cloud Run liveness probes.
"""
from django.db import connection
from django.core.cache import cache
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView


class HealthCheckView(APIView):
    permission_classes = [AllowAny]
    authentication_classes = []

    def get(self, request):
        checks = {}

        # Database connectivity
        try:
            connection.ensure_connection()
            checks['database'] = 'ok'
        except Exception as e:
            checks['database'] = f'error: {str(e)}'

        # Redis connectivity
        try:
            cache.set('health_ping', '1', timeout=5)
            checks['redis'] = 'ok' if cache.get('health_ping') == '1' else 'miss'
        except Exception as e:
            checks['redis'] = f'error: {str(e)}'

        all_ok = all(v == 'ok' for v in checks.values())
        return Response(
            {'status': 'healthy' if all_ok else 'degraded', 'checks': checks},
            status=200 if all_ok else 503,
        )
```

---

### `api/urls.py` — Register new routes

```python
from django.urls import path
from api.logout_view import LogoutView
from api.health_view import HealthCheckView

urlpatterns = [
    path('auth/logout/', LogoutView.as_view(), name='auth-logout'),
    path('health/', HealthCheckView.as_view(), name='health-check'),
]
```

---

### Environment Variables Required

| Variable | Description |
|---|---|
| `REDIS_URL` | Redis connection string (`redis://host:6379/db`) |

---

## Acceptance Criteria

- [ ] `GET /api/v1/health/` returns `{"status": "healthy"}` when DB and Redis are reachable
- [ ] `GET /api/v1/health/` returns 503 with `{"status": "degraded"}` when Redis is down
- [ ] `POST /api/v1/auth/logout/` revokes the JWT JTI in Redis; subsequent requests with the same token return 401
- [ ] Sending a request with a User-Agent that doesn't match the token's `user_agent` claim returns 403
- [ ] After 5 failed authentication attempts from the same IP, the 6th returns 429/403 (axes lockout)
- [ ] Anon requests beyond 60/minute return HTTP 429
- [ ] Authenticated requests beyond 300/minute return HTTP 429
- [ ] All middleware is ordered correctly: CORS → JTI blocklist → UserAgent → Common → Axes
- [ ] `_is_jti_revoked` and `revoke_jti` are unit-testable with a mocked Django cache
