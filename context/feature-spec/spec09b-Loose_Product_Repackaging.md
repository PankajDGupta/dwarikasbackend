# Spec 09b — Loose Product Repackaging & Packet Barcode Creation

## Goal

Implement the packaging job workflow for loose/bulk commodities. When a store receives bulk goods (e.g., 100 kg of rice) and uses a packaging machine to create retail packets of various sizes (e.g., 1 kg, 5 kg, 10 kg), staff can log a **Packaging Job** via a single API call. The system will:

1. Auto-create or update `ProductVariant` rows for each packet size
2. Increment stock quantities on those variants
3. Auto-generate Code 128 barcodes for each packet variant
4. Log the full job as an audit trail

Once packets are in inventory they are just normal `ProductVariant` rows — all existing checkout, order, and POS flows work with zero changes.

**Depends on:** Spec 09 (Barcode Generation) — the `barcode_service` module must exist before this spec is implemented.

---

## Scope

- `POST /api/v1/packaging-jobs/` — log a packaging machine run and produce barcoded inventory
- `GET  /api/v1/packaging-jobs/` — list all packaging jobs (paginated)
- `GET  /api/v1/packaging-jobs/<uuid:id>/` — retrieve a single job with all output lines
- Two new Supabase tables: `packaging_jobs`, `packaging_job_outputs`
- Two new fields on existing tables: `products.is_loose_commodity`, `product_variants.net_weight_value`, `product_variants.unit_of_measure`
- Auth: Staff or Manager only (customers cannot create packaging jobs)

---

## Files to Create / Modify

### `supabase/snippets/003_loose_commodity_repackaging.sql` — New DDL snippet

Apply each statement individually via `supabase db query "..."`:

```sql
-- 1. Mark which products are bulk/loose commodities
ALTER TABLE public.products
  ADD COLUMN IF NOT EXISTS is_loose_commodity BOOLEAN NOT NULL DEFAULT FALSE;

-- 2. Net weight or volume per packet (e.g. 1.000 for a 1kg pack)
ALTER TABLE public.product_variants
  ADD COLUMN IF NOT EXISTS net_weight_value NUMERIC(10, 3) NULL;

-- 3. Unit of measure for net_weight_value
ALTER TABLE public.product_variants
  ADD COLUMN IF NOT EXISTS unit_of_measure TEXT NOT NULL DEFAULT 'unit'
    CHECK (unit_of_measure IN ('unit', 'kg', 'g', 'litre', 'ml'));

-- 4. Audit log: one row per packaging machine run
CREATE TABLE IF NOT EXISTS public.packaging_jobs (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    source_description  TEXT NOT NULL,
    source_variant_id   UUID REFERENCES public.product_variants(id) ON DELETE SET NULL,
    bulk_quantity_used  NUMERIC(10, 3),
    bulk_unit           TEXT,
    notes               TEXT,
    created_by          UUID NOT NULL REFERENCES auth.users(id) ON DELETE SET NULL,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- 5. One row per packet size created in the job
CREATE TABLE IF NOT EXISTS public.packaging_job_outputs (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    job_id              UUID NOT NULL REFERENCES public.packaging_jobs(id) ON DELETE CASCADE,
    variant_id          UUID NOT NULL REFERENCES public.product_variants(id) ON DELETE RESTRICT,
    packets_produced    INTEGER NOT NULL CHECK (packets_produced > 0),
    weight_per_packet   NUMERIC(10, 3),
    unit_of_measure     TEXT,
    barcode_value       TEXT,
    is_new_variant      BOOLEAN NOT NULL DEFAULT FALSE
);

-- 6. Index for fast job output lookups
CREATE INDEX IF NOT EXISTS idx_packaging_job_outputs_job_id
  ON public.packaging_job_outputs(job_id);
```

---

### `inventory/models.py` — Extend existing models + add two new unmanaged models

Add to `Product` model:

```python
is_loose_commodity = models.BooleanField(default=False)
```

Add to `ProductVariant` model:

```python
UNIT_CHOICES = [
    ('unit',  'Unit'),
    ('kg',    'Kilogram'),
    ('g',     'Gram'),
    ('litre', 'Litre'),
    ('ml',    'Millilitre'),
]
net_weight_value = models.DecimalField(max_digits=10, decimal_places=3, null=True, blank=True)
unit_of_measure  = models.TextField(choices=UNIT_CHOICES, default='unit')
```

New unmanaged models at the bottom of `inventory/models.py`:

```python
class PackagingJob(models.Model):
    """
    Audit log of a packaging machine run.
    One job = one session at the machine that produced packets from bulk stock.
    """
    id                 = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    source_description = models.TextField()                          # Free text e.g. "50kg Basmati Rice - INV-001"
    source_variant     = models.ForeignKey(                          # Optional link to bulk ProductVariant
        'ProductVariant',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='packaging_jobs',
        db_column='source_variant_id',
    )
    bulk_quantity_used = models.DecimalField(max_digits=10, decimal_places=3, null=True, blank=True)
    bulk_unit          = models.TextField(null=True, blank=True)     # e.g. 'kg'
    notes              = models.TextField(null=True, blank=True)
    created_by         = models.UUIDField()                          # Supabase auth.users(id)
    created_at         = models.DateTimeField(auto_now_add=True)

    class Meta:
        managed  = False
        db_table = 'packaging_jobs'

    def __str__(self):
        return f"PackagingJob {self.id} — {self.source_description}"


class PackagingJobOutput(models.Model):
    """
    One row per packet size created in a PackagingJob.
    """
    id               = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    job              = models.ForeignKey(
        PackagingJob,
        on_delete=models.CASCADE,
        related_name='outputs',
        db_column='job_id',
    )
    variant          = models.ForeignKey(
        'ProductVariant',
        on_delete=models.RESTRICT,
        related_name='packaging_outputs',
        db_column='variant_id',
    )
    packets_produced = models.IntegerField()
    weight_per_packet = models.DecimalField(max_digits=10, decimal_places=3, null=True, blank=True)
    unit_of_measure  = models.TextField(null=True, blank=True)
    barcode_value    = models.TextField(null=True, blank=True)
    is_new_variant   = models.BooleanField(default=False)

    class Meta:
        managed  = False
        db_table = 'packaging_job_outputs'
```

---

### `inventory/packaging_serializers.py` — New file

```python
"""
Serializers for the Packaging Job workflow.
"""
from rest_framework import serializers

from inventory.models import PackagingJob, PackagingJobOutput, ProductVariant, Product


class PackagingOutputInputSerializer(serializers.Serializer):
    """
    Validates one output line inside the POST /packaging-jobs/ request.

    Either `sku` (existing variant) or `product_id` + `sku` (new variant) must be provided.
    If the SKU does not exist under the given product, a new ProductVariant is auto-created.
    """
    product_id        = serializers.UUIDField(
        help_text="UUID of the parent Product this packet belongs to.",
    )
    sku               = serializers.CharField(
        max_length=100,
        help_text="SKU for this packet size. If not found, a new variant is auto-created.",
    )
    weight_per_packet = serializers.DecimalField(
        max_digits=10, decimal_places=3, required=False, allow_null=True,
        help_text="Net weight/volume per packet (e.g. 1.000 for a 1 kg pack).",
    )
    unit_of_measure   = serializers.ChoiceField(
        choices=['unit', 'kg', 'g', 'litre', 'ml'],
        default='unit',
    )
    packets_produced  = serializers.IntegerField(
        min_value=1,
        help_text="Number of packets produced at this size.",
    )
    retail_price      = serializers.DecimalField(
        max_digits=12, decimal_places=2,
        help_text="Retail selling price for one packet of this size.",
    )


class PackagingJobCreateSerializer(serializers.Serializer):
    """
    Validates the full POST /packaging-jobs/ request body.
    """
    source_description = serializers.CharField(
        help_text="Free-text description of the bulk input used "
                  "(e.g. '50kg Basmati Rice — Lal Qila INV-2026-001').",
    )
    source_variant_id  = serializers.UUIDField(
        required=False, allow_null=True,
        help_text="Optional UUID of the bulk ProductVariant that was consumed.",
    )
    bulk_quantity_used = serializers.DecimalField(
        max_digits=10, decimal_places=3, required=False, allow_null=True,
        help_text="Total bulk quantity consumed (e.g. 50 for 50 kg).",
    )
    bulk_unit          = serializers.ChoiceField(
        choices=['unit', 'kg', 'g', 'litre', 'ml'],
        required=False, allow_null=True,
    )
    notes              = serializers.CharField(required=False, allow_blank=True)
    outputs            = PackagingOutputInputSerializer(many=True, min_length=1)


class PackagingJobOutputResponseSerializer(serializers.ModelSerializer):
    """
    Serializes one output line in the response, including the barcode image URL.
    """
    variant_id        = serializers.UUIDField(source='variant.id', read_only=True)
    sku               = serializers.CharField(source='variant.sku', read_only=True)
    retail_price      = serializers.DecimalField(
        source='variant.retail_price',
        max_digits=12, decimal_places=2, read_only=True,
    )
    barcode_image_url = serializers.SerializerMethodField()

    class Meta:
        model  = PackagingJobOutput
        fields = [
            'id', 'variant_id', 'sku', 'packets_produced',
            'weight_per_packet', 'unit_of_measure',
            'barcode_value', 'barcode_image_url',
            'retail_price', 'is_new_variant',
        ]

    def get_barcode_image_url(self, obj):
        if obj.barcode_value:
            request = self.context.get('request')
            path = f'/api/v1/barcodes/{obj.variant.sku}/code128/'
            if request:
                return request.build_absolute_uri(path)
            return path
        return None


class PackagingJobResponseSerializer(serializers.ModelSerializer):
    """
    Full response shape for a PackagingJob including all output lines.
    """
    outputs = PackagingJobOutputResponseSerializer(many=True, read_only=True)

    class Meta:
        model  = PackagingJob
        fields = [
            'id', 'source_description', 'source_variant_id',
            'bulk_quantity_used', 'bulk_unit',
            'notes', 'created_by', 'created_at',
            'outputs',
        ]
```

---

### `inventory/packaging_views.py` — New file

```python
"""
Packaging Job views.

POST /api/v1/packaging-jobs/   — log a packaging machine run
GET  /api/v1/packaging-jobs/   — list all jobs (paginated)
GET  /api/v1/packaging-jobs/<uuid:id>/ — job detail with outputs
"""
import uuid

from django.db import transaction, DatabaseError
from rest_framework import status, generics
from rest_framework.response import Response
from rest_framework.views import APIView

from api.permissions import IsStaffOrManager
from inventory.barcode_service import generate_code128
from inventory.models import (
    PackagingJob,
    PackagingJobOutput,
    Product,
    ProductVariant,
)
from inventory.packaging_serializers import (
    PackagingJobCreateSerializer,
    PackagingJobResponseSerializer,
)


class PackagingJobListCreateView(APIView):
    """
    GET  /api/v1/packaging-jobs/ — list all packaging jobs
    POST /api/v1/packaging-jobs/ — create a new packaging job
    """
    permission_classes = [IsStaffOrManager]

    # ── GET ──────────────────────────────────────────────────────────────────
    def get(self, request):
        jobs = (
            PackagingJob.objects
            .prefetch_related('outputs__variant')
            .order_by('-created_at')
        )
        serializer = PackagingJobResponseSerializer(
            jobs, many=True, context={'request': request}
        )
        return Response(serializer.data)

    # ── POST ─────────────────────────────────────────────────────────────────
    def post(self, request):
        serializer = PackagingJobCreateSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        data       = serializer.validated_data
        created_by = uuid.UUID(request.user.username)

        try:
            with transaction.atomic():
                # ── Step 1: Resolve optional source variant ──────────────────
                source_variant = None
                if data.get('source_variant_id'):
                    try:
                        source_variant = ProductVariant.objects.get(
                            id=data['source_variant_id']
                        )
                    except ProductVariant.DoesNotExist:
                        return Response(
                            {'error': f"source_variant_id '{data['source_variant_id']}' not found."},
                            status=status.HTTP_404_NOT_FOUND,
                        )

                # ── Step 2: Create the PackagingJob header row ───────────────
                job = PackagingJob.objects.create(
                    source_description=data['source_description'],
                    source_variant=source_variant,
                    bulk_quantity_used=data.get('bulk_quantity_used'),
                    bulk_unit=data.get('bulk_unit'),
                    notes=data.get('notes', ''),
                    created_by=created_by,
                )

                # ── Step 3: Process each output line ────────────────────────
                output_records = []

                for line in data['outputs']:
                    product_id       = line['product_id']
                    sku              = line['sku']
                    packets_produced = line['packets_produced']
                    retail_price     = line['retail_price']
                    weight           = line.get('weight_per_packet')
                    uom              = line.get('unit_of_measure', 'unit')
                    is_new_variant   = False

                    # ── 3a: Validate the product exists ──────────────────────
                    try:
                        product = Product.objects.get(id=product_id)
                    except Product.DoesNotExist:
                        raise ValueError(f"Product '{product_id}' not found.")

                    # ── 3b: Look up or auto-create the ProductVariant ─────────
                    try:
                        variant = ProductVariant.objects.select_for_update().get(
                            sku=sku
                        )
                        # Guard: SKU must belong to the stated product
                        if str(variant.product_id) != str(product_id):
                            raise ValueError(
                                f"SKU '{sku}' already exists under a different product."
                            )
                    except ProductVariant.DoesNotExist:
                        # Auto-create a new variant for this packet size
                        variant = ProductVariant.objects.create(
                            product=product,
                            sku=sku,
                            stock_quantity=0,          # will be incremented below
                            retail_price=retail_price,
                            net_weight_value=weight,
                            unit_of_measure=uom,
                        )
                        is_new_variant = True

                    # ── 3c: Update weight/price fields on the variant ─────────
                    update_fields = []
                    if weight is not None and variant.net_weight_value != weight:
                        variant.net_weight_value = weight
                        update_fields.append('net_weight_value')
                    if variant.unit_of_measure != uom:
                        variant.unit_of_measure = uom
                        update_fields.append('unit_of_measure')
                    if update_fields:
                        variant.save(update_fields=update_fields)

                    # ── 3d: Increment stock ───────────────────────────────────
                    from django.db.models import F
                    ProductVariant.objects.filter(id=variant.id).update(
                        stock_quantity=F('stock_quantity') + packets_produced,
                        retail_price=retail_price,         # always use latest price
                    )

                    # ── 3e: Generate / assign barcode ─────────────────────────
                    barcode_value = variant.barcode or sku
                    if not variant.barcode:
                        # Generate the barcode image (validates SKU is renderable)
                        generate_code128(sku)
                        ProductVariant.objects.filter(id=variant.id).update(barcode=sku)
                        barcode_value = sku

                    # ── 3f: Log the output line ───────────────────────────────
                    output_records.append(
                        PackagingJobOutput(
                            job=job,
                            variant=variant,
                            packets_produced=packets_produced,
                            weight_per_packet=weight,
                            unit_of_measure=uom,
                            barcode_value=barcode_value,
                            is_new_variant=is_new_variant,
                        )
                    )

                PackagingJobOutput.objects.bulk_create(output_records)

                # ── Step 4: Return full job response ─────────────────────────
                job.refresh_from_db()
                response_serializer = PackagingJobResponseSerializer(
                    job, context={'request': request}
                )
                return Response(response_serializer.data, status=status.HTTP_201_CREATED)

        except ValueError as exc:
            return Response({'error': str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        except DatabaseError:
            return Response(
                {'error': 'Database transaction error. Please retry.'},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )


class PackagingJobDetailView(generics.RetrieveAPIView):
    """
    GET /api/v1/packaging-jobs/<uuid:id>/
    Returns a single packaging job with all output lines.
    """
    permission_classes    = [IsStaffOrManager]
    serializer_class      = PackagingJobResponseSerializer
    lookup_field          = 'id'

    def get_queryset(self):
        return PackagingJob.objects.prefetch_related('outputs__variant').all()

    def get_serializer_context(self):
        context = super().get_serializer_context()
        context['request'] = self.request
        return context
```

---

### `inventory/urls.py` — Add packaging job routes

```python
from inventory.packaging_views import PackagingJobListCreateView, PackagingJobDetailView

urlpatterns += [
    path('packaging-jobs/',          PackagingJobListCreateView.as_view(), name='packaging-job-list-create'),
    path('packaging-jobs/<uuid:id>/', PackagingJobDetailView.as_view(),    name='packaging-job-detail'),
]
```

---

### Django migration — State-only migration for new fields + models

```bash
python manage.py makemigrations inventory --name loose_commodity_repackaging
# All models are managed=False — no DDL will run.
# Fake against the local Supabase DB after applying the SQL snippet above:
python manage.py migrate --fake
```

---

## Request / Response Examples

### POST /api/v1/packaging-jobs/ — Request

```json
{
  "source_description": "50kg Basmati Rice — Lal Qila INV-2026-001 (3 bags)",
  "source_variant_id": "a3f1c2d4-0000-0000-0000-000000000001",
  "bulk_quantity_used": 50,
  "bulk_unit": "kg",
  "notes": "Machine A, batch date 2026-06-03",
  "outputs": [
    {
      "product_id": "b2e3d4f5-0000-0000-0000-000000000002",
      "sku": "RICE-1KG",
      "weight_per_packet": 1.000,
      "unit_of_measure": "kg",
      "packets_produced": 20,
      "retail_price": 75.00
    },
    {
      "product_id": "b2e3d4f5-0000-0000-0000-000000000002",
      "sku": "RICE-5KG",
      "weight_per_packet": 5.000,
      "unit_of_measure": "kg",
      "packets_produced": 6,
      "retail_price": 350.00
    },
    {
      "product_id": "b2e3d4f5-0000-0000-0000-000000000002",
      "sku": "RICE-10KG",
      "weight_per_packet": 10.000,
      "unit_of_measure": "kg",
      "packets_produced": 2,
      "retail_price": 680.00
    }
  ]
}
```

### POST /api/v1/packaging-jobs/ — Response (HTTP 201)

```json
{
  "id": "d4e5f6a7-0000-0000-0000-000000000099",
  "source_description": "50kg Basmati Rice — Lal Qila INV-2026-001 (3 bags)",
  "source_variant_id": "a3f1c2d4-0000-0000-0000-000000000001",
  "bulk_quantity_used": "50.000",
  "bulk_unit": "kg",
  "notes": "Machine A, batch date 2026-06-03",
  "created_by": "user-uuid",
  "created_at": "2026-06-03T16:30:00Z",
  "outputs": [
    {
      "id": "output-uuid-1",
      "variant_id": "variant-uuid-1",
      "sku": "RICE-1KG",
      "packets_produced": 20,
      "weight_per_packet": "1.000",
      "unit_of_measure": "kg",
      "barcode_value": "RICE-1KG",
      "barcode_image_url": "https://api.dwarikas.in/api/v1/barcodes/RICE-1KG/code128/",
      "retail_price": "75.00",
      "is_new_variant": true
    },
    {
      "id": "output-uuid-2",
      "variant_id": "variant-uuid-2",
      "sku": "RICE-5KG",
      "packets_produced": 6,
      "weight_per_packet": "5.000",
      "unit_of_measure": "kg",
      "barcode_value": "RICE-5KG",
      "barcode_image_url": "https://api.dwarikas.in/api/v1/barcodes/RICE-5KG/code128/",
      "retail_price": "350.00",
      "is_new_variant": true
    },
    {
      "id": "output-uuid-3",
      "variant_id": "variant-uuid-3",
      "sku": "RICE-10KG",
      "packets_produced": 2,
      "weight_per_packet": "10.000",
      "unit_of_measure": "kg",
      "barcode_value": "RICE-10KG",
      "barcode_image_url": "https://api.dwarikas.in/api/v1/barcodes/RICE-10KG/code128/",
      "retail_price": "680.00",
      "is_new_variant": true
    }
  ]
}
```

---

## What Happens to Packet Variants After Creation

Packets are just regular `ProductVariant` rows. No further changes needed for:

| Flow | Spec | Works automatically |
|---|---|---|
| Barcode label printing | Spec 09 | `barcode_image_url` in response → frontend prints |
| POS barcode scan → sale | Spec 09 / POS | Scanned SKU maps to variant → checkout flow |
| Online catalog listing | Spec 06 | Appears in `GET /api/v1/products/` immediately |
| Checkout reservation | Spec 07 | `POST /api/v1/checkout/reserve/` works normally |
| Order confirmation | Spec 08 | Stock decremented on order confirm |
| ONDC listing | Spec 13 | Auto-included in catalog feed |
| WhatsApp stock query | Spec 14 | Queryable by SKU |

---

## Acceptance Criteria

- [ ] `POST /api/v1/packaging-jobs/` with valid body returns HTTP 201
- [ ] New `ProductVariant` rows are created for SKUs not previously in the database (`is_new_variant: true`)
- [ ] For an already-existing SKU, `stock_quantity` is **incremented** by `packets_produced`, not overwritten
- [ ] `product_variants.barcode` is populated with the SKU string for every output variant
- [ ] `barcode_image_url` in the response resolves to a valid `image/png` (proxy through Spec 09 barcode endpoint)
- [ ] `PackagingJob` and `PackagingJobOutput` rows are written to the database
- [ ] Providing a `source_variant_id` that does not exist returns HTTP 404
- [ ] Providing a SKU that already belongs to a **different** product returns HTTP 400
- [ ] Unauthenticated request returns HTTP 401
- [ ] Customer JWT returns HTTP 403
- [ ] `GET /api/v1/packaging-jobs/` returns paginated list ordered by `created_at` descending
- [ ] `GET /api/v1/packaging-jobs/<uuid>/` returns the job with all output lines and barcode URLs
- [ ] The entire job creation (header row + all output lines + stock updates) rolls back atomically on any failure
- [ ] A packet variant created by this endpoint can be reserved via `POST /api/v1/checkout/reserve/` in the same test run

---

## Test Plan (`inventory/tests/test_packaging.py`)

```python
"""
Test cases for Spec 09b — Loose Product Repackaging.
Use ManagedModelTestRunner so PackagingJob and PackagingJobOutput tables are created in the test DB.
"""

# test_create_job_new_variants
#   POST with 3 output lines where none of the SKUs exist
#   → 201, 3 new ProductVariant rows, stock_quantity = packets_produced each

# test_create_job_existing_variants
#   Create a variant with stock_quantity=10, then POST a job for the same SKU with packets_produced=5
#   → stock_quantity becomes 15 (incremented, not overwritten)

# test_create_job_barcode_assigned
#   POST a job → check product_variants.barcode == sku for each output

# test_create_job_barcode_url_in_response
#   barcode_image_url ends with /api/v1/barcodes/<sku>/code128/

# test_create_job_with_source_variant
#   Provide a valid source_variant_id → job row has source_variant_id set

# test_create_job_invalid_source_variant
#   Provide a non-existent source_variant_id → 404

# test_create_job_sku_product_mismatch
#   SKU already exists under product A, request says product B → 400

# test_create_job_unauthenticated
#   No Authorization header → 401

# test_create_job_customer_forbidden
#   Customer JWT → 403

# test_list_jobs
#   Create 2 jobs, GET /api/v1/packaging-jobs/ → returns both, ordered by created_at desc

# test_detail_job
#   GET /api/v1/packaging-jobs/<id>/ → returns outputs with barcode_image_url

# test_packet_variant_is_reservable
#   After packaging job, POST /api/v1/checkout/reserve/ for the new variant → 201
```
