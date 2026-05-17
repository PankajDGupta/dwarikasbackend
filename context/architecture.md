# Architecture Context

The Dwarikas backend architecture separates high-concurrency transactional logic, external integration hooks, and automated document parsing layers into a stateless container ecosystem. Running on serverless Google Cloud Run infrastructure, it mediates communication between multiple client endpoints (Web, Mobile, POS) and the centralized Supabase database layer.


## Stack

| Component | Technology                                                                                               |
|-----------|----------------------------------------------------------------------------------------------------------|
| Backend | Django 6.0.5                                                                                             |
| API | Django REST Framework 3.17.1                                                                             |
| Database & Auth | Supabase (PostgreSQL) - Persistent cloud database with built-in real-time subscription pipelines         |
| Task Broker | Google Cloud Tasks - Asynchronous task scheduling and background worker execution queue                  |
| Object Vault | Google Cloud Storage (GCS) - Cloud object bucket hosting unstructured media assets and supplier invoices |
| Secret Custodian | Google Cloud Secret Manager - Dynamic decryption of system variables, keys, and API tokens               |
| Filtering | django-filter                                                                                            |
                                                                                  |

## System Boundaries

- dwarikasbackend/settings.py — Central system configuration repository, loading variables from Google Secret Manager and routing database connections.

- api/middleware/ — Custom request middleware validating incoming Supabase asymmetric JWTs locally against the project's JWKS endpoint.  

- api/views/ — Stateless controller routes processing web/mobile interactions, checkout holds, and POS transactions.

- inventory/models.py — Schema representations mapping products, live stock counts, and checkout holds directly to Supabase.  

- inventory/services/ — Execution packages handling barcode generations and formatting configurations for thermal print jobs.  

- tasks/ — Asynchronous endpoints invoked by Google Cloud Tasks to coordinate Document AI invoice parsing.

## Storage Model

- Database (Supabase PostgreSQL): Holds highly relational transaction-integrity data including inventory tables (SKUs), real-time cart reservations, active customer sessions, POS logs, and role metadata mapped from Supabase Auth.  

- Blob/File Storage (Google Cloud Storage): Stores persistent files like raw supplier bills, generated Code 128 or EAN-13 label vectors, and public product catalog imagery.

## Auth and Access Model

- Every user (customer, administrator, or warehouse staff member) registers or logs in via the respective client applications directly utilizing Supabase Auth to obtain an asymmetric JSON Web Token (JWT).

- For every protected endpoint transaction, client applications must append this JWT inside the standard authorization header (Authorization: Bearer <token>).

- Django’s middleware extracts and validates this JWT locally using Supabase's project JWKS endpoint to avoid slow, round-trip network queries.  

- Custom Django permissions determine resource mutations and read rights based on roles and claims embedded inside the locally decoded token.

## Invariants

- Request handlers do not run long-lived background work: All complex, blocking procedures like OCR invoice parsing and receipt compilations are instantly offloaded to asynchronous Google Cloud Tasks worker endpoints.
