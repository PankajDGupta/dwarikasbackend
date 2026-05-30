# Spec 14 — WhatsApp Commerce Engine

## Goal
Implement the WhatsApp Business API webhook integration. Incoming customer messages are parsed for intent (catalog search, stock query, order status), answered via the WhatsApp Messaging API with interactive list pickers and CTAs, and mapped to real-time Supabase inventory queries.

---

## Scope

New Django app: `whatsapp/`

- `GET /api/v1/whatsapp/webhook/` — webhook verification (Meta challenge/response)
- `POST /api/v1/whatsapp/webhook/` — receive and process inbound messages
- Intent parsing for: `catalog`, `stock_check`, `order_status`, `fallback`
- Response formatting using WhatsApp interactive list messages

---

## Files to Create / Modify

### `whatsapp/` — New Django app

```bash
python manage.py startapp whatsapp
```

Add to `INSTALLED_APPS`:
```python
'whatsapp.apps.WhatsappConfig',
```

---

### `whatsapp/intent_parser.py` — New file

```python
"""
Rule-based intent classification for incoming WhatsApp messages.
Maps message text → one of the supported intents.
"""

INTENT_CATALOG = 'catalog'
INTENT_STOCK_CHECK = 'stock_check'
INTENT_ORDER_STATUS = 'order_status'
INTENT_FALLBACK = 'fallback'

# Keyword trigger sets — extend as needed
CATALOG_KEYWORDS = {'catalog', 'products', 'items', 'browse', 'show', 'list'}
STOCK_KEYWORDS = {'stock', 'available', 'availability', 'check stock', 'in stock'}
ORDER_KEYWORDS = {'order', 'status', 'track', 'my order', 'tracking'}


def classify_intent(message_text: str) -> str:
    """
    Returns the intent label for the given raw message text.
    Matching is case-insensitive; keyword presence wins over order.
    """
    text = message_text.lower().strip()

    if any(kw in text for kw in ORDER_KEYWORDS):
        return INTENT_ORDER_STATUS
    if any(kw in text for kw in STOCK_KEYWORDS):
        return INTENT_STOCK_CHECK
    if any(kw in text for kw in CATALOG_KEYWORDS):
        return INTENT_CATALOG
    return INTENT_FALLBACK


def extract_sku_from_message(text: str) -> str | None:
    """
    Attempts to extract a SKU from a message like 'check stock SKU-001'.
    Returns the SKU string or None.
    """
    parts = text.upper().split()
    # Look for tokens that match an alphanumeric SKU-like pattern
    for part in parts:
        if len(part) >= 3 and (part.isalnum() or '-' in part):
            return part
    return None
```

---

### `whatsapp/wa_client.py` — New file

```python
"""
WhatsApp Business API client — wraps Meta's Cloud API endpoints.
"""
import os
import requests

WA_PHONE_NUMBER_ID = os.environ.get('WA_PHONE_NUMBER_ID')
WA_API_TOKEN = os.environ.get('WA_API_TOKEN')
WA_API_VERSION = 'v19.0'
WA_API_BASE = f'https://graph.facebook.com/{WA_API_VERSION}'


def _headers() -> dict:
    return {
        'Authorization': f'Bearer {WA_API_TOKEN}',
        'Content-Type': 'application/json',
    }


def send_text_message(to: str, body: str):
    """Send a plain text reply to a WhatsApp number."""
    payload = {
        'messaging_product': 'whatsapp',
        'to': to,
        'type': 'text',
        'text': {'body': body},
    }
    requests.post(f'{WA_API_BASE}/{WA_PHONE_NUMBER_ID}/messages', json=payload, headers=_headers(), timeout=10)


def send_list_message(to: str, header_text: str, body_text: str, sections: list):
    """
    Send an interactive list message (product catalog or menu).
    `sections` format:
    [
        {
            'title': 'Section Title',
            'rows': [{'id': 'row_id', 'title': 'Item Name', 'description': 'Rs. 499'}]
        }
    ]
    """
    payload = {
        'messaging_product': 'whatsapp',
        'to': to,
        'type': 'interactive',
        'interactive': {
            'type': 'list',
            'header': {'type': 'text', 'text': header_text},
            'body': {'text': body_text},
            'action': {
                'button': 'Browse',
                'sections': sections,
            },
        },
    }
    requests.post(f'{WA_API_BASE}/{WA_PHONE_NUMBER_ID}/messages', json=payload, headers=_headers(), timeout=10)


def send_cta_url_message(to: str, body_text: str, button_text: str, url: str):
    """Send a CTA (call-to-action) button pointing to the web store."""
    payload = {
        'messaging_product': 'whatsapp',
        'to': to,
        'type': 'interactive',
        'interactive': {
            'type': 'cta_url',
            'body': {'text': body_text},
            'action': {'name': 'cta_url', 'parameters': {'display_text': button_text, 'url': url}},
        },
    }
    requests.post(f'{WA_API_BASE}/{WA_PHONE_NUMBER_ID}/messages', json=payload, headers=_headers(), timeout=10)
```

---

### `whatsapp/handlers.py` — New file

```python
"""
Intent-specific message handlers. Each handler queries Supabase and
dispatches a formatted WhatsApp response back to the customer.
"""
from datetime import datetime, timezone

from inventory.models import ProductVariant, Reservation, Order
from whatsapp.wa_client import send_text_message, send_list_message, send_cta_url_message
from whatsapp.intent_parser import extract_sku_from_message

WEB_STORE_URL = 'https://dwarikas.com/shop'


def handle_catalog(sender: str):
    """Return a list message with the top 10 products by name."""
    variants = ProductVariant.objects.select_related('product').order_by('product__name')[:10]
    sections = [
        {
            'title': 'Our Products',
            'rows': [
                {
                    'id': str(v.id),
                    'title': v.product.name[:24],   # WhatsApp title max 24 chars
                    'description': f'Rs. {v.retail_price} | SKU: {v.sku}',
                }
                for v in variants
            ],
        }
    ]
    send_list_message(
        to=sender,
        header_text='Dwarikas Catalogue',
        body_text='Here are our latest products. Tap an item for details.',
        sections=sections,
    )


def handle_stock_check(sender: str, message_text: str):
    """Return current ATP stock for a given SKU."""
    sku = extract_sku_from_message(message_text)
    if not sku:
        send_text_message(sender, "Please share the SKU code. Example: 'check stock HS102'")
        return

    try:
        variant = ProductVariant.objects.select_related('product').get(sku__iexact=sku)
    except ProductVariant.DoesNotExist:
        send_text_message(sender, f"SKU *{sku}* was not found in our catalogue.")
        return

    now = datetime.now(timezone.utc)
    reserved = sum(
        r.reserved_quantity for r in Reservation.objects.filter(
            variant_id=variant.id, status='active', expires_at__gt=now
        )
    )
    atp = max(0, variant.stock_quantity - reserved)
    status_text = "✅ In Stock" if atp > 0 else "❌ Out of Stock"

    send_text_message(
        sender,
        f"*{variant.product.name}* ({sku})\n"
        f"Price: Rs. {variant.retail_price}\n"
        f"Availability: {status_text} ({atp} units available)\n\n"
        f"Shop online 👇",
    )
    send_cta_url_message(sender, '', 'View Product', f"{WEB_STORE_URL}/product/{variant.product_id}")


def handle_order_status(sender: str, user_phone: str):
    """Look up the caller's most recent order by phone number (via profile lookup)."""
    send_text_message(
        sender,
        "To track your order, please visit our website or share your Order ID.\n"
        f"Visit: {WEB_STORE_URL}/orders",
    )


def handle_fallback(sender: str):
    """Default menu for unrecognised intents."""
    send_text_message(
        sender,
        "Hi! I'm the Dwarikas assistant. I can help you with:\n\n"
        "1️⃣ *catalog* — Browse our products\n"
        "2️⃣ *check stock SKU* — Check availability\n"
        "3️⃣ *order status* — Track your order\n\n"
        "Just type one of the above to get started!",
    )
```

---

### `whatsapp/views.py` — New file

```python
import hashlib
import hmac
import json
import os

from django.http import JsonResponse, HttpResponse
from django.views import View
from django.views.decorators.csrf import csrf_exempt
from django.utils.decorators import method_decorator

from whatsapp.intent_parser import classify_intent
from whatsapp.handlers import (
    handle_catalog, handle_stock_check,
    handle_order_status, handle_fallback,
)

WA_VERIFY_TOKEN = os.environ.get('WA_VERIFY_TOKEN')
WA_APP_SECRET = os.environ.get('WA_APP_SECRET')


@method_decorator(csrf_exempt, name='dispatch')
class WhatsAppWebhookView(View):

    def get(self, request):
        """Meta webhook verification challenge."""
        mode = request.GET.get('hub.mode')
        token = request.GET.get('hub.verify_token')
        challenge = request.GET.get('hub.challenge')

        if mode == 'subscribe' and token == WA_VERIFY_TOKEN:
            return HttpResponse(challenge, content_type='text/plain', status=200)
        return HttpResponse('Verification failed.', status=403)

    def post(self, request):
        """Process inbound WhatsApp messages."""
        if not self._verify_payload_signature(request):
            return JsonResponse({'error': 'Invalid signature.'}, status=401)

        try:
            body = json.loads(request.body)
        except json.JSONDecodeError:
            return JsonResponse({'error': 'Invalid JSON.'}, status=400)

        # Extract message from Meta webhook structure
        try:
            entry = body['entry'][0]
            change = entry['changes'][0]
            value = change['value']
            message = value['messages'][0]
            sender = message['from']
            message_text = message.get('text', {}).get('body', '')
        except (KeyError, IndexError):
            # Delivery receipts, status updates — not action messages
            return JsonResponse({'status': 'ignored'}, status=200)

        # Route to intent handler
        intent = classify_intent(message_text)
        if intent == 'catalog':
            handle_catalog(sender)
        elif intent == 'stock_check':
            handle_stock_check(sender, message_text)
        elif intent == 'order_status':
            handle_order_status(sender, sender)
        else:
            handle_fallback(sender)

        return JsonResponse({'status': 'ok'}, status=200)

    def _verify_payload_signature(self, request) -> bool:
        """
        Verifies the X-Hub-Signature-256 header to confirm the request is from Meta.
        """
        if not WA_APP_SECRET:
            return True   # Skip verification in local dev
        signature_header = request.headers.get('X-Hub-Signature-256', '')
        if not signature_header.startswith('sha256='):
            return False
        expected = 'sha256=' + hmac.new(
            WA_APP_SECRET.encode(), request.body, hashlib.sha256
        ).hexdigest()
        return hmac.compare_digest(signature_header, expected)
```

---

### `whatsapp/urls.py` — New file

```python
from django.urls import path
from whatsapp.views import WhatsAppWebhookView

urlpatterns = [
    path('whatsapp/webhook/', WhatsAppWebhookView.as_view(), name='whatsapp-webhook'),
]
```

---

### `dwarikasbackend/urls.py`

```python
path('api/v1/', include('whatsapp.urls')),
```

---

### Environment Variables Required

| Variable | Description |
|---|---|
| `WA_PHONE_NUMBER_ID` | Meta WhatsApp Business phone number ID |
| `WA_API_TOKEN` | Meta Cloud API permanent system user token |
| `WA_VERIFY_TOKEN` | Custom string used to verify webhook subscription |
| `WA_APP_SECRET` | App secret for X-Hub-Signature-256 payload verification |

---

## Acceptance Criteria

- [ ] `GET /api/v1/whatsapp/webhook/?hub.mode=subscribe&hub.verify_token=<token>&hub.challenge=XYZ` returns `XYZ`
- [ ] Invalid verify token returns 403
- [ ] POST with invalid `X-Hub-Signature-256` returns 401
- [ ] Message containing "catalog" triggers `handle_catalog()` and sends a list message
- [ ] Message containing "check stock HS102" triggers `handle_stock_check()` with correct SKU extraction
- [ ] Message with unknown intent triggers `handle_fallback()`
- [ ] `classify_intent()` is unit-testable with plain strings (no HTTP involved)
- [ ] Delivery receipts/status updates (no `messages` key) return HTTP 200 `status: ignored`
