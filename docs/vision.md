# Finance Gatekeeper OS: Product Vision (v1.0)

This document defines the finished product we are building. It is the shared reference for what Gatekeeper looks like when it ships — what it does, how it feels, and what principles are non-negotiable. Every design decision and implementation task should trace back to something written here.

---

## 1. The One-Liner

Gatekeeper is a desktop financial operating system that turns raw Excel deal submissions into validated, archived, auditable records — with the engineering rigor of an enterprise platform and the polish of a consumer productivity app.

---

## 2. Core Principles

**Simple scope, professional execution.** The v1.0 feature set is deliberately small. What elevates this product is not feature count — it is the quality of every detail: the architecture, the security model, the UI finish, the reliability under real-world conditions (bad internet, locked files, partial syncs).

**Platform from day one.** Even though v1.0 ships a single module, the architecture supports multiple modules, multiple user roles, and shared infrastructure. We build the platform correctly now so that adding a second module later is a configuration change, not a rewrite.

**Enterprise engineering, consumer polish.** The codebase follows industry top-tier patterns (Repository/Service/Command, offline-first sync, typed boundaries, structured audit logging). The interface must feel as polished and responsive as Todoist or Linear — snappy transitions, clean typography, zero visual jank.

---

## 3. Who Uses It

**v1.0 users:** The Finance team. They review incoming sales deals, validate financial metrics, and make approve/reject decisions.

**Future users:** Other teams and roles may be onboarded later with their own modules. They enter through the same app, authenticate against the same database, and share core infrastructure (auth, profiles, audit trail). The platform accommodates this without architectural changes.

---

## 4. The Platform Shell

The application is a **host shell** that manages authentication, navigation, and shared services. Modules plug into this shell.

### Authentication

Users log in with their email. Supabase Auth handles credential verification. On successful login, a local encrypted session cache allows offline access for subsequent launches.

### Role-Based Module Access

Every user has a role. Roles determine which modules a user can access. An admin panel (accessible only to the system administrator) controls role and module assignments.

### Smart Module Routing

- **One module assigned:** The user lands directly in that module after login. No selection screen, no extra click.
- **Multiple modules assigned:** The user sees a clean module selection screen and picks where to go.

This routing is automatic based on the user's profile. The experience adapts without configuration.

---

## 5. v1.0 Modules

### Module 1: Deal Gatekeeper (Finance Team)

This is the core module shipping in v1.0. It is a file-driven approval workflow.

**What the user sees:**

A list of incoming deal files, sourced from the monitored SharePoint/OneDrive inbox (a single flat folder — all BUs mixed). Each file appears as a card in a clean, scannable list.

**What happens when I select a file:**

The file is parsed. The user sees a structured display of the deal data — client information, cost breakdown, recurring services, and calculated financial metrics (NPV, IRR, commissions, gross margin). A direct link to the source Excel file is visible and clickable, opening the file natively so the user can inspect or edit the original.

**What happens when I decide:**

- **Approve:** The file is hashed, renamed, encrypted, and moved to the archive (``02_ARCHIVE_APPROVED/{year}/{BU}/``). The transaction (header + all detail rows) is written atomically to the local database and synced to the cloud in the background. The card disappears from the inbox.
- **Reject:** The file is moved to the rejection archive (``03_ARCHIVE_REJECTED/{year}/{BU}/``). A background email notification is sent to the salesperson with the rejection reason. The card disappears from the inbox.

That is the entire workflow. Select, review, decide.

**Financial verification (lightweight):**

The display includes calculated KPIs based on our own financial engine — NPV, IRR, gross margin, and commissions — so the reviewer can see at a glance whether the deal meets expectations. This is informational, not blocking. The reviewer makes the final call.

### Module 2: Admin Panel (System Administrator)

A restricted module available only to the admin. It manages:

- **User profiles:** View registered users and their roles.
- **Module access:** Assign or revoke module access per user.

This is a simple CRUD interface. It exists so the platform can grow without requiring database-level changes to onboard new users or enable new modules.

---

## 6. A Day in the Life

This section describes the concrete experience of a Finance team member using Gatekeeper on a normal workday.

### Morning — Opening the App

You double-click the Gatekeeper icon. The app launches in under 3 seconds. You see the login screen — clean, minimal, no clutter. You type your email and password and hit Sign In.

Since you only have one module assigned (Deal Gatekeeper), the app takes you straight to the inbox view. No menu to navigate, no module to select.

### The Inbox

You see a list of files waiting for review. Each entry shows just enough context to scan quickly — client name, business unit, date received. New files appear automatically as Power Automate routes them from Outlook to the SharePoint inbox.

### Reviewing a Deal

You click on a file. The right side of the screen (or a detail view) loads the parsed deal data: client metadata, fixed costs broken down by line, recurring services with pricing, and the calculated financial KPIs — NPV, IRR, gross margin, commissions.

You notice the margin looks tight. You click the file link — the original Excel opens in your local Excel app. You adjust a cost line, save, and close Excel. Back in Gatekeeper, you hit Refresh. The app re-reads the file, recalculates the metrics, and updates the display with the new numbers.

### Making the Decision

The numbers look right. You click Approve. Instantly:

- The card vanishes from your inbox.
- The file is hashed, renamed with a timestamp, encrypted, and moved to the archive folder.
- The full transaction record is saved locally.
- In the background (invisible to you), the record syncs to the cloud database.

If the deal was bad, you would have clicked Reject, typed a reason, and the salesperson would receive an email notification. The file moves to the rejection archive.

### Afternoon — Offline

Your internet drops. You keep working. Approvals and rejections continue to write to the local database. When connectivity returns, the background sync worker catches up automatically. You never notice the interruption.

### End of Day

Every decision you made today has a full audit trail — who approved what, when, with what file hash, at what financial metrics. The archived files are encrypted and tamper-evident. The cloud database is the golden source of truth.

---

## 7. What v1.0 Is NOT

To keep scope honest, here is what we are explicitly not building:

- **No dashboard or analytics UI.** No charts, no portfolio views, no trend analysis. The database schema will capture the fields needed for future timeline and KPI dashboards (date_received, date_modified, date_processed), but no UI is built for this in v1.0.
- **No audit log viewer UI.** Every state change is logged to the audit trail, but a visual interface to browse and filter those logs is deferred to late-stage development.
- **No auto-update mechanism.** Checking the cloud for new app versions on startup is planned but deferred to late-stage development. The architecture will not block this addition.
- **No workflow automation beyond approve/reject.** No multi-step approval chains, no conditional routing, no escalation rules.
- **No real-time collaboration.** One user reviews one file. There is no simultaneous editing or live cursors.
- **No file editing inside the app.** Gatekeeper displays data and links to the source file. Editing happens in Excel.
- **No mobile or web version.** This is a Windows desktop application.

---

## 8. Quality Bar

The finished product must meet this bar before it ships:

| Dimension | Standard |
|---|---|
| **Startup** | Login screen renders in under 3 seconds. |
| **File parsing** | A standard deal Excel parses in under 500ms. |
| **UI responsiveness** | No operation blocks the main thread. Every click responds instantly. |
| **Offline resilience** | Full approve/reject workflow works without internet. |
| **Visual polish** | Consistent spacing, typography, and color. No misaligned elements, no visual artifacts, no "developer UI." The app looks like it was designed, not coded. |
| **Security** | AES-256 encryption, SHA-256 chain of custody, encrypted session cache, audit trail on every state change. |
| **Reliability** | File lock detection, steady-state sync checks, graceful degradation on every failure mode. No silent errors. |
