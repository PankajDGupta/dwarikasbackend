# Spec 07 — Checkout Reservation & Pessimistic Locking

## Goal
Implement the atomic checkout flow: acquire a pessimistic row-level lock on the target `ProductVariant`, compute the Available-To-Promise (ATP) stock by subtracting active reservations, create a 10-minute hold if stock is sufficient, and reject concurrent requests with HTTP 409 if the ATP check fails.

This is the single most critical concurrency feature of the backend. Every line must be wrapped in `transaction.atomic()`.

---

## Scope
- `POST /api/v1/checkout/reserve/` — create a stock hold (authenticated customers, staff, managers)
- `DELETE /api/v1/checkout/reserve/<uuid:id>/` — release a reservation early (owner or staff)
- `GET /api/v1/checkout/reserve/` — list caller's active reservations (owner only)
- Background expiry cleanup handled by a separate Cloud Tasks worker (defined in Spec 10)

---

## Files to Create / Modify

### `inventory/checkout_views.py` — New file

```python
import uuid
from datetime import datetime, timezone, timedelta

from django.db import transaction, DatabaseError, OperationalError
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework import generics

from inventory.models import ProductVariant, Reservation
from inventory.serializers import ReservationSerializer
from api.permissions import IsOwnerOrStaff

RESERVATION_TTL_MINUTES = 10


class CheckoutReserveView(APIView):
    """
    POST /api/v1/checkout/reserve/
    Acquires a pessimistic row-level lock on the requested SKU variant and creates
    a timed inventory hold to prevent overselling across concurrent channels.
    """
    permission_classes = [IsAuthenticated]

    def post(self, request):
        variant_id = request.data.get('variant_id')
        purchase_qty = request.data.get('quantity', 1)

        # --- Input validation ---
        if not variant_id:
            return Response({'error': 'variant_id is required.'}, status=status.HTTP_400_BAD_REQUEST)
        try:
            variant_id = uuid.UUID(str(variant_id))
            purchase_qty = int(purchase_qty)
            if purchase_qty < 1:
                raise ValueError
        except (ValueError, AttributeError):
            return Response(
                {'error': 'quantity must be a positive integer and variant_id must be a valid UUID.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Extract Supabase UID from the ephemeral user object built in Spec 03
        user_id = uuid.UUID(request.user.username)

        try:
            with transaction.atomic():
                # ── PHASE 1: Acquire pessimistic row-level lock ──────────────────
                # select_for_update() issues a SELECT ... FOR UPDATE, blocking
                # concurrent transactions from modifying this row until we commit.
                try:
                    variant = ProductVariant.objects.select_for_update(nowait=True).get(id=variant_id)
                except ProductVariant.DoesNotExist:
                    return Response({'error': 'SKU variant not found.'}, status=status.HTTP_404_NOT_FOUND)
                except OperationalError:
                    # Another transaction holds the lock — instruct client to retry
                    return Response(
                        {'error': 'Inventory is being updated. Please retry in a moment.'},
                        status=status.HTTP_503_SERVICE_UNAVAILABLE,
                    )

                # ── PHASE 2: Compute Available-To-Promise (ATP) ──────────────────
                # ATP = physical_stock - sum(active reservations that haven't expired)
                now = datetime.now(timezone.utc)
                active_reservations = Reservation.objects.filter(
                    variant_id=variant_id,
                    status='active',
                    expires_at__gt=now,
                ).select_for_update(nowait=True)

                total_reserved = sum(r.reserved_quantity for r in active_reservations)
                atp = variant.stock_quantity - total_reserved

                if atp < purchase_qty:
                    return Response(
                        {
                            'error': 'Insufficient stock available.',
                            'atp': atp,
                            'requested': purchase_qty,
                        },
                        status=status.HTTP_409_CONFLICT,
                    )

                # ── PHASE 3: Create timed reservation ───────────────────────────
                reservation = Reservation.objects.create(
                    variant_id=variant.id,
                    user_id=user_id,
                    reserved_quantity=purchase_qty,
                    expires_at=now + timedelta(minutes=RESERVATION_TTL_MINUTES),
                    status='active',
                )

                return Response(
                    {
                        'reservation_id': str(reservation.id),
                        'variant_id': str(variant.id),
                        'reserved_quantity': purchase_qty,
                        'expires_at': reservation.expires_at.isoformat(),
                        'status': 'active',
                    },
                    status=status.HTTP_201_CREATED,
                )

        except DatabaseError:
            return Response(
                {'error': 'Database transaction error. Please retry.'},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )


class ReservationListView(generics.ListAPIView):
    """
    GET /api/v1/checkout/reserve/
    Returns all active reservations belonging to the requesting user.
    """
    serializer_class = ReservationSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        now = datetime.now(timezone.utc)
        return Reservation.objects.filter(
            user_id=uuid.UUID(self.request.user.username),
            status='active',
            expires_at__gt=now,
        ).select_related('variant')


class ReservationReleaseView(APIView):
    """
    DELETE /api/v1/checkout/reserve/<uuid:reservation_id>/
    Releases a reservation early (e.g., user abandons cart).
    Owners may release their own; staff/managers may release any.
    """
    permission_classes = [IsAuthenticated]

    def delete(self, request, reservation_id):
        try:
            reservation = Reservation.objects.get(id=reservation_id, status='active')
        except Reservation.DoesNotExist:
            return Response({'error': 'Active reservation not found.'}, status=status.HTTP_404_NOT_FOUND)

        # Authorization: owner or staff
        is_owner = str(reservation.user_id) == request.user.username
        is_staff = getattr(request.user, 'role', None) in ('staff', 'manager')

        if not (is_owner or is_staff):
            return Response({'error': 'Permission denied.'}, status=status.HTTP_403_FORBIDDEN)

        reservation.status = 'expired'
        reservation.save(update_fields=['status'])
        return Response(status=status.HTTP_204_NO_CONTENT)
```

---

### `inventory/serializers.py` — Add ReservationSerializer

```python
class ReservationSerializer(serializers.ModelSerializer):
    variant = ProductVariantSerializer(read_only=True)

    class Meta:
        model = Reservation
        fields = ['id', 'variant', 'reserved_quantity', 'expires_at', 'status']
        read_only_fields = ['id', 'expires_at', 'status']
```

---

### `inventory/urls.py` — Add checkout routes

```python
from inventory.checkout_views import (
    CheckoutReserveView,
    ReservationListView,
    ReservationReleaseView,
)

urlpatterns += [
    path('checkout/reserve/', CheckoutReserveView.as_view(), name='checkout-reserve'),
    path('checkout/reserve/list/', ReservationListView.as_view(), name='reservation-list'),
    path('checkout/reserve/<uuid:reservation_id>/', ReservationReleaseView.as_view(), name='reservation-release'),
]
```

---

## Concurrency Behaviour

| Scenario | Expected Outcome |
|----------|-----------------|
| 50 threads request last 1 unit simultaneously | Exactly 1 succeeds (HTTP 201); 49 receive HTTP 409 or 503 |
| Reservation expires and new request arrives | ATP recalculates excluding expired row; new reservation created |
| Staff releases an active reservation | Status set to `expired`, stock freed for next buyer |
| Variant does not exist | HTTP 404 |
| Invalid UUID in body | HTTP 400 |

---

## Acceptance Criteria

- [ ] `POST /api/v1/checkout/reserve/` without JWT returns 401
- [ ] `POST` with insufficient ATP returns 409 with `atp` and `requested` fields
- [ ] `POST` with valid data creates a `Reservation` row with `status='active'`
- [ ] `DELETE /api/v1/checkout/reserve/<id>/` by owner sets `status='expired'`
- [ ] `DELETE` by non-owner customer returns 403
- [ ] The entire checkout flow executes inside a single `transaction.atomic()` block
- [ ] Concurrent test: run 10 simultaneous requests for the last unit — only 1 succeeds (write an integration test using `threading.Thread`)
- [ ] `select_for_update(nowait=True)` is used (not blocking nowait) to avoid deadlock starvation
