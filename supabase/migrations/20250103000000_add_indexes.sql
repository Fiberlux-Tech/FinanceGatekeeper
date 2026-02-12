-- ============================================================================
-- Migration 003: Add Database Indexes for Phase 4
-- ============================================================================
-- Performance optimization: creates indexes on frequently queried columns
-- across the three core transaction tables.
--
-- These indexes accelerate:
--   - Dashboard filtering by approval status, salesman, and submission date
--   - Detail-row lookups via foreign key (transaction_id) on fixed_costs
--     and recurring_services
--
-- NOTE: The sync_queue table is a LOCAL-ONLY offline sync buffer that exists
-- only in the SQLite database.  It does not exist in the Supabase cloud
-- schema, so the idx_sync_queue_status index is intentionally omitted here.
-- That index is created by the SQLite migration (schema.py v7→v8) only.
--
-- All statements use IF NOT EXISTS for idempotency.
--
-- Run this in the Supabase SQL Editor (Dashboard > SQL Editor > New Query).
-- ============================================================================

-- 1. Transaction header indexes (dashboard queries)
CREATE INDEX IF NOT EXISTS idx_transactions_approval_status
    ON public.transactions(approval_status);

CREATE INDEX IF NOT EXISTS idx_transactions_salesman
    ON public.transactions(salesman);

CREATE INDEX IF NOT EXISTS idx_transactions_submission_date
    ON public.transactions(submission_date);

-- 2. Detail table foreign-key indexes (join performance)
CREATE INDEX IF NOT EXISTS idx_fixed_costs_transaction_id
    ON public.fixed_costs(transaction_id);

CREATE INDEX IF NOT EXISTS idx_recurring_services_transaction_id
    ON public.recurring_services(transaction_id);

-- ============================================================================
-- NOTES:
-- - These indexes target the Phase 4 Relational Transaction Engine tables.
--   The tables must already exist before running this migration.
-- - The idx_sync_queue_status index is only in SQLite (local-only table).
-- ============================================================================
