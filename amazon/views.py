import logging
import requests
import uuid
from django.utils import timezone
from rest_framework import status
from rest_framework.views import APIView
from rest_framework.response import Response
from api.permissions import IsStaffOrManager
from inventory.models import Product
from .models import AmazonCredentials, AmazonListing
from .serializers import AmazonListingSyncSerializer, AmazonListingStatusSerializer
from .sp_api_client import put_listings_item
from .sns_verifier import verify_sns_signature
from .sqs_processor import process_sns_notification

logger = logging.getLogger(__name__)

class AmazonListingSyncView(APIView):
    """
    POST /api/v1/amazon/listings/sync/
    Triggers product listing synchronization to Amazon.
    """
    permission_classes = [IsStaffOrManager]

    def post(self, request, *args, **kwargs):
        serializer = AmazonListingSyncSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        product_id = serializer.validated_data["product_id"]
        marketplace_id = serializer.validated_data["marketplace_id"]

        product = Product.objects.get(id=product_id)
        # Find the first variant with a barcode
        variant = None
        for v in product.variants.all():
            if v.barcode:
                variant = v
                break

        if not variant:
            return Response(
                {"error": "Product has no variants with a barcode."},
                status=status.HTTP_400_BAD_REQUEST
            )

        # Idempotency check: prevent duplicate active or submitted listings
        existing_listing = AmazonListing.objects.filter(sku=variant.sku).first()
        if existing_listing and existing_listing.sync_status in ["SUBMITTED", "ACTIVE"]:
            return Response({
                "success": True,
                "sku": variant.sku,
                "status": existing_listing.sync_status,
                "submission_id": str(existing_listing.submission_id) if existing_listing.submission_id else None,
                "message": "Listing already submitted or active."
            }, status=status.HTTP_200_OK)

        # Retrieve Selling Partner Credentials
        credentials = AmazonCredentials.objects.filter(primary_marketplace_id=marketplace_id).first()
        if not credentials:
            credentials = AmazonCredentials.objects.first()
        if not credentials:
            return Response(
                {"error": "Amazon Selling Partner credentials not configured for this marketplace."},
                status=status.HTTP_400_BAD_REQUEST
            )

        # Build payload according to the spec
        payload = {
            "productType": "PRODUCT",
            "requirements": "LISTING",
            "attributes": {
                "item_name": [
                    {
                        "value": product.name,
                        "language_tag": "en_IN",
                        "marketplace_id": marketplace_id
                    }
                ],
                "brand": [
                    {
                        "value": product.brand or "Generic",
                        "language_tag": "en_IN",
                        "marketplace_id": marketplace_id
                    }
                ],
                "externally_assigned_product_identifier": [
                    {
                        "type": "ean" if len(variant.barcode) == 13 else "upc",
                        "value": variant.barcode,
                        "marketplace_id": marketplace_id
                    }
                ],
                "purchasable_offer": [
                    {
                        "marketplace_id": marketplace_id,
                        "currency": "INR",
                        "our_price": [
                            {
                                "schedule": [
                                    {
                                        "value_with_tax": float(variant.retail_price)
                                    }
                                ]
                            }
                        ]
                    }
                ],
                "fulfillment_availability": [
                    {
                        "fulfillment_channel_code": "DEFAULT",
                        "quantity": variant.stock_quantity,
                        "marketplace_id": marketplace_id
                    }
                ],
                "condition_type": [
                    {
                        "value": "new_new",
                        "marketplace_id": marketplace_id
                    }
                ],
                "bullet_point": [
                    {
                        "value": product.description or product.name,
                        "language_tag": "en_IN",
                        "marketplace_id": marketplace_id
                    }
                ]
            }
        }

        # If ASIN already exists from previous matchings, pass it as suggested ASIN
        if existing_listing and existing_listing.asin:
            payload["attributes"]["merchant_suggested_asin"] = [
                {
                    "value": existing_listing.asin,
                    "marketplace_id": marketplace_id
                }
            ]

        try:
            # Synchronous API Invocation
            response_data = put_listings_item(credentials, variant.sku, payload)
        except requests.RequestException as e:
            logger.error(f"Amazon SP-API request failed: {e}")
            return Response(
                {"error": "Amazon SP-API unreachable or returned 5xx.", "detail": str(e)},
                status=status.HTTP_502_BAD_GATEWAY
            )
        except Exception as e:
            logger.error(f"Failed to submit listing: {e}")
            return Response(
                {"error": "Unexpected error invoking SP-API.", "detail": str(e)},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )

        api_status = response_data.get("status")
        submission_id = response_data.get("submissionId")
        issues = response_data.get("issues", [])

        # Create or update listing record
        listing, created = AmazonListing.objects.update_or_create(
            sku=variant.sku,
            defaults={
                "product_id": product.id,
                "marketplace_id": marketplace_id,
                "sync_status": "SUBMITTED" if api_status == "ACCEPTED" else "INVALID",
                "submission_id": uuid.UUID(submission_id) if submission_id else None,
                "validation_issues": issues,
                "price_synced": variant.retail_price,
                "quantity_synced": variant.stock_quantity,
                "last_synced_at": timezone.now(),
            }
        )

        if api_status == "ACCEPTED":
            return Response({
                "success": True,
                "sku": variant.sku,
                "status": "SUBMITTED",
                "submission_id": str(listing.submission_id) if listing.submission_id else None,
                "message": "Listing submission successfully accepted by Amazon. Final state changes will resolve asynchronously."
            }, status=status.HTTP_202_ACCEPTED)
        else:
            return Response({
                "success": False,
                "sku": variant.sku,
                "status": "INVALID",
                "issues": issues
            }, status=status.HTTP_422_UNPROCESSABLE_ENTITY)


class AmazonListingStatusView(APIView):
    """
    GET /api/v1/amazon/listings/{product_id}/status/
    Retrieves listing sync status and validation issues.
    """
    permission_classes = [IsStaffOrManager]

    def get(self, request, product_id, *args, **kwargs):
        # We find the primary listing for the product
        listing = AmazonListing.objects.filter(product_id=product_id).first()
        if not listing:
            return Response(
                {"error": "No Amazon listing tracking found for this product."},
                status=status.HTTP_404_NOT_FOUND
            )
        
        serializer = AmazonListingStatusSerializer(listing)
        return Response(serializer.data, status=status.HTTP_200_OK)


class SQSWebhookView(APIView):
    """
    POST /api/v1/amazon/webhooks/sqs-receiver/
    Consumes AWS SNS notification messages forwarded from SQS queue.
    """
    authentication_classes = []
    permission_classes = []

    def post(self, request, *args, **kwargs):
        payload = request.data

        # 1. Verify SNS Signature
        if not verify_sns_signature(payload):
            return Response({"error": "Invalid signature or untrusted source."}, status=status.HTTP_400_BAD_REQUEST)

        # 2. Handle SNS Handshake / Confirmation requests
        msg_type = payload.get("Type")
        if msg_type == "SubscriptionConfirmation":
            subscribe_url = payload.get("SubscribeURL")
            if subscribe_url:
                try:
                    res = requests.get(subscribe_url, timeout=10)
                    res.raise_for_status()
                    logger.info("Successfully confirmed SNS subscription.")
                    return Response({"message": "Subscription confirmed successfully."}, status=status.HTTP_200_OK)
                except Exception as e:
                    logger.error(f"Failed to confirm SNS subscription: {e}")
                    return Response({"error": "Failed to confirm subscription."}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

        # 3. Process Listing Status Notification
        if msg_type == "Notification":
            result = process_sns_notification(payload)
            if result.get("success"):
                return Response(result, status=status.HTTP_200_OK)
            else:
                return Response(result, status=status.HTTP_400_BAD_REQUEST)

        return Response({"message": f"Ignored message type: {msg_type}"}, status=status.HTTP_200_OK)
