import uuid
from django.db import models
from inventory.models import Product, ProductVariant

class QCPlatformListing(models.Model):
    PLATFORM_CHOICES = [
        ('blinkit', 'Blinkit'),
        ('jiomart', 'JioMart'),
    ]

    STATUS_CHOICES = [
        ('DRAFT', 'Draft'),
        ('VALIDATING', 'Validating'),
        ('SUBMITTED', 'Submitted'),
        ('PENDING_REVIEW', 'Pending Review'),
        ('ACTIVE', 'Active'),
        ('INACTIVE', 'Inactive'),
        ('REJECTED', 'Rejected'),
        ('ERROR', 'Error'),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    product = models.ForeignKey(
        Product,
        on_delete=models.CASCADE,
        related_name='qc_listings',
        db_column='product_id'
    )
    variant = models.ForeignKey(
        ProductVariant,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='qc_listings',
        db_column='variant_id'
    )
    platform = models.CharField(max_length=20, choices=PLATFORM_CHOICES)
    platform_sku = models.TextField(null=True, blank=True)
    platform_upc = models.TextField(null=True, blank=True)
    asin_equivalent = models.TextField(null=True, blank=True)
    sync_status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='DRAFT')
    trace_id = models.TextField(null=True, blank=True)
    submission_guid = models.UUIDField(null=True, blank=True)
    validation_issues = models.JSONField(default=list, blank=True)
    mrp_snapshot = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    selling_price_snapshot = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    fssai_license = models.TextField(null=True, blank=True)
    barcode_validated = models.BooleanField(default=False)
    image_validated = models.BooleanField(default=False)
    last_synced_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        managed = False
        db_table = 'qc_platform_listings'

    def __str__(self):
        return f"QCPlatformListing({self.platform}, sku={self.platform_sku}, status={self.sync_status})"


class QCPlatformCredentials(models.Model):
    PLATFORM_CHOICES = [
        ('blinkit', 'Blinkit'),
        ('jiomart', 'JioMart'),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    platform = models.CharField(max_length=20, choices=PLATFORM_CHOICES, unique=True)
    
    # JioMart / Fynd
    fynd_username = models.TextField(null=True, blank=True)
    fynd_access_token = models.TextField(null=True, blank=True)
    fynd_token_expires_at = models.DateTimeField(null=True, blank=True)

    # Blinkit
    blinkit_vendor_id = models.TextField(null=True, blank=True)
    blinkit_receiver_code = models.TextField(null=True, blank=True)
    blinkit_webhook_secret = models.TextField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        managed = False
        db_table = 'qc_platform_credentials'

    def __str__(self):
        return f"QCPlatformCredentials({self.platform})"


class QCWarehouseMapping(models.Model):
    PLATFORM_CHOICES = [
        ('blinkit', 'Blinkit'),
        ('jiomart', 'JioMart'),
    ]
    MAPPING_TYPE_CHOICES = [
        ('facility', 'Facility'),
        ('pincode', 'Pincode'),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    platform = models.CharField(max_length=20, choices=PLATFORM_CHOICES)
    internal_facility_code = models.TextField()
    platform_location_id = models.TextField()  # warehouse ID or pincode
    mapping_type = models.CharField(max_length=15, choices=MAPPING_TYPE_CHOICES)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        managed = False
        db_table = 'qc_warehouse_mappings'

    def __str__(self):
        return f"QCWarehouseMapping({self.platform}: {self.platform_location_id} -> {self.internal_facility_code})"


class QCPurchaseOrder(models.Model):
    PLATFORM_CHOICES = [
        ('blinkit', 'Blinkit'),
        ('jiomart', 'JioMart'),
    ]
    STATUS_CHOICES = [
        ('RECEIVED', 'Received'),
        ('VERIFIED', 'Verified'),
        ('DISPATCHED', 'Dispatched'),
        ('ASN_SENT', 'ASN Sent'),
        ('INWARDED', 'Inwarded'),
        ('CANCELLED', 'Cancelled'),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    platform = models.CharField(max_length=20, choices=PLATFORM_CHOICES)
    platform_po_id = models.TextField(unique=True)
    vendor_id = models.TextField(null=True, blank=True)
    facility_code = models.TextField(null=True, blank=True)
    po_status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='RECEIVED')
    total_amount = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    asn_reference = models.TextField(null=True, blank=True)
    raw_payload = models.JSONField(null=True, blank=True)
    received_at = models.DateTimeField(auto_now_add=True)
    dispatched_at = models.DateTimeField(null=True, blank=True)
    inwarded_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        managed = False
        db_table = 'qc_purchase_orders'

    def __str__(self):
        return f"QCPurchaseOrder({self.platform_po_id}, status={self.po_status})"


class QCPOLineItem(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    po = models.ForeignKey(
        QCPurchaseOrder,
        on_delete=models.CASCADE,
        related_name='line_items',
        db_column='po_id'
    )
    variant = models.ForeignKey(
        ProductVariant,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='qc_po_items',
        db_column='variant_id'
    )
    platform_sku = models.TextField()
    ordered_quantity = models.IntegerField()
    delivered_quantity = models.IntegerField(default=0)
    unit_price = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    mrp = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)

    class Meta:
        managed = False
        db_table = 'qc_po_line_items'

    def __str__(self):
        return f"QCPOLineItem({self.platform_sku}, qty={self.ordered_quantity})"
