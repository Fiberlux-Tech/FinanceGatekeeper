-- ============================================================================
-- Migration 004: Relational Transaction Engine (Phase 4)
-- ============================================================================
-- Creates the "Big Three" relational tables (transactions, fixed_costs,
-- recurring_services) plus audit_logs and master_variables.
--
-- Architecture:
--   - Header-Detail model: transactions (1) → fixed_costs (N), recurring_services (N)
--   - All three linked by transaction_id (TEXT, format FLX{YY}-{MMDDHHMMSSFFFFF})
--   - Detail rows cascade-delete when parent transaction is removed
--   - Financial amounts use NUMERIC(18,6) for sub-cent precision
--   - Rates/ratios use NUMERIC(10,6) for exchange rates and percentages
--
-- Identity:
--   - Transaction IDs are app-generated TEXT (NOT UUID)
--   - created_by UUID links to profiles(id) for Row Level Security
--   - user_id in audit_logs/master_variables is TEXT (Supabase UUID as string)
--
-- Row Level Security:
--   - Service role (desktop app) has full access — bypasses all policies
--   - SALES users can only see their own transactions (via created_by)
--   - FINANCE/ADMIN users can see and update all transactions
--   - Detail tables inherit access from parent transaction via FK subquery
--
-- Run this in the Supabase SQL Editor (Dashboard > SQL Editor > New Query).
-- ============================================================================


-- ============================================================================
-- 1. TRANSACTIONS (Header Table)
-- ============================================================================

CREATE TABLE IF NOT EXISTS public.transactions (
    -- Identity
    id              TEXT PRIMARY KEY,                  -- FLX{YY}-{timestamp} format
    created_by      UUID REFERENCES public.profiles(id), -- submitting user (for RLS)

    -- Client metadata (from Excel cells C2-C6)
    unidad_negocio  TEXT NOT NULL DEFAULT '',           -- BU: GIGALAN/ESTADO/CORPORATIVO/MAYORISTA
    client_name     TEXT NOT NULL DEFAULT '',
    company_id      INTEGER,                           -- SAP company code
    salesman        TEXT NOT NULL DEFAULT '',           -- display name
    order_id        INTEGER,                           -- SAP order number

    -- Exchange rate (frozen at creation via master_variables_snapshot)
    tipo_cambio     NUMERIC(10,6),                     -- USD→PEN rate

    -- Monthly Recurring Charge
    mrc_original    NUMERIC(18,6),
    mrc_currency    TEXT NOT NULL DEFAULT 'PEN'
                    CHECK (mrc_currency IN ('PEN', 'USD')),
    mrc_pen         NUMERIC(18,6),

    -- Non-Recurring Charge
    nrc_original    NUMERIC(18,6),
    nrc_currency    TEXT NOT NULL DEFAULT 'PEN'
                    CHECK (nrc_currency IN ('PEN', 'USD')),
    nrc_pen         NUMERIC(18,6),

    -- Financial KPIs (computed by financial_engine.py)
    van             NUMERIC(18,6),                     -- NPV (VAN in Spanish)
    tir             NUMERIC(10,6),                     -- IRR (TIR in Spanish)
    payback         INTEGER,                           -- months until breakeven
    total_revenue   NUMERIC(18,6),                     -- sum of all revenue (PEN)
    total_expense   NUMERIC(18,6),                     -- sum of all expense (PEN)

    -- Commission (business-unit-specific rules)
    comisiones              NUMERIC(18,6),
    comisiones_rate         NUMERIC(10,6),             -- as % of total_revenue
    costo_instalacion       NUMERIC(18,6),             -- total fixed costs (PEN)
    costo_instalacion_ratio NUMERIC(10,6),             -- as % of total_revenue
    gross_margin            NUMERIC(18,6),
    gross_margin_ratio      NUMERIC(10,6),

    -- Contract terms
    plazo_contrato      INTEGER,                       -- contract duration (months)
    costo_capital_anual NUMERIC(18,6),                 -- annual cost of capital
    tasa_carta_fianza   NUMERIC(10,6),                 -- performance bond rate
    costo_carta_fianza  NUMERIC(18,6),                 -- performance bond cost (PEN)
    aplica_carta_fianza BOOLEAN NOT NULL DEFAULT FALSE,

    -- GIGALAN-specific fields
    gigalan_region    TEXT,
    gigalan_sale_type TEXT,
    gigalan_old_mrc   NUMERIC(18,6),

    -- Chain of Custody (CLAUDE.md §5)
    file_sha256 TEXT,                                  -- SHA-256 of source Excel file

    -- Cached computation (zero-CPU reads for dashboards)
    master_variables_snapshot JSONB,                    -- frozen rates at creation time
    financial_cache          JSONB,                     -- pre-computed KPI metrics

    -- Approval workflow
    approval_status TEXT NOT NULL DEFAULT 'PENDING'
        CHECK (approval_status IN ('PENDING', 'APPROVED', 'REJECTED', 'CANCELLED')),
    submission_date TIMESTAMPTZ,
    approval_date   TIMESTAMPTZ,
    rejection_note  TEXT,

    -- Timestamps
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);


-- ============================================================================
-- 2. FIXED_COSTS (Detail Table — one-off implementation costs)
-- ============================================================================

CREATE TABLE IF NOT EXISTS public.fixed_costs (
    id                      BIGSERIAL PRIMARY KEY,
    transaction_id          TEXT NOT NULL
                            REFERENCES public.transactions(id) ON DELETE CASCADE,
    categoria               TEXT,
    tipo_servicio           TEXT,
    ticket                  TEXT,
    ubicacion               TEXT,
    cantidad                NUMERIC(18,6),
    costo_unitario_original NUMERIC(18,6),
    costo_unitario_currency TEXT NOT NULL DEFAULT 'USD'
                            CHECK (costo_unitario_currency IN ('PEN', 'USD')),
    costo_unitario_pen      NUMERIC(18,6),
    periodo_inicio          INTEGER NOT NULL DEFAULT 0,
    duracion_meses          INTEGER NOT NULL DEFAULT 1
);


-- ============================================================================
-- 3. RECURRING_SERVICES (Detail Table — MRR/subscription data)
-- ============================================================================

CREATE TABLE IF NOT EXISTS public.recurring_services (
    id                   BIGSERIAL PRIMARY KEY,
    transaction_id       TEXT NOT NULL
                         REFERENCES public.transactions(id) ON DELETE CASCADE,
    tipo_servicio        TEXT,
    nota                 TEXT,
    ubicacion            TEXT,
    quantity             NUMERIC(18,6),
    price_original       NUMERIC(18,6),
    price_currency       TEXT NOT NULL DEFAULT 'PEN'
                         CHECK (price_currency IN ('PEN', 'USD')),
    price_pen            NUMERIC(18,6),
    cost_unit_1_original NUMERIC(18,6),
    cost_unit_2_original NUMERIC(18,6),
    cost_unit_currency   TEXT NOT NULL DEFAULT 'USD'
                         CHECK (cost_unit_currency IN ('PEN', 'USD')),
    cost_unit_1_pen      NUMERIC(18,6),
    cost_unit_2_pen      NUMERIC(18,6),
    proveedor            TEXT
);


-- ============================================================================
-- 4. AUDIT_LOGS (Immutable structured audit trail)
-- ============================================================================
-- Note: Supabase table is "audit_logs" (plural); SQLite uses "audit_log"
-- (singular). The repository layer handles this mapping.

CREATE TABLE IF NOT EXISTS public.audit_logs (
    id          BIGSERIAL PRIMARY KEY,
    timestamp   TIMESTAMPTZ NOT NULL DEFAULT now(),
    action      TEXT NOT NULL,                         -- CREATE, APPROVE, REJECT, etc.
    entity_type TEXT NOT NULL,                         -- Transaction, User, MasterVariable
    entity_id   TEXT NOT NULL,                         -- PK of affected entity
    user_id     TEXT NOT NULL,                         -- Supabase UUID as text
    details     JSONB NOT NULL DEFAULT '{}'::jsonb,    -- flat key-value context
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);


-- ============================================================================
-- 5. MASTER_VARIABLES (Append-only historical record)
-- ============================================================================

CREATE TABLE IF NOT EXISTS public.master_variables (
    id             BIGSERIAL PRIMARY KEY,
    variable_name  TEXT NOT NULL,
    variable_value NUMERIC(18,6) NOT NULL,
    category       TEXT NOT NULL,
    user_id        TEXT NOT NULL,                      -- Supabase UUID as text
    comment        TEXT,
    date_recorded  TIMESTAMPTZ NOT NULL DEFAULT now()
);


-- ============================================================================
-- 6. INDEXES
-- ============================================================================

-- transactions: dashboard filtering + RLS lookups
CREATE INDEX IF NOT EXISTS idx_transactions_approval_status
    ON public.transactions(approval_status);

CREATE INDEX IF NOT EXISTS idx_transactions_salesman
    ON public.transactions(salesman);

CREATE INDEX IF NOT EXISTS idx_transactions_submission_date
    ON public.transactions(submission_date);

CREATE INDEX IF NOT EXISTS idx_transactions_created_by
    ON public.transactions(created_by);

-- detail tables: FK join performance
CREATE INDEX IF NOT EXISTS idx_fixed_costs_transaction_id
    ON public.fixed_costs(transaction_id);

CREATE INDEX IF NOT EXISTS idx_recurring_services_transaction_id
    ON public.recurring_services(transaction_id);

-- audit_logs: compliance queries
CREATE INDEX IF NOT EXISTS idx_audit_logs_entity_id
    ON public.audit_logs(entity_id);

CREATE INDEX IF NOT EXISTS idx_audit_logs_user_id
    ON public.audit_logs(user_id);

-- master_variables: latest-value lookups
CREATE INDEX IF NOT EXISTS idx_master_variables_variable_name
    ON public.master_variables(variable_name);


-- ============================================================================
-- 7. TRIGGERS
-- ============================================================================
-- Reuses handle_updated_at() created in migration 001 (profiles table).

DROP TRIGGER IF EXISTS on_transactions_updated ON public.transactions;
CREATE TRIGGER on_transactions_updated
    BEFORE UPDATE ON public.transactions
    FOR EACH ROW
    EXECUTE FUNCTION public.handle_updated_at();


-- ============================================================================
-- 8. ROW LEVEL SECURITY
-- ============================================================================
-- The desktop app uses the service_role key (bypasses RLS entirely).
-- These policies protect against direct Supabase API/client access.

-- ---- TRANSACTIONS ---------------------------------------------------------

ALTER TABLE public.transactions ENABLE ROW LEVEL SECURITY;

-- Service role: full access (desktop app)
CREATE POLICY "Service role full access on transactions"
    ON public.transactions FOR ALL
    USING (auth.role() = 'service_role');

-- SALES: can see own transactions only (via created_by FK)
CREATE POLICY "Sales users view own transactions"
    ON public.transactions FOR SELECT
    USING (created_by = auth.uid());

-- FINANCE/ADMIN: can see all transactions
CREATE POLICY "Finance and admin view all transactions"
    ON public.transactions FOR SELECT
    USING (EXISTS (
        SELECT 1 FROM public.profiles
        WHERE id = auth.uid() AND role IN ('FINANCE', 'ADMIN')
    ));

-- FINANCE/ADMIN: can update transactions (approval/rejection workflow)
CREATE POLICY "Finance and admin update transactions"
    ON public.transactions FOR UPDATE
    USING (EXISTS (
        SELECT 1 FROM public.profiles
        WHERE id = auth.uid() AND role IN ('FINANCE', 'ADMIN')
    ));

-- Authenticated users: can insert new transactions
CREATE POLICY "Authenticated users insert transactions"
    ON public.transactions FOR INSERT
    WITH CHECK (auth.uid() IS NOT NULL);


-- ---- FIXED_COSTS ----------------------------------------------------------
-- Access inherits from parent transaction via FK subquery.

ALTER TABLE public.fixed_costs ENABLE ROW LEVEL SECURITY;

CREATE POLICY "Service role full access on fixed_costs"
    ON public.fixed_costs FOR ALL
    USING (auth.role() = 'service_role');

CREATE POLICY "Users view own transaction details (fixed_costs)"
    ON public.fixed_costs FOR SELECT
    USING (EXISTS (
        SELECT 1 FROM public.transactions t
        WHERE t.id = transaction_id
          AND (
              t.created_by = auth.uid()
              OR EXISTS (
                  SELECT 1 FROM public.profiles
                  WHERE id = auth.uid() AND role IN ('FINANCE', 'ADMIN')
              )
          )
    ));

CREATE POLICY "Authenticated users insert fixed_costs"
    ON public.fixed_costs FOR INSERT
    WITH CHECK (auth.uid() IS NOT NULL);

CREATE POLICY "Authenticated users delete fixed_costs"
    ON public.fixed_costs FOR DELETE
    USING (auth.uid() IS NOT NULL);


-- ---- RECURRING_SERVICES ---------------------------------------------------
-- Access inherits from parent transaction via FK subquery.

ALTER TABLE public.recurring_services ENABLE ROW LEVEL SECURITY;

CREATE POLICY "Service role full access on recurring_services"
    ON public.recurring_services FOR ALL
    USING (auth.role() = 'service_role');

CREATE POLICY "Users view own transaction details (recurring_services)"
    ON public.recurring_services FOR SELECT
    USING (EXISTS (
        SELECT 1 FROM public.transactions t
        WHERE t.id = transaction_id
          AND (
              t.created_by = auth.uid()
              OR EXISTS (
                  SELECT 1 FROM public.profiles
                  WHERE id = auth.uid() AND role IN ('FINANCE', 'ADMIN')
              )
          )
    ));

CREATE POLICY "Authenticated users insert recurring_services"
    ON public.recurring_services FOR INSERT
    WITH CHECK (auth.uid() IS NOT NULL);

CREATE POLICY "Authenticated users delete recurring_services"
    ON public.recurring_services FOR DELETE
    USING (auth.uid() IS NOT NULL);


-- ---- AUDIT_LOGS -----------------------------------------------------------

ALTER TABLE public.audit_logs ENABLE ROW LEVEL SECURITY;

CREATE POLICY "Service role full access on audit_logs"
    ON public.audit_logs FOR ALL
    USING (auth.role() = 'service_role');

CREATE POLICY "Finance and admin view audit logs"
    ON public.audit_logs FOR SELECT
    USING (EXISTS (
        SELECT 1 FROM public.profiles
        WHERE id = auth.uid() AND role IN ('FINANCE', 'ADMIN')
    ));

CREATE POLICY "Authenticated users insert audit logs"
    ON public.audit_logs FOR INSERT
    WITH CHECK (auth.uid() IS NOT NULL);


-- ---- MASTER_VARIABLES -----------------------------------------------------

ALTER TABLE public.master_variables ENABLE ROW LEVEL SECURITY;

CREATE POLICY "Service role full access on master_variables"
    ON public.master_variables FOR ALL
    USING (auth.role() = 'service_role');

CREATE POLICY "All authenticated users view master variables"
    ON public.master_variables FOR SELECT
    USING (auth.uid() IS NOT NULL);

CREATE POLICY "Finance and admin insert master variables"
    ON public.master_variables FOR INSERT
    WITH CHECK (EXISTS (
        SELECT 1 FROM public.profiles
        WHERE id = auth.uid() AND role IN ('FINANCE', 'ADMIN')
    ));


-- ============================================================================
-- NOTES:
-- - Migration 003 (add_indexes.sql) creates the same indexes with IF NOT
--   EXISTS guards, so running it after this migration is a safe no-op.
-- - The handle_updated_at() trigger function was created in migration 001
--   (profiles table) and is reused here for the transactions table.
-- - audit_logs/master_variables use TEXT for user_id (not UUID FK) to match
--   the SQLite schema where all IDs are stored as text strings.
-- - Detail tables (fixed_costs, recurring_services) have DELETE policies for
--   the replace_for_transaction() compensating-transaction pattern.
-- ============================================================================
