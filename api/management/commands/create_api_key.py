"""
Management command: python manage.py create_api_key --partner-name "My ERP"
Generates a cryptographically secure API key, stores only its SHA-256 hash,
and prints the raw key ONCE to stdout (it cannot be retrieved later).
"""
import hashlib
import secrets
from django.core.management.base import BaseCommand
from inventory.models import ExternalApiKey


class Command(BaseCommand):
    help = 'Generate a new external partner API key.'

    def add_arguments(self, parser):
        parser.add_argument('--partner-name', required=True, type=str)

    def handle(self, *args, **options):
        partner_name = options['partner_name']
        raw_key = secrets.token_urlsafe(48)    # 48 bytes → 64-char URL-safe string
        key_hash = hashlib.sha256(raw_key.encode()).hexdigest()

        ExternalApiKey.objects.create(
            partner_name=partner_name,
            key_hash=key_hash,
            is_active=True
        )

        self.stdout.write(self.style.SUCCESS(
            f'\n[SUCCESS] API key created for partner: {partner_name}\n'
            f'[WARNING] Copy this key now - it will NOT be shown again:\n\n'
            f'   {raw_key}\n'
        ))
