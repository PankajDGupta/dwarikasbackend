"""
Custom Django middleware for advanced security hardening.
"""
import json
import hashlib
from django.http import JsonResponse
from django.conf import settings
from django.core.cache import cache


class AxesLockoutMiddleware:
    """
    Checks if the incoming request's IP is locked out by django-axes.
    If locked out, returns a 403 Forbidden JSON response early.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if not getattr(settings, 'AXES_ENABLED', True):
            return self.get_response(request)

        # Skip lockout check for health check probes to avoid database dependencies
        if request.path.endswith('/health/') or request.path.endswith('/health'):
            return self.get_response(request)

        try:
            from axes.handlers.proxy import AxesProxyHandler
            handler = AxesProxyHandler()
            if handler.is_locked(request):
                return custom_lockout_view(request)
        except Exception:
            # If the database or cache is down, fail open to let request proceed
            pass
        return self.get_response(request)


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
            # Decode without verification here (signature is verified by DRF auth)
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
    """Check cache for revoked JTI."""
    return bool(cache.get(f'revoked_jti:{jti}'))


def revoke_jti(jti: str, ttl_seconds: int = 86400):
    """
    Adds a JTI to the cache blocklist with a TTL matching token expiry.
    Call this from the logout endpoint.
    """
    cache.set(f'revoked_jti:{jti}', True, timeout=ttl_seconds)


def custom_lockout_view(request, credentials=None, *args, **kwargs):
    """
    Custom django-axes lockout callable returning a DRF-compliant JSON payload.
    """
    return JsonResponse(
        {'error': 'Account locked due to too many failed login attempts.'},
        status=403,
    )
