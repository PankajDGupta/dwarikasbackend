from datetime import datetime, timezone, timedelta
from decimal import Decimal
from django.test import TestCase
from django.utils import timezone as dj_timezone

from inventory.models import Product, ProductVariant, Reservation, PurchaseInvoice, InvoiceLineItem
from promotions.models import DiscountSuggestion
from promotions.analytics_service import score_variant, run_discount_analysis


class DiscountAnalyticsServiceTests(TestCase):

    def setUp(self):
        # Create standard test product & variant
        self.product = Product.objects.create(
            name="Test Grocery Product",
            hsn_code="1006",
            gst_slab=5.00,
            product_type="grocery",
            brand="BrandX",
            category="Grains",
        )

        self.variant = ProductVariant.objects.create(
            product=self.product,
            sku="SKU-ANALYTICS-1",
            barcode="8901234567890",
            stock_quantity=100,
            retail_price=Decimal('100.00'),
            mrp=Decimal('120.00'),
        )

        self.now = dj_timezone.now()

    def test_low_stock_excluded_from_analysis(self):
        """Variants with stock quantity < MIN_STOCK_TO_ANALYSE (5) should be skipped."""
        self.variant.stock_quantity = 4
        self.variant.save()

        result = score_variant(self.variant, self.now)
        self.assertIsNone(result)

    def test_low_margin_excluded_from_analysis(self):
        """Variants with margin < MIN_MARGIN_PCT_TO_SUGGEST (15%) should be skipped."""
        # Seeding cost price close to retail price (90 cost vs 100 retail -> 10% margin)
        invoice = PurchaseInvoice.objects.create(
            invoice_number="INV-001",
            status="confirmed",
            uploaded_by="00000000-0000-0000-0000-000000000001",
        )
        InvoiceLineItem.objects.create(
            invoice=invoice,
            sku=self.variant.sku,
            quantity=10,
            unit_price=Decimal('90.00'),
            gst_rate=Decimal('5.00'),
            confidence_score=Decimal('0.95'),
        )

        result = score_variant(self.variant, self.now)
        self.assertIsNone(result)

    def test_score_calculation_critical_case(self):
        """Verify high stock, stale sales, and high margin calculates a high composite score."""
        # Cost is 50, Retail is 100 -> 50% margin -> margin score is min(100, 50 * 2) = 100
        invoice = PurchaseInvoice.objects.create(
            invoice_number="INV-002",
            status="confirmed",
            uploaded_by="00000000-0000-0000-0000-000000000001",
        )
        InvoiceLineItem.objects.create(
            invoice=invoice,
            sku=self.variant.sku,
            quantity=10,
            unit_price=Decimal('50.00'),
            gst_rate=Decimal('5.00'),
            confidence_score=Decimal('0.95'),
        )

        # Abandonment: 5 expired, 0 completed
        for _ in range(5):
            Reservation.objects.create(
                variant=self.variant,
                user_id="00000000-0000-0000-0000-000000000002",
                reserved_quantity=1,
                status="expired",
                expires_at=self.now - timedelta(days=5),
            )

        # Since there are 0 completed orders, velocity is 0 and days_since_last_order is None.
        # stock_score = 100 (due to 0 sales and stock > 5)
        # recency_score = 90 (never sold)
        # abandonment_score = 100 (5/5 expired)
        # margin_score = 100 (50% margin)
        # Composite = 100 * 0.35 + 90 * 0.30 + 100 * 0.15 + 100 * 0.20 = 35 + 27 + 15 + 20 = 97.
        result = score_variant(self.variant, self.now)

        self.assertIsNotNone(result)
        self.assertEqual(result['discount_score'], 97)
        self.assertEqual(result['priority'], 'critical')
        self.assertEqual(result['suggested_discount_type'], 'percentage')
        # margin is 50% -> composite >= 80 -> min(25, 50 * 0.7) = 25%
        self.assertEqual(result['suggested_discount_value'], Decimal('25.00'))
        self.assertEqual(result['suggested_ends_days'], 7)

    def test_score_calculation_margin_fallback_to_mrp(self):
        """Verify margin is calculated using MRP gap if no invoice cost price exists."""
        # Retail price is 80, MRP is 100 -> (100 - 80) / 100 * 100 = 20% margin
        self.variant.retail_price = Decimal('80.00')
        self.variant.mrp = Decimal('100.00')
        self.variant.save()

        result = score_variant(self.variant, self.now)
        self.assertIsNotNone(result)
        self.assertEqual(result['margin_pct'], Decimal('20.00'))

    def test_run_discount_analysis_lifecycle(self):
        """Verify run_discount_analysis creates, updates, and expires suggestions."""
        # Seeding cost of 50 -> 50% margin
        invoice = PurchaseInvoice.objects.create(
            invoice_number="INV-003",
            status="confirmed",
            uploaded_by="00000000-0000-0000-0000-000000000001",
        )
        InvoiceLineItem.objects.create(
            invoice=invoice,
            sku=self.variant.sku,
            quantity=10,
            unit_price=Decimal('50.00'),
        )

        # 1. Run analysis -> Creates a pending suggestion
        stats = run_discount_analysis()
        self.assertEqual(stats['new'], 1)
        self.assertEqual(stats['updated'], 0)
        self.assertEqual(stats['expired'], 0)

        suggestion = DiscountSuggestion.objects.get(variant=self.variant, status='pending')
        self.assertEqual(suggestion.discount_score, 82)

        # 2. Run analysis again without changes -> Updates the suggestion in place
        stats = run_discount_analysis()
        self.assertEqual(stats['new'], 0)
        self.assertEqual(stats['updated'], 1)
        self.assertEqual(stats['expired'], 0)

        # 3. Simulate dismissal (snooze)
        suggestion.status = 'dismissed'
        suggestion.dismissed_until = dj_timezone.now() + timedelta(days=30)
        suggestion.save()

        stats = run_discount_analysis()
        # Should not recreate the suggestion because of the active dismissed_until snooze
        self.assertEqual(stats['new'], 0)
        self.assertEqual(stats['updated'], 0)

        # 4. Simulate snooze expired, and stock sold out (stock < 5) -> status becomes expired
        suggestion.status = 'pending'
        suggestion.save()

        self.variant.stock_quantity = 2
        self.variant.save()

        stats = run_discount_analysis()
        self.assertEqual(stats['new'], 0)
        self.assertEqual(stats['updated'], 0)
        self.assertEqual(stats['expired'], 1)

        suggestion.refresh_from_db()
        self.assertEqual(suggestion.status, 'expired')
