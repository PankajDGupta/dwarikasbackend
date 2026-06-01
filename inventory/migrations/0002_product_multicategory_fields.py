# Generated migration — Spec #06 (amended) — Multi-category product catalog extension
# Adds product_type, material, gender_target, fit_type to the Product model.
# Models are managed=False so this migration updates Django's state only.
# The matching Supabase DDL is in:
#   supabase/snippets/002_product_catalog_multicategory.sql

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('inventory', '0001_initial'),
    ]

    operations = [
        migrations.AddField(
            model_name='product',
            name='product_type',
            field=models.TextField(
                choices=[
                    ('grocery', 'Grocery & FMCG'),
                    ('apparel', 'Apparel & Clothing'),
                    ('general', 'General Merchandise'),
                ],
                default='general',
                help_text='Determines the product category (grocery, apparel, general).',
            ),
        ),
        migrations.AddField(
            model_name='product',
            name='material',
            field=models.TextField(
                blank=True,
                null=True,
                help_text="Fabric or material composition (e.g., '100% Cotton', 'Polyester Blend').",
            ),
        ),
        migrations.AddField(
            model_name='product',
            name='gender_target',
            field=models.TextField(
                choices=[
                    ('men', 'Men'),
                    ('women', 'Women'),
                    ('unisex', 'Unisex'),
                    ('boys', 'Boys'),
                    ('girls', 'Girls'),
                    ('none', 'Not Applicable'),
                ],
                default='none',
                help_text="Target gender for apparel items. Leave as 'none' for non-apparel.",
            ),
        ),
        migrations.AddField(
            model_name='product',
            name='fit_type',
            field=models.TextField(
                blank=True,
                null=True,
                help_text="Garment fit descriptor (e.g., 'Slim Fit', 'Regular Fit', 'Oversized').",
            ),
        ),
        # Update __str__ repr — no DB change needed, this is a state-only AlterModelOptions
        migrations.AlterField(
            model_name='product',
            name='dietary_type',
            field=models.TextField(
                choices=[
                    ('veg', 'Vegetarian'),
                    ('non-veg', 'Non-Vegetarian'),
                    ('egg', 'Contains Egg'),
                    ('none', 'Not Applicable'),
                ],
                default='none',
                help_text="Vegetarian / Non-Veg classification. Leave as 'none' for non-food items.",
            ),
        ),
    ]
