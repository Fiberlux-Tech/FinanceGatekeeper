# Claude AI Agent: S-Tier Coding Standards

## 1. Core Philosophy

Architecture > Speed: Never prioritize a quick fix over the established architectural patterns (Repository, Service, Command).

Zero Technical Debt: If a feature request exposes a flaw in the current codebase, you MUST propose a refactor BEFORE implementing the feature.

Professionalism: You are building an industrial-grade Financial Operating System. Code must be clean, modular, and extensively type-hinted.

## 2. Technical Commandments

Pythonic Type Safety: Strictly follow PEP 484. Use typing and pydantic. NEVER use Any. If a type is uncertain, use Union or Protocol.

The "Thin UI" Rule: No logic in CustomTkinter views. Views only trigger Commands or call Services.

Offline-First Thinking: Always assume the internet is down. Write to SQLite first, sync to Supabase in the background.

Defensive File Handling: Always wrap file operations in "Steady State" checks. Handle OS-level file locks (PermissionError) gracefully with user-friendly warnings.

## 3. Implementation Workflow

Conceptual Review: Discuss the architectural impact of a change before writing code.

Schema Validation: Ensure any data movement follows a Pydantic model.

Atomic Transactions: Use the Command pattern for multi-step processes (Hash -> Rename -> Encrypt -> Move).

Absolute Imports: Always use absolute paths (e.g., from app.logic.engine import calc).

## 4. Refactoring Mandate (Legacy to OS)

When porting code from the legacy files:

Strip Globals: Remove current_app, session, g.user.

Inject Dependencies: Pass DB clients and loggers via __init__.

Pure Math: Ensure the financial_engine.py logic remains pure (input data -> output result) without side effects.

## 5. Security & Audit

Chain of Custody: Every file action MUST re-calculate the SHA-256 hash.

Audit Trail: Log every state change as a structured JSON object.

## 6. Relational Deal Model (Header-Detail)

We have moved away from flat data storage. The core data model uses a Header-Detail structure to support complex subscription deals with variable numbers of cost lines and services.

Three Core Tables (linked by `transaction_id`):

- `transactions` (Header): The parent record. Stores the "Who" and "When" — client metadata, overall deal status, SHA-256 file hash, financial summary fields (NPV, IRR, commissions), and approval state. Every deal has exactly one header row.

- `fixed_costs` (Detail): Child table for one-off implementation costs. A transaction can have 1 or 100 fixed cost lines — the database handles it natively via foreign key to `transactions`. Each row captures category, service type, location, unit cost with currency conversion.

- `recurring_services` (Detail): Child table for MRR/subscription data. Same scalability as fixed_costs. Each row captures service type, quantity, pricing with multi-currency support, and provider information.

Linking Rule: All three tables are linked by `transaction_id`. A transaction MUST have its child rows inserted atomically. Never create orphaned detail rows or headerless cost entries.

Why This Matters: This is the only way to perform professional-grade NPV and IRR analysis across the entire portfolio. Flat structures would create technical debt and data drift as deal complexity grows.

## 7. Identity Strategy (Email-as-Primary-Key)

The user's Email Address is the Username. Email is the unique identifier in Supabase Auth, and we mirror this in our `profiles` table to eliminate "handle" confusion and reduce identity collisions in the Audit Trail.

Display Name: We still capture Full Name so the UI cards and logs feel human and professional.

Business Unit Authorization: The profiles table tracks which BUs (GIGALAN, ESTADO, CORPORATIVO, MAYORISTA) each user is authorized to see.

## 8. Phased Table Creation Strategy

Tables are NOT all created at once. We follow a phased deployment to avoid "big bang" database migrations:

Phase 1 — Identity & Access (Immediate): The `profiles` table is created first (Supabase cloud, then mirrored in local SQLite). Required before the app can display any UI.

Phase 4 — Relational Transaction Engine (Mid-Project): The "Big Three" (`transactions`, `fixed_costs`, `recurring_services`) plus `audit_logs` are created after the Service Layer is finalized, because we need to know the exact mathematical precision required (BIGINT, NUMERIC types).

Creation Process (Two-Step):
1. SQL Migration Script: A clean `.sql` file defining tables, foreign keys, and Row Level Security (RLS), executed in the Supabase SQL Editor.
2. Local Sync Schema: The Python app's Initialization Service checks if the local `gatekeeper_local.db` exists on first run, and builds the identical table structure for Offline-First operation.

## 9. "Pro Move" Safety Guards

File Lock Guard: The app checks for OS file handles. You cannot "Approve" a file if Excel still has it open. Prevents "Permission Denied" crashes and data corruption.

Steady State Check: The app waits for Power Automate and OneDrive to finish syncing before reading a file. No "half-saved" Excel errors.

Dynamic Pathing: The app finds the user's SharePoint folder regardless of local Windows username.