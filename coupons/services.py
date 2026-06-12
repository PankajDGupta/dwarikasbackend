import random
import string
import uuid
from decimal import Decimal
from django.db import transaction
from django.utils import timezone

from coupons.models import Coupon, CouponRedemption
from inventory.models import Reservation


def generate_coupon_code(prefix: str = '', length: int = 8) -> str:
    """
    Generates a unique random alphanumeric coupon code.
    Used by the gaming engine reward system.
    """
    chars = string.ascii_uppercase + string.digits
    while True:
        code = prefix + ''.join(random.choices(chars, k=length))
        if not Coupon.objects.filter(code=code).exists():
            return code


def validate_coupon(code: str, user_id: uuid.UUID, cart_total: Decimal, current_reservation_id: uuid.UUID = None) -> dict:
    """
    Validates a coupon code for a given user and cart total.
    Returns a dict with validation result and computed discount.
    Does NOT apply the coupon — call apply_coupon_to_reservation() for that.

    Returns:
    {
        'valid': bool,
        'error': str | None,
        'coupon': Coupon | None,
        'discount_amount': Decimal | None,
        'final_total': Decimal | None,
    }
    """
    now = timezone.now()

    # 1. Clean up expired pending redemptions to free up limits
    CouponRedemption.objects.filter(
        order__isnull=True,
        reservation__expires_at__lt=now
    ).delete()
    CouponRedemption.objects.filter(
        order__isnull=True,
        reservation__status='expired'
    ).delete()

    try:
        coupon = Coupon.objects.get(code=code.upper().strip())
    except Coupon.DoesNotExist:
        return {'valid': False, 'error': 'Invalid coupon code.', 'coupon': None}

    if not coupon.is_active:
        return {'valid': False, 'error': 'This coupon is no longer active.', 'coupon': coupon}

    if now < coupon.valid_from:
        return {'valid': False, 'error': 'This coupon is not yet valid.', 'coupon': coupon}

    if coupon.valid_until and now > coupon.valid_until:
        return {'valid': False, 'error': 'This coupon has expired.', 'coupon': coupon}

    if coupon.specific_user_id and coupon.specific_user_id != user_id:
        return {'valid': False, 'error': 'This coupon is not valid for your account.', 'coupon': coupon}

    if coupon.min_order_value and cart_total < coupon.min_order_value:
        return {
            'valid': False,
            'error': f'Minimum order value of ₹{coupon.min_order_value} required for this coupon.',
            'coupon': coupon,
        }

    # Check total usage cap (excluding current reservation hold if re-validating)
    if coupon.max_uses is not None:
        uses_query = CouponRedemption.objects.filter(coupon=coupon)
        if current_reservation_id:
            uses_query = uses_query.exclude(reservation_id=current_reservation_id)
        total_uses = uses_query.count()
        if total_uses >= coupon.max_uses:
            return {'valid': False, 'error': 'This coupon has reached its usage limit.', 'coupon': coupon}

    # Check per-user usage (excluding current reservation hold if re-validating)
    user_uses_query = CouponRedemption.objects.filter(coupon=coupon, user_id=user_id)
    if current_reservation_id:
        user_uses_query = user_uses_query.exclude(reservation_id=current_reservation_id)
    user_uses = user_uses_query.count()
    if user_uses >= coupon.uses_per_user:
        return {'valid': False, 'error': 'You have already used this coupon.', 'coupon': coupon}

    # Compute discount
    if coupon.discount_type == 'percentage':
        discount = cart_total * (coupon.discount_value / Decimal('100'))
        if coupon.max_discount_cap:
            discount = min(discount, coupon.max_discount_cap)
    else:
        discount = min(coupon.discount_value, cart_total)

    # Round discount to 2 decimal places
    discount = round(discount, 2)
    final_total = max(cart_total - discount, Decimal('0'))

    return {
        'valid': True,
        'error': None,
        'coupon': coupon,
        'discount_amount': discount,
        'final_total': final_total,
    }


@transaction.atomic
def apply_coupon_to_reservation(coupon: Coupon, reservation: Reservation, user_id: uuid.UUID, discount_amount: Decimal):
    """
    Atomically applies a validated coupon to a reservation.
    Creates a pending CouponRedemption record.
    Locks the Coupon row to prevent race conditions on max_uses.
    """
    # Acquire row lock on coupon to serialize concurrent redemptions
    coupon = Coupon.objects.select_for_update().get(id=coupon.id)

    # Re-validate usage cap inside lock (excluding current reservation if it somehow already exists)
    if coupon.max_uses is not None:
        total_uses = CouponRedemption.objects.filter(coupon=coupon).exclude(reservation_id=reservation.id).count()
        if total_uses >= coupon.max_uses:
            raise ValueError('This coupon has reached its usage limit.')

    # Re-validate per-user cap inside lock
    user_uses = CouponRedemption.objects.filter(coupon=coupon, user_id=user_id).exclude(reservation_id=reservation.id).count()
    if user_uses >= coupon.uses_per_user:
        raise ValueError('You have already used this coupon.')

    # If the reservation already has a coupon applied, clean up its old redemption first
    if reservation.coupon_id:
        CouponRedemption.objects.filter(reservation_id=reservation.id).delete()

    base_price = reservation.effective_price or reservation.variant.retail_price
    # Calculate discount per unit (rounded)
    discount_per_unit = round(discount_amount / reservation.reserved_quantity, 2)
    final_price = max(base_price - discount_per_unit, Decimal('0'))

    # Update reservation with coupon info
    Reservation.objects.filter(id=reservation.id).update(
        coupon_id=coupon.id,
        coupon_discount=discount_amount,
        final_price=final_price,
    )

    # Create pending redemption (order_id filled on order confirm)
    CouponRedemption.objects.create(
        coupon=coupon,
        user_id=user_id,
        reservation_id=reservation.id,
        discount_applied=discount_amount,
    )


def create_gaming_reward_coupon(user_id: uuid.UUID, reward_config: dict, created_by: uuid.UUID) -> Coupon:
    """
    Creates a single-use coupon as a gaming reward for a specific user.
    Called by the gaming engine integration (Spec 21).

    reward_config example:
    {
        'discount_type': 'flat_amount',
        'discount_value': 50.00,
        'valid_days': 7,         # how many days until expiry
        'description': 'Game winner reward'
    }
    """
    from datetime import timedelta
    code = generate_coupon_code(prefix='GAME', length=6)
    valid_until = timezone.now() + timedelta(days=reward_config.get('valid_days', 30))

    coupon = Coupon.objects.create(
        code=code,
        description=reward_config.get('description', 'Gaming reward coupon'),
        discount_type=reward_config['discount_type'],
        discount_value=Decimal(str(reward_config['discount_value'])),
        max_uses=1,
        uses_per_user=1,
        specific_user_id=user_id,
        is_active=True,
        valid_from=timezone.now(),
        valid_until=valid_until,
        created_by=created_by,
        source='gaming_reward',
    )
    return coupon
