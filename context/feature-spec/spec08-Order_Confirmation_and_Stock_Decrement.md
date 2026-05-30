# Spec 08 — Order Confirmation & Atomic Stock Decrement

## Goal
Implement the order confirmation endpoint. When a customer's payment is verified, Django must atomically:
1. Decrement `stock_quantity` on the relevant `ProductVariant`
2. Mark the matching `Reservation` as `completed`
3. Create an `Order` record as the permanent transaction log

All three operations must succeed together or roll back entirely. This is the second phase of the dual-phase checkout flow started in Spec 07.

---

## Scope
- `POST /api/v1/orders/confirm/` — commit a paid reservation into a finalized order
- `GET /api/v1/orders/` — list caller's historical orders (owner) or all orders (staff/manager)
- `GET /api/v1/orders/<uuid:id>/` — retrieve a single order with GST breakdown

---

## Files to Create / Modify

### `inventory/order_views.py` — New file

```python
import uuid
from django.db import transaction, DatabaseError
from rest_framework import status, generics
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from inventory.models import ProductVariant, Reservation, Order
from inventory.serializers import OrderSerializer
from api.permissions import IsStaffOrManager


class OrderConfirmView(APIView):
    """
    POST /api/v1/orders/confirm/
    Atomically decrements stock, marks the reservation completed, and logs the order.

    Expected body:
    {
        "reservation_id": "<uuid>",
        "payment_method": "UPI" | "card" | "cash"
    }
    """
    permission_classes = [IsAuthenticated]

    def post(self, request):
        reservation_id = request.data.get('reservation_id')
        payment_method = request.data.get('payment_method')

        # Input validation
        if not reservation_id or not payment_method:
            return Response(
                {'error': 'reservation_id and payment_method are required.'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if payment_method not in ('UPI', 'card', 'cash'):
            return Response(
                {'error': "payment_method must be one of: 'UPI', 'card', 'cash'."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            reservation_id = uuid.UUID(str(reservation_id))
        except ValueError:
            return Response({'error': 'reservation_id must be a valid UUID.'}, status=status.HTTP_400_BAD_REQUEST)

        user_id = uuid.UUID(request.user.username)

        try:
            with transaction.atomic():
                # ── Step 1: Lock the reservation row ────────────────────────────
                try:
                    reservation = Reservation.objects.select_for_update().get(
                        id=reservation_id,
                        user_id=user_id,
                        status='active',
                    )
                except Reservation.DoesNotExist:
                    return Response(
                        {'error': 'Active reservation not found or does not belong to you.'},
                        status=status.HTTP_404_NOT_FOUND,
                    )

                # ── Step 2: Lock the variant row ─────────────────────────────────
                variant = ProductVariant.objects.select_for_update().get(id=reservation.variant_id)

                # Guard: ensure reservation hasn't expired
                from datetime import datetime, timezone
                if reservation.expires_at < datetime.now(timezone.utc):
                    reservation.status = 'expired'
                    reservation.save(update_fields=['status'])
                    return Response(
                        {'error': 'Reservation has expired. Please restart checkout.'},
                        status=status.HTTP_410_GONE,
                    )

                # Guard: stock must still cover the reserved quantity
                if variant.stock_quantity < reservation.reserved_quantity:
                    return Response(
                        {'error': 'Insufficient physical stock to complete this order.'},
                        status=status.HTTP_409_CONFLICT,
                    )

                # ── Step 3: Decrement stock ───────────────────────────────────────
                # Use F() expressions to avoid read-modify-write race conditions
                from django.db.models import F
                ProductVariant.objects.filter(id=variant.id).update(
                    stock_quantity=F('stock_quantity') - reservation.reserved_quantity
                )

                # ── Step 4: Mark reservation completed ───────────────────────────
                reservation.status = 'completed'
                reservation.save(update_fields=['status'])

                # ── Step 5: Compute GST and log the order ────────────────────────
                variant.refresh_from_db()  # Get updated stock
                unit_price = variant.retail_price
                qty = reservation.reserved_quantity
                product = variant.product
                gst_rate = product.gst_slab / 100

                subtotal = unit_price * qty
                gst_amount = round(subtotal * gst_rate, 2)
                total_amount = round(subtotal + gst_amount, 2)

                order = Order.objects.create(
                    user_id=user_id,
                    total_amount=total_amount,
                    gst_amount=gst_amount,
                    payment_method=payment_method,
                    payment_status='completed',
                )

                return Response(
                    {
                        'order_id': str(order.id),
                        'total_amount': str(total_amount),
                        'gst_amount': str(gst_amount),
                        'payment_method': payment_method,
                        'payment_status': 'completed',
                        'created_at': order.created_at.isoformat(),
                    },
                    status=status.HTTP_201_CREATED,
                )

        except DatabaseError:
            return Response(
                {'error': 'Database transaction error. Please retry.'},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )


class OrderListView(generics.ListAPIView):
    """
    GET /api/v1/orders/
    Customers see only their own orders. Staff/managers see all.
    """
    serializer_class = OrderSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        role = getattr(self.request.user, 'role', 'customer')
        if role in ('staff', 'manager'):
            return Order.objects.order_by('-created_at')
        return Order.objects.filter(
            user_id=uuid.UUID(self.request.user.username)
        ).order_by('-created_at')


class OrderDetailView(generics.RetrieveAPIView):
    """
    GET /api/v1/orders/<uuid:id>/
    """
    serializer_class = OrderSerializer
    permission_classes = [IsAuthenticated]
    lookup_field = 'id'

    def get_queryset(self):
        role = getattr(self.request.user, 'role', 'customer')
        if role in ('staff', 'manager'):
            return Order.objects.all()
        return Order.objects.filter(user_id=uuid.UUID(self.request.user.username))
```

---

### `inventory/serializers.py` — Add OrderSerializer

```python
class OrderSerializer(serializers.ModelSerializer):
    class Meta:
        model = Order
        fields = [
            'id', 'user_id', 'total_amount', 'gst_amount',
            'payment_method', 'payment_status', 'created_at',
        ]
        read_only_fields = ['id', 'created_at']
```

---

### `inventory/urls.py` — Add order routes

```python
from inventory.order_views import OrderConfirmView, OrderListView, OrderDetailView

urlpatterns += [
    path('orders/confirm/', OrderConfirmView.as_view(), name='order-confirm'),
    path('orders/', OrderListView.as_view(), name='order-list'),
    path('orders/<uuid:id>/', OrderDetailView.as_view(), name='order-detail'),
]
```

---

## GST Calculation Logic

All GST is computed server-side from the `product.gst_slab` (stored as a percentage, e.g., `18.00`):

```
subtotal = unit_price × quantity
gst_amount = subtotal × (gst_slab / 100)
total_amount = subtotal + gst_amount
```

The breakdown (`CGST`, `SGST`, `IGST` split for ONDC compliance) is computed in Spec 13 (ONDC integration).

---

## Acceptance Criteria

- [ ] Successful confirm call creates an `Order` with `payment_status='completed'`
- [ ] `stock_quantity` on the variant is decremented by `reserved_quantity`
- [ ] `Reservation.status` is set to `'completed'` after confirm
- [ ] Confirming an expired reservation returns HTTP 410
- [ ] Confirming someone else's reservation returns HTTP 404
- [ ] All three DB writes (decrement + reservation update + order create) roll back together on any failure
- [ ] `GET /api/v1/orders/` returns only the caller's orders for a customer JWT
- [ ] `GET /api/v1/orders/` returns all orders for a staff JWT
