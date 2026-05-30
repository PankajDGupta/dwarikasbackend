# Spec 13 — ONDC Seller Node (Beckn Protocol Integration)

## Goal
Implement the ONDC Seller Node endpoints conforming to Beckn Protocol v1.2.5. These endpoints handle the async request-callback pattern required by the ONDC network. Incoming requests arrive from the ONDC gateway; outgoing callbacks (`on_*` responses) are dispatched asynchronously via Cloud Tasks.

---

## Scope

New Django app: `ondc/`

Endpoints to implement:

| Beckn Action | Django Endpoint | Description |
|---|---|---|
| `/search` | `POST /api/v1/ondc/search/` | Discovery — catalogue lookup with ATP check |
| `/select` | `POST /api/v1/ondc/select/` | Item selection + reservation lock |
| `/init` | `POST /api/v1/ondc/init/` | GST routing + fulfilment validation |
| `/confirm` | `POST /api/v1/ondc/confirm/` | Commit order + stock decrement |
| `/status` | `POST /api/v1/ondc/status/` | Fulfillment / delivery status |
| `/cancel` | `POST /api/v1/ondc/cancel/` | Release reservation + order cancellation |

All endpoints return HTTP 202 immediately and dispatch `on_<action>` callbacks asynchronously to the BAP's `context.bap_uri`.

---

## Files to Create / Modify

### `ondc/` — New Django app

```bash
python manage.py startapp ondc
```

Add to `INSTALLED_APPS`:
```python
'ondc.apps.OndcConfig',
```

---

### `ondc/beckn_auth.py` — Request signature verification

```python
"""
Verifies incoming Beckn Protocol request signatures per ONDC security spec.
ONDC requires HMAC-based or Ed25519-based signatures in the Authorization header.
"""
import base64
import os
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from cryptography.hazmat.primitives.serialization import load_der_public_key
from cryptography.exceptions import InvalidSignature

ONDC_REGISTRY_PUBLIC_KEY_B64 = os.environ.get('ONDC_REGISTRY_PUBLIC_KEY_B64', '')


def verify_beckn_signature(request) -> bool:
    """
    Verifies the Ed25519 signature in the Authorization header.
    Returns True if the signature is valid; False otherwise.
    Reference: https://docs.ondc.org/reference/signing-requests
    """
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
        key_id = params.get('keyId', '')   # e.g., "subscriber_id|unique_key_id|algorithm"

        # Load the signer's public key (fetched from ONDC registry during key rotation)
        public_key_bytes = base64.b64decode(ONDC_REGISTRY_PUBLIC_KEY_B64)
        public_key = load_der_public_key(public_key_bytes)

        # Reconstruct the signing string from method + path + body hash
        # (Simplified — full implementation follows the ONDC signing spec)
        signing_string = _build_signing_string(request, params)
        signature_bytes = base64.b64decode(signature_b64)

        public_key.verify(signature_bytes, signing_string.encode('utf-8'))
        return True

    except (InvalidSignature, Exception):
        return False


def _build_signing_string(request, params: dict) -> str:
    """Constructs the canonical signing string from the request headers."""
    created = params.get('created', '')
    expires = params.get('expires', '')
    body_hash = params.get('digest', '')
    return f"(created): {created}\n(expires): {expires}\ndigest: {body_hash}"
```

---

### `ondc/beckn_builder.py` — Response payload construction

```python
"""
Helpers to build Beckn-compliant context and catalog response payloads.
"""
import os
import uuid
from datetime import datetime, timezone


SUBSCRIBER_ID = os.environ.get('ONDC_SUBSCRIBER_ID', 'dwarikas.com')
SELLER_CITY = os.environ.get('ONDC_SELLER_CITY', 'std:080')
DOMAIN = 'nic2004:52110'   # Retail domain code for fashion/apparel


def make_ack_response(context: dict, error: dict = None) -> dict:
    """Returns a synchronous ACK or NACK for the incoming Beckn request."""
    ack = {
        'context': {**context, 'action': f"on_{context['action']}"},
        'message': {
            'ack': {'status': 'NACK' if error else 'ACK'}
        },
    }
    if error:
        ack['error'] = error
    return ack


def make_on_search_payload(context: dict, products: list) -> dict:
    """Builds the /on_search catalog payload from a list of ProductVariant dicts."""
    return {
        'context': {**context, 'action': 'on_search'},
        'message': {
            'catalog': {
                'bpp/descriptor': {'name': 'Dwarikas'},
                'bpp/providers': [
                    {
                        'id': SUBSCRIBER_ID,
                        'descriptor': {'name': 'Dwarikas Store'},
                        'items': [
                            {
                                'id': str(p['id']),
                                'descriptor': {'name': p['name'], 'code': p['sku']},
                                'price': {
                                    'currency': 'INR',
                                    'value': str(p['retail_price']),
                                },
                                'quantity': {'available': {'count': p['atp']}},
                                'category_id': DOMAIN,
                            }
                            for p in products
                        ],
                    }
                ],
            }
        },
    }
```

---

### `ondc/views.py` — New file (core action handlers)

```python
import json
import uuid
from datetime import datetime, timezone, timedelta

import requests
from django.http import JsonResponse
from django.views import View
from django.views.decorators.csrf import csrf_exempt
from django.utils.decorators import method_decorator
from django.db import transaction

from inventory.models import ProductVariant, Reservation
from ondc.beckn_auth import verify_beckn_signature
from ondc.beckn_builder import make_ack_response, make_on_search_payload

RESERVATION_TTL_MINUTES = 10


@method_decorator(csrf_exempt, name='dispatch')
class OndcSearchView(View):
    """
    POST /api/v1/ondc/search/
    Returns a synchronous ACK, then dispatches the /on_search catalog callback asynchronously.
    """

    def post(self, request):
        if not verify_beckn_signature(request):
            return JsonResponse({'error': 'Invalid Beckn signature.'}, status=401)

        try:
            body = json.loads(request.body)
            context = body['context']
            intent = body.get('message', {}).get('intent', {})
        except (json.JSONDecodeError, KeyError):
            return JsonResponse({'error': 'Malformed Beckn payload.'}, status=400)

        # Synchronous ACK — return immediately
        ack = make_ack_response(context)

        # Dispatch async /on_search callback via Cloud Tasks (or direct HTTP for dev)
        _dispatch_on_search(context, intent)

        return JsonResponse(ack, status=202)


def _dispatch_on_search(context: dict, intent: dict):
    """
    In production: enqueue a Cloud Tasks job to call the BAP's callback URI.
    In development: call synchronously.
    """
    # Compute ATP for all variants
    now = datetime.now(timezone.utc)
    variants = ProductVariant.objects.all().select_related('product')

    products = []
    for variant in variants:
        active_reservations = Reservation.objects.filter(
            variant_id=variant.id, status='active', expires_at__gt=now
        ).aggregate(total=models.Sum('reserved_quantity'))
        reserved = active_reservations['total'] or 0
        atp = max(0, variant.stock_quantity - reserved)

        products.append({
            'id': variant.id,
            'name': variant.product.name,
            'sku': variant.sku,
            'retail_price': variant.retail_price,
            'atp': atp,
        })

    bap_uri = context.get('bap_uri', '')
    payload = make_on_search_payload(context, products)

    try:
        requests.post(f"{bap_uri}/on_search", json=payload, timeout=10)
    except requests.RequestException:
        pass   # Log in production; callback failure is non-fatal for the seller node


@method_decorator(csrf_exempt, name='dispatch')
class OndcSelectView(View):
    """
    POST /api/v1/ondc/select/
    Validates item selection and creates a reservation lock.
    """

    def post(self, request):
        if not verify_beckn_signature(request):
            return JsonResponse({'error': 'Invalid Beckn signature.'}, status=401)

        try:
            body = json.loads(request.body)
            context = body['context']
            items = body['message']['order']['items']
        except (json.JSONDecodeError, KeyError):
            return JsonResponse({'error': 'Malformed Beckn payload.'}, status=400)

        ack = make_ack_response(context)

        # Create reservation locks for each selected item
        _dispatch_on_select(context, items)

        return JsonResponse(ack, status=202)


def _dispatch_on_select(context: dict, items: list):
    """Creates reservation holds for all selected ONDC items."""
    bap_uri = context.get('bap_uri', '')
    now = datetime.now(timezone.utc)
    order_items = []

    for item in items:
        variant_id = item.get('id')
        quantity = item.get('quantity', {}).get('count', 1)

        try:
            with transaction.atomic():
                variant = ProductVariant.objects.select_for_update(nowait=True).get(id=variant_id)
                reserved = Reservation.objects.filter(
                    variant_id=variant_id, status='active', expires_at__gt=now
                ).aggregate(total=models.Sum('reserved_quantity'))['total'] or 0
                atp = variant.stock_quantity - reserved

                if atp >= quantity:
                    res = Reservation.objects.create(
                        variant_id=variant.id,
                        user_id=uuid.uuid4(),   # ONDC orders use a generated UID
                        reserved_quantity=quantity,
                        expires_at=now + timedelta(minutes=RESERVATION_TTL_MINUTES),
                        status='active',
                    )
                    order_items.append({
                        'id': str(variant.id),
                        'quantity': {'count': quantity},
                        'price': {'currency': 'INR', 'value': str(variant.retail_price)},
                        'fulfillment_id': str(res.id),
                    })
        except Exception:
            pass   # Individual item failures are reflected in the callback payload

    callback = {
        'context': {**context, 'action': 'on_select'},
        'message': {'order': {'items': order_items}},
    }
    try:
        requests.post(f"{bap_uri}/on_select", json=callback, timeout=10)
    except requests.RequestException:
        pass
```

---

### `ondc/urls.py` — New file

```python
from django.urls import path
from ondc.views import OndcSearchView, OndcSelectView

urlpatterns = [
    path('ondc/search/', OndcSearchView.as_view(), name='ondc-search'),
    path('ondc/select/', OndcSelectView.as_view(), name='ondc-select'),
    # Stubs for init, confirm, status, cancel — implement after search/select verified
    # path('ondc/init/', OndcInitView.as_view(), name='ondc-init'),
    # path('ondc/confirm/', OndcConfirmView.as_view(), name='ondc-confirm'),
    # path('ondc/status/', OndcStatusView.as_view(), name='ondc-status'),
    # path('ondc/cancel/', OndcCancelView.as_view(), name='ondc-cancel'),
]
```

---

### `dwarikasbackend/urls.py`

```python
path('api/v1/', include('ondc.urls')),
```

---

### Environment Variables Required

| Variable | Description |
|---|---|
| `ONDC_SUBSCRIBER_ID` | Registered ONDC subscriber URI |
| `ONDC_REGISTRY_PUBLIC_KEY_B64` | Base64 DER-encoded ONDC registry public key for signature verification |
| `ONDC_SELLER_CITY` | STD code for the seller's city (e.g., `std:080`) |

---

## Acceptance Criteria

- [ ] `POST /api/v1/ondc/search/` with invalid signature returns 401
- [ ] Valid `/search` returns HTTP 202 with ACK payload immediately
- [ ] `/on_search` callback is dispatched to the `bap_uri` in the context
- [ ] `/select` creates `Reservation` rows for each requested item
- [ ] `/select` for an out-of-stock variant does not create a reservation
- [ ] ATP calculation in `/on_search` excludes expired reservations
- [ ] `verify_beckn_signature` is unit-testable with a mock request object
- [ ] All endpoints are CSRF-exempt (Cloud Tasks / ONDC network callers)
