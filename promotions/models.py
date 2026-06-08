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

    def __str__(self):
        return self.title

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

    def __str__(self):
        return f"PromotionItem({self.id}, promo={self.promotion_id})"


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

    def __str__(self):
        return f"Broadcast({self.phone_number}, status={self.status})"
