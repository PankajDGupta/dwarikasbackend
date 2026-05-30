from django.apps import AppConfig


class InventoryConfig(AppConfig):
    """
    Django app config for the inventory module.

    Hosts all ORM model mirrors of the Supabase public schema:
    profiles, products, product_variants, reservations, orders.

    All models are unmanaged (managed = False) — Supabase owns the DDL.
    Django never creates or alters these tables.
    """
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'inventory'
