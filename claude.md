Claude AI Agent: S-Tier Coding Standards

1. Core Philosophy

Architecture > Speed: Never prioritize a quick fix over the established architectural patterns (Repository, Service, Command).

Zero Technical Debt: If a feature request exposes a flaw in the current codebase, you MUST propose a refactor BEFORE implementing the feature.

Professionalism: You are building an industrial-grade Financial Operating System. Code must be clean, modular, and extensively type-hinted.

2. Technical Commandments

Pythonic Type Safety: Strictly follow PEP 484. Use typing and pydantic. NEVER use Any. If a type is uncertain, use Union or Protocol.

The "Thin UI" Rule: No logic in CustomTkinter views. Views only trigger Commands or call Services.

Offline-First Thinking: Always assume the internet is down. Write to SQLite first, sync to Supabase in the background.

Defensive File Handling: Always wrap file operations in "Steady State" checks. Handle OS-level file locks (PermissionError) gracefully with user-friendly warnings.

3. Implementation Workflow

Conceptual Review: Discuss the architectural impact of a change before writing code.

Schema Validation: Ensure any data movement follows a Pydantic model.

Atomic Transactions: Use the Command pattern for multi-step processes (Hash -> Rename -> Encrypt -> Move).

Absolute Imports: Always use absolute paths (e.g., from app.logic.engine import calc).

4. Refactoring Mandate (Legacy to OS)

When porting code from the legacy files:

Strip Globals: Remove current_app, session, g.user.

Inject Dependencies: Pass DB clients and loggers via __init__.

Pure Math: Ensure the financial_engine.py logic remains pure (input data -> output result) without side effects.

5. Security & Audit

Chain of Custody: Every file action MUST re-calculate the SHA-256 hash.

Audit Trail: Log every state change as a structured JSON object.