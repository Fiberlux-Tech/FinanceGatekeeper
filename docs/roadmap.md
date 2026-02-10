Gatekeeper OS Development Roadmap

Architecture: Relational, Offline-First Financial OS

We have moved from a "simple file processor" to a Relational, Offline-First Gatekeeper. The core data model uses a Header-Detail structure (transactions -> fixed_costs / recurring_services) linked by transaction_id. Tables are deployed in phases — identity first, then the relational transaction engine — to avoid "big bang" migrations.

---

Phase 1: Identity & Access Foundation (Immediate)

[x] Supabase Auth Integration: Login screen + Session management.

[x] JIT Provisioning: Refactor legacy sync logic to map users to roles/modules.

[x] The Host Shell: Build the sidebar and "Module Switcher" to allow future expansion.

[x] Local Persistence: Initialize the SQLite schema for the Sync Queue and Audit Logs.

[x] Profiles Table: Create in Supabase (cloud) first, then mirror in local SQLite. Email-as-Username identity strategy with Full Name for display.

Phase 2: Observer & Native Interaction

[ ] Path Discovery: Implement dynamic resolution of the local SharePoint/OneDrive root.

[ ] Watchdog Thread: Set up real-time monitoring of the 01_Inbox directory.

[ ] Safety Guards: Implement Steady State check (wait for sync) and File Lock Detection (detect if Excel is open).

[ ] Native Open: Link cards to launch Microsoft Excel directly for manual edits.

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

[ ] Relational Schema Deployment: Create transactions, fixed_costs, recurring_services, and audit_logs tables in Supabase and SQLite.

[ ] Approval Command: Atomic sequence of Hash -> Rename -> Encrypt -> Move -> Local DB Write (header + all detail rows).

[ ] Rejection Command: Move -> Background Email Notification (Async Thread).

[ ] Background Sync Worker: Push local SQLite records (all three tables) to Supabase with retry logic.

Phase 5: Admin, Vault & Audit

[ ] Vault UI: Admin-only view to retrieve file encryption passwords from Supabase.

[ ] Timeline Dashboard: View "Date Received" vs. "Date Processed" metrics.

[ ] Structured Logging: Centralized view of the JSON audit trail.

[ ] Version Control: Check cloud for app updates on startup.