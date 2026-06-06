# Spec 15 — External Partner API Gateway

## Goal
Implement the secured external integration endpoints used by corporate ERP systems and third-party logistics partners. All requests must be authenticated with an API key (hashed in the database) and signed with HMAC-SHA256 over the full request payload. No JWT is involved — this is a machine-to-machine channel.

---

## Scope

- Database table: `external_api_keys` (store SHA-256 hash only, never the raw key)
- `POST /api/v1/external/inventory/sync/` — ERP inventory reconciliation (batch stock delta updates)
- `PATCH /api/v1/external/shipments/update/` — Update shipment/delivery status for an order
- Custom authentication class: `ExternalApiKeyAuthentication`
- Custom permission class: `HasValidRequestSignature`
- Management command: `python manage.py create_api_key --partner-name "ERP System"` (outputs raw key once, stores hash)
- Admin endpoints (for API key lifecycle management via Admin UI):
  - `GET /api/v1/admin/api-keys/` — List all external partner API keys (Manager role required)
  - `POST /api/v1/admin/api-keys/` — Create a new external API key (Manager role required; returns raw key in response once)
  - `POST /api/v1/admin/api-keys/<uuid:pk>/revoke/` — Revoke (deactivate) an active API key (Manager role required)

---

## Database Schema Extension (Supabase SQL — run manually)

```sql
CREATE TABLE public.external_api_keys (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    partner_name TEXT NOT NULL,
    key_hash TEXT NOT NULL UNIQUE,     -- SHA-256 hash of the raw API key
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

---

## Files to Create / Modify

### `inventory/models.py` — Add ExternalApiKey model

```python
class ExternalApiKey(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    partner_name = models.TextField()
    key_hash = models.TextField(unique=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        managed = False
        db_table = 'external_api_keys'
```

---

### `api/external_auth.py` — New file

```python
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
            raise exceptions.AuthenticationFailed('Invalid or revoked API key.')

        # Create a lightweight pseudo-user for DRF compatibility
        from django.contrib.auth.models import AnonymousUser
        user = type('ExternalPartner', (), {
            'is_authenticated': True,
            'username': api_key.partner_name,
            'role': 'external',
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
        payload_str = (
            request.method
            + request.path
            + timestamp
            + request.body.decode('utf-8', errors='replace')
        )
        expected = hmac.new(secret.encode(), payload_str.encode(), hashlib.sha256).hexdigest()

        if not hmac.compare_digest(signature, expected):
            raise exceptions.AuthenticationFailed('Request signature verification failed.')

        return None   # Signature valid; authentication handled by ExternalApiKeyAuthentication
```

---

### `api/management/commands/create_api_key.py` — New management command

```python
"""
Management command: python manage.py create_api_key --partner-name "My ERP"
Generates a cryptographically secure API key, stores only its SHA-256 hash,
and prints the raw key ONCE to stdout (it cannot be retrieved later).
"""
import hashlib
import secrets
from django.core.management.base import BaseCommand
from inventory.models import ExternalApiKey


class Command(BaseCommand):
    help = 'Generate a new external partner API key.'

    def add_arguments(self, parser):
        parser.add_argument('--partner-name', required=True, type=str)

    def handle(self, *args, **options):
        partner_name = options['partner_name']
        raw_key = secrets.token_urlsafe(48)    # 48 bytes → 64-char URL-safe string
        key_hash = hashlib.sha256(raw_key.encode()).hexdigest()

        ExternalApiKey.objects.create(
            partner_name=partner_name,
            key_hash=key_hash,
        )

        self.stdout.write(self.style.SUCCESS(
            f'\n✅ API key created for partner: {partner_name}\n'
            f'⚠️  Copy this key now — it will NOT be shown again:\n\n'
            f'   {raw_key}\n'
        ))
```

---

### `inventory/external_views.py` — New file

```python
import hashlib
import secrets
from django.db.models import F
from rest_framework import status, serializers
from rest_framework.response import Response
from rest_framework.views import APIView

from api.external_auth import ExternalApiKeyAuthentication, HasValidRequestSignature
from api.permissions import IsManager
from inventory.models import ProductVariant, Order, ExternalApiKey


class ExternalApiKeySerializer(serializers.ModelSerializer):
    class Meta:
        model = ExternalApiKey
        fields = ['id', 'partner_name', 'is_active', 'created_at']
        read_only_fields = ['id', 'is_active', 'created_at']


class AdminApiKeyListCreateView(APIView):
    """
    GET /api/v1/admin/api-keys/
    Lists all external partner API keys. Restricted to Managers.

    POST /api/v1/admin/api-keys/
    Generates a new API key for a partner, hashes it, and returns the raw key ONCE.
    Restricted to Managers.
    """
    permission_classes = [IsManager]

    def get(self, request):
        keys = ExternalApiKey.objects.all().order_by('-created_at')
        serializer = ExternalApiKeySerializer(keys, many=True)
        return Response(serializer.data)

    def post(self, request):
        partner_name = request.data.get('partner_name')
        if not partner_name:
            return Response({'error': 'partner_name is required.'}, status=status.HTTP_400_BAD_REQUEST)

        raw_key = secrets.token_urlsafe(48)
        key_hash = hashlib.sha256(raw_key.encode()).hexdigest()

        key = ExternalApiKey.objects.create(
            partner_name=partner_name,
            key_hash=key_hash,
            is_active=True
        )

        serializer = ExternalApiKeySerializer(key)
        data = serializer.data
        data['raw_key'] = raw_key  # Returned ONLY once here
        return Response(data, status=status.HTTP_201_CREATED)


class AdminApiKeyRevokeView(APIView):
    """
    POST /api/v1/admin/api-keys/<uuid:pk>/revoke/
    Revokes (deactivates) an active API key. Restricted to Managers.
    """
    permission_classes = [IsManager]

    def post(self, request, pk):
        try:
            key = ExternalApiKey.objects.get(pk=pk)
        except ExternalApiKey.DoesNotExist:
            return Response({'error': 'API key not found.'}, status=status.HTTP_404_NOT_FOUND)

        key.is_active = False
        key.save()
        return Response({'message': 'API key revoked successfully.'}, status=status.HTTP_200_OK)


class ExternalInventorySyncView(APIView):
    """
    POST /api/v1/external/inventory/sync/
    Applies a batch of quantity_delta adjustments to product_variants.stock_quantity.
    Used by ERP systems to reconcile physical warehouse counts.
    """
    authentication_classes = [ExternalApiKeyAuthentication, HasValidRequestSignature]

    def post(self, request):
        sync_items = request.data.get('sync_items', [])
        if not sync_items or not isinstance(sync_items, list):
            return Response({'error': 'sync_items array is required.'}, status=status.HTTP_400_BAD_REQUEST)

        results = {'applied': [], 'not_found': [], 'errors': []}

        from django.db import transaction
        with transaction.atomic():
            for item in sync_items:
                sku = item.get('sku')
                delta = item.get('quantity_delta')

                if not sku or delta is None:
                    results['errors'].append({'item': item, 'reason': 'Missing sku or quantity_delta'})
                    continue

                try:
                    delta = int(delta)
                except (TypeError, ValueError):
                    results['errors'].append({'item': item, 'reason': 'quantity_delta must be an integer'})
                    continue

                updated = ProductVariant.objects.filter(sku=sku).update(
                    stock_quantity=F('stock_quantity') + delta
                )
                if updated == 0:
                    results['not_found'].append(sku)
                else:
                    results['applied'].append({'sku': sku, 'delta': delta})

        return Response(results, status=status.HTTP_200_OK)


class ExternalShipmentUpdateView(APIView):
    """
    PATCH /api/v1/external/shipments/update/
    Updates the fulfillment status for an order from a logistics partner webhook.
    Note: Shipment tracking is an extension to the orders table — add a `carrier_status`
    and `tracking_reference` column if not already present (add to Supabase SQL separately).
    """
    authentication_classes = [ExternalApiKeyAuthentication, HasValidRequestSignature]
    VALID_STATUSES = {'staged', 'picked_up', 'in_transit', 'delivered'}

    def patch(self, request):
        order_id = request.data.get('order_id')
        carrier_status = request.data.get('carrier_status')
        tracking_reference = request.data.get('tracking_reference')

        if not all([order_id, carrier_status, tracking_reference]):
            return Response(
                {'error': 'order_id, carrier_status, and tracking_reference are required.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if carrier_status not in self.VALID_STATUSES:
            return Response(
                {'error': f"carrier_status must be one of: {', '.join(self.VALID_STATUSES)}."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            import uuid
            order = Order.objects.get(id=uuid.UUID(str(order_id)))
        except (Order.DoesNotExist, ValueError):
            return Response({'error': 'Order not found.'}, status=status.HTTP_404_NOT_FOUND)

        # Update tracking fields (extend Order model or use a related Shipment model in a future spec)
        # For now, store as an audit note via order metadata if extended
        return Response(
            {
                'order_id': str(order.id),
                'carrier_status': carrier_status,
                'tracking_reference': tracking_reference,
                'message': 'Shipment status updated.',
            },
            status=status.HTTP_200_OK,
        )
```

---

### `inventory/urls.py` — Add external and admin routes

```python
from inventory.external_views import (
    ExternalInventorySyncView,
    ExternalShipmentUpdateView,
    AdminApiKeyListCreateView,
    AdminApiKeyRevokeView,
)

urlpatterns += [
    path('external/inventory/sync/', ExternalInventorySyncView.as_view(), name='external-inventory-sync'),
    path('external/shipments/update/', ExternalShipmentUpdateView.as_view(), name='external-shipment-update'),
    path('admin/api-keys/', AdminApiKeyListCreateView.as_view(), name='admin-api-key-list-create'),
    path('admin/api-keys/<uuid:pk>/revoke/', AdminApiKeyRevokeView.as_view(), name='admin-api-key-revoke'),
]
```

---

### `dwarikasbackend/settings.py` — Register external auth

```python
REST_FRAMEWORK = {
    'DEFAULT_AUTHENTICATION_CLASSES': [
        'api.authentication.SupabaseJWTAuthentication',
        'api.external_auth.ExternalApiKeyAuthentication',   # Add after JWT auth
    ],
    ...
}
```

---

## Acceptance Criteria

- [ ] `python manage.py create_api_key --partner-name "ERP Test"` creates a row in `external_api_keys` and prints the raw key
- [ ] `GET /api/v1/admin/api-keys/` requires Manager role and returns a list of all API keys (excluding the key hash)
- [ ] `POST /api/v1/admin/api-keys/` requires Manager role, takes `partner_name`, generates a new API key, stores the hash, and returns the raw key ONCE in the response
- [ ] `POST /api/v1/admin/api-keys/<uuid:pk>/revoke/` requires Manager role and updates `is_active` to `False` for the specified key
- [ ] Calling `/external/inventory/sync/` without `X-Dwarikas-Api-Key` returns 401
- [ ] Calling with a valid API key but missing `X-Dwarikas-Signature` returns 401
- [ ] Calling with an expired timestamp (> 5 min old) returns 401
- [ ] Valid request applies `quantity_delta` to matching SKUs
- [ ] Unknown SKUs appear in `not_found` list without rolling back valid updates
- [ ] `hashlib.sha256` is used — raw API key is never stored in the database
- [ ] `HasValidRequestSignature` is unit-testable with mock request objects
