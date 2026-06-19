import uuid
from django.db import models
from inventory.models import Product

class AmazonCredentials(models.Model):
    REGION_CHOICES = [
        ('NA', 'North America'),
        ('EU', 'Europe'),
        ('FE', 'Far East'),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    seller_id = models.CharField(max_length=255, unique=True)
    lwa_client_id = models.CharField(max_length=255)
    lwa_client_secret = models.TextField()
    lwa_refresh_token = models.TextField()
    region = models.CharField(max_length=2, choices=REGION_CHOICES, default='EU')
    primary_marketplace_id = models.CharField(max_length=50)
    authorized_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        managed = False
        db_table = 'amazon_credentials'

    def __str__(self):
        return f"AmazonCredentials({self.seller_id}, region={self.region})"


class AmazonListing(models.Model):
    SYNC_STATUS_CHOICES = [
        ('PENDING', 'Pending'),
        ('SUBMITTED', 'Submitted'),
        ('ACTIVE', 'Active'),
        ('INVALID', 'Invalid'),
        ('ERROR', 'Error'),
        ('SUPPRESSED', 'Suppressed'),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    product = models.ForeignKey(
        Product,
        on_delete=models.CASCADE,
        related_name='amazon_listings',
        db_column='product_id'
    )
    sku = models.CharField(max_length=100, unique=True)
    asin = models.CharField(max_length=10, null=True, blank=True)
    marketplace_id = models.CharField(max_length=50)
    sync_status = models.CharField(max_length=20, choices=SYNC_STATUS_CHOICES, default='PENDING')
    submission_id = models.UUIDField(null=True, blank=True)
    validation_issues = models.JSONField(default=list, blank=True)
    price_synced = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    quantity_synced = models.IntegerField(default=0)
    last_synced_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        managed = False
        db_table = 'amazon_listings'

    def __str__(self):
        return f"AmazonListing({self.sku}, status={self.sync_status})"
