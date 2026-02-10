# FinanceGatekeeper — Pending Refactor Tasks

## Downstream DI Migration (Post `app/` Refactor)

The `app/` top-level files have been refactored to remove globals, singletons, and legacy Flask patterns. The following files still reference the **removed APIs** and must be updated to use the new dependency-injection patterns.

### main.py

- [ ] Replace `from app.config import get_config` with direct `AppConfig()` instantiation
- [ ] Replace `from app.database import db` singleton with `DatabaseManager(url, key, path, logger)` constructor
- [ ] Remove calls to `db.init_supabase()` / `db.init_sqlite()` (config is now passed at construction)
- [ ] Wire up DI: create `AppConfig`, `StructuredLogger`, `DatabaseManager`, and `SessionManager` at startup and inject into services

### app/services/transaction_preview.py

- [ ] Replace `from app.config import get_config` with injected `AppConfig` instance via `__init__`

### app/services/variables.py

- [ ] Replace `from app.config import get_config` with injected `AppConfig` instance via `__init__`
