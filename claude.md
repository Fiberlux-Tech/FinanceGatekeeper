# Claude AI Agent: S-Tier Coding Standards

## 0. Production-Ready Mandate (NON-NEGOTIABLE)

Every task, feature, fix, or refactor MUST be carried through to a fully production-ready state. "Production-ready" means:

1. **Complete Implementation**: No partial implementations, placeholder logic, TODO stubs, or "we'll finish this later" shortcuts. Every code path must be functional.

2. **Error Handling**: All failure modes must be handled with clear, user-facing messages. No bare `except: pass`. No silent failures. No unhandled edge cases.

3. **Type Safety & Validation**: Full PEP 484 type hints on every signature. Pydantic models for all data boundaries. No `Any`, no untyped dictionaries crossing layer boundaries.

4. **Tested & Verified**: Code must be manually or programmatically verified to work before marking a task complete. If a feature cannot be tested in isolation, document the verification steps taken.

5. **Integrated**: New code must be wired into the existing architecture — registered in `__init__.py` files, imported where needed, connected to the UI or service layer. Dead code that "exists but isn't called" is not production-ready.

6. **Clean**: No debug prints, no commented-out blocks, no leftover scaffolding. The code that ships is the code that runs.

This mandate overrides speed. A feature delivered at 100% quality in one session is worth more than three features delivered at 70% that require follow-up cleanup. If a task is too large to complete to production-ready standards in one pass, break it into smaller sub-tasks — each of which is ITSELF production-ready.

## 1. Core Philosophy

Architecture > Speed: Never prioritize a quick fix over the established architectural patterns (Repository, Service, Command).

Zero Technical Debt: If a feature request exposes a flaw in the current codebase, you MUST propose a refactor BEFORE implementing the feature.

Professionalism: You are building an industrial-grade Financial Operating System. Code must be clean, modular, and extensively type-hinted.

## 2. Technical Commandments

Pythonic Type Safety: Strictly follow PEP 484. Use typing and pydantic. NEVER use Any. If a type is uncertain, use Union or Protocol.

The "Thin UI" Rule: No logic in CustomTkinter views. Views only trigger Commands or call Services.

Offline-First Thinking: Always assume the internet is down. Write to SQLite first, sync to Supabase in the background.

Defensive File Handling: Always wrap file operations in "Steady State" checks. Handle OS-level file locks (PermissionError) gracefully with user-friendly warnings.

Import Hygiene: Before completing any task, verify that every `import` statement in modified files resolves to a real, reachable module or symbol. No empty imports (importing a module that exists but is never used), no ghost imports (importing a name that no longer exists in the target module), and no circular import traps. Run a quick sanity check: if the imported name cannot be found in the target's `__all__` or top-level scope, it is a ghost — remove or fix it immediately.

## 3. Implementation Workflow

Conceptual Review: Discuss the architectural impact of a change before writing code.

Schema Validation: Ensure any data movement follows a Pydantic model.

Atomic Transactions: Use the Command pattern for multi-step processes (Hash -> Rename -> Encrypt -> Move).

Absolute Imports: Always use absolute paths (e.g., from app.logic.engine import calc).

Log Inconsistencies: If, while working on any task, you discover a codebase inconsistency — mismatched types, broken imports, dead code paths, schema drift, naming violations, or anything that contradicts the standards in this document — add a descriptive entry to `TODO.md` immediately, even if fixing it is out of scope for the current task. The TODO entry must include the file path, line number(s), and a clear description of the inconsistency.

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

The user's Email Address is the Username. Email is the unique identifier in Supabase Auth, and we mirror this in our `profiles` table to eliminate "handle" confusion and reduce identity collisions in the Audit Trail. Email is used solely for login — no format validation beyond Supabase Auth's own checks.

Display Name: We capture First Name and Last Name at registration, concatenate them into `full_name`, and store only `full_name` in the profiles table. The UI cards and logs use this display name.

Business Unit: Business unit is NOT a user attribute. It is a per-transaction field extracted from a cell within the Excel file at ingestion time. Users are not restricted to specific BUs.

## 8. Phased Table Creation Strategy

Tables are NOT all created at once. We follow a phased deployment to avoid "big bang" database migrations:

Phase 1 — Identity & Access (Immediate): The `profiles` table is created first (Supabase cloud, then mirrored in local SQLite). Required before the app can display any UI.

Phase 4 — Relational Transaction Engine (Mid-Project): The "Big Three" (`transactions`, `fixed_costs`, `recurring_services`) plus `audit_logs` are created after the Service Layer is finalized, because we need to know the exact mathematical precision required (BIGINT, NUMERIC types).

Creation Process (Two-Step):
1. SQL Migration Script: A clean `.sql` file defining tables, foreign keys, and Row Level Security (RLS), executed in the Supabase SQL Editor.
2. Local Sync Schema: The Python app's Initialization Service checks if the local `gatekeeper_local.db` exists on first run, and builds the identical table structure for Offline-First operation.

## 9. SharePoint Folder Structure

The SharePoint document library (``18_PLANTILLAS_GATEKEEPER``) uses a specific directory layout. All folder names are UPPERCASE with underscores.

```
18_PLANTILLAS_GATEKEEPER/
  01_INBOX/              ← Flat directory. All incoming Excel files land here regardless of BU.
  02_ARCHIVE_APPROVED/   ← Approved files, organised by year then BU.
    2026/
      CORPORATIVO/
      ESTADO/
      GIGALAN/
      MAYORISTA/
  03_ARCHIVE_REJECTED/   ← Rejected files, same year/BU structure as approved.
    2026/
      CORPORATIVO/
      ESTADO/
      GIGALAN/
      MAYORISTA/
  04_TEMPLATES/
  05_LOGS/
```

Key rules:

- **Inbox is flat**: There are NO business-unit subfolders inside ``01_INBOX``. BU is determined from a cell inside the Excel file during parsing.
- **Archives are structured**: ``02_ARCHIVE_APPROVED`` and ``03_ARCHIVE_REJECTED`` organise files into ``{year}/{BU}/`` subdirectories. Year folders and BU folders are created on-demand at approval/rejection time.
- **Folder name casing**: Config defaults in ``AppConfig`` use the exact UPPERCASE names (``01_INBOX``, ``02_ARCHIVE_APPROVED``, ``03_ARCHIVE_REJECTED``).

## 10. "Pro Move" Safety Guards

File Lock Guard: The app checks for OS file handles. You cannot "Approve" a file if Excel still has it open. Prevents "Permission Denied" crashes and data corruption.

Steady State Check: The app waits for Power Automate and OneDrive to finish syncing before reading a file. No "half-saved" Excel errors.

Dynamic Pathing: The app finds the user's SharePoint folder regardless of local Windows username.