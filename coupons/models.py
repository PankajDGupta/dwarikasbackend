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

    def __str__(self):
        return f"Coupon({self.code}, type={self.discount_type}, value={self.discount_value})"

    def save(self, *args, **kwargs):
        if self.code:
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

    def __str__(self):
        return f"Redemption({self.id}, coupon={self.coupon.code}, user={self.user_id})"
