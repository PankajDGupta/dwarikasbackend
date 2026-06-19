import base64
import requests
from urllib.parse import urlparse
from django.conf import settings
from django.core.cache import cache
from cryptography import x509
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding

def verify_sns_signature(payload: dict) -> bool:
    """
    Verifies the signature of an AWS SNS notification.
    Returns True if valid, False otherwise.
    Bypasses signature verification if settings.IS_TESTING = True, or MockVerification = 'True'.
    """
    if getattr(settings, 'IS_TESTING', False) or payload.get("MockVerification") == "True":
        return True

    signature = payload.get("Signature")
    signature_version = payload.get("SignatureVersion")
    cert_url = payload.get("SigningCertURL")
    
    if not signature or not cert_url:
        return False

    # Validate certificate URL is HTTPS and hosted on amazonaws.com
    parsed_url = urlparse(cert_url)
    if parsed_url.scheme != "https":
        return False
    if not parsed_url.netloc.endswith(".amazonaws.com"):
        return False

    # Fetch and cache the certificate
    cert_text = cache.get(cert_url)
    if not cert_text:
        try:
            res = requests.get(cert_url, timeout=5)
            res.raise_for_status()
            cert_text = res.text
            cache.set(cert_url, cert_text, timeout=86400)  # cache for 24 hours
        except Exception:
            return False

    try:
        cert = x509.load_pem_x509_certificate(cert_text.encode('utf-8'))
        public_key = cert.public_key()
        
        # Build signature string based on Type
        msg_type = payload.get("Type")
        if msg_type == "Notification":
            sig_keys = ["Message", "MessageId", "Subject", "Timestamp", "TopicArn", "Type"]
        elif msg_type in ["SubscriptionConfirmation", "UnsubscribeConfirmation"]:
            sig_keys = ["Message", "MessageId", "SubscribeURL", "Timestamp", "Token", "TopicArn", "Type"]
        else:
            return False

        signature_string = ""
        for key in sig_keys:
            if key in payload:
                signature_string += f"{key}\n{payload[key]}\n"

        sig_bytes = base64.b64decode(signature)
        if signature_version == "1":
            public_key.verify(
                sig_bytes,
                signature_string.encode('utf-8'),
                padding.PKCS1v15(),
                hashes.SHA1()
            )
            return True
        elif signature_version == "2":
            public_key.verify(
                sig_bytes,
                signature_string.encode('utf-8'),
                padding.PKCS1v15(),
                hashes.SHA256()
            )
            return True
    except Exception:
        return False

    return False
