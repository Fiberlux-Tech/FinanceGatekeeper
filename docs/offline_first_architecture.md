# Offline-First Architecture: Design Rationale

This document explains the conceptual reasoning behind the Local-First / Hybrid sync strategy used by Finance Gatekeeper OS. For the technical specification (schemas, flags, threading model), see [technical_requirements.md](technical_requirements.md) Section 2B.

---

## 1. The "Local Mirror" Strategy (SQLite)

We don't treat Supabase as our primary data source during a live session. Instead, we use a **Local SQLite Database** that lives on your machine in Lima.

**How it works:** When you open the app, it doesn't "ask" Supabase for your profile every time you click a button. It looks at its local SQLite mirror.

**The Benefit:** Accessing a local SQLite database takes microseconds. Accessing Supabase across the ocean takes hundreds of milliseconds. By reading locally, the UI feels "snappy" and instantaneous.

## 2. "Asynchronous" Synchronization

We separate the **UI Actions** from the **Cloud Sync**.

**The Logic:** When you hit "Approve," the app writes the decision to the local SQLite database and moves the file on your disk immediately. The card vanishes from the "Inbox" view instantly.

**The Background Worker:** A separate "Sync Thread" (which the user never sees) notices the new record in SQLite and says, "Okay, I'll try to push this to Supabase now."

**If Internet fails:** The Sync Thread just waits. It retries with an "Exponential Backoff" (it waits 1s, then 2s, then 4s...). Your work isn't blocked; you can keep approving files while the background thread worries about the internet.

## 3. Handling Auth without Internet

This is the trickiest part. For security, the **first time you log in**, you must have internet to verify your credentials with Supabase.

**The Session Cache:** Once you've logged in successfully once, Supabase provides a "Refresh Token." We store an encrypted version of your session state locally.

**The Result:** If you open your laptop on a plane or in a dead zone in Lima, the app can check your local "Encrypted Session." If it's still valid, it lets you into the "Offline Mode" where you can still see your folders and process files.

## 4. Syncing the Relational Deal Model

The Header-Detail structure (`transactions` -> `fixed_costs` / `recurring_services`) requires careful sync logic to maintain data integrity across local and cloud.

**Atomic Local Write:** When a deal is approved, the app writes the transaction header and ALL detail rows (fixed_costs, recurring_services) to SQLite in a single database transaction. If any part fails, the entire write rolls back.

**Sync Order Matters:** The Background Sync Worker pushes records to Supabase in dependency order:
1. First, the `transactions` header row (the parent).
2. Then, all `fixed_costs` rows referencing that transaction.
3. Then, all `recurring_services` rows referencing that transaction.

This ensures foreign key constraints in Supabase PostgreSQL are never violated.

**Conflict Resolution:** Supabase is the "Golden Source of Truth." If a record exists in both local and cloud with different states, the cloud version wins on the next full sync. Local changes that haven't been pushed are preserved in the `sync_queue` until successfully transmitted.

## 5. Why This Is the "Best" Solution

There are only three ways to build this, and we chose the most professional one:

| Approach | Verdict | Reasoning |
|---|---|---|
| **Cloud-Only** | Bad | Laggy UI, breaks if internet drops. |
| **Local-Only** | Bad | No backups, no team collaboration, hard to audit. |
| **Local-First / Hybrid** | **Our Choice** | High-performance local speed + Cloud durability and auditing. |
