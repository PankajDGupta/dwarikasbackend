from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('inventory', '0006_reservation_promotions_fields'),
        ('coupons', '0001_initial'),
    ]

    operations = [
        migrations.AddField(
            model_name='reservation',
            name='coupon',
            field=models.ForeignKey(
                blank=True,
                db_column='coupon_id',
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='reservations',
                to='coupons.coupon',
            ),
        ),
        migrations.AddField(
            model_name='reservation',
            name='coupon_discount',
            field=models.DecimalField(blank=True, decimal_places=2, max_digits=12, null=True),
        ),
        migrations.AddField(
            model_name='reservation',
            name='final_price',
            field=models.DecimalField(blank=True, decimal_places=2, max_digits=12, null=True),
        ),
    ]
