import re
from decimal import Decimal
from inventory.models import Product, ProductVariant

def is_ean13_valid(barcode: str) -> bool:
    if not barcode or not isinstance(barcode, str) or len(barcode) != 13 or not barcode.isdigit():
        return False
    total = 0
    for i in range(12):
        digit = int(barcode[i])
        if i % 2 == 0:
            total += digit * 1
        else:
            total += digit * 3
    check_digit = (10 - (total % 10)) % 10
    return check_digit == int(barcode[12])


def validate_product_for_listing(product: Product, platform: str, fssai_license: str = None, extra_config: dict = None) -> list:
    """
    Validates a product and its variants for listing on the specified platform.
    Returns a list of dicts representing validation issues:
    [
        {
            "code": "ERROR_CODE",
            "message": "Error description",
            "attributeNames": ["attribute_name"]
        }
    ]
    """
    issues = []
    extra_config = extra_config or {}

    # Category analysis to detect food/beverage
    is_food_or_beverage = False
    if product.product_type == 'grocery':
        is_food_or_beverage = True
    else:
        category_lower = (product.category or "").lower()
        subcategory_lower = (product.subcategory or "").lower()
        name_lower = (product.name or "").lower()
        
        food_keywords = {"food", "beverage", "drink", "rice", "grain", "snack", "sweets", "grocery", "spice", "oil"}
        if any(kw in category_lower or kw in subcategory_lower or kw in name_lower for kw in food_keywords):
            is_food_or_beverage = True

    # 1. FSSAI License Check
    if is_food_or_beverage:
        # Check license provided in validation call or listing record
        license_to_check = fssai_license
        if not license_to_check:
            # Fallback to fssai_license in extra_config
            license_to_check = extra_config.get("fssai_license")

        if not license_to_check:
            issues.append({
                "code": "FSSAI_REQUIRED",
                "message": "FSSAI license is mandatory for food and beverage products.",
                "attributeNames": ["fssai_license"]
            })
        elif not isinstance(license_to_check, str) or not license_to_check.isdigit() or len(license_to_check) != 14:
            issues.append({
                "code": "FSSAI_INVALID",
                "message": "FSSAI license must be a valid 14-digit numeric string.",
                "attributeNames": ["fssai_license"]
            })

    # 2. Image check (presence of image URL)
    if not product.image_url:
        issues.append({
            "code": "IMAGE_MISSING",
            "message": "Product must have at least one valid image URL.",
            "attributeNames": ["image_url"]
        })

    # 3. Variants check (must have at least one variant)
    variants = product.variants.all()
    if not variants:
        issues.append({
            "code": "VARIANTS_MISSING",
            "message": "Product must have at least one variant configuration to list.",
            "attributeNames": ["variants"]
        })
        return issues

    for variant in variants:
        # 4. Barcode Validation
        if not variant.barcode:
            issues.append({
                "code": "BARCODE_MISSING",
                "message": f"Variant SKU '{variant.sku}' is missing a barcode.",
                "attributeNames": ["barcode"]
            })
        elif not is_ean13_valid(variant.barcode):
            issues.append({
                "code": "BARCODE_INVALID",
                "message": f"Variant SKU '{variant.sku}' barcode '{variant.barcode}' is not a valid EAN-13 barcode.",
                "attributeNames": ["barcode"]
            })

        # 5. Price Sanity Check (selling_price <= mrp)
        if variant.retail_price and variant.mrp:
            if Decimal(str(variant.retail_price)) > Decimal(str(variant.mrp)):
                issues.append({
                    "code": "PRICE_EXCEEDS_MRP",
                    "message": f"Variant SKU '{variant.sku}' retail price cannot exceed MRP.",
                    "attributeNames": ["retail_price", "mrp"]
                })

        # Platform-specific validation rules
        if platform == 'jiomart':
            # 6. JioMart MOQ Check (stock_quantity >= 500)
            if variant.stock_quantity < 500:
                issues.append({
                    "code": "MOQ_INSUFFICIENT",
                    "message": f"JioMart listings require an initial stock quantity of at least 500 units (SKU '{variant.sku}' has {variant.stock_quantity}).",
                    "attributeNames": ["stock_quantity"]
                })
            
            # 7. JioMart Shelf Life Check (>= 60% remaining)
            # Checked if shelf life percentage is supplied in extra_config
            shelf_life_pct = extra_config.get("shelf_life_pct", 100.0)
            if is_food_or_beverage and shelf_life_pct < 60.0:
                issues.append({
                    "code": "SHELF_LIFE_INSUFFICIENT",
                    "message": f"JioMart perishables must have at least 60% of total shelf life remaining (got {shelf_life_pct}%).",
                    "attributeNames": ["shelf_life"]
                })

        elif platform == 'blinkit':
            # 8. MRP Parity Check (Listing MRP must match Physical variant MRP)
            # The payload mrp is passed in extra_config
            payload_mrp = extra_config.get("mrp")
            if payload_mrp is not None:
                if Decimal(str(payload_mrp)) != Decimal(str(variant.mrp)):
                    issues.append({
                        "code": "MRP_MISMATCH",
                        "message": f"Blinkit listing MRP ({payload_mrp}) must exactly match the physical label MRP ({variant.mrp}).",
                        "attributeNames": ["mrp"]
                    })

    return issues
