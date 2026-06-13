# Dwarikas Backend

## Overview

The Dwarikas Backend is a unified, scalable Django REST Framework (DRF) backend deployed on Google Cloud Run, serving as the central orchestration and transaction-integrity hub for the Dwarikas omnichannel retail ecosystem. It is built specifically for retail administrators, floor staff, and digital customers shopping via web or mobile applications. The application solves the critical problems of inventory desynchronization across digital and physical storefronts ("ghost inventory") and heavy operational data entry overhead by unifying database operations into a real-time Supabase PostgreSQL engine, automating inventory intake through AI-powered document processing (OCR), and protecting concurrent transactions with robust atomic row-level locks.

## Goals

1. Achieve a sub-300ms API response time for customer checkout validations and in-store Point of Sale (POS) inventory checks.
2. Reduce manual warehouse inventory logging overhead by 85% by parsing uploaded vendor bills with a $>95\%$ line-item extraction accuracy using Google Cloud Tasks and Document AI. 
3. Prevent $100\%$ of concurrent overselling events ("ghost inventory sales") across digital and physical sales counters using atomic PostgreSQL transaction boundaries.  

## Core User Flow

1. Step One: User logs into the client application (Next.js web portal, mobile app, or Admin POS UI) and obtains an authenticated token from Supabase Auth.  

2. Step Two: Client applications make REST requests to Django endpoints, passing the JWT in the Authorization header, which the backend custom middleware decodes locally to verify user identity, role, and permissions.  

3. Step Three: During customer checkout, the client triggers a pre-purchase check. Django initiates a pessimistic row-level lock via select_for_update on the matching SKU rows in the database to calculate Available-To-Promise (ATP) stock.  

4. Step Four: Django creates a temporary 10-minute stock hold in the reservations table to secure the item, returning a success payload to the checkout client.  

5. Step Five: Once payment is confirmed, Django executes an atomic PostgreSQL transaction that decrements the product's physical stock, clears the reservation, and triggers a real-time webhook update back to all open customer and admin clients.  

6. Step Six: For inventory ingestion, warehouse staff upload a scanned bill through the Admin portal directly to a secure Google Cloud Storage (GCS) bucket, which triggers a Google Cloud Task event.

7. Step Seven: A serverless background worker pulls the GCS file, invokes Document AI to extract structured line items (product SKUs, descriptions, prices, quantities, and GST rates), and populates a validation queue.

8. Step Eight: Staff view the parsed data side-by-side with the original invoice image in the Human-in-the-Loop validation interface. Once they verify and submit the invoice, Django commits the new quantities directly to the Supabase database and updates catalog listings.

## Features

### Omnichannel API Gateway & Identity Isolation

Supabase Local JWT Validation: Custom Django security middleware validates asymmetric JWT signatures locally against Supabase JWKS endpoints. This eliminates latency-inducing API round trips, returning verification in under 5ms.  

Granular Role-Based Access Control (RBAC): Restricts staff dashboard features (barcode creation, price configurations, and raw file access) to authorized administrative accounts while providing public read-only access to catalogs.  

External Partner Integration Gateway: Secured endpoints for corporate ERP inventory sync and logistics provider updates. Authenticates requests using one-way SHA-256 hashed API keys and validates integrity using HMAC-SHA256 signatures over the payload. Includes admin endpoints allowing managers to securely list, generate, and revoke partner API keys via the Admin UI, displaying the raw key only once upon generation.

### High-Concurrency Transaction Guards
Atomic PostgreSQL Updates: Applies transactional logic (select_for_update database locks) to ensure multiple clients do not purchase the last unit of a given SKU at the same millisecond.  

Temporary Stock Holds: Manages automatic reservations that temporarily block stock from being sold in-store or online while a payment gateway transaction resolves.

### Intelligent Document Processing (IDP)
Serverless Ingestion Pipeline: Coordinates asynchronous Google Cloud Tasks and Document AI APIs to process bill images without blocking critical front-end application threads.

Tabular Line-Item Extraction: Dynamically parses complex, unstructured supplier invoices to extract precise data tables detailing quantities, raw unit costs, and HSN codes.  

### Physical Store Labeling & POS Operations
Dynamic Barcode Generator: Instantly renders EAN-13 and Code 128 barcode image payloads directly from database SKU numbers to prepare items for physical shelf tagging.

Multi-Mode POS Settlement Backend: Integrates with local merchant payment engines to handle split cash-and-card bills and dynamically calculate Central/State GST rates.  

### Decentralized Channel Integrations
ONDC Seller Node Core: Implements the required Beckn Protocol schemas to natively list inventory and coordinate fulfillment across the Open Network for Digital Commerce.  

WhatsApp Commerce Engine: Integrates with the WhatsApp Business API to allow conversational catalog search and real-time inventory queries directly over message threads.

Gaming Engine Integration: Connects with a first-party Unity mobile game using the same Supabase Auth JWT. The game client checks play quotas (earned via completed orders or ad watches) and records plays directly. Supports ad-watch monetization (rewarded ads) using Unity LevelPlay and client-side claims with a daily cap of 5 ad plays. Winners receive single-use reward coupons and optional WhatsApp notifications. Managers configure reward tiers that map win levels to coupon values.

### Promotions & Loyalty Engine
Time-Bound Promotional Discounts: Managers create promotions (percentage or flat-amount discounts) scoped to specific products or variants. Active promotions surface automatically on the customer storefront and are enforced server-side at checkout — the effective discounted price is locked into the reservation to prevent client-side manipulation.

Coupon Code System: Managers issue alphanumeric coupon codes with configurable usage caps, per-user limits, time windows, and user-specific targeting. Customers enter codes at checkout; the backend validates and applies the discount atomically using row-level locks to prevent race conditions.

WhatsApp Promotion Broadcast: Managers can push a live promotion as a rich CTA WhatsApp message to a list of opted-in customer phone numbers with one API call. Each send attempt is audited in a broadcast log.

### Smart Discount Suggestions Engine
Data-Driven Clearance Recommendations: A nightly background analytics job (dispatched via Google Cloud Scheduler → Cloud Tasks) scans all inventory using data already captured in the system — stock levels, order velocity, cart abandonment rates, and invoice cost-of-goods — to compute a composite discount score (0–100) for each variant. Variants scoring above the threshold are surfaced as prioritised suggestions on the manager's dashboard.

One-Click Promotion Activation: Managers review the suggestions queue (ranked by urgency: critical / high / medium) and can approve a suggestion with a single API call, which atomically creates a live Promotion record pre-configured with the AI-suggested discount percentage and duration. No manual form filling required.

### Amazon Marketplace Integration

One-Click Amazon Listing: Store administrators can list any product from Dwarika's catalog onto Amazon Marketplaces (Amazon.in) with a single click from the Admin UI. The system uses the synchronous Amazon SP-API Listings Items v2021-08-01 to provide real-time validation feedback (<2.5 seconds) and downstream asynchronous status tracking via Amazon SNS/SQS webhooks.

Real-Time Validation: The SP-API returns structured error codes immediately when a product attribute (EAN format, brand, etc.) is invalid. These are stored in `validation_issues` and surfaced in the Admin UI with corrected payload suggestions.

Event-Driven Status Tracking: Amazon fires `LISTINGS_ITEM_STATUS_CHANGE` notifications via SNS → SQS. The backend consumes these events to update listing state from `SUBMITTED` → `ACTIVE` or `SUPPRESSED`/`ERROR` in the `amazon_listings` table.

### Quick-Commerce Channel Integration (Blinkit & JioMart)

Unified One-Click Listing: Store administrators can list any catalog product on both **Blinkit** and **JioMart** hyperlocal quick-commerce platforms with a single click from the Admin UI. The middleware normalizes master product data into platform-specific schemas, runs pre-flight compliance validation, and routes payloads through two distinct ingestion pipelines.

JioMart Pipeline: Integrates with Reliance's Fynd 3P Aggregator Gateway (Fynd Konnect v3 REST API) for asynchronous batch catalog creation (up to 100 products/request), non-blocking `trace_id` polling, and field-level error surfacing.

Blinkit Pipeline: Implements a semantic EAN/UPC catalog matching workflow for immediate SKU linking when a product already exists on Blinkit, and a template compilation pipeline (CSV/Excel) routed to Blinkit Category Managers for new products.

Fulfillment Lifecycle Management: Manages downstream B2B Purchase Order (PO) processing for Blinkit (webhook receiver, MRP parity validation, Advanced Shipping Note generation) and JioMart marketplace-shipped order lifecycle (order fetch, manifest closure, return sync).

Operational Metrics: Tracks On-Time In-Full (OTIF) rate, Fill Rate (FR), and Inventory Discrepancy Margin (IDM) via automated data pipelines exposed through a Manager-only metrics API endpoint.


## Scope

### In Scope

- A Dockerized Python Django REST Framework application running as an independent stateless service on Google Cloud Run.

- Database migration tasks executed as isolated run-to-completion Cloud Run Jobs during automated CI/CD pipelines.

- Local JWT validation mechanics leveraging Supabase cryptographic public keys.  

- PostgreSQL transaction isolation, pessimistic lock patterns (select_for_update), and temporary checkout reservation states.  

- Google Cloud Tasks asynchronous integration for document processing, exception queue management, and nightly analytics jobs.

- Integration with Document AI OCR APIs for unstructured invoice parsing.

- Barcode generation endpoints exporting Code 128/EAN-13 standards.

- Compliance hooks for ONDC Seller Node APIs (Beckn Protocol v1.2.5).

- Promotions, coupon codes, and gaming engine coupon reward integration.

- Nightly analytics job for smart discount suggestion scoring using inventory, sales velocity, abandonment, and margin signals — with one-click manager activation.

- External Partner API Gateway supporting inventory sync and shipment status updates, along with Admin REST endpoints for API key lifecycle management (generation, listing, and revocation).

- Amazon Marketplace integration via Amazon SP-API (Listings Items v2021-08-01): one-click product listing from the Admin UI to Amazon.in, synchronous validation feedback, LWA OAuth 2.0 token management, and asynchronous listing status tracking via Amazon SNS/SQS webhooks.

- Quick-Commerce Channel Integration (`quickcommerce/` Django app): unified one-click product listing on Blinkit and JioMart; JioMart async batch ingestion via Fynd Konnect API with `trace_id` polling; Blinkit semantic EAN/UPC catalog matching and CSV template compilation pipeline; Blinkit B2B PO webhook receiver with MRP parity validation and ASN generation; JioMart order lifecycle management and return sync; hyperlocal warehouse/pincode routing maps; OTIF, Fill Rate, and IDM operational metrics.

### Out of Scope

- Development of the front-end user interfaces (the Next.js client web portal, the Admin POS UI, or the iOS/Android mobile client apps are managed by separate frontend teams).

- Client-side print drivers or physical hardware bluetooth pairings (the Next.js client applications communicate with POS thermal label printers directly using Web Bluetooth APIs).

- Hosting a self-managed database infrastructure (the system relies on Supabase for managed cloud PostgreSQL and Row Level Security storage policies).  

- Payment gateway merchant-side account settlements (restricted to standard webhook integrations with payment aggregators like Razorpay or PayU).

## Success Criteria

- Secured JWT Resolution: An authenticated REST call containing a valid Supabase token is validated locally by Django, resolved against database rows, and served back in under 100ms.

- Oversell Elimination: When 50 concurrent virtual threads attempt to check out the last remaining unit of an item, the database processes only 1 transaction successfully while rejecting the remaining 49 with an "Out of Stock" state, preventing any database inconsistencies.

- Hands-Free Ingestion Accuracy: Scanned bill PDFs uploaded by staff consistently extract vendor details, SKUs, and quantities with an accuracy rate exceeding 95%—flagging low-confidence OCR reads to the exception validation UI rather than writing unverified data.
