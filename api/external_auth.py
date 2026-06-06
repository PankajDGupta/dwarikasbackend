"""
Machine-to-machine authentication for external ERP/logistics partners.
No JWT or session involved — uses hashed API key + request signature.
"""
import hashlib
import hmac
import time

from django.conf import settings
from rest_framework import authentication, exceptions

from inventory.models import ExternalApiKey

SIGNATURE_TTL_SECONDS = 300   # Reject requests with a timestamp older than 5 minutes


class ExternalApiKeyAuthentication(authentication.BaseAuthentication):
    """
    Reads X-Dwarikas-Api-Key header, computes SHA-256, and looks it up in the DB.
    Attaches the ExternalApiKey instance to request.auth if valid.
    """

    def authenticate(self, request):
        raw_key = request.headers.get('X-Dwarikas-Api-Key')
        if not raw_key:
            return None   # Not an external partner request — let next authenticator try

        key_hash = hashlib.sha256(raw_key.encode()).hexdigest()

        try:
            api_key = ExternalApiKey.objects.get(key_hash=key_hash, is_active=True)
        except ExternalApiKey.DoesNotExist:
            from django.contrib.auth.signals import user_login_failed
            user_login_failed.send(
                sender=self.__class__,
                credentials={'username': 'api_key_invalid'},
                request=request
            )
            raise exceptions.AuthenticationFailed('Invalid or revoked API key.')

        # Create a lightweight pseudo-user for DRF compatibility
        user = type('ExternalPartner', (), {
            'is_authenticated': True,
            'username': api_key.partner_name,
            'role': 'external',
            'pk': api_key.id,
            'id': api_key.id,
        })()

        return (user, api_key)

    def authenticate_header(self, request):
        return 'X-Dwarikas-Api-Key'


class HasValidRequestSignature(authentication.BaseAuthentication):
    """
    Validates X-Dwarikas-Signature: HMAC-SHA256 over (method + path + timestamp + body).
    Must be used together with ExternalApiKeyAuthentication.
    """

    SIGNING_SECRET_ENV = 'EXTERNAL_SIGNING_SECRET'

    def authenticate(self, request):
        # This class only validates the signature — key auth is done above
        signature = request.headers.get('X-Dwarikas-Signature')
        timestamp = request.headers.get('X-Dwarikas-Timestamp')

        if not signature or not timestamp:
            raise exceptions.AuthenticationFailed(
                'X-Dwarikas-Signature and X-Dwarikas-Timestamp headers are required.'
            )

        # Replay protection: reject stale requests
        try:
            request_time = int(timestamp)
        except ValueError:
            raise exceptions.AuthenticationFailed('Invalid X-Dwarikas-Timestamp format.')

        if abs(time.time() - request_time) > SIGNATURE_TTL_SECONDS:
            raise exceptions.AuthenticationFailed('Request timestamp is expired.')

        # Reconstruct and verify signature
        import os
        secret = os.environ.get(self.SIGNING_SECRET_ENV, '')
        try:
            body_bytes = request.body
        except Exception:
            body_bytes = b''

        payload_str = (
            request.method
            + request.path
            + timestamp
            + body_bytes.decode('utf-8', errors='replace')
        )
        expected = hmac.new(secret.encode(), payload_str.encode(), hashlib.sha256).hexdigest()

        if not hmac.compare_digest(signature, expected):
            from django.contrib.auth.signals import user_login_failed
            user_login_failed.send(
                sender=self.__class__,
                credentials={'username': 'signature_invalid'},
                request=request
            )
            raise exceptions.AuthenticationFailed('Request signature verification failed.')

        return None   # Signature valid; authentication handled by ExternalApiKeyAuthentication

    def authenticate_header(self, request):
        return 'Signature realm="api"'
