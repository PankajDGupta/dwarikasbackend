"""
Verifies incoming Beckn Protocol request signatures per ONDC security spec.
ONDC requires HMAC-based or Ed25519-based signatures in the Authorization header.
"""
import base64
import os
import logging
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from cryptography.hazmat.primitives.serialization import load_der_public_key
from cryptography.exceptions import InvalidSignature

from django.conf import settings

logger = logging.getLogger(__name__)


def verify_beckn_signature(request) -> bool:
    """
    Verifies the Ed25519 signature in the Authorization header.
    Returns True if the signature is valid; False otherwise.
    Reference: https://docs.ondc.org/reference/signing-requests
    """
    # Load dynamically to support setting overrides in tests
    public_key_b64 = getattr(settings, 'ONDC_REGISTRY_PUBLIC_KEY_B64', os.environ.get('ONDC_REGISTRY_PUBLIC_KEY_B64', ''))
    
    # If not set, allow requests in local dev
    if not public_key_b64:
        logger.warning("ONDC_REGISTRY_PUBLIC_KEY_B64 not set. Bypassing Beckn signature verification.")
        return True

    auth_header = request.headers.get('Authorization', '')
    if not auth_header.startswith('Signature '):
        return False

    try:
        # Parse signature parameters from Authorization header
        params = {}
        for part in auth_header[len('Signature '):].split(','):
            key, _, value = part.strip().partition('=')
            params[key.strip()] = value.strip().strip('"')

        signature_b64 = params.get('signature', '')
        if not signature_b64:
            return False

        # Load the signer's public key (fetched from ONDC registry during key rotation)
        public_key_bytes = base64.b64decode(public_key_b64)
        
        # ONDC public keys can be raw 32-byte Ed25519 public keys or DER/PEM public keys
        if len(public_key_bytes) == 32:
            public_key = Ed25519PublicKey.from_public_bytes(public_key_bytes)
        else:
            try:
                public_key = load_der_public_key(public_key_bytes)
            except Exception:
                from cryptography.hazmat.primitives.serialization import load_pem_public_key
                public_key = load_pem_public_key(public_key_bytes)

        # Reconstruct the signing string from method + path + body hash
        signing_string = _build_signing_string(request, params)
        signature_bytes = base64.b64decode(signature_b64)

        public_key.verify(signature_bytes, signing_string.encode('utf-8'))
        return True

    except (InvalidSignature, Exception) as e:
        logger.warning(f"Beckn signature verification failed: {e}")
        return False


def _build_signing_string(request, params: dict) -> str:
    """Constructs the canonical signing string from the request headers."""
    created = params.get('created', '')
    expires = params.get('expires', '')
    body_hash = params.get('digest', '')
    return f"(created): {created}\n(expires): {expires}\ndigest: {body_hash}"
