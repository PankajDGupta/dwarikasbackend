import importlib
from django.test.runner import DiscoverRunner

# Load migration dynamically since it starts with a number (invalid python identifier for direct import)
initial_migration = importlib.import_module('inventory.migrations.0001_initial')



class ManagedModelTestRunner(DiscoverRunner):
    """
    Test runner that dynamically converts 'managed = False' models and their
    migrations to 'managed = True' in-memory during tests, so that tables are
    created in the test database.

    Covers:
      - inventory/ app models (Spec #04 onwards)
      - payments/ app models (Spec #17 — PaymentTransaction)
    """

    # Apps whose unmanaged models should be promoted to managed during tests
    MANAGED_APPS = {'inventory', 'payments', 'pos', 'promotions', 'coupons'}

    def setup_databases(self, **kwargs):
        import os
        import importlib
        from django.conf import settings

        # ── 1. Promote unmanaged migration operations to managed=True ─────────
        for app_label in self.MANAGED_APPS:
            migrations_dir = os.path.join(settings.BASE_DIR, app_label, 'migrations')
            if os.path.exists(migrations_dir):
                for filename in os.listdir(migrations_dir):
                    if filename.endswith('.py') and not filename.startswith('__'):
                        migration_name = filename[:-3]
                        try:
                            mig = importlib.import_module(f'{app_label}.migrations.{migration_name}')
                            for op in mig.Migration.operations:
                                if hasattr(op, 'options') and 'managed' in op.options:
                                    op.options['managed'] = True
                        except Exception:
                            pass

        # ── 2. Promote unmanaged model classes to managed=True ────────────────
        from django.apps import apps
        for model in apps.get_models():
            if model._meta.app_label in self.MANAGED_APPS:
                model._meta.managed = True

        return super().setup_databases(**kwargs)
