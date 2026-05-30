"""
Inventory ORM models — Spec #04.

These models are Django-managed mirrors of the Supabase PostgreSQL public schema.
They are intentionally configured as unmanaged (managed = False) so that Django's
migration machinery never executes DDL (CREATE TABLE / ALTER TABLE) against Supabase.

Supabase owns all DDL via its own SQL migration files and RLS policies.
Django uses these models solely to:
  - Run ORM queries (select, filter, annotate)
  - Acquire pessimistic row-level locks via select_for_update()
  - Validate data shapes at the Python layer

References:
  - context/feature-spec/spec04-Database_Models_and_Schema.md
  - context/system_design.md  §3 — Unified Database Schema
"""

import uuid
from django.db import models


class Profile(models.Model):
    """
    Mirror of public.profiles — stores RBAC role data linked to Supabase Auth users.

    The `id` field maps directly to auth.users(id) in Supabase. It is NOT a Django
    AutoField; the UUID is set by Supabase Auth on registration and passed in here.

    Role values are enforced by a CHECK constraint on the Supabase side; the
    ROLE_CHOICES here provide Python-level validation and readable display labels.
    """
    ROLE_CHOICES = [
        ('customer', 'Customer'),
        ('staff', 'Staff'),
        ('manager', 'Manager'),
    ]

    id = models.UUIDField(primary_key=True)   # Maps to auth.users(id) — set by Supabase
    role = models.TextField(choices=ROLE_CHOICES, default='customer')
    phone_number = models.TextField(unique=True, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        managed = False          # Supabase owns DDL; Django never runs CREATE TABLE
        db_table = 'profiles'

    def __str__(self):
        return f"Profile({self.id}, role={self.role})"


class Product(models.Model):
    """
    Mirror of public.products — master product catalog table.

    Contains catalog-level metadata shared across all SKU variants.
    GST slab rates align with HSN-code-level tax classification used
    in the ONDC Beckn fulfillment flow (CGST + SGST routing).
    """
    DIETARY_CHOICES = [
        ('veg', 'Vegetarian'),
        ('non-veg', 'Non-Vegetarian'),
        ('egg', 'Contains Egg'),
        ('none', 'Not Applicable'),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.TextField()
    hsn_code = models.TextField()
    gst_slab = models.DecimalField(max_digits=5, decimal_places=2, default=18.00)
    brand = models.TextField(null=True, blank=True)
    category = models.TextField(null=True, blank=True)
    subcategory = models.TextField(null=True, blank=True)
    description = models.TextField(null=True, blank=True)
    image_url = models.TextField(null=True, blank=True)
    dietary_type = models.TextField(choices=DIETARY_CHOICES, default='none')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        managed = False
        db_table = 'products'

    def __str__(self):
        return f"{self.name} (HSN: {self.hsn_code})"


class ProductVariant(models.Model):
    """
    Mirror of public.product_variants — SKU-level variant table.

    Holds physical stock_quantity for each size/color combination.
    This is the row targeted by select_for_update() during the checkout
    pessimistic lock flow (Spec #07) to prevent concurrent overselling.

    ATP (Available-To-Promise) is derived dynamically:
        ATP = stock_quantity - SUM(active reservation quantities)
    """
    UOM_CHOICES = [
        ('kg', 'Kilograms'),
        ('g', 'Grams'),
        ('L', 'Litres'),
        ('ml', 'Millilitres'),
        ('pcs', 'Pieces'),
        ('pack', 'Pack'),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    product = models.ForeignKey(
        Product,
        on_delete=models.CASCADE,
        related_name='variants',
        db_column='product_id',
    )
    sku = models.TextField(unique=True)
    barcode = models.TextField(unique=True, null=True, blank=True)
    size = models.TextField(null=True, blank=True)
    color = models.TextField(null=True, blank=True)
    stock_quantity = models.IntegerField(default=0)
    retail_price = models.DecimalField(max_digits=12, decimal_places=2)
    mrp = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    weight_volume = models.TextField(null=True, blank=True)
    net_quantity = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    unit_of_measure = models.TextField(choices=UOM_CHOICES, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        managed = False
        db_table = 'product_variants'

    def __str__(self):
        return f"SKU: {self.sku} | Stock: {self.stock_quantity}"


class Reservation(models.Model):
    """
    Mirror of public.reservations — temporary stock holds.

    Created atomically when a checkout session begins; expires after 10 minutes.
    Expired rows (expires_at <= now()) are ignored in all ATP calculations.

    user_id is stored as a raw UUID field rather than a Django FK because
    Supabase auth.users lives in a separate PostgreSQL schema that Django
    cannot traverse as a FK target.

    Status lifecycle: active → completed (on payment) | expired (on timeout)
    """
    STATUS_CHOICES = [
        ('active', 'Active'),
        ('completed', 'Completed'),
        ('expired', 'Expired'),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    variant = models.ForeignKey(
        ProductVariant,
        on_delete=models.CASCADE,
        related_name='reservations',
        db_column='variant_id',
    )
    user_id = models.UUIDField()              # Supabase auth.users(id) — not a Django FK
    reserved_quantity = models.IntegerField()
    expires_at = models.DateTimeField()
    status = models.TextField(choices=STATUS_CHOICES, default='active')

    class Meta:
        managed = False
        db_table = 'reservations'

    def __str__(self):
        return f"Reservation({self.id}, variant={self.variant_id}, status={self.status})"


class Order(models.Model):
    """
    Mirror of public.orders — completed order transaction log.

    Written atomically when a checkout is confirmed (Spec #08).
    user_id is nullable to support guest checkout flows and ON DELETE SET NULL
    behaviour on the Supabase side when a user account is deleted.

    payment_method aligns with the POS settlement modes supported by
    the Multi-Mode POS Settlement Backend feature.
    """
    PAYMENT_METHOD_CHOICES = [
        ('UPI', 'UPI'),
        ('card', 'Card'),
        ('cash', 'Cash'),
    ]
    PAYMENT_STATUS_CHOICES = [
        ('pending', 'Pending'),
        ('completed', 'Completed'),
        ('failed', 'Failed'),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user_id = models.UUIDField(null=True, blank=True)   # Supabase auth.users(id)
    total_amount = models.DecimalField(max_digits=12, decimal_places=2)
    gst_amount = models.DecimalField(max_digits=12, decimal_places=2)
    payment_method = models.TextField(choices=PAYMENT_METHOD_CHOICES)
    payment_status = models.TextField(choices=PAYMENT_STATUS_CHOICES, default='pending')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        managed = False
        db_table = 'orders'

    def __str__(self):
        return f"Order({self.id}, {self.payment_method}, status={self.payment_status})"
