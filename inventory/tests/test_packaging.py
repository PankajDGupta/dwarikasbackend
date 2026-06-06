"""
Tests for Spec #09b — Loose Product Repackaging & Packet Barcode Creation.
"""
import uuid
import jwt
from django.test import TestCase, override_settings
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient
from django.conf import settings

from inventory.models import Product, ProductVariant, PackagingJob, PackagingJobOutput, Reservation


def _jwt(role, user_id="aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee", email="user@test.com"):
    """Generate a HS256 JWT matching the SupabaseJWTAuthentication expectations."""
    payload = {
        "aud": "authenticated",
        "sub": user_id,
        "email": email,
        "app_metadata": {"role": role},
    }
    return jwt.encode(payload, settings.SUPABASE_JWT_SECRET, algorithm="HS256")


CUSTOMER_UUID = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
STAFF_UUID    = "cccccccc-dddd-eeee-ffff-aaaaaaaaaaaa"


@override_settings(SUPABASE_JWT_SECRET="test-jwt-secret-key-at-least-32-chars-long")
class LooseProductRepackagingTests(TestCase):
    """Tests for Packaging Job endpoints and workflow."""

    def setUp(self):
        self.client = APIClient()
        self.list_create_url = reverse('packaging-job-list-create')

        # Create bulk commodity product
        self.bulk_product = Product.objects.create(
            name="Bulk Basmati Rice",
            hsn_code="1006",
            gst_slab=5.00,
            is_loose_commodity=True,
            brand="Lal Qila",
            category="Grocery",
        )

        # Create bulk variant (what we consume)
        self.bulk_variant = ProductVariant.objects.create(
            product=self.bulk_product,
            sku="RICE-BULK-50KG",
            stock_quantity=10,
            retail_price=2500.00,
            unit_of_measure="kg",
        )

        # Create normal customer and staff tokens
        self.customer_token = _jwt("customer", CUSTOMER_UUID)
        self.staff_token    = _jwt("staff",    STAFF_UUID)

    def _get_detail_url(self, job_id):
        return reverse('packaging-job-detail', kwargs={'id': job_id})

    # ── Auth & RBAC ─────────────────────────────────────────────────────────

    def test_create_job_unauthenticated_returns_401(self):
        """POST without auth -> 401."""
        response = self.client.post(self.list_create_url, data={})
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_create_job_customer_returns_403(self):
        """POST as customer -> 403."""
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {self.customer_token}")
        response = self.client.post(self.list_create_url, data={})
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    # ── Input & Business Validation ──────────────────────────────────────────

    def test_create_job_invalid_source_variant(self):
        """Providing a non-existent source_variant_id -> 404."""
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {self.staff_token}")
        payload = {
            "source_description": "Bulk Rice Repackaging",
            "source_variant_id": str(uuid.uuid4()),
            "bulk_quantity_used": 50.0,
            "bulk_unit": "kg",
            "outputs": [
                {
                    "product_id": str(self.bulk_product.id),
                    "sku": "RICE-1KG",
                    "weight_per_packet": 1.0,
                    "unit_of_measure": "kg",
                    "packets_produced": 50,
                    "retail_price": 75.00
                }
            ]
        }
        response = self.client.post(self.list_create_url, data=payload, format='json')
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
        self.assertIn("not found", response.data['error'])

    def test_create_job_sku_product_mismatch(self):
        """SKU already exists under product A, request tries to map it to product B -> 400."""
        # Create a variant under bulk_product with SKU 'EXISTS-ON- rice'
        ProductVariant.objects.create(
            product=self.bulk_product,
            sku="RICE-PACKET-SKU",
            stock_quantity=1,
            retail_price=10.00
        )
        # Create another product
        other_product = Product.objects.create(
            name="Different Product", hsn_code="9999", gst_slab=18.00
        )

        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {self.staff_token}")
        payload = {
            "source_description": "Bulk Rice Repackaging",
            "outputs": [
                {
                    "product_id": str(other_product.id),
                    "sku": "RICE-PACKET-SKU",
                    "weight_per_packet": 1.0,
                    "unit_of_measure": "kg",
                    "packets_produced": 5,
                    "retail_price": 12.00
                }
            ]
        }
        response = self.client.post(self.list_create_url, data=payload, format='json')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("already exists under a different product", response.data['error'])

    # ── Success Workflows ───────────────────────────────────────────────────

    def test_create_job_new_variants_and_barcode_auto_assignment(self):
        """
        POST a job creating entirely new variants:
        - ProductVariant rows must be created.
        - stock_quantity must equal packets_produced.
        - barcode value must be set to SKU.
        - barcode_image_url must be constructed correctly.
        """
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {self.staff_token}")
        payload = {
            "source_description": "50kg Basmati Rice - Bag #12",
            "source_variant_id": str(self.bulk_variant.id),
            "bulk_quantity_used": 50.0,
            "bulk_unit": "kg",
            "notes": "First machine run of the day",
            "outputs": [
                {
                    "product_id": str(self.bulk_product.id),
                    "sku": "RICE-1KG",
                    "weight_per_packet": 1.0,
                    "unit_of_measure": "kg",
                    "packets_produced": 30,
                    "retail_price": 80.00
                },
                {
                    "product_id": str(self.bulk_product.id),
                    "sku": "RICE-5KG",
                    "weight_per_packet": 5.0,
                    "unit_of_measure": "kg",
                    "packets_produced": 4,
                    "retail_price": 375.00
                }
            ]
        }

        response = self.client.post(self.list_create_url, data=payload, format='json')
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)

        # Validate response payload fields
        data = response.data
        self.assertIn('id', data)
        self.assertEqual(data['source_description'], payload['source_description'])
        self.assertEqual(str(data['source_variant_id']), str(self.bulk_variant.id))
        self.assertEqual(float(data['bulk_quantity_used']), 50.0)
        self.assertEqual(data['bulk_unit'], "kg")
        self.assertEqual(data['notes'], "First machine run of the day")
        self.assertEqual(str(data['created_by']), STAFF_UUID)

        outputs = data['outputs']
        self.assertEqual(len(outputs), 2)

        # Output 1 verification
        self.assertEqual(outputs[0]['sku'], "RICE-1KG")
        self.assertEqual(outputs[0]['packets_produced'], 30)
        self.assertEqual(float(outputs[0]['weight_per_packet']), 1.0)
        self.assertEqual(outputs[0]['unit_of_measure'], "kg")
        self.assertEqual(outputs[0]['barcode_value'], "RICE-1KG")
        self.assertEqual(outputs[0]['is_new_variant'], True)
        self.assertIn("/api/v1/barcodes/RICE-1KG/code128/", outputs[0]['barcode_image_url'])

        # Output 2 verification
        self.assertEqual(outputs[1]['sku'], "RICE-5KG")
        self.assertEqual(outputs[1]['packets_produced'], 4)
        self.assertEqual(float(outputs[1]['weight_per_packet']), 5.0)
        self.assertEqual(outputs[1]['unit_of_measure'], "kg")
        self.assertEqual(outputs[1]['barcode_value'], "RICE-5KG")
        self.assertEqual(outputs[1]['is_new_variant'], True)
        self.assertIn("/api/v1/barcodes/RICE-5KG/code128/", outputs[1]['barcode_image_url'])

        # Verify database entities are created
        self.assertTrue(ProductVariant.objects.filter(sku="RICE-1KG").exists())
        self.assertTrue(ProductVariant.objects.filter(sku="RICE-5KG").exists())

        v1 = ProductVariant.objects.get(sku="RICE-1KG")
        self.assertEqual(v1.stock_quantity, 30)
        self.assertEqual(v1.barcode, "RICE-1KG")
        self.assertEqual(v1.net_weight_value, 1.0)
        self.assertEqual(v1.unit_of_measure, "kg")

        v2 = ProductVariant.objects.get(sku="RICE-5KG")
        self.assertEqual(v2.stock_quantity, 4)
        self.assertEqual(v2.barcode, "RICE-5KG")
        self.assertEqual(v2.net_weight_value, 5.0)
        self.assertEqual(v2.unit_of_measure, "kg")

        # Verify packaging job logs in the database
        self.assertEqual(PackagingJob.objects.count(), 1)
        job = PackagingJob.objects.get()
        self.assertEqual(job.source_description, "50kg Basmati Rice - Bag #12")
        self.assertEqual(job.source_variant, self.bulk_variant)
        self.assertEqual(float(job.bulk_quantity_used), 50.0)
        self.assertEqual(str(job.created_by), STAFF_UUID)

        self.assertEqual(PackagingJobOutput.objects.count(), 2)

    def test_create_job_existing_variants_increment_stock(self):
        """
        POST a job for an already-existing variant SKU:
        - Should increment stock_quantity, not overwrite.
        - Should retain/update barcode.
        """
        # Pre-create the packet variant with stock=10
        variant = ProductVariant.objects.create(
            product=self.bulk_product,
            sku="RICE-10KG",
            barcode="PRE-BARCODE- rice",
            stock_quantity=10,
            retail_price=650.00,
            net_weight_value=10.0,
            unit_of_measure="kg"
        )

        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {self.staff_token}")
        payload = {
            "source_description": "Bulk rice packaging",
            "outputs": [
                {
                    "product_id": str(self.bulk_product.id),
                    "sku": "RICE-10KG",
                    "weight_per_packet": 10.0,
                    "unit_of_measure": "kg",
                    "packets_produced": 5,
                    "retail_price": 680.00   # Updated price
                }
            ]
        }

        response = self.client.post(self.list_create_url, data=payload, format='json')
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)

        # Check response details
        output = response.data['outputs'][0]
        self.assertEqual(output['sku'], "RICE-10KG")
        self.assertEqual(output['is_new_variant'], False)
        # Should return existing barcode value
        self.assertEqual(output['barcode_value'], "PRE-BARCODE- rice")

        # Validate database state
        variant.refresh_from_db()
        # Stock: 10 + 5 = 15
        self.assertEqual(variant.stock_quantity, 15)
        # Retail price: updated to 680.00
        self.assertEqual(float(variant.retail_price), 680.00)

    # ── Database Rollback Integrity ──────────────────────────────────────────

    def test_atomic_rollback_on_failure(self):
        """
        If processing fails on the 2nd output line (e.g. invalid product ID),
        the entire job creation must roll back. No new variants created, no stock
        updated, and no packaging job logs saved.
        """
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {self.staff_token}")
        payload = {
            "source_description": "Atomic test job",
            "outputs": [
                {
                    "product_id": str(self.bulk_product.id),
                    "sku": "RICE-VALID-SKU",
                    "packets_produced": 10,
                    "retail_price": 50.00
                },
                {
                    "product_id": str(uuid.uuid4()),    # Invalid Product ID -> throws ValueError
                    "sku": "RICE-INVALID-SKU",
                    "packets_produced": 5,
                    "retail_price": 200.00
                }
            ]
        }

        response = self.client.post(self.list_create_url, data=payload, format='json')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

        # Verify no job rows written
        self.assertEqual(PackagingJob.objects.count(), 0)
        self.assertEqual(PackagingJobOutput.objects.count(), 0)

        # Verify first variant was NOT created (rolled back)
        self.assertFalse(ProductVariant.objects.filter(sku="RICE-VALID-SKU").exists())

    # ── Querying & Detail Endpoints ──────────────────────────────────────────

    def test_list_and_detail_endpoints(self):
        """Verify GET list and GET detail endpoints return properly formatted logs."""
        # Create two jobs manually
        job1 = PackagingJob.objects.create(
            source_description="Bulk Job 1", notes="Job 1 note", created_by=uuid.UUID(STAFF_UUID)
        )
        job2 = PackagingJob.objects.create(
            source_description="Bulk Job 2", notes="Job 2 note", created_by=uuid.UUID(STAFF_UUID)
        )

        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {self.staff_token}")

        # Test list endpoint
        response = self.client.get(self.list_create_url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        # Verify paginated response structure
        self.assertIn('results', response.data)
        results = response.data['results']
        self.assertEqual(len(results), 2)
        # Should be ordered by created_at descending
        self.assertEqual(results[0]['source_description'], "Bulk Job 2")
        self.assertEqual(results[1]['source_description'], "Bulk Job 1")

        # Test detail endpoint
        detail_url = self._get_detail_url(job1.id)
        response = self.client.get(detail_url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(str(response.data['id']), str(job1.id))
        self.assertEqual(response.data['source_description'], "Bulk Job 1")
        self.assertEqual(response.data['notes'], "Job 1 note")

    # ── Integration with Reservation ─────────────────────────────────────────

    def test_packet_variant_is_reservable(self):
        """
        Verify that a newly repackaged packet variant is instantly available
        and reservable via the checkout reserve endpoint.
        """
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {self.staff_token}")
        payload = {
            "source_description": "Packaging Rice for checkout",
            "outputs": [
                {
                    "product_id": str(self.bulk_product.id),
                    "sku": "RESERVE-PACK-SKU",
                    "weight_per_packet": 2.0,
                    "unit_of_measure": "kg",
                    "packets_produced": 10,
                    "retail_price": 160.00
                }
            ]
        }
        response = self.client.post(self.list_create_url, data=payload, format='json')
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)

        variant_id = response.data['outputs'][0]['variant_id']

        # Now reserve this variant using the customer token via Spec 07 endpoint
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {self.customer_token}")
        reserve_url = reverse('checkout-reserve')
        reserve_payload = {
            "variant_id": str(variant_id),
            "quantity": 3
        }

        response = self.client.post(reserve_url, data=reserve_payload, format='json')
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)

        # Check in DB
        res = Reservation.objects.get(id=response.data['reservation_id'])
        self.assertEqual(res.status, 'active')
        self.assertEqual(res.reserved_quantity, 3)
        self.assertEqual(str(res.variant_id), str(variant_id))
