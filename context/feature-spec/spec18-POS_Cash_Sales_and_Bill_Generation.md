# Spec 18 — POS Cash Sales & In-Store Bill Generation

## Goal

Enable the physical store checkout flow where:
1. A floor staff member scans or selects multiple items for a walk-in customer
2. The customer pays cash at the counter
3. The backend records the sale atomically across all line items
4. The backend returns a structured bill payload that the frontend renders and sends to a thermal printer

This spec introduces three things that do not exist yet:
- A **POS Cart** (multi-item basket scoped to a POS session)
- **OrderItems** (line-item detail linked to an Order)
- A **Bill / Receipt endpoint** that returns an itemized, GST-compliant receipt payload

> **Relationship to existing specs:**
> - Spec 07 (Reservation): Individual item locking is reused inside the POS cart confirm flow
> - Spec 08 (Order Confirm): Remains valid only for single-item flows. POS multi-item cash sales use the new `POST /api/v1/pos/cart/confirm/` endpoint in this spec
> - Spec 09 (Barcode): Unchanged — staff still scan SKUs via the barcode endpoint to look up variant IDs
> - Spec 17 (Razorpay): Covers online payments only. Cash POS is entirely handled by this spec

---

## Scope

New Django app: `pos/`

### Endpoints

| Method | Path | Actor | Description |
|---|---|---|---|
| `POST` | `/api/v1/pos/cart/` | Staff / Manager | Create a new POS cart session |
| `POST` | `/api/v1/pos/cart/<uuid:cart_id>/items/` | Staff / Manager | Add a line item (SKU + quantity) to a cart |
| `DELETE` | `/api/v1/pos/cart/<uuid:cart_id>/items/<uuid:item_id>/` | Staff / Manager | Remove a line item from a cart |
| `GET` | `/api/v1/pos/cart/<uuid:cart_id>/` | Staff / Manager | View cart contents with ATP check and subtotals |
| `POST` | `/api/v1/pos/cart/<uuid:cart_id>/confirm/` | Staff / Manager | Commit the sale: lock stock atomically, create Order + OrderItems, return bill |
| `DELETE` | `/api/v1/pos/cart/<uuid:cart_id>/` | Staff / Manager | Abandon a cart (releases any held reservations) |
| `GET` | `/api/v1/pos/bill/<uuid:order_id>/` | Staff / Manager | Re-fetch the printable bill for any historical POS order |

---

## Why a Separate `pos/` App?

The online checkout (Specs 07–08–17) is a **two-phase** flow:
1. Customer reserves → timer runs → payment gateway confirms

The POS cash flow is a **single-phase** flow:
1. Staff builds cart → staff confirms cash received → stock decrements immediately → bill prints

These are fundamentally different transaction models. Mixing them into `inventory/` would make `order_views.py` too complex and harder to test. A dedicated `pos/` app keeps the concerns clean.

---

## Walk-In Customer Handling

Physical store customers often have **no Supabase account**. The POS cart does not require a customer UUID. The `PosCart` model stores an optional `customer_phone` (TEXT) for GST invoice purposes. If the customer provides a phone number, staff enters it; otherwise the sale is recorded as anonymous (`customer_phone = NULL`).

Staff JWT provides the authentication and authorization context. The `staff_user_id` (from the JWT `sub` claim) is recorded on every cart and order as the responsible operator.

---

## Files to Create / Modify

### `pos/` — New Django app

```bash
python manage.py startapp pos
```

Add to `INSTALLED_APPS`:
```python
'pos.apps.PosConfig',
```

---

### `pos/models.py`

```python
import uuid
from django.db import models


class PosCart(models.Model):
    """
    A POS session cart scoped to a single staff member's billing session.
    Represents the basket before confirmation. Abandoned carts are soft-deleted.
    """
    STATUS_CHOICES = [
        ('open', 'Open'),
        ('confirmed', 'Confirmed'),
        ('abandoned', 'Abandoned'),
    ]
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    staff_user_id = models.UUIDField()           # Supabase auth.users(id) of the staff member
    customer_phone = models.TextField(null=True, blank=True)   # Optional — for GST bill
    status = models.TextField(choices=STATUS_CHOICES, default='open')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        managed = False
        db_table = 'pos_carts'

    def __str__(self):
        return f"Cart {self.id} | {self.status}"


class PosCartItem(models.Model):
    """
    A single line item inside a PosCart.
    Each item corresponds to one ProductVariant and a requested quantity.
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    cart = models.ForeignKey(
        PosCart,
        on_delete=models.CASCADE,
        related_name='items',
        db_column='cart_id',
    )
    variant = models.ForeignKey(
        'inventory.ProductVariant',
        on_delete=models.CASCADE,
        related_name='pos_cart_items',
        db_column='variant_id',
    )
    quantity = models.IntegerField()
    unit_price = models.DecimalField(max_digits=12, decimal_places=2)  # Snapshot at time of add
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        managed = False
        db_table = 'pos_cart_items'
        unique_together = [('cart', 'variant')]   # One row per variant per cart


class OrderItem(models.Model):
    """
    Line-item detail for a confirmed Order. Links specific ProductVariants to an Order.
    This is the permanent record that allows bill reconstruction and GST reporting.
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    order = models.ForeignKey(
        'inventory.Order',
        on_delete=models.CASCADE,
        related_name='items',
        db_column='order_id',
    )
    variant = models.ForeignKey(
        'inventory.ProductVariant',
        on_delete=models.SET_NULL,
        null=True,
        related_name='order_items',
        db_column='variant_id',
    )
    sku_snapshot = models.TextField()               # Snapshot SKU — preserved even if variant deleted
    product_name_snapshot = models.TextField()      # Snapshot product name
    hsn_code_snapshot = models.TextField()          # Snapshot HSN for GST filing
    gst_slab_snapshot = models.DecimalField(max_digits=5, decimal_places=2)
    quantity = models.IntegerField()
    unit_price = models.DecimalField(max_digits=12, decimal_places=2)
    subtotal = models.DecimalField(max_digits=12, decimal_places=2)   # unit_price × quantity
    gst_amount = models.DecimalField(max_digits=12, decimal_places=2)  # subtotal × gst_slab/100
    line_total = models.DecimalField(max_digits=12, decimal_places=2)  # subtotal + gst_amount
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        managed = False
        db_table = 'order_items'
```

---

### `pos/serializers.py`

```python
from rest_framework import serializers
from pos.models import PosCart, PosCartItem, OrderItem
from inventory.serializers import ProductVariantSerializer


class PosCartItemSerializer(serializers.ModelSerializer):
    variant = ProductVariantSerializer(read_only=True)
    variant_id = serializers.UUIDField(write_only=True)

    class Meta:
        model = PosCartItem
        fields = ['id', 'variant', 'variant_id', 'quantity', 'unit_price', 'created_at']
        read_only_fields = ['id', 'unit_price', 'created_at']


class PosCartSerializer(serializers.ModelSerializer):
    items = PosCartItemSerializer(many=True, read_only=True)

    class Meta:
        model = PosCart
        fields = ['id', 'staff_user_id', 'customer_phone', 'status', 'items', 'created_at']
        read_only_fields = ['id', 'staff_user_id', 'status', 'created_at']


class OrderItemSerializer(serializers.ModelSerializer):
    class Meta:
        model = OrderItem
        fields = [
            'id', 'sku_snapshot', 'product_name_snapshot', 'hsn_code_snapshot',
            'gst_slab_snapshot', 'quantity', 'unit_price', 'subtotal', 'gst_amount', 'line_total',
        ]
        read_only_fields = fields
```

---

### `pos/views.py`

```python
import uuid
from datetime import datetime, timezone

from django.db import transaction, DatabaseError
from django.db.models import F
from rest_framework import status, generics
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from api.permissions import IsStaffOrManager
from inventory.models import ProductVariant, Reservation, Order
from pos.models import PosCart, PosCartItem, OrderItem
from pos.serializers import PosCartSerializer, PosCartItemSerializer, OrderItemSerializer


class PosCartCreateView(APIView):
    """
    POST /api/v1/pos/cart/
    Creates a new POS cart session. Staff only.

    Optional body:
    {
        "customer_phone": "9876543210"   // for GST bill
    }
    """
    permission_classes = [IsAuthenticated, IsStaffOrManager]

    def post(self, request):
        staff_user_id = uuid.UUID(request.user.username)
        customer_phone = request.data.get('customer_phone', None)

        cart = PosCart.objects.create(
            staff_user_id=staff_user_id,
            customer_phone=customer_phone,
            status='open',
        )
        return Response({
            'cart_id': str(cart.id),
            'status': 'open',
            'customer_phone': cart.customer_phone,
        }, status=status.HTTP_201_CREATED)


class PosCartItemAddView(APIView):
    """
    POST /api/v1/pos/cart/<uuid:cart_id>/items/
    Adds a variant to the cart (or updates quantity if already present).
    Does NOT lock stock yet — locking happens at confirm time.

    Expected body:
    {
        "variant_id": "<uuid>",
        "quantity": 2
    }
    """
    permission_classes = [IsAuthenticated, IsStaffOrManager]

    def post(self, request, cart_id):
        staff_user_id = uuid.UUID(request.user.username)

        try:
            cart = PosCart.objects.get(id=cart_id, staff_user_id=staff_user_id, status='open')
        except PosCart.DoesNotExist:
            return Response({'error': 'Open cart not found.'}, status=status.HTTP_404_NOT_FOUND)

        variant_id = request.data.get('variant_id')
        quantity = request.data.get('quantity', 1)

        if not variant_id:
            return Response({'error': 'variant_id is required.'}, status=status.HTTP_400_BAD_REQUEST)
        try:
            variant_id = uuid.UUID(str(variant_id))
            quantity = int(quantity)
            if quantity < 1:
                raise ValueError
        except (ValueError, AttributeError):
            return Response({'error': 'quantity must be a positive integer.'}, status=status.HTTP_400_BAD_REQUEST)

        try:
            variant = ProductVariant.objects.select_related('product').get(id=variant_id)
        except ProductVariant.DoesNotExist:
            return Response({'error': 'Variant not found.'}, status=status.HTTP_404_NOT_FOUND)

        # Upsert: if variant already in cart, update quantity; else create
        item, created = PosCartItem.objects.update_or_create(
            cart=cart,
            variant=variant,
            defaults={
                'quantity': quantity,
                'unit_price': variant.retail_price,
            },
        )

        return Response({
            'item_id': str(item.id),
            'variant_id': str(variant.id),
            'sku': variant.sku,
            'product_name': variant.product.name,
            'quantity': item.quantity,
            'unit_price': str(item.unit_price),
            'line_subtotal': str(item.unit_price * item.quantity),
        }, status=status.HTTP_201_CREATED if created else status.HTTP_200_OK)


class PosCartItemRemoveView(APIView):
    """
    DELETE /api/v1/pos/cart/<uuid:cart_id>/items/<uuid:item_id>/
    Removes a single line item from the cart.
    """
    permission_classes = [IsAuthenticated, IsStaffOrManager]

    def delete(self, request, cart_id, item_id):
        staff_user_id = uuid.UUID(request.user.username)

        try:
            cart = PosCart.objects.get(id=cart_id, staff_user_id=staff_user_id, status='open')
        except PosCart.DoesNotExist:
            return Response({'error': 'Open cart not found.'}, status=status.HTTP_404_NOT_FOUND)

        try:
            item = PosCartItem.objects.get(id=item_id, cart=cart)
        except PosCartItem.DoesNotExist:
            return Response({'error': 'Cart item not found.'}, status=status.HTTP_404_NOT_FOUND)

        item.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


class PosCartDetailView(APIView):
    """
    GET /api/v1/pos/cart/<uuid:cart_id>/
    Returns the current cart contents with live ATP stock check and GST-inclusive subtotals.
    Does not lock — purely informational before confirmation.
    """
    permission_classes = [IsAuthenticated, IsStaffOrManager]

    def get(self, request, cart_id):
        staff_user_id = uuid.UUID(request.user.username)

        try:
            cart = PosCart.objects.prefetch_related('items__variant__product').get(
                id=cart_id, staff_user_id=staff_user_id
            )
        except PosCart.DoesNotExist:
            return Response({'error': 'Cart not found.'}, status=status.HTTP_404_NOT_FOUND)

        now = datetime.now(timezone.utc)
        line_items = []
        grand_subtotal = 0
        grand_gst = 0

        for item in cart.items.all():
            variant = item.variant
            product = variant.product

            # Live ATP check (non-locking — informational only)
            from django.db.models import Sum as DjSum
            reserved = Reservation.objects.filter(
                variant_id=variant.id, status='active', expires_at__gt=now,
            ).aggregate(total=DjSum('reserved_quantity'))['total'] or 0
            atp = max(0, variant.stock_quantity - reserved)

            gst_rate = product.gst_slab / 100
            subtotal = item.unit_price * item.quantity
            gst_amount = round(subtotal * gst_rate, 2)
            line_total = round(subtotal + gst_amount, 2)

            grand_subtotal += subtotal
            grand_gst += gst_amount

            line_items.append({
                'item_id': str(item.id),
                'variant_id': str(variant.id),
                'sku': variant.sku,
                'product_name': product.name,
                'hsn_code': product.hsn_code,
                'gst_slab': str(product.gst_slab),
                'quantity': item.quantity,
                'unit_price': str(item.unit_price),
                'subtotal': str(subtotal),
                'gst_amount': str(gst_amount),
                'line_total': str(line_total),
                'atp_available': atp,
                'stock_ok': atp >= item.quantity,
            })

        return Response({
            'cart_id': str(cart.id),
            'status': cart.status,
            'customer_phone': cart.customer_phone,
            'items': line_items,
            'totals': {
                'subtotal': str(round(grand_subtotal, 2)),
                'total_gst': str(round(grand_gst, 2)),
                'grand_total': str(round(grand_subtotal + grand_gst, 2)),
            },
        })


class PosCartConfirmView(APIView):
    """
    POST /api/v1/pos/cart/<uuid:cart_id>/confirm/
    Commits the POS cash sale atomically:
    1. Acquires SELECT FOR UPDATE locks on every variant in the cart
    2. Verifies ATP for every line item
    3. Decrements stock for all items
    4. Creates Order + OrderItems
    5. Marks cart as confirmed
    6. Returns a fully itemized bill payload for thermal printing

    Expected body:
    {
        "cash_tendered": "500.00",   // Amount of cash given by customer (for change calculation)
        "payment_method": "cash"     // Always "cash" for this endpoint; validated server-side
    }
    """
    permission_classes = [IsAuthenticated, IsStaffOrManager]

    def post(self, request, cart_id):
        staff_user_id = uuid.UUID(request.user.username)
        cash_tendered_raw = request.data.get('cash_tendered')

        try:
            cart = PosCart.objects.prefetch_related('items__variant__product').get(
                id=cart_id, staff_user_id=staff_user_id, status='open'
            )
        except PosCart.DoesNotExist:
            return Response({'error': 'Open cart not found.'}, status=status.HTTP_404_NOT_FOUND)

        if not cart.items.exists():
            return Response({'error': 'Cart is empty.'}, status=status.HTTP_400_BAD_REQUEST)

        if not cash_tendered_raw:
            return Response({'error': 'cash_tendered is required.'}, status=status.HTTP_400_BAD_REQUEST)

        try:
            from decimal import Decimal
            cash_tendered = Decimal(str(cash_tendered_raw))
        except Exception:
            return Response({'error': 'cash_tendered must be a valid decimal number.'}, status=status.HTTP_400_BAD_REQUEST)

        try:
            with transaction.atomic():
                items = list(cart.items.select_related('variant__product').all())

                # ── Step 1: Acquire locks on all variants in a consistent order ──────
                # Lock variants in UUID order to prevent deadlocks when multiple
                # POS sessions bill for the same items concurrently.
                variant_ids = sorted([item.variant_id for item in items])
                locked_variants = {
                    v.id: v
                    for v in ProductVariant.objects.select_for_update(nowait=True).filter(id__in=variant_ids)
                }

                # ── Step 2: Verify ATP for all items ─────────────────────────────────
                now = datetime.now(timezone.utc)
                from django.db.models import Sum as DjSum
                insufficient = []

                for item in items:
                    variant = locked_variants[item.variant_id]
                    reserved = Reservation.objects.filter(
                        variant_id=variant.id, status='active', expires_at__gt=now
                    ).aggregate(total=DjSum('reserved_quantity'))['total'] or 0
                    atp = variant.stock_quantity - reserved

                    if atp < item.quantity:
                        insufficient.append({
                            'sku': variant.sku,
                            'requested': item.quantity,
                            'atp': max(0, atp),
                        })

                if insufficient:
                    return Response({
                        'error': 'Insufficient stock for one or more items.',
                        'insufficient_items': insufficient,
                    }, status=status.HTTP_409_CONFLICT)

                # ── Step 3: Compute bill totals ───────────────────────────────────────
                from decimal import Decimal, ROUND_HALF_UP
                line_item_records = []
                grand_subtotal = Decimal('0.00')
                grand_gst = Decimal('0.00')

                for item in items:
                    variant = locked_variants[item.variant_id]
                    product = variant.product
                    gst_rate = product.gst_slab / 100
                    subtotal = item.unit_price * item.quantity
                    gst_amount = (subtotal * gst_rate).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
                    line_total = subtotal + gst_amount

                    grand_subtotal += subtotal
                    grand_gst += gst_amount

                    line_item_records.append({
                        'item': item,
                        'variant': variant,
                        'product': product,
                        'subtotal': subtotal,
                        'gst_amount': gst_amount,
                        'line_total': line_total,
                    })

                grand_total = grand_subtotal + grand_gst
                change_due = cash_tendered - grand_total

                if change_due < 0:
                    return Response({
                        'error': 'Cash tendered is less than the total amount due.',
                        'grand_total': str(grand_total),
                        'cash_tendered': str(cash_tendered),
                        'shortfall': str(abs(change_due)),
                    }, status=status.HTTP_400_BAD_REQUEST)

                # ── Step 4: Decrement stock for all items ─────────────────────────────
                for rec in line_item_records:
                    ProductVariant.objects.filter(id=rec['variant'].id).update(
                        stock_quantity=F('stock_quantity') - rec['item'].quantity
                    )

                # ── Step 5: Create Order ──────────────────────────────────────────────
                order = Order.objects.create(
                    user_id=None,           # Walk-in customer — no Supabase account
                    total_amount=grand_total,
                    gst_amount=grand_gst,
                    payment_method='cash',
                    payment_status='completed',
                )

                # ── Step 6: Create OrderItems ─────────────────────────────────────────
                order_items_created = []
                for rec in line_item_records:
                    oi = OrderItem.objects.create(
                        order=order,
                        variant=rec['variant'],
                        sku_snapshot=rec['variant'].sku,
                        product_name_snapshot=rec['product'].name,
                        hsn_code_snapshot=rec['product'].hsn_code,
                        gst_slab_snapshot=rec['product'].gst_slab,
                        quantity=rec['item'].quantity,
                        unit_price=rec['item'].unit_price,
                        subtotal=rec['subtotal'],
                        gst_amount=rec['gst_amount'],
                        line_total=rec['line_total'],
                    )
                    order_items_created.append(oi)

                # ── Step 7: Mark cart confirmed ───────────────────────────────────────
                cart.status = 'confirmed'
                cart.save(update_fields=['status'])

        except Exception as e:
            if 'could not obtain lock' in str(e).lower():
                return Response(
                    {'error': 'Stock is being updated. Please retry in a moment.'},
                    status=status.HTTP_503_SERVICE_UNAVAILABLE,
                )
            return Response({'error': 'Transaction failed. Please retry.'}, status=status.HTTP_503_SERVICE_UNAVAILABLE)

        # ── Build printable bill payload ──────────────────────────────────────────
        bill = _build_bill_payload(
            order=order,
            order_items=order_items_created,
            cart=cart,
            cash_tendered=cash_tendered,
            change_due=change_due,
        )
        return Response(bill, status=status.HTTP_201_CREATED)


class PosCartAbandonView(APIView):
    """
    DELETE /api/v1/pos/cart/<uuid:cart_id>/
    Abandons an open cart. Does not release reservations (POS carts don't hold reservations).
    """
    permission_classes = [IsAuthenticated, IsStaffOrManager]

    def delete(self, request, cart_id):
        staff_user_id = uuid.UUID(request.user.username)

        try:
            cart = PosCart.objects.get(id=cart_id, staff_user_id=staff_user_id, status='open')
        except PosCart.DoesNotExist:
            return Response({'error': 'Open cart not found.'}, status=status.HTTP_404_NOT_FOUND)

        cart.status = 'abandoned'
        cart.save(update_fields=['status'])
        return Response(status=status.HTTP_204_NO_CONTENT)


class PosBillView(APIView):
    """
    GET /api/v1/pos/bill/<uuid:order_id>/
    Re-fetches the printable bill payload for any confirmed POS order.
    Used when the printer jams and staff need to reprint.
    """
    permission_classes = [IsAuthenticated, IsStaffOrManager]

    def get(self, request, order_id):
        try:
            order = Order.objects.get(id=order_id, payment_method='cash')
        except Order.DoesNotExist:
            return Response({'error': 'Cash order not found.'}, status=status.HTTP_404_NOT_FOUND)

        order_items = list(OrderItem.objects.filter(order=order))
        bill = _build_bill_payload(order=order, order_items=order_items, cart=None)
        return Response(bill)


# ── Helper: build the printable bill dict ─────────────────────────────────────

def _build_bill_payload(order, order_items, cart=None, cash_tendered=None, change_due=None) -> dict:
    """
    Constructs a GST-compliant bill payload. The frontend uses this to:
    1. Render a bill preview on the POS screen
    2. Construct the ZPL stream for thermal printer output
    """
    from decimal import Decimal
    line_items = []
    for oi in order_items:
        # GST split: for intra-state sales, CGST + SGST = GST total
        # For inter-state: IGST = GST total
        # POS assumption: intra-state — split equally
        cgst = (oi.gst_amount / 2).quantize(Decimal('0.01'))
        sgst = oi.gst_amount - cgst   # Handles odd-paise rounding

        line_items.append({
            'sku': oi.sku_snapshot,
            'product_name': oi.product_name_snapshot,
            'hsn_code': oi.hsn_code_snapshot,
            'gst_slab': str(oi.gst_slab_snapshot),
            'quantity': oi.quantity,
            'unit_price': str(oi.unit_price),
            'subtotal': str(oi.subtotal),
            'cgst': str(cgst),
            'sgst': str(sgst),
            'gst_total': str(oi.gst_amount),
            'line_total': str(oi.line_total),
        })

    # GST totals for the invoice footer
    total_cgst = sum(Decimal(li['cgst']) for li in line_items)
    total_sgst = sum(Decimal(li['sgst']) for li in line_items)

    bill = {
        'bill_type': 'POS_CASH',
        'order_id': str(order.id),
        'order_date': order.created_at.isoformat(),
        'customer_phone': cart.customer_phone if cart else None,
        'line_items': line_items,
        'totals': {
            'subtotal': str(order.total_amount - order.gst_amount),
            'total_cgst': str(total_cgst),
            'total_sgst': str(total_sgst),
            'total_gst': str(order.gst_amount),
            'grand_total': str(order.total_amount),
        },
        'payment': {
            'method': 'cash',
            'cash_tendered': str(cash_tendered) if cash_tendered is not None else None,
            'change_due': str(change_due) if change_due is not None else None,
        },
        'store': {
            'name': 'Dwarikas',
            'gstin': 'YOUR_GSTIN_HERE',   # Load from settings/Secret Manager
        },
    }
    return bill
```

---

### `pos/urls.py` — New file

```python
from django.urls import path
from pos.views import (
    PosCartCreateView,
    PosCartItemAddView,
    PosCartItemRemoveView,
    PosCartDetailView,
    PosCartConfirmView,
    PosCartAbandonView,
    PosBillView,
)

urlpatterns = [
    path('pos/cart/', PosCartCreateView.as_view(), name='pos-cart-create'),
    path('pos/cart/<uuid:cart_id>/', PosCartDetailView.as_view(), name='pos-cart-detail'),
    path('pos/cart/<uuid:cart_id>/items/', PosCartItemAddView.as_view(), name='pos-cart-item-add'),
    path('pos/cart/<uuid:cart_id>/items/<uuid:item_id>/', PosCartItemRemoveView.as_view(), name='pos-cart-item-remove'),
    path('pos/cart/<uuid:cart_id>/confirm/', PosCartConfirmView.as_view(), name='pos-cart-confirm'),
    path('pos/cart/<uuid:cart_id>/abandon/', PosCartAbandonView.as_view(), name='pos-cart-abandon'),
    path('pos/bill/<uuid:order_id>/', PosBillView.as_view(), name='pos-bill'),
]
```

---

### `dwarikasbackend/urls.py`

```python
path('api/v1/', include('pos.urls')),
```

---

### Supabase Migration — New tables

Create `supabase/snippets/003_pos_tables.sql`:

```sql
-- POS Cart session table
CREATE TABLE IF NOT EXISTS public.pos_carts (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    staff_user_id   UUID NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
    customer_phone  TEXT,
    status          TEXT NOT NULL DEFAULT 'open'
                    CHECK (status IN ('open', 'confirmed', 'abandoned')),
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    updated_at      TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_pos_cart_staff ON public.pos_carts(staff_user_id);
```

```sql
-- POS Cart line items
CREATE TABLE IF NOT EXISTS public.pos_cart_items (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    cart_id     UUID NOT NULL REFERENCES public.pos_carts(id) ON DELETE CASCADE,
    variant_id  UUID NOT NULL REFERENCES public.product_variants(id) ON DELETE CASCADE,
    quantity    INTEGER NOT NULL CHECK (quantity > 0),
    unit_price  NUMERIC(12, 2) NOT NULL,
    created_at  TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE (cart_id, variant_id)
);
```

```sql
-- Order line items (also used by online orders in future)
CREATE TABLE IF NOT EXISTS public.order_items (
    id                      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    order_id                UUID NOT NULL REFERENCES public.orders(id) ON DELETE CASCADE,
    variant_id              UUID REFERENCES public.product_variants(id) ON DELETE SET NULL,
    sku_snapshot            TEXT NOT NULL,
    product_name_snapshot   TEXT NOT NULL,
    hsn_code_snapshot       TEXT NOT NULL,
    gst_slab_snapshot       NUMERIC(5, 2) NOT NULL,
    quantity                INTEGER NOT NULL CHECK (quantity > 0),
    unit_price              NUMERIC(12, 2) NOT NULL,
    subtotal                NUMERIC(12, 2) NOT NULL,
    gst_amount              NUMERIC(12, 2) NOT NULL,
    line_total              NUMERIC(12, 2) NOT NULL,
    created_at              TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_order_items_order ON public.order_items(order_id);
```

```sql
-- Patch orders table: add 'online' payment method and 'refunded' status (from Spec 17)
ALTER TABLE public.orders
    DROP CONSTRAINT IF EXISTS orders_payment_method_check;

ALTER TABLE public.orders
    ADD CONSTRAINT orders_payment_method_check
    CHECK (payment_method IN ('UPI', 'card', 'cash', 'online'));

ALTER TABLE public.orders
    DROP CONSTRAINT IF EXISTS orders_payment_status_check;

ALTER TABLE public.orders
    ADD CONSTRAINT orders_payment_status_check
    CHECK (payment_status IN ('pending', 'completed', 'failed', 'refunded'));
```

---

### `inventory/models.py` — Add `OrderItem` FK reference (patch)

Update `Order.PAYMENT_METHOD_CHOICES` and `PAYMENT_STATUS_CHOICES` (same patch as Spec 17):

```python
PAYMENT_METHOD_CHOICES = [
    ('UPI', 'UPI'),
    ('card', 'Card'),
    ('cash', 'Cash'),
    ('online', 'Online'),   # Razorpay-managed UPI/card (Spec 17)
]
PAYMENT_STATUS_CHOICES = [
    ('pending', 'Pending'),
    ('completed', 'Completed'),
    ('failed', 'Failed'),
    ('refunded', 'Refunded'),  # Spec 17
]
```

---

## Full POS Cash Flow Sequence

```
POS Terminal (Next.js Admin UI)      Django Backend          Supabase DB
        |                                  |                      |
        |-- POST /pos/cart/ ------------>  |                      |
        |   { customer_phone: optional }   |                      |
        |<-- { cart_id } --------------    | INSERT pos_cart      |
        |                                  |                      |
        |-- POST /pos/cart/<id>/items/ --> |                      |
        |   { variant_id, quantity }       |                      |
        |<-- { line_subtotal, atp } -----  | INSERT pos_cart_item |
        |   (repeat for each scanned item) |                      |
        |                                  |                      |
        |-- GET /pos/cart/<id>/ -------->  |                      |
        |<-- { items, totals, atp_check }  |   (ATP live check)   |
        |                                  |                      |
        |  [Staff accepts cash from customer]                      |
        |                                  |                      |
        |-- POST /pos/cart/<id>/confirm/ > |                      |
        |   { cash_tendered: "500.00" }    |                      |
        |                           SELECT FOR UPDATE (all SKUs)  |
        |                           ATP verification (all items)  |
        |                           UPDATE stock_quantity -= qty  |
        |                           INSERT orders                 |
        |                           INSERT order_items (×N)       |
        |                           UPDATE pos_cart status        |
        |<-- { bill: { line_items,         |                      |
        |      totals, change_due } } ---- |                      |
        |                                  |                      |
        |  [Frontend renders bill on screen]
        |  [Frontend sends ZPL to thermal printer via Web Bluetooth/Serial]
```

---

## GST Split on POS Bills (Intra-State)

For physical store (intra-state) transactions, GST splits as:

```
CGST = GST Total / 2
SGST = GST Total - CGST   (absorbs odd-paise rounding)
```

For ONDC / inter-state orders (Spec 13): `IGST = GST Total`

Store GSTIN is loaded from environment: `STORE_GSTIN` (add to Google Secret Manager).

---

## Concurrency Safety

Unlike online checkout (Spec 07), the POS cart does **not pre-reserve stock**. This is intentional — POS is an in-store, immediate transaction. The locking happens at `confirm/` time:

- All variant rows are locked in UUID-sorted order (prevents deadlock when two cashiers bill the same SKUs simultaneously)
- ATP is verified under the lock
- Stock is decremented inside the same `transaction.atomic()` block

If a lock cannot be acquired (another POS terminal is confirming the same SKU at the same instant), the caller receives HTTP 503 with a retry instruction.

---

## Environment Variables Required

| Variable | Description |
|---|---|
| `STORE_GSTIN` | Store's GSTIN number for printed invoices (e.g., `27AAAAA0000A1Z5`) |
| `STORE_NAME` | Legal store name for bill header |
| `STORE_ADDRESS` | Store address for bill header |

---

## Acceptance Criteria

- [ ] `POST /api/v1/pos/cart/` by a customer JWT returns 403
- [ ] `POST /api/v1/pos/cart/` by a staff JWT returns 201 with a `cart_id`
- [ ] Adding an item to a cart twice updates quantity rather than creating a duplicate row
- [ ] `GET /api/v1/pos/cart/<id>/` returns live ATP status per line item
- [ ] `POST /api/v1/pos/cart/<id>/confirm/` with insufficient stock returns 409 listing all failing SKUs
- [ ] `POST /api/v1/pos/cart/<id>/confirm/` with `cash_tendered` less than grand total returns 400 with shortfall
- [ ] Successful confirm atomically creates `Order` + N `OrderItem` rows and decrements stock for all variants
- [ ] Bill payload includes `line_items` with `cgst`, `sgst`, `hsn_code` per line
- [ ] Bill payload includes `cash_tendered` and `change_due`
- [ ] Two POS terminals confirming the last unit simultaneously: exactly 1 succeeds (HTTP 201), the other gets 409 or 503
- [ ] `GET /api/v1/pos/bill/<order_id>/` re-fetches a complete bill for a past POS order
- [ ] `DELETE /api/v1/pos/cart/<id>/abandon/` marks cart as abandoned
- [ ] `OrderItem` rows preserve `sku_snapshot`, `product_name_snapshot`, `hsn_code_snapshot` — these survive even if the variant is later deleted
- [ ] `STORE_GSTIN` appears in the bill payload `store` section from environment config
