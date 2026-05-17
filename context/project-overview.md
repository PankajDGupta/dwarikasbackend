# [Project Name]

## Overview

The Dwarikas Central API Engine is a unified, scalable Django REST Framework (DRF) backend deployed on Google Cloud Run, serving as the central orchestration and transaction-integrity hub for the Dwarikas omnichannel retail ecosystem. It is built specifically for retail administrators, floor staff, and digital customers shopping via web or mobile applications. The application solves the critical problems of inventory desynchronization across digital and physical storefronts ("ghost inventory") and heavy operational data entry overhead by unifying database operations into a real-time Supabase PostgreSQL engine, automating inventory intake through AI-powered document processing (OCR), and protecting concurrent transactions with robust atomic row-level locks.

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

## Scope

### In Scope

- A Dockerized Python Django REST Framework application running as an independent stateless service on Google Cloud Run.

- Database migration tasks executed as isolated run-to-completion Cloud Run Jobs during automated CI/CD pipelines.

- Local JWT validation mechanics leveraging Supabase cryptographic public keys.  

- PostgreSQL transaction isolation, pessimistic lock patterns (select_for_update), and temporary checkout reservation states.  

- Google Cloud Tasks asynchronous integration for document processing and exception queue management.

- Integration with Document AI OCR APIs for unstructured invoice parsing.

- Barcode generation endpoints exporting Code 128/EAN-13 standards.

- Compliance hooks for ONDC Seller Node APIs (Beckn Protocol v1.2.5).

### Out of Scope

- Development of the front-end user interfaces (the Next.js client web portal, the Admin POS UI, or the iOS/Android mobile client apps are managed by separate frontend teams).

- Client-side print drivers or physical hardware bluetooth pairings (the Next.js client applications communicate with POS thermal label printers directly using Web Bluetooth APIs).

- Hosting a self-managed database infrastructure (the system relies on Supabase for managed cloud PostgreSQL and Row Level Security storage policies).  

- Payment gateway merchant-side account settlements (restricted to standard webhook integrations with payment aggregators like Razorpay or PayU).

## Success Criteria

- Secured JWT Resolution: An authenticated REST call containing a valid Supabase token is validated locally by Django, resolved against database rows, and served back in under 100ms.

- Oversell Elimination: When 50 concurrent virtual threads attempt to check out the last remaining unit of an item, the database processes only 1 transaction successfully while rejecting the remaining 49 with an "Out of Stock" state, preventing any database inconsistencies.

- Hands-Free Ingestion Accuracy: Scanned bill PDFs uploaded by staff consistently extract vendor details, SKUs, and quantities with an accuracy rate exceeding 95%—flagging low-confidence OCR reads to the exception validation UI rather than writing unverified data.
