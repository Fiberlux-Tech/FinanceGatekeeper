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

Phase 3: Parsing & Card UI (The "Filter")

[ ] Excel Parser Refactor: Update parser to handle local file paths and extract metadata.

[ ] Card Engine: Build the scrollable Card UI featuring broad info and "Refresh" button.

[ ] Hashing Engine: Generate SHA-256 fingerprints for every file ingestion and update.

[ ] Connectivity Dot: Real-time UI indicator for Sync Status (Green/Yellow/Red).

Phase 4: Relational Transaction Engine (Mid-Project)

This phase creates the "Big Three" relational tables. We wait until Phase 4 because the Service Layer must be finalized first so we know the exact mathematical precision required (BIGINT, NUMERIC types for financial calculations).

Table Creation (Two-Step Process):
1. SQL Migration Script: Clean .sql file with tables, foreign keys, and RLS. Run in Supabase SQL Editor.
2. Local Sync Schema: Python Initialization Service builds the identical structure in gatekeeper_local.db on first run.

Core Tables:
- transactions (Header): Client metadata, deal status, SHA-256 file hash, financial summaries (NPV, IRR, commissions), approval state.
- fixed_costs (Detail): One-off implementation costs linked to transactions via transaction_id. Supports infinite scalability per deal.
- recurring_services (Detail): MRR/subscription data linked to transactions via transaction_id. Multi-currency pricing and provider tracking.
- audit_logs: Structured JSON history of every decision (User ID, Action, File Hash, Timestamp).

[ ] Service Layer: Port the Math Engine (NPV/IRR/Commissions) into pure Python services.

[ ] Relational Schema Deployment: Create transactions, fixed_costs, recurring_services, and audit_logs tables in Supabase and SQLite. IMPORTANT: The Supabase transactions CREATE TABLE must include the `file_sha256 TEXT` column (Chain of Custody, CLAUDE.md §5). The local SQLite schema and Pydantic model already have this field — the cloud migration must match.

[ ] Approval Command: Atomic sequence of Hash -> Rename -> Encrypt -> Move to 02_ARCHIVE_APPROVED/{year}/{BU}/ -> Local DB Write (header + all detail rows).

[ ] Rejection Command: Move to 03_ARCHIVE_REJECTED/{year}/{BU}/ -> Background Email Notification (Async Thread).

[ ] Background Sync Worker: Push local SQLite records (all three tables) to Supabase with retry logic.

Phase 5: Late-Stage — Dashboard, Audit UI & Auto-Update

These features are deferred to the final development phase. However, the database schema and service layer must be designed to support them from the start — capturing timeline fields (date_received, date_modified, date_processed), writing structured audit logs, and storing version metadata. No UI is built until this phase.

[ ] Timeline Dashboard: View "Date Received" vs. "Date Processed" metrics across the portfolio.

[ ] Audit Log Viewer: Centralized UI to browse and filter the structured JSON audit trail.

[ ] Auto-Update Check: Check cloud for app updates on startup and notify the user.
