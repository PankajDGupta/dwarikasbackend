import json
import logging
from django.utils import timezone
from .models import AmazonListing

logger = logging.getLogger(__name__)

def process_sns_notification(sns_payload: dict) -> dict:
    """
    Parses an SNS notification message, maps states, and updates the matching
    AmazonListing record in the database.
    """
    if sns_payload.get("Type") != "Notification":
        return {"success": False, "reason": f"Unsupported Type: {sns_payload.get('Type')}"}

    message_str = sns_payload.get("Message")
    if not message_str:
        return {"success": False, "reason": "Missing Message content"}

    try:
        message = json.loads(message_str)
    except json.JSONDecodeError:
        return {"success": False, "reason": "Failed to decode Message JSON string"}

    notification_type = message.get("notificationType")
    payload = message.get("payload", {})

    sku = payload.get("sku")
    if not sku:
        return {"success": False, "reason": "SKU not found in message payload"}

    try:
        listing = AmazonListing.objects.get(sku=sku)
    except AmazonListing.DoesNotExist:
        return {"success": False, "reason": f"AmazonListing not found for SKU: {sku}"}

    # Update ASIN if provided
    asin = payload.get("asin")
    if asin:
        listing.asin = asin

    # Map status updates
    status = payload.get("status")
    
    if notification_type == "LISTINGS_ITEM_STATUS_CHANGE":
        if status == "BUYABLE":
            listing.sync_status = "ACTIVE"
        elif status == "SUPPRESSED":
            listing.sync_status = "SUPPRESSED"
        # If not BUYABLE/SUPPRESSED, keep as is or set appropriate mapping if defined.
        listing.last_synced_at = timezone.now()
        
    elif notification_type == "LISTINGS_ITEM_ISSUES_CHANGE" or payload.get("issues"):
        issues = payload.get("issues", [])
        listing.validation_issues = issues
        # If there's an error severity issue, mark status as ERROR
        if any(issue.get("severity") == "ERROR" for issue in issues):
            listing.sync_status = "ERROR"
        listing.last_synced_at = timezone.now()
        
    else:
        # Default fallback for other notification types
        listing.last_synced_at = timezone.now()

    listing.save()
    return {"success": True, "sku": sku, "status": listing.sync_status}
