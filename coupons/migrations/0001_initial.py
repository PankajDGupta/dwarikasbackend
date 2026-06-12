import uuid
import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        ('inventory', '0006_reservation_promotions_fields'),
    ]

    operations = [
        migrations.CreateModel(
            name='Coupon',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('code', models.TextField(unique=True)),
                ('description', models.TextField(blank=True, null=True)),
                ('discount_type', models.TextField(choices=[('percentage', 'Percentage'), ('flat_amount', 'Flat Amount')])),
                ('discount_value', models.DecimalField(decimal_places=2, max_digits=10)),
                ('max_discount_cap', models.DecimalField(blank=True, decimal_places=2, max_digits=10, null=True)),
                ('min_order_value', models.DecimalField(blank=True, decimal_places=2, max_digits=10, null=True)),
                ('max_uses', models.IntegerField(blank=True, null=True)),
                ('uses_per_user', models.IntegerField(default=1)),
                ('specific_user_id', models.UUIDField(blank=True, null=True)),
                ('is_active', models.BooleanField(default=True)),
                ('valid_from', models.DateTimeField()),
                ('valid_until', models.DateTimeField(blank=True, null=True)),
                ('created_by', models.UUIDField()),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('source', models.TextField(choices=[('manual', 'Manual'), ('gaming_reward', 'Gaming Reward'), ('referral', 'Referral')], default='manual')),
            ],
            options={
                'db_table': 'coupons',
                'managed': False,
            },
        ),
        migrations.CreateModel(
            name='CouponRedemption',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('user_id', models.UUIDField()),
                ('discount_applied', models.DecimalField(decimal_places=2, max_digits=12)),
                ('redeemed_at', models.DateTimeField(auto_now_add=True)),
                ('coupon', models.ForeignKey(db_column='coupon_id', on_delete=django.db.models.deletion.CASCADE, related_name='redemptions', to='coupons.coupon')),
                ('order', models.ForeignKey(blank=True, db_column='order_id', null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='coupon_redemptions', to='inventory.order')),
                ('reservation', models.ForeignKey(blank=True, db_column='reservation_id', null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='coupon_redemptions', to='inventory.reservation')),
            ],
            options={
                'db_table': 'coupon_redemptions',
                'managed': False,
            },
        ),
    ]
