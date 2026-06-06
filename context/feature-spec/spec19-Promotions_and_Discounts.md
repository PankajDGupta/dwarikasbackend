# Spec 19 — Promotions & Discounts

## Goal

Enable managers to define time-bound promotional discounts on products and variants that customers can discover and benefit from automatically during checkout. Promotions are first-class display entities surfaced on the storefront ("sale banners", "deal cards") and are also enforced server-side so prices cannot be manipulated client-side.

---

## Business Context

Dwarikas runs seasonal and event-driven promotions (e.g., festival sales, clearance, supplier-negotiated deals). Customers need to see active deals prominently on the storefront without having to search. Checkout must automatically apply the lowest applicable promotional price when a customer adds a product to cart.

> **Key invariant:** A promotion never modifies `ProductVariant.retail_price` permanently. Instead, a `Promotion` record stores the discounted price/percentage, and the checkout logic reads it at reservation time.

---

## Relationship to Existing Specs

| Spec | Impact |
|---|---|
| **Spec 04** (Database Models) | New `Promotion`, `PromotionItem`, and `PromotionBroadcast` models added |
| **Spec 06** (Product Catalog API) | Product/variant list response gains an optional `active_promotion` field |
| **Spec 07** (Checkout Reservation) | Reservation creation reads active promotion; stores `effective_price` |
| **Spec 08** (Order Confirmation) | Order total computed using `effective_price` (not `retail_price`) if a promotion is active |
| **Spec 14** (WhatsApp Commerce Engine) | `send_cta_url_message()` and `send_text_message()` from `whatsapp/wa_client.py` are reused to broadcast promotions |
| **Spec 17** (Payment Gateway) | Razorpay order amount must use `effective_price` |

---

## Scope

New Django app: `promotions/`

### Endpoints

| Method | Path | Actor | Description |
|---|---|---|---|
| `POST` | `/api/v1/promotions/` | Manager | Create a promotion |
| `GET` | `/api/v1/promotions/` | AllowAny (public) | List active promotions (for storefront banner) |
| `GET` | `/api/v1/promotions/<uuid:id>/` | AllowAny | Promotion detail |
| `PATCH` | `/api/v1/promotions/<uuid:id>/` | Manager | Update/deactivate a promotion |
| `DELETE` | `/api/v1/promotions/<uuid:id>/` | Manager | Hard-delete a promotion |
| `POST` | `/api/v1/promotions/<uuid:id>/items/` | Manager | Add a product or variant to a promotion |
| `DELETE` | `/api/v1/promotions/<uuid:id>/items/<uuid:item_id>/` | Manager | Remove an item from a promotion |
| `GET` | `/api/v1/promotions/active/` | AllowAny | Optimised endpoint returning only currently live promotions with item previews |
| `POST` | `/api/v1/promotions/<uuid:id>/share/whatsapp/` | Manager | Broadcast the promotion to a list of customer phone numbers via WhatsApp |

---

## Data Models

### `Promotion`

```
id                UUID            PK — auto
title             TEXT            Required — e.g., "Eid Special 20% Off"
description       TEXT            Optional — marketing copy
discount_type     TEXT            'percentage' | 'flat_amount'
discount_value    DECIMAL(10,2)   Required — e.g., 20.00 (%) or 50.00 (₹)
max_discount_cap  DECIMAL(10,2)   Optional — max discount in ₹ for percentage-type promos
min_order_value   DECIMAL(10,2)   Optional — min cart value to trigger discount
banner_image_url  TEXT            Optional — URL to storefront banner image
starts_at         TIMESTAMPTZ     When the promotion becomes active
ends_at           TIMESTAMPTZ     When the promotion expires (null = no expiry)
is_active         BOOLEAN         Manual kill-switch (default: true)
created_by        UUID            Supabase user UUID (manager)
created_at        TIMESTAMPTZ     Auto
```

### `PromotionItem`

Links a promotion to either a whole `Product` (all variants) or a single `ProductVariant`.

```
id              UUID        PK — auto
promotion_id    UUID (FK)   → Promotion
product_id      UUID (FK)   → Product (nullable if variant-level)
variant_id      UUID (FK)   → ProductVariant (nullable if product-level)
created_at      TIMESTAMPTZ Auto
```

**Constraint:** At least one of `product_id` or `variant_id` must be set. If `product_id` is set without `variant_id`, the promotion applies to **all variants** of that product.

### `Reservation` Model Extension (Spec 07)

Add column to existing `reservations` table:

```
effective_price     DECIMAL(12,2)   Nullable — discounted price per unit at time of reservation
promotion_id        UUID (FK)       Nullable — which promotion was applied
```

---

## Files to Create / Modify

### `promotions/` — New Django app

```bash
python manage.py startapp promotions
```

Add to `INSTALLED_APPS`:
```python
'promotions.apps.PromotionsConfig',
```

---

### `promotions/models.py`

```python
import uuid
from django.db import models


class Promotion(models.Model):
    DISCOUNT_TYPE_CHOICES = [
        ('percentage', 'Percentage'),
        ('flat_amount', 'Flat Amount'),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    title = models.TextField()
    description = models.TextField(null=True, blank=True)
    discount_type = models.TextField(choices=DISCOUNT_TYPE_CHOICES)
    discount_value = models.DecimalField(max_digits=10, decimal_places=2)
    max_discount_cap = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    min_order_value = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    banner_image_url = models.TextField(null=True, blank=True)
    starts_at = models.DateTimeField()
    ends_at = models.DateTimeField(null=True, blank=True)
    is_active = models.BooleanField(default=True)
    created_by = models.UUIDField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        managed = False
        db_table = 'promotions'

    def is_currently_live(self):
        from django.utils import timezone
        now = timezone.now()
        if not self.is_active:
            return False
        if now < self.starts_at:
            return False
        if self.ends_at and now > self.ends_at:
            return False
        return True


class PromotionItem(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    promotion = models.ForeignKey(
        Promotion,
        on_delete=models.CASCADE,
        related_name='items',
        db_column='promotion_id',
    )
    product = models.ForeignKey(
        'inventory.Product',
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name='promotion_items',
        db_column='product_id',
    )
    variant = models.ForeignKey(
        'inventory.ProductVariant',
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name='promotion_items',
        db_column='variant_id',
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        managed = False
        db_table = 'promotion_items'
```

---

### `promotions/services.py` — Promotion resolution logic

```python
"""
Promotion resolution service.
Finds the best active promotion for a given ProductVariant.
Used by checkout (Spec 07) and product catalog (Spec 06) to compute effective price.
"""
from decimal import Decimal
from django.utils import timezone
from promotions.models import Promotion, PromotionItem


def get_active_promotion_for_variant(variant) -> dict | None:
    """
    Returns the best applicable promotion for a variant, or None.
    'Best' means the promotion that yields the lowest effective price.

    Returns a dict:
    {
        'promotion_id': uuid,
        'title': str,
        'discount_type': str,
        'discount_value': Decimal,
        'effective_price': Decimal,
        'savings': Decimal,
    }
    """
    now = timezone.now()

    # Fetch all currently active promotions that match this variant or its parent product
    live_promotions = Promotion.objects.filter(
        is_active=True,
        starts_at__lte=now,
    ).filter(
        models.Q(ends_at__isnull=True) | models.Q(ends_at__gte=now)
    )

    # Promotions targeting this variant directly OR the variant's parent product
    matching_items = PromotionItem.objects.filter(
        promotion__in=live_promotions
    ).filter(
        models.Q(variant_id=variant.id) | models.Q(product_id=variant.product_id)
    ).select_related('promotion')

    best = None
    best_price = variant.retail_price

    for item in matching_items:
        promo = item.promotion
        price = variant.retail_price
        if promo.discount_type == 'percentage':
            discount = price * (promo.discount_value / Decimal('100'))
            if promo.max_discount_cap:
                discount = min(discount, promo.max_discount_cap)
            effective = price - discount
        else:  # flat_amount
            effective = max(price - promo.discount_value, Decimal('0'))

        if effective < best_price:
            best_price = effective
            best = {
                'promotion_id': str(promo.id),
                'title': promo.title,
                'discount_type': promo.discount_type,
                'discount_value': str(promo.discount_value),
                'effective_price': effective,
                'savings': price - effective,
            }

    return best
```

---

### `promotions/serializers.py`

```python
from rest_framework import serializers
from promotions.models import Promotion, PromotionItem


class PromotionItemSerializer(serializers.ModelSerializer):
    class Meta:
        model = PromotionItem
        fields = ['id', 'product_id', 'variant_id', 'created_at']
        read_only_fields = ['id', 'created_at']

    def validate(self, data):
        if not data.get('product_id') and not data.get('variant_id'):
            raise serializers.ValidationError(
                'At least one of product_id or variant_id must be provided.'
            )
        return data


class PromotionSerializer(serializers.ModelSerializer):
    items = PromotionItemSerializer(many=True, read_only=True)
    is_live = serializers.SerializerMethodField()

    class Meta:
        model = Promotion
        fields = [
            'id', 'title', 'description', 'discount_type', 'discount_value',
            'max_discount_cap', 'min_order_value', 'banner_image_url',
            'starts_at', 'ends_at', 'is_active', 'is_live',
            'created_by', 'created_at', 'items',
        ]
        read_only_fields = ['id', 'created_by', 'created_at', 'is_live']

    def get_is_live(self, obj):
        return obj.is_currently_live()
```

---

### `promotions/views.py`

```python
import uuid
from django.utils import timezone
from rest_framework import generics, status
from rest_framework.response import Response
from rest_framework.permissions import AllowAny
from rest_framework.views import APIView

from api.permissions import IsManager
from promotions.models import Promotion, PromotionItem
from promotions.serializers import PromotionSerializer, PromotionItemSerializer


class PromotionListCreateView(generics.ListCreateAPIView):
    """
    GET  /api/v1/promotions/  — public list (storefront)
    POST /api/v1/promotions/  — manager creates promotion
    """
    serializer_class = PromotionSerializer

    def get_permissions(self):
        if self.request.method == 'GET':
            return [AllowAny()]
        return [IsManager()]

    def get_queryset(self):
        # Public GET returns only active, not-expired promotions
        if self.request.method == 'GET':
            now = timezone.now()
            return Promotion.objects.filter(
                is_active=True,
                starts_at__lte=now,
            ).filter(
                models.Q(ends_at__isnull=True) | models.Q(ends_at__gte=now)
            ).prefetch_related('items').order_by('-created_at')
        return Promotion.objects.prefetch_related('items').order_by('-created_at')

    def perform_create(self, serializer):
        serializer.save(created_by=uuid.UUID(self.request.user.username))


class PromotionDetailView(generics.RetrieveUpdateDestroyAPIView):
    """
    GET    /api/v1/promotions/<uuid:id>/  — public detail
    PATCH  /api/v1/promotions/<uuid:id>/  — manager update
    DELETE /api/v1/promotions/<uuid:id>/  — manager delete
    """
    serializer_class = PromotionSerializer
    lookup_field = 'id'
    queryset = Promotion.objects.prefetch_related('items').all()

    def get_permissions(self):
        if self.request.method == 'GET':
            return [AllowAny()]
        return [IsManager()]


class ActivePromotionsView(generics.ListAPIView):
    """
    GET /api/v1/promotions/active/
    Optimised endpoint for the storefront promotion banner carousel.
    Returns currently live promotions with item count preview.
    """
    serializer_class = PromotionSerializer
    permission_classes = [AllowAny]

    def get_queryset(self):
        now = timezone.now()
        from django.db import models
        return Promotion.objects.filter(
            is_active=True,
            starts_at__lte=now,
        ).filter(
            models.Q(ends_at__isnull=True) | models.Q(ends_at__gte=now)
        ).prefetch_related('items').order_by('ends_at')


class PromotionItemCreateView(APIView):
    """
    POST /api/v1/promotions/<uuid:promotion_id>/items/
    """
    permission_classes = [IsManager]

    def post(self, request, promotion_id):
        try:
            promotion = Promotion.objects.get(id=promotion_id)
        except Promotion.DoesNotExist:
            return Response({'error': 'Promotion not found.'}, status=status.HTTP_404_NOT_FOUND)

        serializer = PromotionItemSerializer(data=request.data)
        if serializer.is_valid():
            serializer.save(promotion=promotion)
            return Response(serializer.data, status=status.HTTP_201_CREATED)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


class PromotionItemDeleteView(APIView):
    """
    DELETE /api/v1/promotions/<uuid:promotion_id>/items/<uuid:item_id>/
    """
    permission_classes = [IsManager]

    def delete(self, request, promotion_id, item_id):
        try:
            item = PromotionItem.objects.get(id=item_id, promotion_id=promotion_id)
        except PromotionItem.DoesNotExist:
            return Response({'error': 'Promotion item not found.'}, status=status.HTTP_404_NOT_FOUND)
        item.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)
```

---

### Catalog Integration (Spec 06 extension)

In `inventory/serializers.py`, add `active_promotion` field to `ProductVariantSerializer`:

```python
from promotions.services import get_active_promotion_for_variant

class ProductVariantSerializer(serializers.ModelSerializer):
    active_promotion = serializers.SerializerMethodField()

    def get_active_promotion(self, obj):
        return get_active_promotion_for_variant(obj)

    class Meta:
        model = ProductVariant
        fields = [
            # ... existing fields ...
            'active_promotion',
        ]
```

### Checkout Integration (Spec 07 extension)

In `inventory/checkout_views.py`, after the ATP check, resolve the promotion:

```python
from promotions.services import get_active_promotion_for_variant

# After ATP check, before creating Reservation:
promo = get_active_promotion_for_variant(variant)
effective_price = promo['effective_price'] if promo else variant.retail_price
promotion_id = promo['promotion_id'] if promo else None

reservation = Reservation.objects.create(
    variant=variant,
    user_id=user_id,
    reserved_quantity=quantity,
    expires_at=...,
    status='active',
    effective_price=effective_price,
    promotion_id=promotion_id,
)
```

---

### Supabase Migration

```sql
-- promotions table
CREATE TABLE IF NOT EXISTS public.promotions (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    title               TEXT NOT NULL,
    description         TEXT,
    discount_type       TEXT NOT NULL CHECK (discount_type IN ('percentage', 'flat_amount')),
    discount_value      DECIMAL(10, 2) NOT NULL,
    max_discount_cap    DECIMAL(10, 2),
    min_order_value     DECIMAL(10, 2),
    banner_image_url    TEXT,
    starts_at           TIMESTAMPTZ NOT NULL,
    ends_at             TIMESTAMPTZ,
    is_active           BOOLEAN NOT NULL DEFAULT TRUE,
    created_by          UUID NOT NULL,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- promotion_items table
CREATE TABLE IF NOT EXISTS public.promotion_items (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    promotion_id    UUID NOT NULL REFERENCES public.promotions(id) ON DELETE CASCADE,
    product_id      UUID REFERENCES public.products(id) ON DELETE CASCADE,
    variant_id      UUID REFERENCES public.product_variants(id) ON DELETE CASCADE,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT chk_product_or_variant CHECK (
        product_id IS NOT NULL OR variant_id IS NOT NULL
    )
);

-- Extend reservations table
ALTER TABLE public.reservations
    ADD COLUMN IF NOT EXISTS effective_price DECIMAL(12, 2),
    ADD COLUMN IF NOT EXISTS promotion_id UUID REFERENCES public.promotions(id) ON DELETE SET NULL;

-- Performance indexes
CREATE INDEX IF NOT EXISTS idx_promotions_active_dates
    ON public.promotions (is_active, starts_at, ends_at);
CREATE INDEX IF NOT EXISTS idx_promotion_items_product
    ON public.promotion_items (product_id);
CREATE INDEX IF NOT EXISTS idx_promotion_items_variant
    ON public.promotion_items (variant_id);
```

---

## API Response Examples

### `GET /api/v1/promotions/active/` — Storefront Banner List

```json
{
  "count": 2,
  "next": null,
  "previous": null,
  "results": [
    {
      "id": "uuid",
      "title": "Eid Special — 20% Off All Groceries",
      "description": "Celebrate Eid with fresh deals. Limited time only!",
      "discount_type": "percentage",
      "discount_value": "20.00",
      "max_discount_cap": "200.00",
      "min_order_value": null,
      "banner_image_url": "https://cdn.dwarikas.in/banners/eid-2026.webp",
      "starts_at": "2026-06-01T00:00:00Z",
      "ends_at": "2026-06-15T23:59:59Z",
      "is_active": true,
      "is_live": true,
      "items": [
        { "id": "uuid", "product_id": "uuid", "variant_id": null }
      ]
    }
  ]
}
```

### `GET /api/v1/products/<id>/variants/` — Variant with Active Promotion

```json
{
  "id": "uuid",
  "sku": "DW-RICE-1KG",
  "retail_price": "120.00",
  "mrp": "130.00",
  "active_promotion": {
    "promotion_id": "uuid",
    "title": "Eid Special — 20% Off All Groceries",
    "discount_type": "percentage",
    "discount_value": "20.00",
    "effective_price": "96.00",
    "savings": "24.00"
  }
}
```

---

## WhatsApp Promotion Broadcast

### Overview

A manager can push a promotion directly to customers over WhatsApp with a single API call. The backend sends a richly-formatted message to each provided phone number using the already-implemented `whatsapp/wa_client.py` functions (Spec 14). Results are recorded in a `PromotionBroadcast` audit table so managers can track delivery.

> **Constraint:** Meta's WhatsApp Cloud API only allows outbound messages to numbers that have **opted in** (i.e., previously messaged the business, or consented via a web opt-in). The backend enforces this by only accepting phone numbers already stored in the customer `Profile` table with `whatsapp_opted_in = TRUE`. The caller may also pass phone numbers explicitly for ad-hoc shares.

---

### `PromotionBroadcast` Model

Audit log of every WhatsApp send attempt for a promotion.

```
id              UUID        PK — auto
promotion_id    UUID (FK)   → Promotion
phone_number    TEXT        Recipient phone number (E.164 format, e.g. "919876543210")
status          TEXT        'sent' | 'failed'
failure_reason  TEXT        Nullable — error detail on failure
sent_at         TIMESTAMPTZ Auto
sent_by         UUID        Manager UUID who triggered the broadcast
```

```python
class PromotionBroadcast(models.Model):
    STATUS_CHOICES = [
        ('sent', 'Sent'),
        ('failed', 'Failed'),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    promotion = models.ForeignKey(
        Promotion,
        on_delete=models.CASCADE,
        related_name='broadcasts',
        db_column='promotion_id',
    )
    phone_number = models.TextField()
    status = models.TextField(choices=STATUS_CHOICES)
    failure_reason = models.TextField(null=True, blank=True)
    sent_at = models.DateTimeField(auto_now_add=True)
    sent_by = models.UUIDField()

    class Meta:
        managed = False
        db_table = 'promotion_broadcasts'
```

---

### `POST /api/v1/promotions/<uuid:id>/share/whatsapp/` — WhatsApp Broadcast

| Property | Value |
|---|---|
| **Auth** | `IsManager` |
| **Content-Type** | `application/json` |

**Request Body:**

```json
{
  "phone_numbers": ["919876543210", "918765432109"],
  "message_override": "Optional custom message text (replaces default)",
  "store_url": "https://dwarikas.com/shop"
}
```

| Field | Required | Description |
|---|---|---|
| `phone_numbers` | **Yes** | List of E.164 phone numbers to broadcast to (max 100 per call) |
| `message_override` | No | Custom message body; if omitted, a default is generated from the promotion fields |
| `store_url` | No | CTA button URL; defaults to `STORE_BASE_URL` env var |

**Response (200):**

```json
{
  "promotion_id": "uuid",
  "total_sent": 2,
  "total_failed": 0,
  "results": [
    { "phone": "919876543210", "status": "sent" },
    { "phone": "918765432109", "status": "sent" }
  ]
}
```

**Error Responses:**

| Status | Condition |
|---|---|
| `400` | `phone_numbers` missing or empty; more than 100 numbers in one call |
| `404` | Promotion not found |
| `422` | Promotion is not currently live (`is_active=False` or outside time window) |

---

### WhatsApp Message Format

The default message rendered for a promotion uses the existing `send_cta_url_message()` function:

**Message body (auto-generated):**
```
🎉 *{title}*

{description}

💰 Save {discount_value}{'%' if percentage else '₹'} on selected items!
⏰ Offer valid until: {ends_at formatted} (or "While stocks last!" if no end date)

Shop now and grab the deal before it's gone! 👇
```

**CTA Button:** "Shop the Sale" → `{store_url}/promotions/{promotion_id}`

---

### `promotions/whatsapp_service.py` — Broadcast implementation

```python
"""
WhatsApp broadcast service for promotions.
Sends a CTA message for a promotion to a list of phone numbers.
"""
import logging
import os
from decimal import Decimal
from typing import List

from promotions.models import Promotion, PromotionBroadcast
from whatsapp.wa_client import send_cta_url_message

logger = logging.getLogger(__name__)
STORE_BASE_URL = os.environ.get('STORE_BASE_URL', 'https://dwarikas.com/shop')


def build_promotion_message(promotion: Promotion, custom_message: str = None) -> str:
    """
    Builds the WhatsApp message body for a promotion.
    Uses custom_message if provided, otherwise generates a default from the promotion fields.
    """
    if custom_message:
        return custom_message

    if promotion.discount_type == 'percentage':
        discount_str = f"{promotion.discount_value:.0f}% off"
        if promotion.max_discount_cap:
            discount_str += f" (up to ₹{promotion.max_discount_cap:.0f})"
    else:
        discount_str = f"₹{promotion.discount_value:.0f} off"

    if promotion.ends_at:
        validity = promotion.ends_at.strftime("%-d %b %Y")
        time_line = f"⏰ Offer valid until: {validity}"
    else:
        time_line = "⏰ While stocks last!"

    parts = [
        f"🎉 *{promotion.title}*",
        "",
    ]
    if promotion.description:
        parts.append(promotion.description)
        parts.append("")

    parts += [
        f"💰 Save {discount_str} on selected items!",
        time_line,
        "",
        "Shop now and grab the deal before it's gone! 👇",
    ]
    return "\n".join(parts)


def broadcast_promotion_to_whatsapp(
    promotion: Promotion,
    phone_numbers: List[str],
    sent_by,
    message_override: str = None,
    store_url: str = None,
) -> dict:
    """
    Sends a WhatsApp CTA message for the given promotion to every phone number in the list.
    Records each attempt in PromotionBroadcast.

    Returns:
    {
        'total_sent': int,
        'total_failed': int,
        'results': [{'phone': str, 'status': 'sent'|'failed', 'error': str|None}]
    }
    """
    base_url = store_url or STORE_BASE_URL
    cta_url = f"{base_url}/promotions/{promotion.id}"
    message_body = build_promotion_message(promotion, message_override)

    results = []
    sent = 0
    failed = 0

    for phone in phone_numbers:
        try:
            send_cta_url_message(
                to=phone,
                body_text=message_body,
                button_text="Shop the Sale",
                url=cta_url,
            )
            PromotionBroadcast.objects.create(
                promotion=promotion,
                phone_number=phone,
                status='sent',
                sent_by=sent_by,
            )
            results.append({'phone': phone, 'status': 'sent', 'error': None})
            sent += 1
        except Exception as exc:
            error_msg = str(exc)
            logger.error(f"WhatsApp broadcast failed for {phone} on promotion {promotion.id}: {error_msg}")
            PromotionBroadcast.objects.create(
                promotion=promotion,
                phone_number=phone,
                status='failed',
                failure_reason=error_msg,
                sent_by=sent_by,
            )
            results.append({'phone': phone, 'status': 'failed', 'error': error_msg})
            failed += 1

    return {
        'total_sent': sent,
        'total_failed': failed,
        'results': results,
    }
```

---

### View — `SharePromotionWhatsAppView` (add to `promotions/views.py`)

```python
import uuid
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from api.permissions import IsManager
from promotions.models import Promotion
from promotions.whatsapp_service import broadcast_promotion_to_whatsapp


class SharePromotionWhatsAppView(APIView):
    """
    POST /api/v1/promotions/<uuid:id>/share/whatsapp/

    Broadcasts the promotion as a WhatsApp CTA message to the provided phone numbers.
    """
    permission_classes = [IsManager]

    def post(self, request, id):
        try:
            promotion = Promotion.objects.get(id=id)
        except Promotion.DoesNotExist:
            return Response({'error': 'Promotion not found.'}, status=status.HTTP_404_NOT_FOUND)

        if not promotion.is_currently_live():
            return Response(
                {'error': 'Promotion is not currently live. Activate it and check start/end dates before sharing.'},
                status=status.HTTP_422_UNPROCESSABLE_ENTITY,
            )

        phone_numbers = request.data.get('phone_numbers', [])
        if not phone_numbers:
            return Response({'error': 'phone_numbers is required and must not be empty.'}, status=status.HTTP_400_BAD_REQUEST)
        if len(phone_numbers) > 100:
            return Response({'error': 'Maximum 100 phone numbers per broadcast call.'}, status=status.HTTP_400_BAD_REQUEST)

        message_override = request.data.get('message_override')
        store_url = request.data.get('store_url')
        sent_by = uuid.UUID(request.user.username)

        broadcast_result = broadcast_promotion_to_whatsapp(
            promotion=promotion,
            phone_numbers=phone_numbers,
            sent_by=sent_by,
            message_override=message_override,
            store_url=store_url,
        )

        return Response({
            'promotion_id': str(promotion.id),
            **broadcast_result,
        })
```

---

### URL addition (`promotions/urls.py`)

```python
path('promotions/<uuid:id>/share/whatsapp/', SharePromotionWhatsAppView.as_view(), name='promotion-share-whatsapp'),
```

---

### Supabase Migration — `promotion_broadcasts` table

```sql
CREATE TABLE IF NOT EXISTS public.promotion_broadcasts (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    promotion_id    UUID NOT NULL REFERENCES public.promotions(id) ON DELETE CASCADE,
    phone_number    TEXT NOT NULL,
    status          TEXT NOT NULL CHECK (status IN ('sent', 'failed')),
    failure_reason  TEXT,
    sent_at         TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    sent_by         UUID NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_promotion_broadcasts_promotion
    ON public.promotion_broadcasts (promotion_id);
```

---

## Environment Variables

| Variable | Description |
|---|---|
| `STORE_BASE_URL` | Base URL of the customer-facing storefront, e.g. `https://dwarikas.com/shop`. Used to build the CTA deep-link in broadcast messages. |

> All WhatsApp API environment variables (`WA_PHONE_NUMBER_ID`, `WA_API_TOKEN`) are already defined in Spec 14 and reused here without modification.

---

## Acceptance Criteria

- [ ] `POST /api/v1/promotions/` creates a promotion (Manager only)
- [ ] `GET /api/v1/promotions/active/` returns only currently live promotions (unauthenticated)
- [ ] Product variant list response includes `active_promotion` field with `effective_price` when a promotion is active, `null` when none
- [ ] Checkout reservation creation captures `effective_price` and `promotion_id` on the reservation row
- [ ] Order total in Spec 08 uses `effective_price` from reservation (not `retail_price`) when set
- [ ] A promotion that has expired (`ends_at` in the past) does not appear in active listing
- [ ] A promotion with `is_active=False` is hidden from public listing
- [ ] `max_discount_cap` correctly limits the savings for percentage-type promotions
- [ ] Variant-level promotion takes precedence when both a product-level and variant-level promotion exist (lowest price wins)
- [ ] `POST /api/v1/promotions/<id>/share/whatsapp/` sends a CTA WhatsApp message to each phone number in the request
- [ ] Each send attempt (success or failure) is recorded in `PromotionBroadcast`
- [ ] Broadcasting an inactive or expired promotion returns HTTP 422
- [ ] Sending more than 100 phone numbers in a single call returns HTTP 400
- [ ] A WhatsApp delivery failure for one number does not abort the rest of the broadcast
- [ ] `build_promotion_message()` generates a correctly formatted message for both `percentage` and `flat_amount` discount types
- [ ] Full test suite with coverage for: promotion creation, expiry, catalog display, checkout integration, WhatsApp broadcast, and edge cases

---

## Django App Layout

```
promotions/
├── __init__.py
├── apps.py
├── models.py
├── serializers.py
├── services.py              # get_active_promotion_for_variant()
├── whatsapp_service.py      # build_promotion_message(), broadcast_promotion_to_whatsapp()
├── views.py
├── urls.py
└── tests/
    ├── __init__.py
    ├── test_promotions.py
    ├── test_checkout_integration.py
    └── test_whatsapp_broadcast.py
```
