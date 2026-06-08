import os
import uuid
from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_UP

from django.db import transaction, DatabaseError, OperationalError
from django.db.models import F, Sum as DjSum
from rest_framework import status
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
        grand_subtotal = Decimal('0.00')
        grand_gst = Decimal('0.00')

        for item in cart.items.all():
            variant = item.variant
            product = variant.product

            # Live ATP check (non-locking — informational only)
            reserved = Reservation.objects.filter(
                variant_id=variant.id, status='active', expires_at__gt=now,
            ).aggregate(total=DjSum('reserved_quantity'))['total'] or 0
            atp = max(0, variant.stock_quantity - reserved)

            gst_rate = product.gst_slab / 100
            subtotal = item.unit_price * item.quantity
            gst_amount = (subtotal * gst_rate).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
            line_total = subtotal + gst_amount

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
        payment_method = request.data.get('payment_method', 'cash')

        if payment_method != 'cash':
            return Response({'error': 'Only cash payment is supported on this endpoint.'}, status=status.HTTP_400_BAD_REQUEST)

        try:
            cart = PosCart.objects.prefetch_related('items__variant__product').get(
                id=cart_id, staff_user_id=staff_user_id, status='open'
            )
        except PosCart.DoesNotExist:
            return Response({'error': 'Open cart not found.'}, status=status.HTTP_404_NOT_FOUND)

        if not cart.items.exists():
            return Response({'error': 'Cart is empty.'}, status=status.HTTP_400_BAD_REQUEST)

        if cash_tendered_raw is None:
            return Response({'error': 'cash_tendered is required.'}, status=status.HTTP_400_BAD_REQUEST)

        try:
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
                
                try:
                    locked_variants = {
                        v.id: v
                        for v in ProductVariant.objects.select_for_update(nowait=True).filter(id__in=variant_ids).order_by('id')
                    }
                except (OperationalError, DatabaseError) as e:
                    if 'could not obtain lock' in str(e).lower() or 'lock' in str(e).lower():
                        return Response(
                            {'error': 'Inventory is being updated. Please retry in a moment.'},
                            status=status.HTTP_503_SERVICE_UNAVAILABLE,
                        )
                    raise

                # ── Step 2: Verify ATP for all items ─────────────────────────────────
                now = datetime.now(timezone.utc)
                
                # Fetch all active reservations for these variants locked via select_for_update
                active_reservations = list(Reservation.objects.filter(
                    variant_id__in=variant_ids,
                    status='active',
                    expires_at__gt=now,
                ).select_for_update(nowait=True))

                reserved_by_variant = {}
                for res in active_reservations:
                    reserved_by_variant[res.variant_id] = reserved_by_variant.get(res.variant_id, 0) + res.reserved_quantity

                insufficient = []
                for item in items:
                    variant = locked_variants[item.variant_id]
                    reserved = reserved_by_variant.get(variant.id, 0)
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

        except (OperationalError, DatabaseError) as e:
            if 'could not obtain lock' in str(e).lower() or 'lock' in str(e).lower():
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
    DELETE /api/v1/pos/cart/<uuid:cart_id>/abandon/
    Abandons an open cart. Marks status as 'abandoned'.
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
    line_items = []
    for oi in order_items:
        # GST split: for intra-state sales, CGST + SGST = GST total
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

    store_name = os.environ.get('STORE_NAME', 'Dwarikas')
    store_address = os.environ.get('STORE_ADDRESS', '')
    store_gstin = os.environ.get('STORE_GSTIN', 'YOUR_GSTIN_HERE')

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
            'name': store_name,
            'address': store_address,
            'gstin': store_gstin,
        },
    }
    return bill
