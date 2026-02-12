-- ============================================================================
-- Migration 001: Profiles Table
-- ============================================================================
-- Phase 1: Identity & Access Foundation
--
-- Creates the `profiles` table in the public schema, linked 1:1 to
-- Supabase auth.users.  This table is the authoritative source for
-- user identity fields beyond what Supabase Auth stores natively.
--
-- Identity strategy:
--   - Email = login credential + unique identifier (from auth.users)
--   - Full Name = display name in UI, logs, and audit trail
--   - Role = application-level RBAC (SALES, FINANCE, ADMIN)
--
-- Run this in the Supabase SQL Editor (Dashboard > SQL Editor > New Query).
-- ============================================================================

-- 1. Create the profiles table
CREATE TABLE IF NOT EXISTS public.profiles (
    id          UUID PRIMARY KEY REFERENCES auth.users(id) ON DELETE CASCADE,
    email       TEXT NOT NULL UNIQUE,
    full_name   TEXT NOT NULL DEFAULT '',
    role        TEXT NOT NULL DEFAULT 'SALES'
                CHECK (role IN ('SALES', 'FINANCE', 'ADMIN')),
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- 2. Index for email lookups
CREATE INDEX IF NOT EXISTS idx_profiles_email ON public.profiles(email);

-- 3. Auto-update the updated_at timestamp on row changes
CREATE OR REPLACE FUNCTION public.handle_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = now();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS on_profiles_updated ON public.profiles;
CREATE TRIGGER on_profiles_updated
    BEFORE UPDATE ON public.profiles
    FOR EACH ROW
    EXECUTE FUNCTION public.handle_updated_at();

-- 4. Auto-create a profile row when a new user signs up
--    Extracts full_name from user_metadata (set during signup).
CREATE OR REPLACE FUNCTION public.handle_new_user()
RETURNS TRIGGER AS $$
BEGIN
    INSERT INTO public.profiles (id, email, full_name, role)
    VALUES (
        NEW.id,
        NEW.email,
        COALESCE(NEW.raw_user_meta_data->>'full_name', ''),
        COALESCE(NEW.raw_user_meta_data->>'role', 'SALES')
    );
    RETURN NEW;
END;
$$ LANGUAGE plpgsql SECURITY DEFINER SET search_path = public;

DROP TRIGGER IF EXISTS on_auth_user_created ON auth.users;
CREATE TRIGGER on_auth_user_created
    AFTER INSERT ON auth.users
    FOR EACH ROW
    EXECUTE FUNCTION public.handle_new_user();

-- 5. Row Level Security
ALTER TABLE public.profiles ENABLE ROW LEVEL SECURITY;

-- Users can read their own profile
CREATE POLICY "Users can view own profile"
    ON public.profiles FOR SELECT
    USING (auth.uid() = id);

-- Users can update their own profile (name only, not role)
CREATE POLICY "Users can update own profile"
    ON public.profiles FOR UPDATE
    USING (auth.uid() = id)
    WITH CHECK (auth.uid() = id);

-- Service role can do everything (used by the desktop app via service key)
CREATE POLICY "Service role full access"
    ON public.profiles FOR ALL
    USING (auth.role() = 'service_role');

-- ============================================================================
-- NOTES:
-- - The desktop app reads profiles via the Supabase client to get full_name.
-- - JIT provisioning syncs profiles to local SQLite on every login.
-- - Role changes are made by ADMIN users through the service role key.
-- ============================================================================
