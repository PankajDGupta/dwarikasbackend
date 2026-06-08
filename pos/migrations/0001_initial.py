import django.db.models.deletion
import uuid

from django.db import migrations, models


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        ('inventory', '0005_external_api_keys'),
    ]

    operations = [
        migrations.CreateModel(
            name='PosCart',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('staff_user_id', models.UUIDField()),
                ('customer_phone', models.TextField(blank=True, null=True)),
                ('status', models.TextField(
                    choices=[('open', 'Open'), ('confirmed', 'Confirmed'), ('abandoned', 'Abandoned')],
                    default='open',
                )),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
            ],
            options={
                'db_table': 'pos_carts',
                'managed': False,
            },
        ),
        migrations.CreateModel(
            name='PosCartItem',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('quantity', models.IntegerField()),
                ('unit_price', models.DecimalField(decimal_places=2, max_digits=12)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('cart', models.ForeignKey(
                    db_column='cart_id',
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='items',
                    to='pos.poscart',
                )),
                ('variant', models.ForeignKey(
                    db_column='variant_id',
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='pos_cart_items',
                    to='inventory.productvariant',
                )),
            ],
            options={
                'db_table': 'pos_cart_items',
                'managed': False,
                'unique_together': {('cart', 'variant')},
            },
        ),
        migrations.CreateModel(
            name='OrderItem',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('sku_snapshot', models.TextField()),
                ('product_name_snapshot', models.TextField()),
                ('hsn_code_snapshot', models.TextField()),
                ('gst_slab_snapshot', models.DecimalField(decimal_places=2, max_digits=5)),
                ('quantity', models.IntegerField()),
                ('unit_price', models.DecimalField(decimal_places=2, max_digits=12)),
                ('subtotal', models.DecimalField(decimal_places=2, max_digits=12)),
                ('gst_amount', models.DecimalField(decimal_places=2, max_digits=12)),
                ('line_total', models.DecimalField(decimal_places=2, max_digits=12)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('order', models.ForeignKey(
                    db_column='order_id',
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='items',
                    to='inventory.order',
                )),
                ('variant', models.ForeignKey(
                    db_column='variant_id',
                    null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    related_name='order_items',
                    to='inventory.productvariant',
                )),
            ],
            options={
                'db_table': 'order_items',
                'managed': False,
            },
        ),
    ]
