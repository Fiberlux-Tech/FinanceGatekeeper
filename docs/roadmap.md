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

Phase 1.5: Complete Authentication Pipeline

Phase 1 delivered the skeleton (login UI, session manager, JIT provisioning). Phase 1.5 closes every gap so that a real user can register, log in, recover a password, and log out — end-to-end — before we move on to business features.

1.5.1 — Registration (Request Access)

[x] Wire supabase.auth.sign_up(): Collect first name, last name, email, and password from the Request Access form. Pass full_name (first + last) and default role SALES as user_metadata so the handle_new_user trigger populates the profiles table automatically.

[x] Client-side validation: Email format regex (RFC 5322 simplified). Password policy: minimum 8 characters, at least 1 uppercase, 1 lowercase, 1 digit, 1 special character. Show inline errors per field before submitting.

[x] Post-signup UX: After successful sign_up, show a confirmation message: "Account created. Check your email to verify, then sign in." Switch the user back to the Sign In tab automatically.

[x] Duplicate email handling: Catch AuthError when the email already exists and show "An account with this email already exists. Try signing in."

[x] Loading state: Disable the Create Account button and show "Creating account..." while the request is in flight.

1.5.2 — Login Hardening

[x] Specific error messages: Catch gotrue.errors.AuthApiError and map error codes to human messages: invalid_credentials -> "Incorrect email or password.", user_not_found -> "No account found for this email.", user_banned -> "Your account has been deactivated. Contact your administrator.", email_not_confirmed -> "Please verify your email before signing in."

[x] Network vs auth errors: Distinguish connection failures (ConnectionError, TimeoutError) from authentication failures (AuthApiError). Show "Cannot reach the server. Check your internet connection." for network issues.

[x] Rate-limit guard: After 3 consecutive failed login attempts, disable the Sign In button for 30 seconds with a countdown. Prevents brute-force hammering.

[x] Email normalization: Apply .strip().lower() to email input before any API call. Prevents case-sensitivity mismatches.

1.5.3 — Offline Login Security

[x] Password hash storage: When an online login succeeds, derive a PBKDF2-HMAC-SHA256 hash of the password (with a random salt) and store it alongside the encrypted session cache. On offline login, verify the entered password against this hash before granting access. This closes the shared-computer vulnerability where offline login currently accepts any password for a cached email.

[x] Offline login audit: Log offline login events with a distinct event type (OFFLINE_LOGIN) in the structured logger so they are visible in the audit trail.

1.5.4 — Token Lifecycle

[x] Refresh failure -> forced logout: If token refresh fails with an auth error (refresh token expired or revoked), immediately clear the session and redirect to the login screen with the message "Your session has expired. Please sign in again." Only retry on transient network errors.

[x] Server-side logout: Call supabase.auth.sign_out() in _handle_logout before clearing local state. Wrap in try/except so offline logout still works.

[x] Logout audit event: Log a structured LOGOUT event with user email and timestamp.

1.5.5 — Password Reset

[x] "Forgot Password?" link: Add a clickable label below the Sign In button. When clicked, show an inline email input and a "Send Reset Link" button.

[x] Reset email dispatch: Call supabase.auth.reset_password_for_email(email) and show "If this email is registered, you will receive a password reset link."

[x] UX feedback: Disable the reset button while the request is in flight. Show success/error states.

1.5.6 — Email Confirmation Awareness

[x] Post-signup guard: After sign_up, if Supabase has email confirmation enabled, the user cannot sign in until they click the verification link. Detect the email_not_confirmed error on login and show "Please check your inbox and verify your email before signing in."

[x] Resend confirmation: Add a "Resend verification email" action that calls supabase.auth.resend(type="signup", email=email).

Done Criteria: A brand-new user can open the app, register via Request Access, receive a verification email, confirm, sign in, have their profile provisioned, reset their password if forgotten, log out (server-side revoked), and log in offline with password verification. All error paths show clear, human-readable messages.

---

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