# FinanceGatekeeper — Pending Tasks

## Completed in Phase 1

The following items from the original TODO have been resolved:

- [x] `main.py` — Full DI rewrite: `AppConfig`, `DatabaseManager`, `SessionManager`, schema init, `create_services()`, `ModuleRegistry`, `AppShell` launch
- [x] `app/services/variables.py` — `AppConfig` injected via `__init__`
- [x] `app/services/__init__.py` — `create_services(db, config)` signature updated
- [x] `app/services/email_service.py` — Fixed `AppConfig.validate_email_config()` → `config.validate_email_config()` (was calling instance method as class method)

## Deferred: Systemic `logging.Logger` → `StructuredLogger` Type Mismatch (M1)

All pre-existing services and repositories declare `logger: logging.Logger` in their constructors, but receive `StructuredLogger` at runtime. `StructuredLogger` duck-types correctly (exposes `.info()`, `.warning()`, `.error()`, etc.), so there is no runtime issue. However, static type checkers will flag the mismatch.

`BaseService` has been fixed to use `StructuredLogger`. The following files still use `logging.Logger` and should be updated in a future cleanup pass:

### Services
- [ ] `app/services/email_service.py` — `logger: logging.Logger`
- [ ] `app/services/kpi.py` — `logger: logging.Logger`
- [ ] `app/services/jit_provisioning.py` — `logger: logging.Logger`
- [ ] `app/services/variables.py` — `logger: logging.Logger`
- [ ] `app/services/transaction_workflow.py` — `logger: logging.Logger`
- [ ] `app/services/transaction_preview.py` — `logger: logging.Logger`
- [ ] `app/services/excel_parser.py` — `logger: logging.Logger` (constructor + `safe_float()`)
- [ ] `app/services/transaction_crud.py` — `logger: logging.Logger`
- [ ] `app/services/users.py` — `logger: logging.Logger`

### Repositories
- [ ] `app/repositories/base_repository.py` — `logger: logging.Logger`
- [ ] `app/repositories/user_repository.py` — `logger: logging.Logger`
- [ ] `app/repositories/transaction_repository.py` — `logger: logging.Logger`
- [ ] `app/repositories/master_variable_repository.py` — `logger: logging.Logger`
- [ ] `app/repositories/fixed_cost_repository.py` — `logger: logging.Logger`
- [ ] `app/repositories/recurring_service_repository.py` — `logger: logging.Logger`

## Deferred: Static PBKDF2 Salt in Session Cache (M4)

`app/services/session_cache.py` uses a static salt (`b"FinanceGatekeeper_v1_session_salt"`) for PBKDF2 key derivation. The key is combined with machine-specific identity (hostname + OS user) at runtime, which is acceptable for the current threat model (local desktop app, single-user machine). If the threat model changes (e.g. multi-tenant deployment), consider deriving per-installation salts.

## Deferred: `transaction_preview.py` DI for `AppConfig`

- [ ] `app/services/transaction_preview.py` — Still calls `get_config()` internally. Should receive `AppConfig` via `__init__` like `VariableService`.
