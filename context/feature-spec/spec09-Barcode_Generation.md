# Spec 09 — Barcode Generation Endpoints

## Goal
Implement REST endpoints that generate EAN-13 and Code 128 barcode images on demand from a SKU identifier. Images are returned as PNG binary streams (for direct browser rendering or ZPL label embedding). Staff and managers only.

---

## Scope
- `GET /api/v1/barcodes/<str:sku>/code128/` — generate a Code 128 barcode (internal batches)
- `GET /api/v1/barcodes/<str:sku>/ean13/` — generate an EAN-13 barcode (retail shelf labels)
- Response: `Content-Type: image/png` with raw PNG bytes
- Add `python-barcode[images]` and `Pillow` to dependencies

---

## Files to Create / Modify

### `inventory/barcode_service.py` — New file

```python
"""
Barcode generation service.
Encapsulates all barcode rendering logic; views are kept thin.
"""
from io import BytesIO
import barcode
from barcode.writer import ImageWriter


# ── Shared writer configuration ────────────────────────────────────────────────
_BASE_OPTIONS = {
    'module_height': 15.0,
    'module_width': 0.2,
    'quiet_zone': 2.0,
    'write_text': True,
    'text_distance': 4.0,
    'font_size': 8,
}


def _sanitize(value: str) -> str:
    """Strip non-alphanumeric characters to prevent injection into barcode payload."""
    return ''.join(c for c in value if c.isalnum() or c in ('-', '_'))


def generate_code128(sku: str, batch_id: str = '') -> BytesIO:
    """
    Generates a Code 128 barcode PNG buffer.
    Payload format: <SKU>-<BATCH_ID>  (batch_id optional).
    """
    safe_sku = _sanitize(sku)
    safe_batch = _sanitize(batch_id)
    payload = f"{safe_sku}-{safe_batch}" if safe_batch else safe_sku

    code128_cls = barcode.get_barcode_class('code128')
    buffer = BytesIO()
    code128_cls(payload, writer=ImageWriter()).write(buffer, options=_BASE_OPTIONS)
    buffer.seek(0)
    return buffer


def generate_ean13(sku: str) -> BytesIO:
    """
    Generates an EAN-13 barcode PNG buffer.
    EAN-13 requires exactly 12 numeric digits (the 13th is a check digit computed automatically).
    The SKU is zero-padded or truncated to 12 digits.
    Raises ValueError if the SKU cannot be coerced to a 12-digit numeric string.
    """
    # Extract numeric characters only
    numeric_sku = ''.join(c for c in sku if c.isdigit())
    if not numeric_sku:
        raise ValueError(f"SKU '{sku}' contains no numeric characters for EAN-13 encoding.")

    # Pad or truncate to exactly 12 digits
    ean_payload = numeric_sku[:12].zfill(12)

    ean13_cls = barcode.get_barcode_class('ean13')
    buffer = BytesIO()
    ean13_cls(ean_payload, writer=ImageWriter()).write(buffer, options=_BASE_OPTIONS)
    buffer.seek(0)
    return buffer
```

---

### `inventory/barcode_views.py` — New file

```python
from django.http import HttpResponse
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status

from api.permissions import IsStaffOrManager
from inventory.barcode_service import generate_code128, generate_ean13
from inventory.models import ProductVariant


class Code128BarcodeView(APIView):
    """
    GET /api/v1/barcodes/<str:sku>/code128/?batch_id=<optional>
    Returns a PNG image stream of a Code 128 barcode for internal inventory labelling.
    """
    permission_classes = [IsStaffOrManager]

    def get(self, request, sku):
        # Verify SKU exists in the database before generating
        if not ProductVariant.objects.filter(sku=sku).exists():
            return Response({'error': f"SKU '{sku}' not found."}, status=status.HTTP_404_NOT_FOUND)

        batch_id = request.query_params.get('batch_id', '')
        buffer = generate_code128(sku, batch_id=batch_id)

        response = HttpResponse(buffer.read(), content_type='image/png')
        response['Content-Disposition'] = f'inline; filename="{sku}-code128.png"'
        response['Cache-Control'] = 'no-store'   # Barcodes must always be freshly generated
        return response


class EAN13BarcodeView(APIView):
    """
    GET /api/v1/barcodes/<str:sku>/ean13/
    Returns a PNG image stream of an EAN-13 barcode for standard retail shelf labels.
    """
    permission_classes = [IsStaffOrManager]

    def get(self, request, sku):
        if not ProductVariant.objects.filter(sku=sku).exists():
            return Response({'error': f"SKU '{sku}' not found."}, status=status.HTTP_404_NOT_FOUND)

        try:
            buffer = generate_ean13(sku)
        except ValueError as exc:
            return Response({'error': str(exc)}, status=status.HTTP_422_UNPROCESSABLE_ENTITY)

        response = HttpResponse(buffer.read(), content_type='image/png')
        response['Content-Disposition'] = f'inline; filename="{sku}-ean13.png"'
        response['Cache-Control'] = 'no-store'
        return response
```

---

### `inventory/urls.py` — Add barcode routes

```python
from inventory.barcode_views import Code128BarcodeView, EAN13BarcodeView

urlpatterns += [
    path('barcodes/<str:sku>/code128/', Code128BarcodeView.as_view(), name='barcode-code128'),
    path('barcodes/<str:sku>/ean13/', EAN13BarcodeView.as_view(), name='barcode-ean13'),
]
```

---

### `pyproject.toml` — Add dependencies

```toml
"python-barcode[images]>=0.15.1",
"Pillow>=10.0.0",
```

---

## ZPL Integration Note

The Next.js client assembles the thermal printer ZPL command stream directly. The barcode endpoint provides the numeric payload only via the PNG; the ZPL `^BC` / `^BE` commands are constructed client-side using the SKU string returned in the product API (Spec 06). No ZPL is generated server-side.

---

## Acceptance Criteria

- [ ] `GET /api/v1/barcodes/SKU001/code128/` returns HTTP 200 with `Content-Type: image/png`
- [ ] Response body is a valid PNG file (verify with `Pillow.Image.open(BytesIO(response.content))`)
- [ ] Unauthenticated request returns 401
- [ ] Customer JWT returns 403
- [ ] Unknown SKU returns 404
- [ ] `GET /api/v1/barcodes/NONUMERIC/ean13/` returns 422 with descriptive error
- [ ] PNG dimensions are non-zero (image is not blank/corrupt)
- [ ] `generate_code128` and `generate_ean13` are unit-testable independently of the HTTP layer
