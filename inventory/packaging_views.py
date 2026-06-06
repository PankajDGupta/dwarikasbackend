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
from rest_framework.pagination import PageNumberPagination

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
    GET  /api/v1/packaging-jobs/ — list all packaging jobs (paginated)
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
        paginator = PageNumberPagination()
        page = paginator.paginate_queryset(jobs, request, view=self)
        if page is not None:
            serializer = PackagingJobResponseSerializer(
                page, many=True, context={'request': request}
            )
            return paginator.get_paginated_response(serializer.data)

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
                        # Locking variant row if it exists
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
