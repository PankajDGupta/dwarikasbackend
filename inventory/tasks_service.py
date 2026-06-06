"""
Google Cloud Tasks queue dispatcher.
"""
import json
import os
import logging
import threading
import uuid
import requests
from google.cloud import tasks_v2

logger = logging.getLogger(__name__)

CLOUD_TASKS_PROJECT = os.environ.get('GCP_PROJECT_ID')
CLOUD_TASKS_LOCATION = os.environ.get('GCP_TASKS_LOCATION', 'asia-south1')
CLOUD_TASKS_QUEUE = os.environ.get('GCP_TASKS_QUEUE', 'invoice-processing-queue')
WORKER_URL = os.environ.get('TASKS_WORKER_URL')   # Full URL of the Cloud Tasks worker endpoint

_client = None


def _get_client() -> tasks_v2.CloudTasksClient:
    global _client
    if _client is None:
        _client = tasks_v2.CloudTasksClient()
    return _client


def _local_worker_dispatch(url: str, payload: dict):
    """
    Simulates Google Cloud Tasks worker request locally by spawning an asynchronous HTTP POST call.
    """
    def run():
        try:
            logger.info(f"Local Tasks: Dispatching POST request to {url}")
            response = requests.post(url, json=payload, headers={'Content-Type': 'application/json'})
            logger.info(f"Local Tasks: Received response {response.status_code} from {url}")
        except Exception as e:
            logger.warning(f"Local Tasks: Failed to dispatch request to {url}: {e} (Expected in offline/test environments)")

    thread = threading.Thread(target=run)
    thread.daemon = True
    thread.start()


def enqueue_invoice_processing(invoice_id: str, gcs_object_path: str) -> str:
    """
    Pushes a lightweight metadata payload to the Cloud Tasks queue.
    If GCP_PROJECT_ID or other tasks environment variables are not set,
    spins up a local thread to post the payload directly to the simulated local worker endpoint.
    Returns the created task name (or a mock task name if local).
    """
    payload_dict = {
        'invoice_id': invoice_id,
        'gcs_object_path': gcs_object_path,
    }

    if not CLOUD_TASKS_PROJECT or not WORKER_URL:
        target_url = f"{WORKER_URL or 'http://127.0.0.1:8000'}/api/v1/tasks/process-invoice/"
        _local_worker_dispatch(target_url, payload_dict)
        return f"projects/local-project/locations/local-loc/queues/local-queue/tasks/{uuid.uuid4()}"

    client = _get_client()
    parent = client.queue_path(CLOUD_TASKS_PROJECT, CLOUD_TASKS_LOCATION, CLOUD_TASKS_QUEUE)

    payload = json.dumps(payload_dict).encode('utf-8')

    task = {
        'http_request': {
            'http_method': tasks_v2.HttpMethod.POST,
            'url': f"{WORKER_URL}/api/v1/tasks/process-invoice/",
            'headers': {'Content-Type': 'application/json'},
            'body': payload,
            # Cloud Tasks authenticates to Cloud Run using OIDC
            'oidc_token': {
                'service_account_email': os.environ.get('TASKS_SERVICE_ACCOUNT_EMAIL'),
            },
        }
    }

    created_task = client.create_task(request={'parent': parent, 'task': task})
    return created_task.name
