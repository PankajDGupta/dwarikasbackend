# Spec 04 — Database Models & Schema

## Goal
Define Django ORM models that mirror the Supabase PostgreSQL schema. These models are **not** used to create Supabase tables (Supabase owns the schema); they are Django-managed mirrors that allow Django's ORM, `select_for_update`, and migration machinery to operate against the existing tables.

---

## Scope
- Create a new Django app: `inventory/`
- Define ORM models for all core tables
- Configure models as **unmanaged** (`managed = False`) so Django never creates or alters these tables — Supabase RLS policies own the schema
- Create the initial migration (`--fake` it against the existing Supabase database)
- Register the `inventory` app in `INSTALLED_APPS`

---

## Environment Strategy

Per Supabase's [Managing Environments guide](https://supabase.com/docs/guides/deployment/managing-environments), the project uses a **three-tier environment model**:

| Environment | Database | `DATABASE_URL` Source |
|---|---|---|
| **Local** | `supabase start` (Docker) — full Postgres + Auth at `localhost:54322` | `supabase status` output |
| **Staging** | Separate Supabase project linked via `supabase link --project-ref $STAGING_ID` | Supabase Dashboard → Settings → Database |
| **Production** | Main Supabase project | Injected at Cloud Run startup via Google Secret Manager |

### Local Development Pre-requisites
1. Install the [Supabase CLI](https://supabase.com/docs/guides/cli/getting-started)
2. Have Docker Desktop running
3. Run `supabase init` once in the project root (creates `supabase/` directory)
4. Run `supabase start` to spin up the local stack
5. Run `supabase status` to get the local `DATABASE_URL`, `SUPABASE_URL`, and `ANON_KEY`
6. Store these in a `.env.local` file (git-ignored):

```bash
DATABASE_URL=postgresql://postgres:postgres@127.0.0.1:54322/postgres
SUPABASE_JWT_SECRET=<local-jwt-secret-from-supabase-status>
```

> **Note:** The local Supabase stack runs a real PostgreSQL instance identical to production. All `manage.py` commands (check, makemigrations, migrate) run against this local DB during development.

### Schema Seeding (Local)
Because Django models are `managed = False`, Django never creates the tables. Before running migrations, seed the local database with the SQL schema defined in `context/system_design.md`:

```bash
# Option A: Apply via Supabase Studio UI at http://localhost:54323
# Option B: Apply via supabase db reset (if a seed migration file exists)
supabase db reset
```

---

## Files to Create / Modify

### `inventory/` — New Django app

Run:
```bash
python manage.py startapp inventory
```

### `inventory/models.py`

```python
import uuid
from django.db import models


class Profile(models.Model):
    """
    Mirror of public.profiles — stores RBAC role data linked to Supabase Auth users.
    The `id` field maps directly to auth.users(id) in Supabase.
    """
    ROLE_CHOICES = [
        ('customer', 'Customer'),
        ('staff', 'Staff'),
        ('manager', 'Manager'),
    ]
    id = models.UUIDField(primary_key=True)   # Maps to auth.users(id) — set by Supabase
    role = models.TextField(choices=ROLE_CHOICES, default='customer')
    phone_number = models.TextField(unique=True, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        managed = False          # Supabase owns DDL
        db_table = 'profiles'


class Product(models.Model):
    """
    Master product catalog table.
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.TextField()
    hsn_code = models.TextField()
    gst_slab = models.DecimalField(max_digits=5, decimal_places=2, default=18.00)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        managed = False
        db_table = 'products'

    def __str__(self):
        return f"{self.name} (HSN: {self.hsn_code})"


class ProductVariant(models.Model):
    """
    SKU-level variant table — holds physical stock quantities.
    This is the row targeted by select_for_update() during checkout locking.
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    product = models.ForeignKey(
        Product,
        on_delete=models.CASCADE,
        related_name='variants',
        db_column='product_id',
    )
    sku = models.TextField(unique=True)
    barcode = models.TextField(unique=True, null=True, blank=True)
    size = models.TextField(null=True, blank=True)
    color = models.TextField(null=True, blank=True)
    stock_quantity = models.IntegerField(default=0)
    retail_price = models.DecimalField(max_digits=12, decimal_places=2)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        managed = False
        db_table = 'product_variants'

    def __str__(self):
        return f"SKU: {self.sku} | Stock: {self.stock_quantity}"


class Reservation(models.Model):
    """
    Temporary stock holds created during the checkout flow.
    Expires after 10 minutes; expired rows are ignored in ATP calculations.
    """
    STATUS_CHOICES = [
        ('active', 'Active'),
        ('completed', 'Completed'),
        ('expired', 'Expired'),
    ]
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    variant = models.ForeignKey(
        ProductVariant,
        on_delete=models.CASCADE,
        related_name='reservations',
        db_column='variant_id',
    )
    user_id = models.UUIDField()              # Supabase auth.users(id) — not a FK in Django
    reserved_quantity = models.IntegerField()
    expires_at = models.DateTimeField()
    status = models.TextField(choices=STATUS_CHOICES, default='active')

    class Meta:
        managed = False
        db_table = 'reservations'


class Order(models.Model):
    """
    Completed order transaction log. Written atomically when checkout is confirmed.
    """
    PAYMENT_METHOD_CHOICES = [
        ('UPI', 'UPI'),
        ('card', 'Card'),
        ('cash', 'Cash'),
    ]
    PAYMENT_STATUS_CHOICES = [
        ('pending', 'Pending'),
        ('completed', 'Completed'),
        ('failed', 'Failed'),
    ]
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user_id = models.UUIDField(null=True, blank=True)   # Supabase auth.users(id)
    total_amount = models.DecimalField(max_digits=12, decimal_places=2)
    gst_amount = models.DecimalField(max_digits=12, decimal_places=2)
    payment_method = models.TextField(choices=PAYMENT_METHOD_CHOICES)
    payment_status = models.TextField(choices=PAYMENT_STATUS_CHOICES, default='pending')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        managed = False
        db_table = 'orders'
```

---

### `dwarikasbackend/settings.py` — Add inventory app

```python
INSTALLED_APPS = [
    ...
    'inventory.apps.InventoryConfig',
]
```

---

### Fake Initial Migration

Because `managed = False`, Django never runs DDL against any Supabase database. After adding models, create and fake the migration so Django's migration state is aligned without touching the tables:

```bash
# 1. Start local Supabase stack (Docker must be running)
supabase start

# 2. Seed the schema into the local database
#    Apply the SQL from context/system_design.md via Studio UI (http://localhost:54323)
#    or create a Supabase migration file and run:
supabase db reset

# 3. Set local DATABASE_URL from `supabase status`
export DATABASE_URL="postgresql://postgres:postgres@127.0.0.1:54322/postgres"

# 4. Create and fake the Django migration
python manage.py makemigrations inventory
python manage.py migrate --fake-initial
```

For **staging** and **production**, replace `DATABASE_URL` with the respective project's connection string. The `--fake-initial` flag is safe because all models have `managed = False` — Django only marks the migration as applied without executing any DDL.

---

## Acceptance Criteria

- [ ] `python manage.py check` passes with zero errors against the local Supabase stack
- [ ] `python manage.py makemigrations --check` reports no unapplied model changes
- [ ] All five models (`Profile`, `Product`, `ProductVariant`, `Reservation`, `Order`) import cleanly from `inventory.models`
- [ ] No `managed = True` model attempts to create or alter a Supabase table
- [ ] Running `python manage.py migrate --fake-initial` completes without errors against the **local** Supabase `DATABASE_URL` (and subsequently against the staging project before merging to `main`)
