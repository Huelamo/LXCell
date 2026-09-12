# LXCell Roadmap

Last updated: 2026-09-12

## Phase 0 - Project Foundation

Status: complete

Goals:

- Preserve the pre-AI-intervention code in a dedicated branch.
- Record project constraints, preferences, and decisions in repo documentation.
- Prevent accidental commits of real personal finance workbooks.

## Phase 1 - Core Accounting Model

Status: complete

Goals:

- Define normalized entities for transactions, accounts, categories, budgets, users, import batches, and classification rules.
- Choose the local persistence layer, likely SQLite unless a better free and reliable option is justified.
- Add tests for the core accounting behavior.

Expected result:

- A Python domain layer that can create, store, query, and summarize transactions independently of any GUI.

Completion note:

- Phase 1 is complete as the local accounting foundation. SQLite and SQLAlchemy
  are implemented, core entities are covered by tests, manual-entry workflows
  preserve audit trails, and reporting can summarize cashflow, category totals,
  and budget actuals without depending on the UI.
- Rich import workflows, spreadsheet-equivalent reporting, classification
  automation, final UI design, and multi-user product workflows continue in
  later phases.

## Phase 2 - Historical Excel Import

Status: in progress

Goals:

- Import existing yearly workbooks in read-only mode.
- Convert `Registro` sheets into normalized transactions.
- Import or reconstruct budgets from `Presupuestos`.
- Validate imported monthly totals against `Seguimiento`.
- Map older categories into the canonical 2026 category structure.

Expected result:

- A trusted historical dataset that reproduces the spreadsheet totals.

## Phase 3 - Reporting Equivalent To Excel

Status: pending

Goals:

- Recreate the useful outputs of `Seguimiento`.
- Show budget vs actuals by month and category.
- Track expected vs actual savings.
- Keep reports traceable to individual transactions.

Expected result:

- LXCell can replace the main review workflow currently done in Excel or Google Sheets.

## Phase 4 - Statement Import

Status: in progress

Goals:

- Import user-provided bank statements.
- Start with the most active personal and shared statement exports.
- Add lower-volume statement sources after the first importer is stable.
- Detect duplicate transactions across repeated imports.
- Keep original import metadata for auditability.

Expected result:

- The user can import statements instead of typing transactions manually.

Current design direction:

- Start with preview-first imports for user-provided Spanish PDF bank or card
  statement exports, requiring the user to select an existing account.
- Preserve file and row-level audit metadata without storing original files.
- Combine file-hash and normalized row-hash duplicate detection.
- Run conservative deterministic category suggestions against existing active
  categories, leaving uncertain rows pending review.
- First read-only PDF parser implemented; Streamlit preview, account-scoped
  duplicate checks, classification suggestions, and confirmed writes remain
  follow-up work.

Detailed review log: `docs/phase_4_statement_import.md`.

## Phase 5 - Classification Rules

Status: pending

Goals:

- Learn deterministic rules from historical classification patterns.
- Auto-classify obvious recurring merchants and descriptions.
- Mark uncertain transactions for user review.
- Keep a record of which rule or suggestion classified each transaction.

Expected result:

- Most repetitive spending is categorized automatically, with review for ambiguous cases.

Phase 4 dependency:

- The first statement importer will include a small deterministic classification
  layer so imported transactions can receive high-confidence category
  suggestions. Broader rule learning, management, and automation remain Phase 5.

## Phase 6 - User Interface

Status: pending

Goals:

- Build a user-facing Spanish interface.
- Support importing statements, reviewing classifications, editing transactions, managing budgets, and viewing dashboards.
- Evaluate Streamlit first unless project constraints point elsewhere.

Expected result:

- A usable local application for day-to-day personal finance work.

## Phase 7 - Multi-User Workflows

Status: pending

Goals:

- Support separate user profiles and datasets.
- Make it easy to manage finances for family or friends without mixing data.
- Keep per-user categories, budgets, accounts, and import history isolated.

Expected result:

- The app can serve the user, family members, and interested friends safely.

## Phase 8 - Agentic Assistance

Status: pending

Goals:

- Add AI-assisted review once the data model and audit trail are reliable.
- Suggest categories, identify anomalies, explain deviations, and prepare monthly summaries.
- Keep human confirmation in the loop for financial records.

Expected result:

- LXCell becomes a review assistant, not just a register.
