"""
Razorpay SDK wrapper — Spec #17.

Centralises all Razorpay client creation and crypto operations so they can be
easily mocked in unit tests. No direct Razorpay calls should appear outside
this module.

References:
  - context/feature-spec/spec17-Payment_Gateway_Integration.md
  - https://razorpay.com/docs/payments/server-integration/python/
"""

import hashlib
import hmac
import os

import razorpay

RAZORPAY_KEY_ID = os.environ.get('RAZORPAY_KEY_ID', '')
RAZORPAY_KEY_SECRET = os.environ.get('RAZORPAY_KEY_SECRET', '')


def get_client() -> razorpay.Client:
    """Returns an authenticated Razorpay client instance."""
    return razorpay.Client(auth=(RAZORPAY_KEY_ID, RAZORPAY_KEY_SECRET))


def create_razorpay_order(amount_paise: int, currency: str = 'INR', receipt: str = '') -> dict:
    """
    Creates a Razorpay Order server-side.

    Args:
        amount_paise: Total amount in paise (₹1 = 100 paise). Must be at least 100.
        currency:     ISO 4217 currency code. Default: 'INR'.
        receipt:      Idempotency receipt string — use reservation_id.

    Returns:
        Full Razorpay order dict including 'id', 'amount', 'currency', 'status'.

    Raises:
        razorpay.errors.BadRequestError: on invalid inputs (e.g., amount < 100).
        Exception: on network errors or service unavailability.
    """
    client = get_client()
    return client.order.create({
        'amount': amount_paise,
        'currency': currency,
        'receipt': receipt,      # Use reservation_id as idempotency key
        'payment_capture': 1,   # Auto-capture on payment success
    })


def verify_payment_signature(
    razorpay_order_id: str,
    razorpay_payment_id: str,
    razorpay_signature: str,
) -> bool:
    """
    Verifies the HMAC-SHA256 signature returned by the Razorpay frontend SDK.

    The signature is computed as:
        HMAC-SHA256(razorpay_order_id + '|' + razorpay_payment_id, RAZORPAY_KEY_SECRET)

    Uses hmac.compare_digest() to prevent timing-attack leakage.

    See: https://razorpay.com/docs/payments/server-integration/python/payment-gateway/build-integration/#14-verify-payment-signature
    """
    message = f"{razorpay_order_id}|{razorpay_payment_id}"
    expected_signature = hmac.new(
        RAZORPAY_KEY_SECRET.encode('utf-8'),
        message.encode('utf-8'),
        hashlib.sha256,
    ).hexdigest()
    return hmac.compare_digest(expected_signature, razorpay_signature)


def verify_webhook_signature(
    webhook_body: bytes,
    webhook_signature: str,
    webhook_secret: str,
) -> bool:
    """
    Verifies Razorpay webhook signatures using the webhook secret.

    The webhook secret is SEPARATE from the API Key Secret — it is generated
    when registering the webhook endpoint on the Razorpay Dashboard.

    Uses hmac.compare_digest() to prevent timing-attack leakage.

    See: https://razorpay.com/docs/webhooks/validate-test/#validate-payment-related-webhooks
    """
    expected = hmac.new(
        webhook_secret.encode('utf-8'),
        webhook_body,
        hashlib.sha256,
    ).hexdigest()
    return hmac.compare_digest(expected, webhook_signature)


def issue_refund(
    razorpay_payment_id: str,
    amount_paise: int,
    notes: dict = None,
) -> dict:
    """
    Issues a full or partial refund via the Razorpay Refunds API.

    Args:
        razorpay_payment_id: The Razorpay payment ID (pay_XXXXXX).
        amount_paise:        Amount to refund in paise.
        notes:               Optional dict of metadata (e.g., reason, order_id).

    Returns:
        Razorpay refund object dict including 'id', 'amount', 'status'.

    Raises:
        razorpay.errors.BadRequestError: if payment cannot be refunded.
        Exception: on network errors.
    """
    client = get_client()
    payload = {'amount': amount_paise}
    if notes:
        payload['notes'] = notes
    return client.payment.refund(razorpay_payment_id, payload)
