"""
Initial migration for payments app — PaymentTransaction model.

The model is marked managed=False in production (Supabase owns the DDL).
The ManagedModelTestRunner overrides managed=False to True during tests
so that Django creates the table in the test database.

Apply the Supabase table via:
    supabase/snippets/003_payment_transactions.sql
"""

import django.db.models.deletion
import uuid

from django.db import migrations, models


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        # inventory app models are needed for FK references
        ('inventory', '0005_external_api_keys'),
    ]

    operations = [
        migrations.CreateModel(
            name='PaymentTransaction',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('user_id', models.UUIDField()),
                ('razorpay_order_id', models.TextField(unique=True)),
                ('razorpay_payment_id', models.TextField(blank=True, null=True)),
                ('razorpay_signature', models.TextField(blank=True, null=True)),
                ('amount_paise', models.IntegerField()),
                ('currency', models.TextField(default='INR')),
                ('status', models.TextField(
                    choices=[
                        ('created', 'Created'),
                        ('attempted', 'Attempted'),
                        ('paid', 'Paid'),
                        ('failed', 'Failed'),
                        ('refunded', 'Refunded'),
                    ],
                    default='created',
                )),
                ('failure_reason', models.TextField(blank=True, null=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('reservation', models.ForeignKey(
                    db_column='reservation_id',
                    null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    related_name='payment_transactions',
                    to='inventory.reservation',
                )),
                ('order', models.OneToOneField(
                    blank=True,
                    db_column='order_id',
                    null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    related_name='payment_transaction',
                    to='inventory.order',
                )),
            ],
            options={
                'db_table': 'payment_transactions',
                'managed': False,
            },
        ),
    ]
