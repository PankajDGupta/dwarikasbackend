"""
Google Document AI integration for structured invoice extraction.
"""
import os
import base64
from dataclasses import dataclass, field
from typing import List
from google.cloud import documentai_v1 as documentai

DOCUMENT_AI_PROJECT = os.environ.get('GCP_PROJECT_ID')
DOCUMENT_AI_LOCATION = os.environ.get('DOCUMENT_AI_LOCATION', 'us')
DOCUMENT_AI_PROCESSOR_ID = os.environ.get('DOCUMENT_AI_PROCESSOR_ID')

CONFIDENCE_THRESHOLD = 0.90  # Fields below this are flagged for human review


@dataclass
class ExtractedLineItem:
    sku: str = ''
    description: str = ''
    quantity: int = None
    unit_price: float = None
    gst_rate: float = None
    confidence_score: float = 1.0
    needs_review: bool = False


@dataclass
class ExtractedInvoice:
    invoice_number: str = ''
    vendor_name: str = ''
    vendor_gstin: str = ''
    issued_at: str = ''         # ISO date string, e.g. '2024-01-15'
    line_items: List[ExtractedLineItem] = field(default_factory=list)
    raw_confidence: float = 1.0


def get_mock_extracted_invoice(file_bytes: bytes, mime_type: str) -> ExtractedInvoice:
    """
    Returns a deterministic mock invoice for testing and local development fallback.
    """
    return ExtractedInvoice(
        invoice_number="INV-MOCK-12345",
        vendor_name="Mock Vendor Ltd",
        vendor_gstin="27MOCKA1234B1Z1",
        issued_at="2026-06-01",
        line_items=[
            ExtractedLineItem(
                sku="SKU-MOCK-1",
                description="Mock product item 1",
                quantity=10,
                unit_price=150.00,
                gst_rate=18.00,
                confidence_score=0.95,
                needs_review=False
            ),
            ExtractedLineItem(
                sku="SKU-MOCK-2",
                description="Mock product item 2 (low confidence)",
                quantity=5,
                unit_price=50.00,
                gst_rate=12.00,
                confidence_score=0.85,
                needs_review=True
            )
        ],
        raw_confidence=0.90
    )


def process_invoice_bytes(file_bytes: bytes, mime_type: str) -> ExtractedInvoice:
    """
    Submits raw file bytes to Document AI and returns a structured ExtractedInvoice.
    Falls back to mock parser if configuration variables are missing.
    """
    if not DOCUMENT_AI_PROJECT or not DOCUMENT_AI_PROCESSOR_ID:
        return get_mock_extracted_invoice(file_bytes, mime_type)

    client = documentai.DocumentProcessorServiceClient()
    processor_name = client.processor_path(
        DOCUMENT_AI_PROJECT, DOCUMENT_AI_LOCATION, DOCUMENT_AI_PROCESSOR_ID
    )

    raw_document = documentai.RawDocument(content=file_bytes, mime_type=mime_type)
    request = documentai.ProcessRequest(name=processor_name, raw_document=raw_document)
    result = client.process_document(request=request)
    document = result.document

    return _parse_document(document)


def _parse_document(document) -> ExtractedInvoice:
    """
    Maps Document AI entity responses to the ExtractedInvoice dataclass.
    Field names correspond to a trained Document AI Invoice Parser processor.
    """
    invoice = ExtractedInvoice()
    line_item_map: dict = {}   # Keyed by line item index

    for entity in document.entities:
        confidence = entity.confidence
        value = entity.mention_text.strip()

        # --- Header-level fields ---
        if entity.type_ == 'invoice_id':
            invoice.invoice_number = value
        elif entity.type_ == 'supplier_name':
            invoice.vendor_name = value
        elif entity.type_ == 'supplier_tax_id':
            invoice.vendor_gstin = value
        elif entity.type_ == 'invoice_date':
            invoice.issued_at = value

        # --- Line item fields (Document AI groups these as sub-entities) ---
        elif entity.type_ == 'line_item':
            idx = id(entity)    # Unique reference per entity object
            item = ExtractedLineItem()

            for prop in entity.properties:
                prop_confidence = prop.confidence
                prop_value = prop.mention_text.strip()
                item.confidence_score = min(item.confidence_score, prop_confidence)

                if prop.type_ == 'line_item/product_code':
                    item.sku = prop_value
                elif prop.type_ == 'line_item/description':
                    item.description = prop_value
                elif prop.type_ in ('line_item/quantity', 'line_item/unit'):
                    try:
                        item.quantity = int(float(prop_value))
                    except (ValueError, TypeError):
                        item.confidence_score = 0.0
                elif prop.type_ == 'line_item/unit_price':
                    try:
                        item.unit_price = float(prop_value.replace(',', ''))
                    except (ValueError, TypeError):
                        item.confidence_score = 0.0
                elif prop.type_ == 'line_item/tax_amount':
                    try:
                        item.gst_rate = float(prop_value.replace('%', ''))
                    except (ValueError, TypeError):
                        pass

            item.needs_review = item.confidence_score < CONFIDENCE_THRESHOLD
            line_item_map[idx] = item

    invoice.line_items = list(line_item_map.values())
    return invoice
