Gatekeeper OS Development Roadmap

Phase 1: Auth & Modular Skeleton

[ ] Supabase Auth Integration: Login screen + Session management.

[ ] JIT Provisioning: Refactor legacy sync logic to map users to roles/modules.

[ ] The Host Shell: Build the sidebar and "Module Switcher" to allow future expansion.

[ ] Local Persistence: Initialize the SQLite schema for the Sync Queue and Audit Logs.

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

Phase 4: Commands & Transaction Logic

[ ] Service Layer: Port the Math Engine (NPV/IRR/Commissions) into pure Python services.

[ ] Approval Command: Atomic sequence of Hash -> Rename -> Encrypt -> Move -> Local DB Write.

[ ] Rejection Command: Move -> Background Email Notification (Async Thread).

[ ] Background Sync Worker: Push local SQLite records to Supabase with retry logic.

Phase 5: Admin, Vault & Audit

[ ] Vault UI: Admin-only view to retrieve file encryption passwords from Supabase.

[ ] Timeline Dashboard: View "Date Received" vs. "Date Processed" metrics.

[ ] Structured Logging: Centralized view of the JSON audit trail.

[ ] Version Control: Check cloud for app updates on startup.