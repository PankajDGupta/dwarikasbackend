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
