import jwt
from django.test import TestCase, override_settings
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient
from django.conf import settings

from inventory.models import Product, ProductVariant


def generate_test_jwt(role, user_id="user-123", email="user@example.com"):
    """Helper: generate a valid JWT payload for the test environment."""
    payload = {
        "aud": "authenticated",
        "sub": user_id,
        "email": email,
        "app_metadata": {
            "role": role
        }
    }
    return jwt.encode(payload, settings.SUPABASE_JWT_SECRET, algorithm="HS256")


@override_settings(SUPABASE_JWT_SECRET="test-jwt-secret-key-at-least-32-chars-long")
class ProductCatalogAPITests(TestCase):

    def setUp(self):
        self.client = APIClient()

        # Create sample products
        self.product1 = Product.objects.create(
            name="Cotton Silk Saree",
            hsn_code="6101",
            gst_slab=18.00,
            brand="Dwarikas",
            category="Apparel",
            subcategory="Saree",
            description="Handcrafted silk saree",
            dietary_type="none"
        )
        self.product2 = Product.objects.create(
            name="Handcrafted Wooden Toy",
            hsn_code="9503",
            gst_slab=12.00,
            brand="ToyCraft",
            category="Toys",
            dietary_type="none"
        )

        # Create sample variants
        self.variant1 = ProductVariant.objects.create(
            product=self.product1,
            sku="DWA-SAREE-RED",
            barcode="8901234567890",
            size="Free Size",
            color="Red",
            stock_quantity=10,
            retail_price=4999.00,
            mrp=5500.00,
            weight_volume="500g",
            net_quantity=1.00,
            unit_of_measure="pcs"
        )
        self.variant2 = ProductVariant.objects.create(
            product=self.product1,
            sku="DWA-SAREE-BLUE",
            barcode="8901234567891",
            size="Free Size",
            color="Blue",
            stock_quantity=5,
            retail_price=4999.00,
            mrp=5500.00,
            weight_volume="500g",
            net_quantity=1.00,
            unit_of_measure="pcs"
        )
        self.variant3 = ProductVariant.objects.create(
            product=self.product2,
            sku="TOY-WOOD-HORSE",
            barcode="8901234567892",
            size="Small",
            color="Brown",
            stock_quantity=20,
            retail_price=1200.00,
            mrp=1500.00,
            weight_volume="300g",
            net_quantity=1.00,
            unit_of_measure="pcs"
        )

        # Generate authorization headers for different roles
        self.customer_token = generate_test_jwt("customer")
        self.staff_token = generate_test_jwt("staff")
        self.manager_token = generate_test_jwt("manager")

    def test_public_product_list(self):
        """Anonymous users can list products without authentication."""
        url = reverse("product-list")
        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        # Check pagination is applied
        self.assertIn("results", response.data)
        self.assertIn("count", response.data)
        self.assertEqual(response.data["count"], 2)

        # Verify nesting of variants
        product_data = response.data["results"][0]
        self.assertIn("variants", product_data)
        # Verify the product attributes are exposed
        self.assertIn("brand", product_data)
        self.assertIn("category", product_data)

    def test_public_product_detail(self):
        """Anonymous users can retrieve a single product with variants."""
        url = reverse("product-detail", kwargs={"id": self.product1.id})
        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["name"], self.product1.name)
        self.assertEqual(len(response.data["variants"]), 2)
        # Verify variant attributes are serialized
        variant_data = response.data["variants"][0]
        self.assertIn("mrp", variant_data)
        self.assertIn("weight_volume", variant_data)
        self.assertIn("net_quantity", variant_data)
        self.assertIn("unit_of_measure", variant_data)

    def test_public_variant_list(self):
        """Anonymous users can list variants of a product."""
        url = reverse("variant-list", kwargs={"product_id": self.product1.id})
        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        # Note: variant list endpoint is not paginated by default in standard setup unless configured,
        # but let's check structure. Since views.py uses ListCreateAPIView and DEFAULT_PAGINATION_CLASS is configured,
        # it is paginated! Let's check results.
        self.assertIn("results", response.data)
        self.assertEqual(response.data["count"], 2)

    def test_create_product_permissions(self):
        """Verify role restrictions on product creation."""
        url = reverse("product-list")
        payload = {
            "name": "New Handicraft Item",
            "hsn_code": "9701",
            "gst_slab": 12.00,
            "brand": "RuralArt",
            "category": "Handicrafts"
        }

        # 1. Anonymous request -> 401 Unauthorized
        response = self.client.post(url, data=payload)
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

        # 2. Customer request -> 403 Forbidden
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {self.customer_token}")
        response = self.client.post(url, data=payload)
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

        # 3. Staff request -> 201 Created
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {self.staff_token}")
        response = self.client.post(url, data=payload)
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data["name"], payload["name"])

        # 4. Manager request -> 201 Created
        payload["name"] = "Another Handicraft Item"
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {self.manager_token}")
        response = self.client.post(url, data=payload)
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)

    def test_update_product_permissions(self):
        """Verify role restrictions on product updates."""
        url = reverse("product-detail", kwargs={"id": self.product1.id})
        payload = {"name": "Updated Cotton Silk Saree"}

        # 1. Anonymous request -> 401 Unauthorized
        response = self.client.patch(url, data=payload)
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

        # 2. Customer request -> 403 Forbidden
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {self.customer_token}")
        response = self.client.patch(url, data=payload)
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

        # 3. Staff request -> 200 OK
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {self.staff_token}")
        response = self.client.patch(url, data=payload)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["name"], payload["name"])

        # 4. Manager request -> 200 OK
        payload["name"] = "Manager Updated Saree"
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {self.manager_token}")
        response = self.client.patch(url, data=payload)
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_delete_product_permissions(self):
        """Verify role restrictions on product deletion (Manager only)."""
        url = reverse("product-detail", kwargs={"id": self.product2.id})

        # 1. Anonymous request -> 401 Unauthorized
        response = self.client.delete(url)
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

        # 2. Customer request -> 403 Forbidden
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {self.customer_token}")
        response = self.client.delete(url)
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

        # 3. Staff request -> 403 Forbidden
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {self.staff_token}")
        response = self.client.delete(url)
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

        # 4. Manager request -> 204 No Content
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {self.manager_token}")
        response = self.client.delete(url)
        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)

    def test_create_variant_permissions(self):
        """Verify role restrictions on variant creation."""
        url = reverse("variant-list", kwargs={"product_id": self.product1.id})
        payload = {
            "sku": "DWA-SAREE-GREEN",
            "size": "Free Size",
            "color": "Green",
            "stock_quantity": 8,
            "retail_price": 4999.00
        }

        # 1. Anonymous request -> 401 Unauthorized
        response = self.client.post(url, data=payload)
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

        # 2. Customer request -> 403 Forbidden
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {self.customer_token}")
        response = self.client.post(url, data=payload)
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

        # 3. Staff request -> 201 Created
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {self.staff_token}")
        response = self.client.post(url, data=payload)
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data["sku"], payload["sku"])

    def test_filtering_and_searching(self):
        """Verify query parameter filters and search terms work as expected."""
        url = reverse("product-list")

        # 1. Filter by HSN code (case-insensitive iexact)
        response = self.client.get(f"{url}?hsn_code=6101")
        self.assertEqual(response.data["count"], 1)
        self.assertEqual(response.data["results"][0]["name"], self.product1.name)

        # 2. Filter by GST slab
        response = self.client.get(f"{url}?gst_slab=12.00")
        self.assertEqual(response.data["count"], 1)
        self.assertEqual(response.data["results"][0]["name"], self.product2.name)

        # 3. Search by name
        response = self.client.get(f"{url}?search=wooden")
        self.assertEqual(response.data["count"], 1)
        self.assertEqual(response.data["results"][0]["name"], self.product2.name)

        # 4. Search by HSN
        response = self.client.get(f"{url}?search=6101")
        self.assertEqual(response.data["count"], 1)
        self.assertEqual(response.data["results"][0]["name"], self.product1.name)

    def test_filtering_by_id(self):
        """Verify that products can be filtered by their UUID primary key."""
        url = reverse("product-list")
        response = self.client.get(f"{url}?id={self.product1.id}")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["count"], 1)
        self.assertEqual(response.data["results"][0]["name"], self.product1.name)


    def test_variant_filtering_and_searching(self):
        """Verify variant filters work as expected."""
        url = reverse("variant-list", kwargs={"product_id": self.product1.id})

        # 1. Filter variants by color
        response = self.client.get(f"{url}?color=Red")
        self.assertEqual(response.data["count"], 1)
        self.assertEqual(response.data["results"][0]["sku"], self.variant1.sku)

        # 2. Filter variants by size
        response = self.client.get(f"{url}?size=Free Size")
        self.assertEqual(response.data["count"], 2)

        # 3. Search variants by SKU/barcode
        response = self.client.get(f"{url}?search=BLUE")
        self.assertEqual(response.data["count"], 1)
        self.assertEqual(response.data["results"][0]["sku"], self.variant2.sku)

    def test_n_plus_one_queries_prevention(self):
        """Ensure listing products uses prefetch_related to avoid N+1 queries."""
        url = reverse("product-list")

        # Create additional products and variants to see if query count remains constant
        for i in range(5):
            p = Product.objects.create(name=f"Batch Product {i}", hsn_code="1111", gst_slab=18.00)
            ProductVariant.objects.create(product=p, sku=f"BATCH-SKU-{i}", retail_price=10.0)

        # Evaluate query count.
        # Queries expected:
        # 1. count query (pagination)
        # 2. products fetch query
        # 3. variants prefetch query
        with self.assertNumQueries(3):
            response = self.client.get(url)
            self.assertEqual(response.status_code, status.HTTP_200_OK)
            # Evaluate all items in paginated results list to trigger lazy evaluations
            for product in response.data["results"]:
                for variant in product["variants"]:
                    _ = variant["sku"]
