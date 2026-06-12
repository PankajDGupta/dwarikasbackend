import uuid
from decimal import Decimal
from django.db import transaction
from django.utils import timezone
from rest_framework import generics, status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from api.permissions import IsManager
from coupons.models import Coupon, CouponRedemption
from coupons.serializers import CouponSerializer
from coupons.services import validate_coupon, apply_coupon_to_reservation
from inventory.models import Reservation


class CouponListCreateView(generics.ListCreateAPIView):
    """
    GET  /api/v1/coupons/  — Manager lists all coupons
    POST /api/v1/coupons/  — Manager creates coupon
    """
    serializer_class = CouponSerializer
    permission_classes = [IsAuthenticated, IsManager]
    queryset = Coupon.objects.order_by('-created_at')

    def perform_create(self, serializer):
        serializer.save(created_by=uuid.UUID(self.request.user.username))


class CouponDetailView(generics.RetrieveUpdateDestroyAPIView):
    """
    GET /PATCH /DELETE /api/v1/coupons/<uuid:id>/
    """
    serializer_class = CouponSerializer
    permission_classes = [IsAuthenticated, IsManager]
    queryset = Coupon.objects.all()
    lookup_field = 'id'


class CouponValidateView(APIView):
    """
    GET /api/v1/coupons/validate/<str:code>/
    Checks if a coupon code is valid for the calling user WITHOUT applying it.
    Used by the frontend to show discount preview before checkout.
    """
    permission_classes = [IsAuthenticated]

    def get(self, request, code):
        reservation_id = request.query_params.get('reservation_id')
        cart_total = Decimal('0')
        reservation_uuid = None

        if reservation_id:
            try:
                reservation_uuid = uuid.UUID(str(reservation_id))
                reservation = Reservation.objects.select_related('variant').get(
                    id=reservation_uuid,
                    user_id=uuid.UUID(request.user.username),
                    status='active',
                )
                base_price = reservation.effective_price or reservation.variant.retail_price
                cart_total = base_price * reservation.reserved_quantity
            except (Reservation.DoesNotExist, ValueError):
                return Response({'error': 'Reservation not found.'}, status=status.HTTP_404_NOT_FOUND)

        result = validate_coupon(code, uuid.UUID(request.user.username), cart_total, current_reservation_id=reservation_uuid)

        if not result['valid']:
            return Response({'valid': False, 'error': result['error']}, status=status.HTTP_400_BAD_REQUEST)

        return Response({
            'valid': True,
            'code': result['coupon'].code,
            'discount_type': result['coupon'].discount_type,
            'discount_value': str(result['coupon'].discount_value),
            'discount_amount': str(result['discount_amount']),
            'final_total': str(result['final_total']),
        })


class ApplyCouponView(APIView):
    """
    POST /api/v1/checkout/apply-coupon/
    Validates and atomically applies a coupon to an active reservation.

    Expected body:
    {
        "reservation_id": "<uuid>",
        "coupon_code": "WELCOME100"
    }
    """
    permission_classes = [IsAuthenticated]

    def post(self, request):
        reservation_id = request.data.get('reservation_id')
        coupon_code = request.data.get('coupon_code')

        if not reservation_id or not coupon_code:
            return Response(
                {'error': 'reservation_id and coupon_code are required.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        user_id = uuid.UUID(request.user.username)

        try:
            reservation_uuid = uuid.UUID(str(reservation_id))
            reservation = Reservation.objects.select_related('variant__product').get(
                id=reservation_uuid,
                user_id=user_id,
                status='active',
            )
        except (Reservation.DoesNotExist, ValueError):
            return Response({'error': 'Active reservation not found.'}, status=status.HTTP_404_NOT_FOUND)

        if reservation.expires_at < timezone.now():
            reservation.status = 'expired'
            reservation.save(update_fields=['status'])
            return Response({'error': 'Reservation has expired.'}, status=status.HTTP_410_GONE)

        base_price = reservation.effective_price or reservation.variant.retail_price
        cart_total = base_price * reservation.reserved_quantity

        # If re-applying the same coupon or another coupon, exclude current reservation
        result = validate_coupon(coupon_code, user_id, cart_total, current_reservation_id=reservation_uuid)
        if not result['valid']:
            return Response({'error': result['error']}, status=status.HTTP_400_BAD_REQUEST)

        try:
            apply_coupon_to_reservation(
                coupon=result['coupon'],
                reservation=reservation,
                user_id=user_id,
                discount_amount=result['discount_amount'],
            )
        except ValueError as e:
            return Response({'error': str(e)}, status=status.HTTP_409_CONFLICT)

        reservation.refresh_from_db()
        return Response({
            'reservation_id': str(reservation_id),
            'coupon_code': result['coupon'].code,
            'discount_amount': str(result['discount_amount']),
            'final_total': str(result['final_total']),
            'message': 'Coupon applied successfully.',
        })


class RemoveCouponView(APIView):
    """
    DELETE /api/v1/checkout/remove-coupon/<uuid:reservation_id>/
    Removes a previously applied coupon from an active reservation.
    """
    permission_classes = [IsAuthenticated]

    def delete(self, request, reservation_id):
        user_id = uuid.UUID(request.user.username)

        try:
            reservation_uuid = uuid.UUID(str(reservation_id))
            reservation = Reservation.objects.get(
                id=reservation_uuid,
                user_id=user_id,
                status='active',
            )
        except (Reservation.DoesNotExist, ValueError):
            return Response({'error': 'Active reservation not found.'}, status=status.HTTP_404_NOT_FOUND)

        if not reservation.coupon_id:
            return Response({'error': 'No coupon applied to this reservation.'}, status=status.HTTP_400_BAD_REQUEST)

        with transaction.atomic():
            # Delete the pending redemption record
            CouponRedemption.objects.filter(
                reservation_id=reservation_uuid,
                user_id=user_id,
                order__isnull=True,
            ).delete()

            # Clear coupon fields on reservation
            Reservation.objects.filter(id=reservation_uuid).update(
                coupon_id=None,
                coupon_discount=None,
                final_price=None,
            )

        return Response({'message': 'Coupon removed successfully.'}, status=status.HTTP_200_OK)
