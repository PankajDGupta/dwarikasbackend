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
                # To prevent deadlocks, we lock the rows in the parent-to-child order:
                # 1. ProductVariant
                # 2. Reservation
                # Since we only have reservation_id, we first read the variant_id from the
                # reservation without acquiring a lock.
                try:
                    res_info = Reservation.objects.filter(
                        id=reservation_id,
                        user_id=user_id,
                        status='active',
                    ).values('variant_id').get()
                    variant_id = res_info['variant_id']
                except Reservation.DoesNotExist:
                    return Response(
                        {'error': 'Active reservation not found or does not belong to you.'},
                        status=status.HTTP_404_NOT_FOUND,
                    )

                # ── Step 1: Lock the variant row ─────────────────────────────────
                variant = ProductVariant.objects.select_for_update().get(id=variant_id)

                # ── Step 2: Lock the reservation row ────────────────────────────
                reservation = Reservation.objects.select_for_update().get(id=reservation_id)

                # Guard: double check reservation has not been completed/expired by a concurrent request
                if reservation.status != 'active' or str(reservation.user_id) != str(user_id):
                    return Response(
                        {'error': 'Active reservation not found or does not belong to you.'},
                        status=status.HTTP_404_NOT_FOUND,
                    )

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
