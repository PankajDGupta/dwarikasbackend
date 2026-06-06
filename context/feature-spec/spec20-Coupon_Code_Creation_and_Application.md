# Spec 20 — Coupon Code Creation & Application

## Goal

Enable managers to create alphanumeric coupon codes that customers can enter at checkout to receive a discount. Coupons are single-use or multi-use, can be scoped to specific users or be public, and must be validated server-side before affecting the checkout price. This is distinct from Promotions (Spec 19), which apply automatically — coupons require explicit user input.

---

## Business Context

Coupons are a key customer acquisition and retention tool for Dwarikas:
- **Welcome coupons** given to new customer signups (e.g., `WELCOME100` for ₹100 off first order)
- **Gaming rewards** — coupons issued to users who win in the integrated gaming engine (Spec 21)
- **Referral bonuses** — coupons shared between customers
- **Staff-distributed** — physical coupons printed and given at the store counter

> **Key invariant:** A coupon's discount is applied to the **reservation-level price** (which may already reflect a promotion from Spec 19). The coupon discount is additive — it is applied on top of any existing promotional price. The server enforces usage limits atomically to prevent double-redemption races.

---

## Relationship to Existing Specs

| Spec | Impact |
|---|---|
| **Spec 07** (Checkout Reservation) | New `POST /checkout/apply-coupon/` endpoint validates and "pre-applies" a coupon to a reservation |
| **Spec 08** (Order Confirmation) | Order creation records which coupon was used and its discount amount |
| **Spec 19** (Promotions) | Coupon discount stacks after promotion effective price is applied |
| **Spec 21** (Gaming Engine) | Gaming engine calls an internal service to auto-generate and assign coupon codes as rewards |

---

## Scope

Managed within the new `promotions/` Django app created in Spec 19 (or as its own `coupons/` app).

### Endpoints

| Method | Path | Actor | Description |
|---|---|---|---|
| `POST` | `/api/v1/coupons/` | Manager | Create a coupon code |
| `GET` | `/api/v1/coupons/` | Manager | List all coupons (with usage stats) |
| `GET` | `/api/v1/coupons/<uuid:id>/` | Manager | Coupon detail |
| `PATCH` | `/api/v1/coupons/<uuid:id>/` | Manager | Update/deactivate a coupon |
| `DELETE` | `/api/v1/coupons/<uuid:id>/` | Manager | Hard-delete a coupon |
| `POST` | `/api/v1/checkout/apply-coupon/` | Authenticated Customer | Validate a coupon code and apply it to an active reservation |
| `DELETE` | `/api/v1/checkout/remove-coupon/<uuid:reservation_id>/` | Authenticated Customer | Remove an applied coupon from a reservation |
| `GET` | `/api/v1/coupons/validate/<str:code>/` | Authenticated | Check if a coupon code is valid (without applying it) |

---

## Data Models

### `Coupon`

```
id                  UUID            PK — auto
code                TEXT            UNIQUE, case-insensitive — e.g., "WELCOME100"
description         TEXT            Optional — for manager reference
discount_type       TEXT            'percentage' | 'flat_amount'
discount_value      DECIMAL(10,2)   e.g., 10.00 (%) or 100.00 (₹)
max_discount_cap    DECIMAL(10,2)   Optional — max ₹ discount for percentage coupons
min_order_value     DECIMAL(10,2)   Optional — minimum cart value required
max_uses            INTEGER         Optional — null means unlimited
uses_per_user       INTEGER         Default 1 — how many times one user can use it
specific_user_id    UUID (FK)       Optional — if set, only this user can redeem it
is_active           BOOLEAN         Manual kill-switch (default: true)
valid_from          TIMESTAMPTZ     When the coupon becomes usable
valid_until         TIMESTAMPTZ     Optional — when the coupon expires
created_by          UUID            Manager UUID
created_at          TIMESTAMPTZ     Auto
source              TEXT            'manual' | 'gaming_reward' | 'referral' — tracks origin
```

### `CouponRedemption`

Immutable audit log of every successful coupon use.

```
id                  UUID        PK — auto
coupon_id           UUID (FK)   → Coupon
user_id             UUID        Supabase user who redeemed
order_id            UUID (FK)   → Order (nullable until order confirmed)
reservation_id      UUID (FK)   → Reservation — when coupon was pre-applied
discount_applied    DECIMAL(12,2) Actual ₹ discount granted
redeemed_at         TIMESTAMPTZ Auto
```

### `Reservation` Model Extension (Spec 07 + Spec 19)

Additional columns (may already have `effective_price`, `promotion_id` from Spec 19):

```
coupon_id           UUID (FK)       Nullable → Coupon applied at reservation time
coupon_discount     DECIMAL(12,2)   Nullable — ₹ amount discounted via coupon
final_price         DECIMAL(12,2)   Nullable — effective_price minus coupon_discount
```

---

## Business Logic

### Coupon Validation Rules (enforced server-side)

1. **Existence**: Code must exist in `coupons` table (case-insensitive).
2. **Active**: `is_active = TRUE`.
3. **Time window**: `valid_from <= now <= valid_until` (or `valid_until IS NULL`).
4. **Total usage cap**: If `max_uses` is set, total `CouponRedemption` count < `max_uses`.
5. **Per-user cap**: Count of `CouponRedemption` records for this user < `uses_per_user`.
6. **Specific user**: If `specific_user_id` is set, caller UUID must match.
7. **Min order value**: If `min_order_value` is set, the reservation's total value must be ≥ `min_order_value`.

### Discount Application

```
base_price        = reservation.effective_price  (from Spec 19, or retail_price if no promo)
base_total        = base_price × reserved_quantity

if coupon.discount_type == 'percentage':
    discount = base_total × (coupon.discount_value / 100)
    if coupon.max_discount_cap:
        discount = min(discount, max_discount_cap)
else:  # flat_amount
    discount = coupon.discount_value

final_total = max(base_total - discount, 0)
coupon_discount_per_unit = discount / reserved_quantity
final_price_per_unit = base_price - coupon_discount_per_unit
```

### Atomicity (race condition prevention)

The `apply-coupon` endpoint must:
1. Validate all coupon rules.
2. Acquire a `SELECT FOR UPDATE` lock on the `Coupon` row.
3. Re-check `max_uses` inside the lock.
4. Write the `coupon_id`, `coupon_discount`, and `final_price` to the `Reservation` row.
5. Create a **pending** `CouponRedemption` record (linked to reservation, not yet to an order).

When the order is confirmed (Spec 08), the `CouponRedemption.order_id` is filled in. If the reservation expires without confirmation, the pending redemption is deleted (so the coupon is freed for the user).

---

## Files to Create / Modify

### `coupons/` — New Django app (or extend `promotions/`)

```bash
python manage.py startapp coupons
```

Add to `INSTALLED_APPS`:
```python
'coupons.apps.CouponsConfig',
```

---

### `coupons/models.py`

```python
import uuid
from django.db import models


class Coupon(models.Model):
    DISCOUNT_TYPE_CHOICES = [
        ('percentage', 'Percentage'),
        ('flat_amount', 'Flat Amount'),
    ]
    SOURCE_CHOICES = [
        ('manual', 'Manual'),
        ('gaming_reward', 'Gaming Reward'),
        ('referral', 'Referral'),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    code = models.TextField(unique=True)  # Normalised to UPPERCASE at save
    description = models.TextField(null=True, blank=True)
    discount_type = models.TextField(choices=DISCOUNT_TYPE_CHOICES)
    discount_value = models.DecimalField(max_digits=10, decimal_places=2)
    max_discount_cap = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    min_order_value = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    max_uses = models.IntegerField(null=True, blank=True)
    uses_per_user = models.IntegerField(default=1)
    specific_user_id = models.UUIDField(null=True, blank=True)
    is_active = models.BooleanField(default=True)
    valid_from = models.DateTimeField()
    valid_until = models.DateTimeField(null=True, blank=True)
    created_by = models.UUIDField()
    created_at = models.DateTimeField(auto_now_add=True)
    source = models.TextField(choices=SOURCE_CHOICES, default='manual')

    class Meta:
        managed = False
        db_table = 'coupons'

    def save(self, *args, **kwargs):
        self.code = self.code.upper().strip()
        super().save(*args, **kwargs)


class CouponRedemption(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    coupon = models.ForeignKey(
        Coupon,
        on_delete=models.CASCADE,
        related_name='redemptions',
        db_column='coupon_id',
    )
    user_id = models.UUIDField()
    order = models.ForeignKey(
        'inventory.Order',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        db_column='order_id',
        related_name='coupon_redemptions',
    )
    reservation = models.ForeignKey(
        'inventory.Reservation',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        db_column='reservation_id',
        related_name='coupon_redemptions',
    )
    discount_applied = models.DecimalField(max_digits=12, decimal_places=2)
    redeemed_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        managed = False
        db_table = 'coupon_redemptions'
```

---

### `coupons/services.py` — Core validation & application logic

```python
"""
Coupon validation and application service.
Called from checkout views and the gaming reward issuance flow.
"""
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


def validate_coupon(code: str, user_id: uuid.UUID, cart_total: Decimal) -> dict:
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

    # Check total usage cap
    if coupon.max_uses is not None:
        total_uses = CouponRedemption.objects.filter(coupon=coupon, order__isnull=False).count()
        if total_uses >= coupon.max_uses:
            return {'valid': False, 'error': 'This coupon has reached its usage limit.', 'coupon': coupon}

    # Check per-user usage
    user_uses = CouponRedemption.objects.filter(
        coupon=coupon, user_id=user_id, order__isnull=False
    ).count()
    if user_uses >= coupon.uses_per_user:
        return {'valid': False, 'error': 'You have already used this coupon.', 'coupon': coupon}

    # Compute discount
    if coupon.discount_type == 'percentage':
        discount = cart_total * (coupon.discount_value / Decimal('100'))
        if coupon.max_discount_cap:
            discount = min(discount, coupon.max_discount_cap)
    else:
        discount = min(coupon.discount_value, cart_total)

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

    # Re-validate usage cap inside lock
    if coupon.max_uses is not None:
        total_uses = CouponRedemption.objects.filter(coupon=coupon, order__isnull=False).count()
        if total_uses >= coupon.max_uses:
            raise ValueError('Coupon usage limit reached.')

    base_price = reservation.effective_price or reservation.variant.retail_price
    final_price = max(base_price - (discount_amount / reservation.reserved_quantity), Decimal('0'))

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
```

---

### `coupons/views.py`

```python
import uuid
from decimal import Decimal
from django.db import transaction
from rest_framework import generics, status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from api.permissions import IsManager
from coupons.models import Coupon, CouponRedemption
from coupons.serializers import CouponSerializer, CouponRedemptionSerializer
from coupons.services import validate_coupon, apply_coupon_to_reservation
from inventory.models import Reservation


class CouponListCreateView(generics.ListCreateAPIView):
    """
    GET  /api/v1/coupons/  — Manager lists all coupons
    POST /api/v1/coupons/  — Manager creates coupon
    """
    serializer_class = CouponSerializer
    permission_classes = [IsManager]
    queryset = Coupon.objects.order_by('-created_at')

    def perform_create(self, serializer):
        serializer.save(created_by=uuid.UUID(self.request.user.username))


class CouponDetailView(generics.RetrieveUpdateDestroyAPIView):
    """
    GET /PATCH /DELETE /api/v1/coupons/<uuid:id>/
    """
    serializer_class = CouponSerializer
    permission_classes = [IsManager]
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

        if reservation_id:
            try:
                reservation = Reservation.objects.select_related('variant').get(
                    id=reservation_id,
                    user_id=uuid.UUID(request.user.username),
                    status='active',
                )
                base_price = reservation.effective_price or reservation.variant.retail_price
                cart_total = base_price * reservation.reserved_quantity
            except Reservation.DoesNotExist:
                return Response({'error': 'Reservation not found.'}, status=status.HTTP_404_NOT_FOUND)

        result = validate_coupon(code, uuid.UUID(request.user.username), cart_total)

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
            reservation = Reservation.objects.select_related('variant__product').get(
                id=reservation_id,
                user_id=user_id,
                status='active',
            )
        except Reservation.DoesNotExist:
            return Response({'error': 'Active reservation not found.'}, status=status.HTTP_404_NOT_FOUND)

        from datetime import datetime, timezone as tz
        if reservation.expires_at < datetime.now(tz.utc):
            return Response({'error': 'Reservation has expired.'}, status=status.HTTP_410_GONE)

        base_price = reservation.effective_price or reservation.variant.retail_price
        cart_total = base_price * reservation.reserved_quantity

        result = validate_coupon(coupon_code, user_id, cart_total)
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
            reservation = Reservation.objects.get(
                id=reservation_id,
                user_id=user_id,
                status='active',
            )
        except Reservation.DoesNotExist:
            return Response({'error': 'Active reservation not found.'}, status=status.HTTP_404_NOT_FOUND)

        if not reservation.coupon_id:
            return Response({'error': 'No coupon applied to this reservation.'}, status=status.HTTP_400_BAD_REQUEST)

        with transaction.atomic():
            # Delete the pending redemption record
            CouponRedemption.objects.filter(
                reservation_id=reservation_id,
                user_id=user_id,
                order__isnull=True,
            ).delete()

            # Clear coupon fields on reservation
            Reservation.objects.filter(id=reservation_id).update(
                coupon_id=None,
                coupon_discount=None,
                final_price=None,
            )

        return Response({'message': 'Coupon removed successfully.'}, status=status.HTTP_200_OK)
```

---

### Supabase Migration

```sql
-- Coupons table
CREATE TABLE IF NOT EXISTS public.coupons (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    code                TEXT UNIQUE NOT NULL,   -- Always stored UPPERCASE
    description         TEXT,
    discount_type       TEXT NOT NULL CHECK (discount_type IN ('percentage', 'flat_amount')),
    discount_value      DECIMAL(10, 2) NOT NULL,
    max_discount_cap    DECIMAL(10, 2),
    min_order_value     DECIMAL(10, 2),
    max_uses            INTEGER,                -- NULL = unlimited
    uses_per_user       INTEGER NOT NULL DEFAULT 1,
    specific_user_id    UUID,                   -- NULL = public coupon
    is_active           BOOLEAN NOT NULL DEFAULT TRUE,
    valid_from          TIMESTAMPTZ NOT NULL,
    valid_until         TIMESTAMPTZ,            -- NULL = no expiry
    created_by          UUID NOT NULL,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    source              TEXT NOT NULL DEFAULT 'manual'
                        CHECK (source IN ('manual', 'gaming_reward', 'referral'))
);

-- Coupon redemptions audit log
CREATE TABLE IF NOT EXISTS public.coupon_redemptions (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    coupon_id           UUID NOT NULL REFERENCES public.coupons(id) ON DELETE CASCADE,
    user_id             UUID NOT NULL,
    order_id            UUID REFERENCES public.orders(id) ON DELETE SET NULL,
    reservation_id      UUID REFERENCES public.reservations(id) ON DELETE SET NULL,
    discount_applied    DECIMAL(12, 2) NOT NULL,
    redeemed_at         TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Extend reservations table
ALTER TABLE public.reservations
    ADD COLUMN IF NOT EXISTS coupon_id       UUID REFERENCES public.coupons(id) ON DELETE SET NULL,
    ADD COLUMN IF NOT EXISTS coupon_discount DECIMAL(12, 2),
    ADD COLUMN IF NOT EXISTS final_price     DECIMAL(12, 2);

-- Performance indexes
CREATE UNIQUE INDEX IF NOT EXISTS idx_coupons_code ON public.coupons (UPPER(code));
CREATE INDEX IF NOT EXISTS idx_coupons_specific_user ON public.coupons (specific_user_id) WHERE specific_user_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_coupon_redemptions_user ON public.coupon_redemptions (user_id);
CREATE INDEX IF NOT EXISTS idx_coupon_redemptions_coupon ON public.coupon_redemptions (coupon_id);
```

---

## API Response Examples

### `POST /api/v1/checkout/apply-coupon/` — Success

```json
{
  "reservation_id": "uuid",
  "coupon_code": "WELCOME100",
  "discount_amount": "100.00",
  "final_total": "140.00",
  "message": "Coupon applied successfully."
}
```

### `GET /api/v1/coupons/validate/GAMEXYZ123/?reservation_id=<uuid>` — Success

```json
{
  "valid": true,
  "code": "GAMEXYZ123",
  "discount_type": "flat_amount",
  "discount_value": "50.00",
  "discount_amount": "50.00",
  "final_total": "90.00"
}
```

### `GET /api/v1/coupons/validate/EXPIRED123/` — Failure

```json
{
  "valid": false,
  "error": "This coupon has expired."
}
```

---

## Acceptance Criteria

- [ ] Manager can create a coupon via `POST /api/v1/coupons/`
- [ ] `POST /api/v1/checkout/apply-coupon/` validates all rules: active, time window, usage cap, per-user cap, user-specific, min order value
- [ ] Atomic row lock on `Coupon` prevents race condition on `max_uses` when two users try to claim the last use simultaneously
- [ ] A `CouponRedemption` record is created when coupon is applied to reservation
- [ ] `CouponRedemption.order_id` is populated when order is confirmed (Spec 08 integration)
- [ ] If reservation expires, the pending `CouponRedemption` record is removed (coupon freed)
- [ ] `DELETE /checkout/remove-coupon/<id>/` successfully removes coupon and pending redemption
- [ ] `GET /api/v1/coupons/validate/<code>/` returns discount preview without side effects
- [ ] Coupon code lookup is case-insensitive
- [ ] Gaming engine can call `create_gaming_reward_coupon()` to auto-generate a user-specific coupon (Spec 21 integration)
- [ ] Full test suite covering: creation, validation rules, concurrency, apply/remove, order integration

---

## Django App Layout

```
coupons/
├── __init__.py
├── apps.py
├── models.py
├── serializers.py
├── services.py          # validate_coupon(), apply_coupon_to_reservation(), create_gaming_reward_coupon()
├── views.py
├── urls.py
└── tests/
    ├── __init__.py
    ├── test_coupons.py
    └── test_apply_coupon.py
```
