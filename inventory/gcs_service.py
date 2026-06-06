"""
Google Cloud Storage helpers for invoice file management.
"""
import os
import uuid
from django.conf import settings
from google.cloud import storage

GCS_BUCKET_NAME = os.environ.get('GCS_INVOICE_BUCKET')
_client = None


def _get_client() -> storage.Client:
    global _client
    if _client is None:
        _client = storage.Client()
    return _client


def upload_invoice_to_gcs(file_obj, filename: str, content_type: str) -> str:
    """
    Uploads a file-like object to the private GCS invoice bucket.
    If GCS_BUCKET_NAME is not configured (e.g. locally), falls back to saving
    the file to the local media directory and returns a local file path URI.
    """
    if not GCS_BUCKET_NAME:
        # Local fallback mode
        media_dir = os.path.join(settings.BASE_DIR, 'media', 'invoices')
        os.makedirs(media_dir, exist_ok=True)
        unique_id = uuid.uuid4()
        local_filename = f"{unique_id}_{filename}"
        local_path = os.path.join(media_dir, local_filename)

        # Save file to local path
        with open(local_path, 'wb+') as destination:
            for chunk in file_obj.chunks():
                destination.write(chunk)

        # Return file path URI
        return f"file:///{local_path.replace(os.sep, '/')}"

    client = _get_client()
    bucket = client.bucket(GCS_BUCKET_NAME)

    # Namespace files under a UUID subdirectory to prevent collisions
    object_name = f"invoices/{uuid.uuid4()}/{filename}"
    blob = bucket.blob(object_name)
    blob.upload_from_file(file_obj, content_type=content_type)

    return f"gs://{GCS_BUCKET_NAME}/{object_name}"


def generate_signed_url(gcs_object_path: str, expiry_minutes: int = 60) -> str:
    """
    Returns a time-limited signed URL for viewing an invoice image in the HITL UI.
    Supports file:/// local fallback path.
    gcs_object_path format: gs://<bucket>/<object> or file:///<path>
    """
    if gcs_object_path.startswith('file:///'):
        return gcs_object_path

    if not GCS_BUCKET_NAME:
        return gcs_object_path

    client = _get_client()
    # Parse bucket and object from path
    path_parts = gcs_object_path.replace('gs://', '').split('/', 1)
    bucket_name, object_name = path_parts[0], path_parts[1]
    blob = client.bucket(bucket_name).blob(object_name)

    from datetime import timedelta
    url = blob.generate_signed_url(expiration=timedelta(minutes=expiry_minutes), method='GET')
    return url
