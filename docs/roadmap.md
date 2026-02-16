Gatekeeper OS Development Roadmap

Architecture: Relational, Offline-First Financial OS

We have moved from a "simple file processor" to a Relational, Offline-First Gatekeeper. The core data model uses a Header-Detail structure (transactions -> fixed_costs / recurring_services) linked by transaction_id. Tables are deployed in phases — identity first, then the relational transaction engine — to avoid "big bang" migrations.

---

Phase 1.5: Authentication Pipeline (COMPLETE)

[x] Auth Service: Login, registration, logout, password reset orchestration via typed AuthResult/ValidationResult models.

[x] Session Cache: AES-256-GCM encrypted offline session storage with PBKDF2-HMAC-SHA256 key derivation.

[x] JIT Provisioning: Automatic local profile sync on first login.

[x] Rate Limiting: Per-user lockout with HMAC-signed persistence to prevent file-level tampering.

[x] Login UI: Tabbed Sign In / Register / Request Access with real-time validation and lockout countdown.

[x] Security Hardening: PBKDF2 iterations raised to 600,000 (OWASP 2023), salt file permissions restricted on POSIX, control character rejection in name fields, offline email mismatch records failed attempts, best-effort password memory clearing.

---

Phase 2: Observer & Native Interaction (COMPLETE)

[x] Path Discovery: Dynamic resolution of the local SharePoint/OneDrive root via config override, user-stored path (SQLite app_settings), Windows Registry, and %OneDriveCommercial% fallback cascade.

[x] Path Configuration UI: Post-login inline view prompts users to browse and select their SharePoint folder when auto-detection fails. Persists to SQLite for future sessions. Settings sidebar module for reconfiguration.

[x] Watchdog Thread: Real-time monitoring of the flat 01_INBOX directory using watchdog on a daemon thread. Detects .xlsx creation, modification, and deletion with typed FileEvent dispatch. All files land in the inbox root — BU is determined from the Excel contents, not folder structure.

[x] Safety Guards: Steady State check (size-stabilisation polling for OneDrive/Power Automate sync), File Lock Detection (exclusive open test), temp-marker detection (~$file), and SHA-256 chain-of-custody hashing.

[x] Native Open: Launch Excel workbooks and containing folders via os.startfile (Windows) with xdg-open/open fallbacks for other platforms.

---

Phase 3: Parsing & Card UI (The "Filter") (COMPLETE)

[x] Excel Parser Refactor: Added `extract_metadata(path)` for lightweight header-only parsing (~50ms) and `process_local_file(path)` for full pipeline from filesystem paths. Optional `file_guards` dependency for lock/sync safety checks. Fixed pre-existing `_NUMERIC_FIELDS` key casing bug in `_extract_header_data()`.

[x] Card Engine: Master-detail split panel (`InboxCardView`) with scrollable `FileCard` list (left) and `DetailPanel` (right). Cards show client name, MRC, salesman, and date. Detail panel shows financial summary, chain of custody, metadata, and action buttons. `InboxScanService` orchestrates scanning with per-file error isolation. All I/O on worker threads with `self.after()` marshalling. Watchdog events debounced at 500ms per path.

[x] Hashing Engine: SHA-256 fingerprints computed via `FileGuardsService.compute_sha256()` during `InboxScanService.scan_inbox()` and `scan_single_file()`. Hash displayed in detail panel Chain of Custody section. Recomputed on every file refresh/modification event.

[x] Connectivity Dot: Global sync status indicator already implemented in Phase 2 `StatusBar` (Green=online, Yellow=pending sync, Red=offline). Per-card file status dots (Ready/Locked/Syncing) added in Phase 3 `FileCard` and `DetailPanel` components.

Phase 4: Relational Transaction Engine (Mid-Project)

This phase creates the "Big Three" relational tables. We wait until Phase 4 because the Service Layer must be finalized first so we know the exact mathematical precision required (BIGINT, NUMERIC types for financial calculations).

Table Creation (Two-Step Process):
1. SQL Migration Script: Clean .sql file with tables, foreign keys, and RLS. Run in Supabase SQL Editor.
2. Local Sync Schema: Python Initialization Service builds the identical structure in gatekeeper_local.db on first run.

Core Tables:
- transactions (Header): Client metadata, deal status, SHA-256 file hash, financial summaries (NPV, IRR, commissions), approval state. Includes `created_by UUID` FK to profiles for user-based RLS.
- fixed_costs (Detail): One-off implementation costs linked to transactions via transaction_id. Supports infinite scalability per deal.
- recurring_services (Detail): MRR/subscription data linked to transactions via transaction_id. Multi-currency pricing and provider tracking.
- audit_logs: Structured JSON history of every decision (User ID, Action, File Hash, Timestamp).
- master_variables: Append-only historical record of system-wide financial rates.

[x] Service Layer: Port the Math Engine (NPV/IRR/Commissions) into pure Python services. Implemented as stateless, type-safe functions in `app/services/financial_engine.py` (orchestrator), `app/utils/math_utils.py` (NPV/IRR), and `app/services/commission_rules.py` (business-unit rules). ESTADO and GIGALAN commission rules complete; CORPORATIVO and MAYORISTA return 0.0 pending Finance team rate tables. Actively consumed by TransactionCrudService, TransactionWorkflowService, TransactionPreviewService, and ExcelParserService.

[x] Relational Schema Deployment: Supabase migration 004 (`20250104000000_relational_transaction_engine.sql`) creates all 5 tables (transactions, fixed_costs, recurring_services, audit_logs, master_variables) with NUMERIC(18,6) precision for financial amounts, NUMERIC(10,6) for rates/ratios, JSONB for cached computations, CHECK constraints on currencies and approval_status, foreign keys with CASCADE deletes, 10 indexes, `updated_at` trigger, and 20 RLS policies (service_role full access, SALES sees own transactions via `created_by`, FINANCE/ADMIN sees all). Local SQLite schema bumped to v10 with `created_by TEXT` column added to transactions. Pydantic Transaction model and repository `_SQLITE_COLUMNS` updated to match.

[x] Approval Command: Full approve-archive pipeline implemented in `FileArchivalService.archive_approved()` and orchestrated by `TransactionWorkflowService.approve_transaction_with_archival()`. Sequence: file readiness check (lock/sync/temp marker) → SHA-256 chain-of-custody verification → filename rename (`{transaction_id}_{original}`) → Fernet AES-128-CBC encryption with DPAPI-protected key (Windows) → `shutil.move` to `02_ARCHIVE_APPROVED/{year}/{BU}/` → DB approval (RBAC, metrics recalculation, audit log, email notification). UI: green "Approve" button in DetailPanel Decision section, worker-thread execution with error dialog on failure, card removal on success.

[x] Rejection Command: Reject-archive pipeline in `FileArchivalService.archive_rejected()` orchestrated by `TransactionWorkflowService.reject_transaction_with_archival()`. Same safety guards and hash verification as approval but targets `03_ARCHIVE_REJECTED/{year}/{BU}/` and skips encryption. UI: red "Reject" button with `CTkInputDialog` for rejection note (mandatory), worker-thread execution, DB rejection with rejection_note persistence, email notification via `EmailService.send_status_update_email()`.

[x] Background Sync Worker: `SyncWorkerService` daemon thread drains `sync_queue` table to Supabase. Polls pending rows (batch of 50) with exponential backoff (30s base, 300s cap, 2^n scaling). Supports insert/update/upsert/replace operations across 6 allowed tables (transactions, fixed_costs, recurring_services, audit_log, master_variables, profiles). Failed rows retry up to 5 times before being marked `permanently_failed`. Started on login, stopped on logout/close. Thread-safe via `DatabaseManager.write_lock`.

Phase 5: Late-Stage — Dashboard, Audit UI & Auto-Update

These features are deferred to the final development phase. However, the database schema and service layer must be designed to support them from the start — capturing timeline fields (date_received, date_modified, date_processed), writing structured audit logs, and storing version metadata. No UI is built until this phase.

[ ] Timeline Dashboard: View "Date Received" vs. "Date Processed" metrics across the portfolio.

[ ] Audit Log Viewer: Centralized UI to browse and filter the structured JSON audit trail.

[ ] Auto-Update Check: Check cloud for app updates on startup and notify the user.
