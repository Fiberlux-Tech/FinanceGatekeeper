Finance Gatekeeper OS: Project Blueprint

1. Project Overview

A modular, high-security desktop application designed to automate the ingestion, financial validation, and archival of sales subscription data. It serves as a "Gatekeeper" between raw Excel inputs (SharePoint/OneDrive) and a structured Supabase (Cloud PostgreSQL) source of truth.

2. Operational Logic & Data Flow

Ingestion: Power Automate monitors Outlook and routes Excel attachments to BU-labeled subfolders in 01_Inbox.

Local Observation: The Desktop App monitors these local folders in real-time, generating UI "Cards" for each file.

Decision Gate: Finance users review cards, optionally open/edit source files via native link, refresh data to re-validate, and finally Approve or Reject.

The Transaction:

Approved: File is hashed (SHA-256), renamed (YYYYMMDDHHmmss_CLIENT.xlsx), encrypted, moved to Archive, and synced to Supabase via the background worker.

Rejected: File is moved to a rejection folder; a background service notifies the salesperson with specific reasons.

3. Folder Architecture (SharePoint Root)

01_Inbox/: Active landing zone for ESTADO, GIGALAN, CORPORATIVO, MAYORISTA.

02_Archive_Approved/[YYYY]/[BU]/: Locked golden records, password-protected.

03_Archive_Rejected/[BU]/: History of failed submissions for audit.

4. Modular Host Architecture (Extensibility)

The application is built as a Host Shell to support future growth (e.g., adding a "Collections" or "Budgeting" module later).

The Shell: Handles Authentication, Sidebar navigation, Background Sync Worker, and Local Persistence (SQLite).

The Modules: Plug-and-play views that share core services (Database Client, Encryption Engine, Logging).

5. Legacy Refactor & Integration

The system repurposes existing business logic from legacy files while modernizing the infrastructure:

Refactor Target: Strip Flask/SQLAlchemy dependencies from financial_engine.py and excel_parser.py.

Pattern Shift: Move to a Repository/Service pattern using supabase-py and local sqlite3.