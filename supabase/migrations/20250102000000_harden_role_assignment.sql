-- ============================================================================
-- Migration 002: Harden Role Assignment
-- ============================================================================
-- Security fix: prevent role escalation through signup metadata or
-- direct profile UPDATE via the Supabase API.
--
-- Problem 1: handle_new_user() reads `role` from raw_user_meta_data,
--   allowing a direct API call with {"role": "ADMIN"} to bypass the
--   client's enforcement of SALES-only registration.
--
-- Problem 2: The UPDATE RLS policy does not restrict which columns a
--   user can modify, allowing self-role-escalation via direct API.
--
-- Fixes:
--   1. Replace the trigger to always assign 'SALES' regardless of metadata.
--   2. Drop and recreate the UPDATE policy with a role-immutability check.
-- ============================================================================

-- 1. Replace handle_new_user to ignore user-supplied role
CREATE OR REPLACE FUNCTION public.handle_new_user()
RETURNS TRIGGER AS $$
BEGIN
    INSERT INTO public.profiles (id, email, full_name, role)
    VALUES (
        NEW.id,
        NEW.email,
        COALESCE(NEW.raw_user_meta_data->>'full_name', ''),
        'SALES'  -- Always default to SALES; only admin can escalate
    );
    RETURN NEW;
END;
$$ LANGUAGE plpgsql SECURITY DEFINER SET search_path = public;

-- 2. Drop the permissive UPDATE policy and replace with role-locked version
DROP POLICY IF EXISTS "Users can update own profile" ON public.profiles;

CREATE POLICY "Users can update own profile"
    ON public.profiles FOR UPDATE
    USING (auth.uid() = id)
    WITH CHECK (
        auth.uid() = id
        AND role = (SELECT p.role FROM public.profiles p WHERE p.id = auth.uid())
    );

-- ============================================================================
-- NOTES:
-- - Role escalation is now only possible through the service_role key
--   (used by admin operations in the desktop app).
-- - The WITH CHECK subquery ensures the role column cannot be changed
--   by the row owner — any UPDATE that modifies `role` will be rejected.
-- ============================================================================
