from decimal import Decimal
from django.test import TestCase
from inventory.models import Product, ProductVariant
from quickcommerce.validation_engine import is_ean13_valid, validate_product_for_listing

class PreFlightValidationTests(TestCase):

    def setUp(self):
        # Create a standard grocery product
        self.product = Product.objects.create(
            name="Organics Whole Wheat Flour",
            hsn_code="1101",
            gst_slab=Decimal("5.00"),
            product_type="grocery",
            brand="Organics",
            category="Grocery",
            image_url="https://example.com/wheat.jpg"
        )
        # Create a standard variant with valid barcode (EAN-13 for "8901234567890" check digit is 0)
        self.variant = ProductVariant.objects.create(
            product=self.product,
            sku="ORG-WHEAT-5KG",
            barcode="8901234567890",
            stock_quantity=600,
            retail_price=Decimal("250.00"),
            mrp=Decimal("280.00")
        )

    def test_ean13_check_digit_validator(self):
        """Verify check-digit calculations for EAN-13 barcodes."""
        self.assertTrue(is_ean13_valid("8901234567890"))
        # Invalid check digit
        self.assertFalse(is_ean13_valid("8901234567891"))
        # Non-digits or wrong length
        self.assertFalse(is_ean13_valid("89012345678"))
        self.assertFalse(is_ean13_valid("89012345678901"))
        self.assertFalse(is_ean13_valid("890123abc7890"))

    def test_fssai_license_validation(self):
        """Verify FSSAI license checks for food/beverage items."""
        # 1. Grocery product without FSSAI should fail
        issues = validate_product_for_listing(self.product, 'jiomart')
        self.assertTrue(any(i["code"] == "FSSAI_REQUIRED" for i in issues))

        # 2. Invalid FSSAI format (less than 14 digits)
        issues = validate_product_for_listing(self.product, 'jiomart', fssai_license="12345")
        self.assertTrue(any(i["code"] == "FSSAI_INVALID" for i in issues))

        # 3. Valid FSSAI format (14 digits)
        issues = validate_product_for_listing(self.product, 'jiomart', fssai_license="10012345000001")
        self.assertFalse(any(i["code"] in ["FSSAI_REQUIRED", "FSSAI_INVALID"] for i in issues))

    def test_image_checklist_validation(self):
        """Verify validation fails if no image URL is attached."""
        self.product.image_url = ""
        self.product.save()
        issues = validate_product_for_listing(self.product, 'jiomart', fssai_license="10012345000001")
        self.assertTrue(any(i["code"] == "IMAGE_MISSING" for i in issues))

    def test_price_sanity_validation(self):
        """Verify selling price cannot exceed MRP."""
        self.variant.retail_price = Decimal("300.00")
        self.variant.mrp = Decimal("280.00")
        self.variant.save()
        issues = validate_product_for_listing(self.product, 'jiomart', fssai_license="10012345000001")
        self.assertTrue(any(i["code"] == "PRICE_EXCEEDS_MRP" for i in issues))

    def test_jiomart_moq_validation(self):
        """Verify JioMart MOQ constraint (stock_quantity >= 500)."""
        self.variant.stock_quantity = 300
        self.variant.save()
        issues = validate_product_for_listing(self.product, 'jiomart', fssai_license="10012345000001")
        self.assertTrue(any(i["code"] == "MOQ_INSUFFICIENT" for i in issues))

    def test_jiomart_shelf_life_validation(self):
        """Verify JioMart perishables shelf life remaining constraint (>= 60%)."""
        # Shelf life 50% remaining
        issues = validate_product_for_listing(
            self.product, 'jiomart',
            fssai_license="10012345000001",
            extra_config={"shelf_life_pct": 50.0}
        )
        self.assertTrue(any(i["code"] == "SHELF_LIFE_INSUFFICIENT" for i in issues))

    def test_blinkit_mrp_parity_validation(self):
        """Verify Blinkit listing MRP matches physical label MRP."""
        issues = validate_product_for_listing(
            self.product, 'blinkit',
            fssai_license="10012345000001",
            extra_config={"mrp": 290.00}  # PO/listing MRP is 290 vs physical label MRP 280
        )
        self.assertTrue(any(i["code"] == "MRP_MISMATCH" for i in issues))
