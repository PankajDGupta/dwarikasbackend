from decimal import Decimal
from quickcommerce.models import QCPlatformListing

def normalize_to_jiomart_schema(listing: QCPlatformListing, fssai_license: str = None) -> dict:
    """
    Normalizes a product and variant to JioMart Fynd Konnect v3 payload schema.
    """
    product = listing.product
    variant = listing.variant

    # Construct clean tax rule identifier
    gst_pct = int(product.gst_slab)
    tax_rule_id = f"{product.hsn_code}-{gst_pct}pct"

    # Construct product name ensuring it leads with brand name if brand is present
    brand_prefix = f"{product.brand} " if product.brand and not product.name.lower().startswith(product.brand.lower()) else ""
    full_name = f"{brand_prefix}{product.name}"
    if variant and variant.size:
        full_name = f"{full_name} - {variant.size}"

    # Default sizes block
    sizes_list = []
    if variant:
        sizes_list.append({
            "size": variant.size or "1unit",
            "price": float(variant.retail_price),
            "quantity": variant.stock_quantity
        })

    item_payload = {
        "item_code": variant.sku if variant else f"DW-{product.hsn_code}",
        "name": full_name,
        "brand": product.brand or "Dwarikas",
        "category": product.category or "General",
        "mrp": float(variant.mrp) if variant and variant.mrp else float(variant.retail_price) if variant else 0.0,
        "selling_price": float(variant.retail_price) if variant else 0.0,
        "tax_identifier": {
            "tax_rule_id": tax_rule_id
        },
        "identifiers": [
            {
                "type": "EAN",
                "value": variant.barcode if variant else ""
            }
        ],
        "media": [
            {
                "url": product.image_url or ""
            }
        ] if product.image_url else [],
        "sizes": sizes_list
    }

    # FSSAI license attachment
    license_val = fssai_license or listing.fssai_license
    if license_val:
        item_payload["fssai_license"] = license_val

    return {"items": [item_payload]}


def normalize_to_blinkit_template(listing: QCPlatformListing, fssai_license: str = None) -> dict:
    """
    Compiles a new product catalog listing record into a Blinkit CSV/Excel template dictionary format.
    """
    product = listing.product
    variant = listing.variant

    brand_prefix = f"{product.brand} " if product.brand and not product.name.lower().startswith(product.brand.lower()) else ""
    full_name = f"{brand_prefix}{product.name}"
    if variant and variant.size:
        full_name = f"{full_name} - {variant.size}"

    return {
        "Product UPC": variant.barcode if variant else "",
        "Listing Reference Number": str(listing.submission_guid or ""),
        "brand": product.brand or "Dwarikas",
        "name": full_name,
        "category": product.category or "General",
        "MRP": float(variant.mrp) if variant and variant.mrp else float(variant.retail_price) if variant else 0.0,
        "selling_price": float(variant.retail_price) if variant else 0.0,
        "fssai_license": fssai_license or listing.fssai_license or "",
        "image_url": product.image_url or "",
        "hsn_code": product.hsn_code or "",
        "tax_slab": float(product.gst_slab)
    }
