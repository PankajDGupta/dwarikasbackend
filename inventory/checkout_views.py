"""
Checkout Reservation & Pessimistic Locking — Spec #07.

Implements the atomic checkout flow:
  1. Acquire a SELECT ... FOR UPDATE (nowait) lock on the target ProductVariant row.
  2. Compute Available-To-Promise (ATP) by subtracting the sum of all active,
     non-expired reservation quantities from physical stock.
  3. If ATP >= requested quantity, create a 10-minute timed Reservation.
  4. Reject with HTTP 409 when ATP is insufficient, HTTP 503 when the lock is
     contended, and HTTP 400/404 for malformed input.

All write operations execute inside a single `transaction.atomic()` block.

References:
  - context/feature-spec/spec07-Checkout_Reservation_and_Locking.md
"""

import uuid
from datetime import datetime, timezone, timedelta

from django.db import transaction, DatabaseError, OperationalError
from rest_framework import status, generics
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from inventory.models import ProductVariant, Reservation
from inventory.serializers import ReservationSerializer

RESERVATION_TTL_MINUTES = 10


class CheckoutReserveView(APIView):
    """
    POST /api/v1/checkout/reserve/

    Acquires a pessimistic row-level lock on the requested SKU variant and
    creates a timed inventory hold to prevent overselling across concurrent
    channels.

    Request body:
        variant_id  (UUID, required)  — the ProductVariant to lock
        quantity    (int,  optional)  — units to reserve; defaults to 1

    Responses:
        201 Created  — reservation created successfully
        400          — missing/invalid input
        404          — variant not found
        409          — insufficient ATP (stock exhausted by active reservations)
        503          — lock contention; client should retry
    """
    permission_classes = [IsAuthenticated]

    def post(self, request):
        variant_id = request.data.get('variant_id')
        purchase_qty = request.data.get('quantity', 1)

        # ── Input validation ────────────────────────────────────────────────
        if not variant_id:
            return Response(
                {'error': 'variant_id is required.'},
                status=status.HTTP_400_BAD_REQUEST,
            )
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

        # Extract the Supabase UID from the ephemeral user object built in Spec #03.
        # `request.user.username` holds the JWT `sub` claim (a UUID string).
        user_id = uuid.UUID(request.user.username)

        try:
            with transaction.atomic():
                # ── PHASE 1: Acquire pessimistic row-level lock ──────────────
                # select_for_update(nowait=True) issues SELECT … FOR UPDATE NOWAIT.
                # If another transaction already holds a write lock on this row,
                # PostgreSQL raises an OperationalError immediately (no blocking
                # wait) so we can return HTTP 503 without stalling the thread.
                try:
                    variant = ProductVariant.objects.select_for_update(nowait=True).get(
                        id=variant_id
                    )
                except ProductVariant.DoesNotExist:
                    return Response(
                        {'error': 'SKU variant not found.'},
                        status=status.HTTP_404_NOT_FOUND,
                    )
                except OperationalError:
                    # Another transaction holds the lock — instruct client to retry
                    return Response(
                        {'error': 'Inventory is being updated. Please retry in a moment.'},
                        status=status.HTTP_503_SERVICE_UNAVAILABLE,
                    )

                # ── PHASE 2: Compute Available-To-Promise (ATP) ──────────────
                # ATP = physical_stock − sum(active reservations that haven't expired)
                # We also lock the active reservation rows so that a racing transaction
                # cannot read stale values while we're inside this block.
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

                # ── PHASE 3: Create timed reservation ────────────────────────
                from decimal import Decimal
                from promotions.services import get_active_promotion_for_variant
                promo = get_active_promotion_for_variant(variant)
                effective_price = Decimal(promo['effective_price']) if promo else variant.retail_price
                promotion_id = uuid.UUID(promo['promotion_id']) if promo else None

                reservation = Reservation.objects.create(
                    variant_id=variant.id,
                    user_id=user_id,
                    reserved_quantity=purchase_qty,
                    expires_at=now + timedelta(minutes=RESERVATION_TTL_MINUTES),
                    status='active',
                    effective_price=effective_price,
                    promotion_id=promotion_id,
                )

                return Response(
                    {
                        'reservation_id': str(reservation.id),
                        'variant_id': str(variant.id),
                        'reserved_quantity': purchase_qty,
                        'expires_at': reservation.expires_at.isoformat(),
                        'status': 'active',
                        'effective_price': str(reservation.effective_price) if reservation.effective_price is not None else None,
                        'promotion_id': str(reservation.promotion_id) if reservation.promotion_id is not None else None,
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
    GET /api/v1/checkout/reserve/list/

    Returns all currently active (non-expired) reservations belonging to the
    authenticated user. Expired rows are silently excluded from the queryset.
    """
    serializer_class = ReservationSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        now = datetime.now(timezone.utc)
        return Reservation.objects.filter(
            user_id=uuid.UUID(self.request.user.username),
            status='active',
            expires_at__gt=now,
        ).select_related('variant').order_by('expires_at')


class ReservationReleaseView(APIView):
    """
    DELETE /api/v1/checkout/reserve/<uuid:reservation_id>/

    Releases a reservation early (e.g., user abandons cart). Transitions the
    row status from 'active' to 'expired' so that ATP calculations immediately
    reflect the freed stock.

    Authorization rules:
        - Owners may release their own reservations.
        - Staff and managers may release any active reservation.
    """
    permission_classes = [IsAuthenticated]

    def delete(self, request, reservation_id):
        try:
            reservation = Reservation.objects.get(id=reservation_id, status='active')
        except Reservation.DoesNotExist:
            return Response(
                {'error': 'Active reservation not found.'},
                status=status.HTTP_404_NOT_FOUND,
            )

        # Authorization: owner or staff/manager
        is_owner = str(reservation.user_id) == request.user.username
        is_staff = getattr(request.user, 'role', None) in ('staff', 'manager')

        if not (is_owner or is_staff):
            return Response(
                {'error': 'Permission denied.'},
                status=status.HTTP_403_FORBIDDEN,
            )

        reservation.status = 'expired'
        reservation.save(update_fields=['status'])
        return Response(status=status.HTTP_204_NO_CONTENT)
