"""
Rule-based intent classification for incoming WhatsApp messages.
Maps message text → one of the supported intents.
"""

INTENT_CATALOG = 'catalog'
INTENT_STOCK_CHECK = 'stock_check'
INTENT_ORDER_STATUS = 'order_status'
INTENT_FALLBACK = 'fallback'

# Keyword trigger sets — extend as needed
CATALOG_KEYWORDS = {'catalog', 'products', 'items', 'browse', 'show', 'list'}
STOCK_KEYWORDS = {'stock', 'available', 'availability', 'check stock', 'in stock'}
ORDER_KEYWORDS = {'order', 'status', 'track', 'my order', 'tracking'}


def classify_intent(message_text: str) -> str:
    """
    Returns the intent label for the given raw message text.
    Matching is case-insensitive; keyword presence wins over order.
    """
    text = message_text.lower().strip()

    if any(kw in text for kw in ORDER_KEYWORDS):
        return INTENT_ORDER_STATUS
    if any(kw in text for kw in STOCK_KEYWORDS):
        return INTENT_STOCK_CHECK
    if any(kw in text for kw in CATALOG_KEYWORDS):
        return INTENT_CATALOG
    return INTENT_FALLBACK


def extract_sku_from_message(text: str) -> str | None:
    """
    Attempts to extract a SKU from a message like 'check stock SKU-001'.
    Returns the SKU string or None.
    """
    IGNORE_WORDS = {
        'CHECK', 'STOCK', 'CATALOG', 'PRODUCTS', 'ITEMS', 'BROWSE', 'SHOW', 'LIST',
        'ORDER', 'STATUS', 'TRACK', 'MY', 'TRACKING', 'IN', 'AVAILABLE', 'AVAILABILITY',
        'IS', 'GET', 'FOR', 'PLEASE', 'THE', 'CODE', 'SKU', 'HELLO', 'HI', 'HEY'
    }
    # Clean the input text, removing punctuation that might cling to tokens
    cleaned_text = "".join(c if c.isalnum() or c.isspace() or c == '-' else " " for c in text)
    parts = cleaned_text.upper().split()
    # Look for tokens that match an alphanumeric SKU-like pattern and are not in ignore list
    for part in parts:
        if part in IGNORE_WORDS:
            continue
        if len(part) >= 3 and (part.isalnum() or '-' in part):
            return part
    return None

