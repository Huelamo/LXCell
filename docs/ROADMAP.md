# LXCell Roadmap

Last updated: 2026-08-05

## Phase 0 - Project Foundation

Status: in progress

Goals:

- Preserve the pre-AI-intervention code in a dedicated branch.
- Record project constraints, preferences, and decisions in repo documentation.
- Prevent accidental commits of real personal finance workbooks.

## Phase 1 - Core Accounting Model

Status: pending

Goals:

- Define normalized entities for transactions, accounts, categories, budgets, users, import batches, and classification rules.
- Choose the local persistence layer, likely SQLite unless a better free and reliable option is justified.
- Add tests for the core accounting behavior.

Expected result:

- A Python domain layer that can create, store, query, and summarize transactions independently of any GUI.

## Phase 2 - Historical Excel Import

Status: pending

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

Status: pending

Goals:

- Import user-provided bank statements.
- Start with Revolut personal and shared Revolut exports.
- Add ING after Revolut import is stable.
- Detect duplicate transactions across repeated imports.
- Keep original import metadata for auditability.

Expected result:

- The user can import statements instead of typing transactions manually.

## Phase 5 - Classification Rules

Status: pending

Goals:

- Learn deterministic rules from historical classification patterns.
- Auto-classify obvious recurring merchants and descriptions.
- Mark uncertain transactions for user review.
- Keep a record of which rule or suggestion classified each transaction.

Expected result:

- Most repetitive spending is categorized automatically, with review for ambiguous cases.

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
