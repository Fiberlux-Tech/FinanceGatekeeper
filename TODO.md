# FinanceGatekeeper — Pending Tasks

## Completed in Phase 1

The following items from the original TODO have been resolved:

- [x] `main.py` — Full DI rewrite: `AppConfig`, `DatabaseManager`, `SessionManager`, schema init, `create_services()`, `ModuleRegistry`, `AppShell` launch
- [x] `app/services/variables.py` — `AppConfig` injected via `__init__`
- [x] `app/services/__init__.py` — `create_services(db, config)` signature updated
- [x] `app/services/email_service.py` — Fixed `AppConfig.validate_email_config()` → `config.validate_email_config()` (was calling instance method as class method)

## Resolved: Systemic `logging.Logger` → `StructuredLogger` Type Mismatch (M1)

All services, repositories, and the `log_audit_event` utility now use `StructuredLogger` in their type hints, matching the runtime type. Static type checkers no longer flag a mismatch.

### Services
- [x] `app/services/email_service.py` — `logger: StructuredLogger`
- [x] `app/services/kpi.py` — `logger: StructuredLogger`
- [x] `app/services/jit_provisioning.py` — `logger: StructuredLogger`
- [x] `app/services/variables.py` — `logger: StructuredLogger`
- [x] `app/services/transaction_workflow.py` — `logger: StructuredLogger`
- [x] `app/services/transaction_preview.py` — `logger: StructuredLogger`
- [x] `app/services/excel_parser.py` — `logger: StructuredLogger` (constructor + `safe_float()`)
- [x] `app/services/transaction_crud.py` — `logger: StructuredLogger`
- [x] `app/services/users.py` — `logger: StructuredLogger`

### Repositories
- [x] `app/repositories/base_repository.py` — `logger: StructuredLogger`
- [x] `app/repositories/user_repository.py` — `logger: StructuredLogger`
- [x] `app/repositories/transaction_repository.py` — `logger: StructuredLogger`
- [x] `app/repositories/master_variable_repository.py` — `logger: StructuredLogger`
- [x] `app/repositories/fixed_cost_repository.py` — `logger: StructuredLogger`
- [x] `app/repositories/recurring_service_repository.py` — `logger: StructuredLogger`

### Utilities
- [x] `app/utils/audit.py` — `log_audit_event(logger: StructuredLogger, ...)`

## Deferred: Static PBKDF2 Salt in Session Cache (M4)

`app/services/session_cache.py` uses a static salt (`b"FinanceGatekeeper_v1_session_salt"`) for PBKDF2 key derivation. The key is combined with machine-specific identity (hostname + OS user) at runtime, which is acceptable for the current threat model (local desktop app, single-user machine). If the threat model changes (e.g. multi-tenant deployment), consider deriving per-installation salts.

## Deferred: `transaction_preview.py` DI for `AppConfig`

- [ ] `app/services/transaction_preview.py` — Still calls `get_config()` internally. Should receive `AppConfig` via `__init__` like `VariableService`.
