"""
Payment ORM models — Spec #17.

PaymentTransaction is an audit log for every payment attempt tied to a reservation.
It is intentionally configured as unmanaged (managed = False) — Supabase owns DDL.

Apply the payment_transactions table via:
    supabase/snippets/003_payment_transactions.sql

References:
  - context/feature-spec/spec17-Payment_Gateway_Integration.md
"""

import uuid
from django.db import models


class PaymentTransaction(models.Model):
    """
    Audit log for every payment attempt tied to a reservation.

    Separate from the Order model to allow multiple retries per reservation.
    A single reservation may have multiple PaymentTransaction rows (e.g., user
    attempts payment twice), but only one will end up in 'paid' status.

    Status lifecycle:
        created   → Razorpay order created, user hasn't paid yet
        attempted → User started payment in Razorpay widget
        paid      → Payment confirmed (signature verified or webhook)
        failed    → Payment failed or signature mismatch
        refunded  → Full refund issued via staff endpoint

    razorpay_order_id is UNIQUE — one Razorpay order per PaymentTransaction.
    """
    STATUS_CHOICES = [
        ('created', 'Created'),
        ('attempted', 'Attempted'),
        ('paid', 'Paid'),
        ('failed', 'Failed'),
        ('refunded', 'Refunded'),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    reservation = models.ForeignKey(
        'inventory.Reservation',
        on_delete=models.SET_NULL,
        null=True,
        related_name='payment_transactions',
        db_column='reservation_id',
    )
    order = models.OneToOneField(
        'inventory.Order',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='payment_transaction',
        db_column='order_id',
    )
    user_id = models.UUIDField()                          # Supabase auth.users(id)
    razorpay_order_id = models.TextField(unique=True)    # order_XXXXXX from Razorpay
    razorpay_payment_id = models.TextField(null=True, blank=True)
    razorpay_signature = models.TextField(null=True, blank=True)
    amount_paise = models.IntegerField()                  # Amount in paise (100 paise = ₹1)
    currency = models.TextField(default='INR')
    status = models.TextField(choices=STATUS_CHOICES, default='created')
    failure_reason = models.TextField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        managed = False     # Supabase owns DDL — table created via supabase/snippets/003_payment_transactions.sql
        db_table = 'payment_transactions'

    def __str__(self):
        return f"{self.razorpay_order_id} | {self.status}"
