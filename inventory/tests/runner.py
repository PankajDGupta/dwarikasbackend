import importlib
from django.test.runner import DiscoverRunner

# Load migration dynamically since it starts with a number (invalid python identifier for direct import)
initial_migration = importlib.import_module('inventory.migrations.0001_initial')



class ManagedModelTestRunner(DiscoverRunner):
    """
    Test runner that dynamically converts 'managed = False' models and their
    migrations to 'managed = True' in-memory during tests, so that tables are
    created in the test database.
    """

    def setup_databases(self, **kwargs):
        # 1. Modify initial migration operations in-memory to set managed=True
        for op in initial_migration.Migration.operations:
            if hasattr(op, 'options') and 'managed' in op.options:
                op.options['managed'] = True


        # 2. Also set managed=True on actual Django model classes in-memory
        from django.apps import apps
        for model in apps.get_models():
            if model._meta.app_label == 'inventory':
                model._meta.managed = True

        return super().setup_databases(**kwargs)
