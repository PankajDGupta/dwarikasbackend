import os
import json
import logging
import requests
from decimal import Decimal
from django.utils import timezone
from django.conf import settings
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from rest_framework.permissions import IsAuthenticated, AllowAny
from api.permissions import IsStaffOrManager, IsManager
from inventory.models import Product, ProductVariant
from quickcommerce.models import QCPlatformListing, QCPlatformCredentials, QCPurchaseOrder
from quickcommerce.validation_engine import validate_product_for_listing
from quickcommerce.pipelines.jiomart_pipeline import submit_jiomart_batch, enqueue_jiomart_polling, get_fynd_access_token
from quickcommerce.pipelines.blinkit_pipeline import submit_blinkit_listing
from quickcommerce.fulfillment.blinkit_fulfillment import process_blinkit_po, submit_blinkit_asn, verify_blinkit_webhook_signature
from quickcommerce.fulfillment.jiomart_fulfillment import process_jiomart_order, close_jiomart_manifest
from quickcommerce.metrics.calculator import get_operational_metrics
from quickcommerce.serializers import (
    ListingSyncTriggerSerializer,
    QCListingStatusSerializer,
    BlinkitPoWebhookSerializer,
    BlinkitAsnSubmitSerializer,
    JioMartOrderWebhookSerializer,
    JioMartManifestCloseSerializer,
    OperationalMetricsQuerySerializer
)

logger = logging.getLogger(__name__)

class ListingsSyncView(APIView):
    """
    POST /api/v1/quickcommerce/listings/sync/
    Triggers unified one-click listing sync for Blinkit and JioMart channels.
    """
    permission_classes = [IsStaffOrManager]

    def post(self, request):
        serializer = ListingSyncTriggerSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        product_id = serializer.validated_data["product_id"]
        platforms = serializer.validated_data["platforms"]
        fssai_license = serializer.validated_data.get("fssai_license")
        marketplace_config = serializer.validated_data.get("marketplace_config", {})

        try:
            product = Product.objects.get(id=product_id)
        except Product.DoesNotExist:
            return Response({"error": "Product not found in Dwarika's catalog."}, status=status.HTTP_404_NOT_FOUND)

        results = {}
        validation_warnings = []

        # Enforce pre-flight validation per platform
        for platform in platforms:
            config = marketplace_config.get(platform, {})
            # Merge fssai_license if passed in config
            if fssai_license:
                config["fssai_license"] = fssai_license

            issues = validate_product_for_listing(product, platform, fssai_license, config)
            if issues:
                return Response({
                    "success": False,
                    "error": f"Pre-flight validation failed for {platform}.",
                    "validation_issues": issues
                }, status=status.HTTP_422_UNPROCESSABLE_ENTITY)

        # Execute listing pipeline per platform
        for platform in platforms:
            config = marketplace_config.get(platform, {})
            
            # Get or create active listing record
            variant = product.variants.first() # assume primary variant for single sync
            listing, created = QCPlatformListing.objects.get_or_create(
                product=product,
                variant=variant,
                platform=platform,
                defaults={
                    "sync_status": "DRAFT",
                    "fssai_license": fssai_license
                }
            )

            # Idempotency guard: if ACTIVE already, skip SP-API listing call
            if not created and listing.sync_status == "ACTIVE":
                results[platform] = {
                    "status": "ACTIVE",
                    "matched_upc": listing.platform_upc,
                    "message": "Product already active on platform catalog. Sync skipped."
                }
                continue

            # Set MRP and selling price snapshots
            if variant:
                listing.mrp_snapshot = variant.mrp or variant.retail_price
                listing.selling_price_snapshot = variant.retail_price
                listing.fssai_license = fssai_license or config.get("fssai_license")
                listing.save()

            if platform == 'jiomart':
                # Trigger Fynd Konnect batch POST
                res = submit_jiomart_batch([listing], fssai_license)
                results['jiomart'] = {
                    "status": res["status"],
                    "trace_id": res.get("trace_id"),
                    "message": res["message"]
                }
            elif platform == 'blinkit':
                # Trigger semantic UPC match or sheet compilation
                res = submit_blinkit_listing(listing, fssai_license, config)
                results['blinkit'] = {
                    "status": res["status"],
                    "matched_upc": res.get("matched_upc"),
                    "submission_guid": res.get("submission_guid"),
                    "message": res["message"]
                }

        return Response({
            "success": True,
            "product_id": str(product.id),
            "results": results,
            "validation_warnings": validation_warnings
        }, status=status.HTTP_202_ACCEPTED)


class ListingStatusView(APIView):
    """
    GET /api/v1/quickcommerce/listings/{product_id}/status/
    Returns list of sync status entries for a given catalog product.
    """
    permission_classes = [IsStaffOrManager]

    def get(self, request, product_id):
        try:
            product = Product.objects.get(id=product_id)
        except Product.DoesNotExist:
            return Response({"error": "Product not found."}, status=status.HTTP_404_NOT_FOUND)

        listings = QCPlatformListing.objects.filter(product=product)
        serializer = QCListingStatusSerializer(listings, many=True)
        return Response({
            "product_id": str(product.id),
            "listings": serializer.data
        }, status=status.HTTP_200_OK)


class BlinkitPoWebhookView(APIView):
    """
    POST /api/v1/quickcommerce/blinkit/webhook/po/
    Receives incoming B2B PO events from Blinkit.
    Signature validated via HMAC-SHA256 header.
    """
    authentication_classes = []
    permission_classes = [AllowAny]

    def post(self, request):
        # Validate webhook signature
        incoming_sig = request.headers.get("X-Blinkit-Signature")
        secret_record = QCPlatformCredentials.objects.filter(platform='blinkit').first()
        secret = secret_record.blinkit_webhook_secret if secret_record else None

        # Allow bypass in testing if no secret configured
        is_testing = getattr(settings, 'IS_TESTING', False)
        if not is_testing and secret:
            body_bytes = request.body
            if not verify_blinkit_webhook_signature(body_bytes, incoming_sig, secret):
                return Response({"error": "Invalid webhook signature."}, status=status.HTTP_401_UNAUTHORIZED)

        serializer = BlinkitPoWebhookSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        # Process the PO
        payload = serializer.validated_data
        po = process_blinkit_po(payload)

        # Raise warning / alert if MRP mismatch was flagged
        response_data = {
            "success": True,
            "po_id": po.platform_po_id,
            "status": po.po_status
        }
        if po.raw_payload and po.raw_payload.get("mrp_mismatch"):
            response_data["warning"] = "MRP mismatch detected. Fulfillment blocked."

        return Response(response_data, status=status.HTTP_200_OK)


class BlinkitAsnSubmitView(APIView):
    """
    POST /api/v1/quickcommerce/blinkit/asn/submit/
    Submits Advanced Shipping Note after warehouse dispatch.
    """
    permission_classes = [IsStaffOrManager]

    def post(self, request):
        serializer = BlinkitAsnSubmitSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        po_id = serializer.validated_data["po_id"]
        dispatched_items = serializer.validated_data["dispatched_items"]
        tracking_ref = serializer.validated_data.get("tracking_reference")

        try:
            po = submit_blinkit_asn(po_id, dispatched_items, tracking_ref)
        except ValueError as e:
            return Response({"error": str(e)}, status=status.HTTP_409_CONFLICT)

        return Response({
            "asn_reference": po.asn_reference,
            "po_id": po.platform_po_id,
            "status": po.po_status,
            "message": "Advanced Shipping Note transmitted to Blinkit dark store successfully."
        }, status=status.HTTP_200_OK)


class JioMartOrderWebhookView(APIView):
    """
    POST /api/v1/quickcommerce/jiomart/webhook/order/
    Receives new order notifications from JioMart.
    """
    authentication_classes = []
    permission_classes = [AllowAny]

    def post(self, request):
        serializer = JioMartOrderWebhookSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        res = process_jiomart_order(serializer.validated_data)
        return Response(res, status=status.HTTP_200_OK)


class JioMartManifestCloseView(APIView):
    """
    POST /api/v1/quickcommerce/jiomart/manifest/close/
    Closes shipping manifest and signals dispatch to JioMart.
    """
    permission_classes = [IsStaffOrManager]

    def post(self, request):
        serializer = JioMartManifestCloseSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        order_id = serializer.validated_data["jiomart_order_id"]
        manifest_id = serializer.validated_data["manifest_id"]

        try:
            res = close_jiomart_manifest(order_id, manifest_id)
        except ValueError as e:
            return Response({"error": str(e)}, status=status.HTTP_404_NOT_FOUND)

        return Response(res, status=status.HTTP_200_OK)


class OperationalMetricsView(APIView):
    """
    GET /api/v1/quickcommerce/metrics/
    Returns OTIF, Fill Rate, and IDM calculations. Manager-only REST endpoint.
    """
    permission_classes = [IsManager]

    def get(self, request):
        serializer = OperationalMetricsQuerySerializer(data=request.query_params)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        platform = serializer.validated_data.get("platform")
        from_date = serializer.validated_data.get("from_date")
        to_date = serializer.validated_data.get("to_date")

        metrics = get_operational_metrics(platform, from_date, to_date)
        return Response(metrics, status=status.HTTP_200_OK)


class JioMartBatchPollView(APIView):
    """
    POST /api/v1/quickcommerce/tasks/poll-jiomart-status/
    Internal Cloud Task worker endpoint to check batch upload status.
    """
    authentication_classes = []
    permission_classes = [AllowAny]

    def post(self, request):
        # Header task validation to prevent direct internet invocation
        task_name = request.headers.get('X-CloudTasks-TaskName')
        is_testing = getattr(settings, 'IS_TESTING', False)
        if not is_testing and not task_name:
            return Response({"error": "Forbidden"}, status=status.HTTP_403_FORBIDDEN)

        trace_id = request.data.get("trace_id")
        retry_count = request.data.get("retry_count", 0)

        # Fetch matching listings
        listings = QCPlatformListing.objects.filter(trace_id=trace_id, platform='jiomart')
        if not listings.exists():
            return Response({"message": f"No listings found for trace_id {trace_id}"}, status=status.HTTP_200_OK)

        # Call batch status endpoint
        base_url = os.environ.get('FYND_API_BASE_URL', 'https://fyndkonnect.konnect.uat.fyndx1.de')
        url = f"{base_url}/v3/catalog/batch-status?trace_id={trace_id}"
        
        access_token = get_fynd_access_token()
        headers = {"x-access-token": access_token}

        # Mock check
        if is_testing or base_url.startswith('mock://'):
            # Simulating status change based on retry count
            if retry_count >= 1:
                batch_status = "COMPLETED"
                batch_errors = []
            else:
                batch_status = "PROCESSING"
                batch_errors = []
        else:
            try:
                res = requests.get(url, headers=headers, timeout=10)
                if res.status_code == 200:
                    data = res.json()
                    batch_status = data.get("status", "PROCESSING")
                    batch_errors = data.get("errors", [])
                else:
                    batch_status = "ERROR"
                    batch_errors = [{"code": "API_ERROR", "message": f"HTTP {res.status_code}"}]
            except Exception as e:
                batch_status = "ERROR"
                batch_errors = [{"code": "CONN_ERROR", "message": str(e)}]

        if batch_status == "COMPLETED":
            listings.update(sync_status="ACTIVE", validation_issues=[])
        elif batch_status == "ERROR" or batch_status == "REJECTED":
            # Store errors
            for listing in listings:
                listing.sync_status = "ERROR"
                listing.validation_issues = batch_errors
                listing.save()
        else: # PROCESSING / SUBMITTED
            # Re-enqueue polling task up to 5 attempts
            if retry_count < 5:
                enqueue_jiomart_polling(trace_id, retry_count + 1)

        return Response({
            "trace_id": trace_id,
            "status": batch_status,
            "retry_count": retry_count
        }, status=status.HTTP_200_OK)
