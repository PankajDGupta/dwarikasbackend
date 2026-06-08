import uuid
from django.db import models


class PosCart(models.Model):
    """
    A POS session cart scoped to a single staff member's billing session.
    Represents the basket before confirmation. Abandoned carts are soft-deleted/marked abandoned.
    """
    STATUS_CHOICES = [
        ('open', 'Open'),
        ('confirmed', 'Confirmed'),
        ('abandoned', 'Abandoned'),
    ]
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    staff_user_id = models.UUIDField()           # Supabase auth.users(id) of the staff member
    customer_phone = models.TextField(null=True, blank=True)   # Optional — for GST bill
    status = models.TextField(choices=STATUS_CHOICES, default='open')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        managed = False
        db_table = 'pos_carts'

    def __str__(self):
        return f"Cart {self.id} | {self.status}"


class PosCartItem(models.Model):
    """
    A single line item inside a PosCart.
    Each item corresponds to one ProductVariant and a requested quantity.
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    cart = models.ForeignKey(
        PosCart,
        on_delete=models.CASCADE,
        related_name='items',
        db_column='cart_id',
    )
    variant = models.ForeignKey(
        'inventory.ProductVariant',
        on_delete=models.CASCADE,
        related_name='pos_cart_items',
        db_column='variant_id',
    )
    quantity = models.IntegerField()
    unit_price = models.DecimalField(max_digits=12, decimal_places=2)  # Snapshot at time of add
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        managed = False
        db_table = 'pos_cart_items'
        unique_together = [('cart', 'variant')]   # One row per variant per cart


class OrderItem(models.Model):
    """
    Line-item detail for a confirmed Order. Links specific ProductVariants to an Order.
    This is the permanent record that allows bill reconstruction and GST reporting.
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    order = models.ForeignKey(
        'inventory.Order',
        on_delete=models.CASCADE,
        related_name='items',
        db_column='order_id',
    )
    variant = models.ForeignKey(
        'inventory.ProductVariant',
        on_delete=models.SET_NULL,
        null=True,
        related_name='order_items',
        db_column='variant_id',
    )
    sku_snapshot = models.TextField()               # Snapshot SKU — preserved even if variant deleted
    product_name_snapshot = models.TextField()      # Snapshot product name
    hsn_code_snapshot = models.TextField()          # Snapshot HSN for GST filing
    gst_slab_snapshot = models.DecimalField(max_digits=5, decimal_places=2)
    quantity = models.IntegerField()
    unit_price = models.DecimalField(max_digits=12, decimal_places=2)
    subtotal = models.DecimalField(max_digits=12, decimal_places=2)   # unit_price × quantity
    gst_amount = models.DecimalField(max_digits=12, decimal_places=2)  # subtotal × gst_slab/100
    line_total = models.DecimalField(max_digits=12, decimal_places=2)  # subtotal + gst_amount
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        managed = False
        db_table = 'order_items'
