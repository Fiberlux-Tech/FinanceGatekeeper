Finance Gatekeeper OS: Project Blueprint

1. Project Overview

A modular, high-security desktop application designed to automate the ingestion, financial validation, and archival of sales subscription data. It serves as a "Gatekeeper" between raw Excel inputs (SharePoint/OneDrive) and a structured Supabase (Cloud PostgreSQL) source of truth. The system uses a Relational, Offline-First architecture with a Header-Detail data model for scalable deal management.

2. Operational Logic & Data Flow

Ingestion: Power Automate monitors Outlook and routes Excel attachments to BU-labeled subfolders in 01_Inbox.

Local Observation: The Desktop App monitors these local folders in real-time, generating UI "Cards" for each file.

Decision Gate: Finance users review cards, optionally open/edit source files via native link, refresh data to re-validate, and finally Approve or Reject.

The Transaction:

Approved: File is hashed (SHA-256), renamed (YYYYMMDDHHmmss_CLIENT.xlsx), encrypted, moved to Archive. The transaction header and all detail rows (fixed_costs, recurring_services) are written atomically to local SQLite, then synced to Supabase via the background worker.

Rejected: File is moved to a rejection folder; a background service notifies the salesperson with specific reasons.

3. Relational Deal Model (Header-Detail)

The core data architecture uses a three-table relational structure to handle complex subscription deals:

Header — `transactions`: The parent record storing client metadata (name, company, salesman), deal status, SHA-256 file hash linking the DB record to the physical Excel file in SharePoint, and aggregated financial metrics (NPV, IRR, commissions, gross margin).

Detail — `fixed_costs`: Child table for one-off implementation costs. Each row captures category, service type, location, quantity, and unit cost with currency conversion. A deal can have 1 or 100 fixed cost lines — the database handles it natively via foreign key.

Detail — `recurring_services`: Child table for MRR/subscription line items. Each row captures service type, quantity, multi-currency pricing, and provider. Same infinite scalability as fixed_costs.

Linking: All three tables are linked by `transaction_id`. Detail rows MUST be inserted atomically with their parent header. This structure enables professional-grade NPV and IRR analysis across the entire portfolio.

4. Identity Strategy (Email-as-Primary-Key)

Email Address is the Username. Since email is the unique identifier in Supabase Auth, we mirror it in the `profiles` table to eliminate "handle" confusion and reduce identity collisions in the Audit Trail.

Full Name is captured for display in UI cards and logs.

5. Folder Architecture (SharePoint Root)

01_Inbox/: Active landing zone for ESTADO, GIGALAN, CORPORATIVO, MAYORISTA.

02_Archive_Approved/[YYYY]/[BU]/: Locked golden records, password-protected.

03_Archive_Rejected/[BU]/: History of failed submissions for audit.

6. Modular Host Architecture (Extensibility)

The application is built as a Host Shell to support future growth (e.g., adding a "Collections" or "Budgeting" module later).

The Shell: Handles Authentication, Sidebar navigation, Background Sync Worker, and Local Persistence (SQLite).

The Modules: Plug-and-play views that share core services (Database Client, Encryption Engine, Logging).

7. Legacy Refactor & Integration

The system repurposes existing business logic from legacy files while modernizing the infrastructure:

Refactor Target: Strip Flask/SQLAlchemy dependencies from financial_engine.py and excel_parser.py.

Pattern Shift: Move to a Repository/Service pattern using supabase-py and local sqlite3.