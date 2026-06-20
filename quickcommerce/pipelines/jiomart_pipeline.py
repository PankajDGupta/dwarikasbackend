import os
import uuid
import logging
import requests
import datetime
import threading
import time
from django.utils import timezone
from django.conf import settings
from quickcommerce.models import QCPlatformListing, QCPlatformCredentials
from quickcommerce.schema_normalizer import normalize_to_jiomart_schema

logger = logging.getLogger(__name__)

def get_fynd_access_token() -> str:
    """
    Retrieves or refreshes the OAuth 2.0 access token for Fynd Konnect API.
    """
    cred, created = QCPlatformCredentials.objects.get_or_create(platform='jiomart')
    if cred.fynd_access_token and cred.fynd_token_expires_at and cred.fynd_token_expires_at > timezone.now():
        return cred.fynd_access_token

    # Simulate token fetch / refresh via Fynd Konnect credentials or environments
    # Fallback to environment variables
    username = os.environ.get('FYND_USERNAME', cred.fynd_username or 'dwarika_test')
    secret_token = os.environ.get('FYND_ACCESS_TOKEN', 'mock-fynd-token')

    # Update database credentials store
    cred.fynd_username = username
    cred.fynd_access_token = secret_token
    cred.fynd_token_expires_at = timezone.now() + datetime.timedelta(hours=1)
    cred.save()

    return secret_token


def submit_jiomart_batch(listings: list, fssai_license: str = None) -> dict:
    """
    Posts a batch of listings (up to 100) to Fynd Konnect REST API.
    Updates sync_status to SUBMITTED and trace_id in DB, then triggers polling.
    """
    if not listings:
        return {"success": False, "message": "No listings provided."}

    # Normalize to parent-child structure
    items_list = []
    for listing in listings:
        normalized = normalize_to_jiomart_schema(listing, fssai_license)
        items_list.extend(normalized["items"])

    batch_payload = {"items": items_list}

    # API Endpoint configuration
    base_url = os.environ.get('FYND_API_BASE_URL', 'https://fyndkonnect.konnect.uat.fyndx1.de')
    url = f"{base_url}/v3/catalog/product"

    access_token = get_fynd_access_token()
    headers = {
        "x-access-token": access_token,
        "Content-Type": "application/json"
    }

    trace_id = str(uuid.uuid4())
    status_state = "SUBMITTED"
    message_text = "Batch accepted by Fynd Konnect. Polling for COMPLETED status."

    # Call external gateway (with stub fallback for tests)
    is_testing = getattr(settings, 'IS_TESTING', False)
    if is_testing or base_url.startswith('mock://'):
        # Mock response in test context
        response_data = {
            "trace_id": trace_id,
            "status": "PROCESSING"
        }
        success = True
    else:
        try:
            res = requests.post(url, json=batch_payload, headers=headers, timeout=10)
            if res.status_code in [200, 202]:
                response_data = res.json()
                trace_id = response_data.get("trace_id", trace_id)
                success = True
            else:
                response_data = {"error": f"HTTP {res.status_code}: {res.text}"}
                status_state = "ERROR"
                message_text = f"Fynd Konnect batch submission failed: {res.text}"
                success = False
        except Exception as e:
            response_data = {"error": str(e)}
            status_state = "ERROR"
            message_text = f"Fynd Konnect connection failed: {str(e)}"
            success = False

    # Persist batch updates on listings
    for listing in listings:
        listing.trace_id = trace_id
        listing.sync_status = status_state
        if not success:
            listing.validation_issues = [response_data]
        listing.last_synced_at = timezone.now()
        listing.save()

    if success:
        # Trigger the non-blocking polling task
        enqueue_jiomart_polling(trace_id)

    return {
        "success": success,
        "trace_id": trace_id,
        "status": status_state,
        "message": message_text
    }


def enqueue_jiomart_polling(trace_id: str, retry_count: int = 0) -> str:
    """
    Enqueues a background task to poll JioMart batch status.
    If local/testing, runs in a separate thread after a small delay.
    """
    payload_dict = {
        'trace_id': trace_id,
        'retry_count': retry_count
    }

    is_testing = getattr(settings, 'IS_TESTING', False)
    cloud_project = os.environ.get('GCP_PROJECT_ID')
    worker_url = os.environ.get('TASKS_WORKER_URL')

    if is_testing or not cloud_project or not worker_url:
        if is_testing:
            return "projects/local/locations/local/queues/local/tasks/mock-poll-task"
        # Local thread-based dispatch
        def run():
            time.sleep(15.0)
            try:
                target_url = f"{worker_url or 'http://127.0.0.1:8000'}/api/v1/quickcommerce/tasks/poll-jiomart-status/"
                headers = {
                    'Content-Type': 'application/json',
                    'X-CloudTasks-TaskName': f'local-poll-{uuid.uuid4()}'
                }
                requests.post(target_url, json=payload_dict, headers=headers, timeout=5)
            except Exception as e:
                logger.warning(f"JioMart Local Poller failed: {e}")

        thread = threading.Thread(target=run)
        thread.daemon = True
        thread.start()
        return "projects/local/locations/local/queues/local/tasks/mock-poll-task"

    # Enqueue in real Cloud Tasks Queue
    from google.cloud import tasks_v2
    import json
    
    client = tasks_v2.CloudTasksClient()
    location = os.environ.get('GCP_TASKS_LOCATION', 'asia-south1')
    queue_name = os.environ.get('GCP_TASKS_QUEUE', 'invoice-processing-queue')
    parent = client.queue_path(cloud_project, location, queue_name)

    # Set scheduling time 15 seconds in the future
    schedule_time = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(seconds=15)
    
    task = {
        'http_request': {
            'http_method': tasks_v2.HttpMethod.POST,
            'url': f"{worker_url}/api/v1/quickcommerce/tasks/poll-jiomart-status/",
            'headers': {'Content-Type': 'application/json'},
            'body': json.dumps(payload_dict).encode('utf-8'),
            'oidc_token': {
                'service_account_email': os.environ.get('TASKS_SERVICE_ACCOUNT_EMAIL'),
            },
        },
        'schedule_time': schedule_time
    }

    created_task = client.create_task(request={'parent': parent, 'task': task})
    return created_task.name
