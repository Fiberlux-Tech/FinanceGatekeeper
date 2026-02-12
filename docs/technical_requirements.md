Finance Gatekeeper OS: Technical Specifications & Standards

0. Production-Ready Standard

Every piece of work — whether a new feature, a bug fix, a refactor, or a schema change — MUST be delivered to a fully production-ready state before it is considered complete. This is a binding standard, not a suggestion.

Production-ready means:

- Complete: No placeholder logic, TODO comments, or partial implementations. Every code path is functional and reachable.
- Robust: All error conditions are handled explicitly. Network failures, invalid input, file locks, race conditions — each has a defined recovery path with a user-facing message.
- Type-safe: Full PEP 484 type annotations on all function signatures. Pydantic models validate data at every system boundary (UI input, Excel parsing, API responses, database reads).
- Integrated: New code is wired into the existing architecture. Services are registered, imports are connected, UI elements trigger real logic. Code that exists but is never called is not production-ready.
- Clean: No debug prints, no commented-out code, no dead imports, no leftover scaffolding from development.
- Verified: Every deliverable is confirmed to work — either through automated tests or documented manual verification steps — before it is marked complete.

If a task cannot be completed to this standard in a single pass, it must be decomposed into smaller sub-tasks, each of which independently meets the production-ready bar.

1. Technical Stack

Language: Python 3.10+ (Core logic and GUI).

GUI Framework: CustomTkinter (Modern, high-DPI desktop interface).

Cloud Database & Auth: Supabase (PostgreSQL via REST/Realtime).

Local Persistence: SQLite3 (Sync Queue & Local Cache).

Excel Engine: Pandas & Openpyxl (Strictly for parsing and math).

Security: PyCryptodome (Encryption) & Hashlib (SHA-256).

2. Core Architectural Patterns

A. The "Observer" Pattern

The application implements a background observer (using watchdog or a timed polling thread) that monitors the local 01_INBOX directory and updates the UI state immediately when files appear or disappear. The inbox is a flat directory — all incoming files land here regardless of business unit. BU is determined from a cell within the Excel file during parsing.

B. Offline-First Synchronization (Local Persistence)

To ensure zero interface lag, the application follows a Local-First approach:

Local Cache: All transactions (Approvals/Rejections) are first written to a local SQLite database with a sync_status = 'pending' flag.

Sync Worker: A background thread monitors connectivity and pushes pending records to Supabase.

State Recovery: On startup, the app checks the SQLite DB for unsynced data and attempts to resume ingestion.

C. Relational Deal Model (Header-Detail)

The core data model uses three tables linked by `transaction_id`:

1. `transactions` (Header): The parent record. Stores client metadata, overall deal status, SHA-256 file hash, financial summary fields (NPV, IRR, commissions, gross margin), and approval state.

2. `fixed_costs` (Detail): Child table for one-off implementation costs. Foreign key to `transactions.id`. Supports unlimited cost lines per deal. Fields include category, service type, location, quantity, unit cost with currency conversion (original + PEN).

3. `recurring_services` (Detail): Child table for MRR/subscription line items. Foreign key to `transactions.id`. Supports unlimited service lines per deal. Fields include service type, quantity, multi-currency pricing (original + PEN), and provider.

Atomicity Rule: When writing a deal, the header and ALL detail rows must be inserted within a single database transaction. Never create orphaned detail rows.

Precision Requirements: Financial fields use REAL in SQLite and NUMERIC/BIGINT in Supabase PostgreSQL to maintain professional-grade precision for NPV/IRR calculations.

D. Repository & Service Layer

Repository Pattern: UI layer is decoupled from data sources. All data access must go through a Repository layer (e.g., SalesRepository) which abstracts the source (SQLite, Excel, or Supabase).

Service Layer: Encapsulates complex financial rules (NPV/IRR/Commissions) into pure Python Service classes.

E. Schema Deployment Strategy (Two-Step Process)

Tables are not created ad-hoc. Each schema change follows a controlled process:

1. SQL Migration Script: A versioned `.sql` file defining tables, foreign keys, indexes, and Row Level Security (RLS) policies. Executed in the Supabase SQL Editor.

2. Local Sync Schema: The Python Initialization Service (`app/schema.py`) checks the local `gatekeeper_local.db` on startup. If the schema version is behind, it applies the new table definitions idempotently. The `schema_version` table tracks applied migrations.

F. User-Triggered Re-validation (Manual Refresh)

Each file card features a "Refresh" action allowing users to modify the Excel file locally (for metric compliance) and force a re-parse. This invalidates the old SHA-256 hash and re-runs the financial engine.

G. Command Pattern (Atomic Transactions)

Operations involving multiple steps (Approve/Reject) are encapsulated in Command objects. This ensures atomic execution (Hash -> Rename -> Encrypt -> Move to archive/{year}/{BU}/ -> DB Write for header + all detail rows) and provides a clear path for rollbacks.

H. SharePoint Folder Structure

The SharePoint document library (18_PLANTILLAS_GATEKEEPER) uses UPPERCASE folder names:

- 01_INBOX: Flat directory. All incoming Excel files land here regardless of business unit.
- 02_ARCHIVE_APPROVED/{year}/{BU}/: Approved files organised by year then BusinessUnit. Folders created on-demand.
- 03_ARCHIVE_REJECTED/{year}/{BU}/: Rejected files, same structure as approved.
- 04_TEMPLATES: Excel templates.
- 05_LOGS: Log files.

Business unit is NOT inferred from folder structure. It is extracted from a cell within the Excel file during parsing.

I. Identity Strategy (Email-as-Primary-Key)

Email Address serves as the Username across the system. Since email is the unique identifier in Supabase Auth, we mirror it in the `profiles` table to eliminate handle confusion and prevent identity collisions in the Audit Trail. Full Name is captured separately for display in UI cards and logs.

3. "Pro Move" Safety & Integrity

A. Steady State & File Lock Detection

Steady State: App waits for file release by the OS before ingestion to avoid reading partial syncs from OneDrive or Power Automate.

Handle Check: The app intercepts "Approve" commands if the file is still open in Excel, displaying a warning: "Please close the Excel file before finalizing approval."

B. Dynamic Path Resolution

The app resolves SharePoint/OneDrive roots dynamically (e.g., using os.path.expanduser) to ensure the local file paths are valid across different user machines (e.g., C:\Users\[username]\...).

C. Chain of Custody (SHA-256)

Every file ingested is assigned a unique digital fingerprint. This hash is stored in both SQLite and Supabase to prove that the archived file is identical to the one approved in the UI.

4. Coding Standards & Practices

A. Type Safety & Defensive Programming

Strict Type Hinting: Explicitly avoid using Any. All function signatures must have clearly defined input/output types.

Defensive Conversion: Avoid forced type casting (e.g., int(value)) without prior validation.

Pydantic Models: Use Pydantic for schema validation when data enters the system from external sources.

B. Separation of Concerns (Thin UI)

UI classes (ctk.CTkFrame) must contain zero business logic. They purely handle event triggers and layout.

Business logic belongs in the Service layer; data access belongs in the Repository layer.

C. Import & Dependency Strategy

Absolute Imports: All imports must be absolute (e.g., from app.services.auth import login).

Dependency Injection: Inject Repositories and Services into UI components via __init__ to ensure modularity.

D. Naming Convention Standardization (snake_case Gate)

The codebase bridges two environments with conflicting naming conventions:

Backend (Supabase/PostgreSQL): Uses snake_case (e.g., client_name, total_amount).

Frontend/External Sources (Excel/JSON): May provide data in camelCase (e.g., clientName) or PascalCase.

To eliminate "dictionary key drift" and maintain strict PEP 8 compliance across the Service Layer and Financial Engine, the following rules apply:

1. Repository-Level Transformation: All incoming data keys MUST be converted to snake_case at the Repository level (ExcelParser, SupabaseClient) during the ingestion phase. The UI and Logic layers must never encounter camelCase keys.

2. Centralized Utility: A pure utility function in app/utils/string_helpers.py handles all naming conversions. This is the single source of truth for key normalization.

3. Zero Manual Mapping: Manually mapping keys (e.g., data['clientName'] to data['client_name']) throughout the codebase is prohibited. All mapping must flow through the centralized converter to prevent typos and brittle code.

4. Outbound Conversion: When writing data back to external systems that require camelCase (e.g., JSON APIs), the Repository layer applies the reverse transformation on the way out.

5. Extensibility & Future-Proofing

Plug-and-Play Modules: The application shell uses a "Dynamic Loader" pattern. Adding a new module (e.g., "Collections") should only require adding a new class to a modules/ directory without modifying core logic.

Shared Services: Core services (Auth, Logging, Sync) are provided as shared resources available to any future module.

6. Performance & Error Handling

Startup Target: < 3 seconds to the Login screen.

File Ingestion: Parsing a standard Sales Excel should take < 500ms.

Non-Blocking UI: All I/O and heavy math must reside in worker threads.

State Recovery: Local SQLite cache ensures no data is lost even if the application is closed before a sync completes.

7. Structured Logging & Timeline

Audit Trail: Every transaction is logged as JSON, containing User ID, Action, File Hash, and Timestamp.

Timeline Tracking: Tracking the full lifecycle: date_received (Power Automate), date_modified (Refresh), and date_processed (Decision).