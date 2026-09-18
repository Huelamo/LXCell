# Phase 2 Historical Excel Import

Last updated: 2026-08-23

This document records the design review for historical Excel workbook import.
Historical personal finance workbooks are read-only source material. Do not
commit real workbook contents, filenames, categories tied to real usage, or
personal financial values.

## Review Status

Accepted blocks:

- Block 1: historical Excel dry-run preview.
- Block 2: local UI historical Excel preview.
- Block 3: dry-run validation against `Seguimiento`.
- Block 4: confirmed-import preparation screen.
- Block 5: normalized transaction semantics for confirmed import.
- Block 6: confirmed historical Excel database import.
- Block 7: per-import historical category mapping.
- Block 8: persisted file-scoped historical category mappings.

Pending blocks:

- Reimport or replace workflows for corrected historical workbooks.

## Block 1 - Historical Excel Dry-Run Preview

Accepted:

- Add `src/lxcell/importers/excel_historical.py`.
- Implement `HistoricalExcelDryRunImporter`.
- Open workbooks with `openpyxl` in read-only mode using `read_only=True` and
  `data_only=True`.
- Default to reading the `Registro` sheet.
- Detect the header row by finding a date column such as `Fecha`, `Date`,
  `Día`, or `Dia`.
- Treat non-empty, non-ignored columns after header detection as historical
  source category columns.
- Ignore common non-category columns such as totals, comments, notes, and
  observations.
- Convert each non-zero numeric category cell into a transaction candidate in
  memory.
- Preserve source row number, source column name, source category name, raw
  amount, raw row payload, optional comment text, and parsed date.
- Compute a SHA-256 file hash for preview metadata.
- Return summary properties for transaction count, source categories, totals by
  source category, and totals by month.
- Do not write to the database during preview.
- Do not create `ImportBatch`, `ImportedTransactionSource`, `Transaction`,
  category mapping records, or validation records during preview.
- Use anonymized synthetic workbooks generated in tests rather than committing
  real personal finance files.

Current interpretation:

- Source amounts are preserved with the sign used in the spreadsheet.
- Expense columns preview positive source amounts as outflows.
- Expense columns preview negative source amounts as inflows, representing
  refunds or reimbursements.
- Income columns preview positive source amounts as inflows.
- Income columns preview negative source amounts as outflows, representing
  reversals or corrections.
- Income columns are detected first from green header fill and then from
  generic income-like header names such as salary, payroll, income, or interest.
- Preview tables show source spreadsheet totals and source spreadsheet amounts,
  while direction indicates how LXCell would interpret each candidate.
- Signed accounting impact remains available internally for later validation,
  but is not the primary preview display because it can make expense amounts
  look inverted relative to the spreadsheet.

Deferred:

- Source category mapping review.
- Duplicate detection against existing database records.
- Writing `ImportBatch` and `ImportedTransactionSource` rows.
- Creating normalized `Transaction` rows.
- Budget import from `Presupuestos`.

Rationale:

- Historical workbooks are trusted source material, so import should first prove
  it can read and summarize data without mutating either the workbook or the
  LXCell database.
- A dry-run preview lets the user inspect source categories, totals, ignored
  rows, and parsing assumptions before committing historical records.
- Keeping this parser separate from services and UI gives Phase 2 a small,
  testable foundation before category mapping and database writes introduce
  more irreversible decisions.

## Block 2 - Local UI Historical Excel Preview

Accepted:

- Add an `Importar` tab to the local Streamlit UI.
- Let the user upload a `.xlsx` workbook and select the sheet name, defaulting
  to `Registro`.
- Copy the uploaded workbook only to a temporary file for parsing.
- Delete the temporary copy after preview parsing completes.
- Reuse `HistoricalExcelDryRunImporter` for the UI preview.
- Show candidate count, source category count, ignored row count, source hash
  prefix, monthly totals, source-category totals, and the first transaction
  candidates.
- Keep this workflow preview-only: no `ImportBatch`, source rows, transactions,
  categories, or mappings are written to the database.
- Do not persist uploaded workbook files or commit workbook fixtures from real
  personal finance data.

Deferred:

- Mapping source categories to LXCell categories.
- Duplicate detection against existing LXCell transactions.
- User confirmation before database writes.
- Import batch audit records.
- Post-import validation against historical monthly tracking totals.

Rationale:

- The user needs a visible entry point for historical Excel review before
  committing to irreversible database import behavior.
- Keeping the first UI workflow read-only preserves the safety rule that
  historical spreadsheets are source material and database writes require an
  explicit later design step.

## Block 3 - Dry-Run Validation Against Seguimiento

Accepted:

- Read the `Seguimiento` sheet during historical Excel preview when present.
- Treat `Registro` as the source of transaction candidates and `Seguimiento` as
  the trusted historical aggregate reference.
- Validate only the first `Seguimiento` table for now: monthly spending totals
  by category.
- Exclude income categories from validation against that first table, because
  income categories are summarized in a later `Seguimiento` table that is not
  part of this validation block.
- Stop reading that first table when the contiguous monthly rows end, before
  annual totals, accumulated surplus/deficit rows, financial-analysis tables,
  or budget-comparison tables.
- Compare source spreadsheet amounts from `Registro`, not signed LXCell
  accounting impact, against `Seguimiento`.
- Match categories by normalized category/header name.
- Support a row-oriented tracking layout with months in rows and categories in
  columns.
- Support a category-oriented tracking layout with categories in rows and
  months in columns.
- Parse month cells from dates, `YYYY-MM`, month/year strings, and month names
  when the workbook contains a single candidate year.
- Compare aggregates by month and source category.
- Preserve raw source amounts for aggregate validation.
- Aggregate raw `Registro` amounts first and round the aggregate to minor units
  only after grouping by month and source category, matching spreadsheet
  `SUM`/`SUMIFS` behavior more closely than row-by-row cent rounding.
- Ignore explicit zero-value cells in `Seguimiento` for unmatched-category
  detection, because `Registro` has no source row for a zero movement.
- Use a tolerance of one minor unit to avoid failing on one-cent display or
  floating-point differences.
- Expose matches and differences by month and category in the local UI.
- Expose unmatched Registro/Seguimiento categories at header/category level,
  not at month/category level, because categories are defined by the table
  headers while months are only row dimensions.
- Keep validation read-only: do not write import batches, transactions,
  mappings, or validation records to the database.

Deferred:

- Persisting validation results as import audit records.
- User-managed category mapping before confirmed import.
- Handling formulas with missing cached values in source workbooks.
- Workbook-specific layout overrides if future real files contain variations
  that cannot be inferred safely.

Rationale:

- Historical workbooks have been used as the user's reference system for years,
  so `Seguimiento` is the best available acceptance test for whether LXCell is
  reading and aggregating `Registro` correctly.
- A conservative comparison before database import catches parser, sign, date,
  and category-name issues while the workflow is still fully reversible.

## Block 4 - Confirmed-Import Preparation Screen

Accepted:

- Add a preparation section after historical Excel preview and `Seguimiento`
  validation.
- Require clean validation before a workbook can be considered ready for
  database import:
  - no validation differences outside tolerance;
  - no expense categories only in `Registro`;
  - no categories only in `Seguimiento`.
- Treat historical Excel workbooks as mixed-account historical sources.
- Use a technical per-profile account named `Excel histórico` for future
  confirmed imports from historical workbooks.
- Do not attempt to infer the original bank account for historical Excel rows.
- Detect previously completed imports by `source_file_hash` and
  `source_system = excel_historical`.
- Treat a changed workbook hash as a different source file.
- Build a category preparation plan from source category names:
  - reuse an existing LXCell category when the normalized source name matches;
  - create a new category when no matching category exists;
  - mark a conflict when the generated canonical key collides with a different
    existing category.
- Infer new category type from source column kind: expense columns create
  expense categories, income columns create income categories.
- Keep this block read-only. It prepares the user-facing checks for a later
  write step but does not create accounts, categories, transactions, import
  batches, or source rows yet.

Deferred:

- Actual database writes for confirmed import.
- Persisting import validation results.
- User-driven resolution UI for category conflicts.
- Reimport or replace workflows for corrected historical workbooks.

Rationale:

- Historical workbooks aggregate multiple real-world accounts, and preserving a
  technical `Excel histórico` account is more honest than inventing account
  provenance that the source workbook did not track.
- Categories may reasonably originate from the historical workbook itself, but
  name/key collisions need to be visible before writing data.
- File-hash duplicate detection prevents accidental double import while still
  allowing deliberately corrected workbooks to be treated as new source files.

## Block 5 - Normalized Transaction Semantics For Confirmed Import

Accepted:

- Historical Excel expense columns with positive source amounts should create
  outflow transactions with `transaction_type = expense`.
- Historical Excel expense columns with negative source amounts should create
  inflow transactions with `transaction_type = refund`.
- Refunds should keep the original expense category, because they reduce prior
  spending in that category rather than becoming income.
- Historical Excel income columns with positive source amounts should create
  inflow transactions with `transaction_type = income`.
- Historical Excel income columns with negative source amounts are expected to
  be rare. If present, they should create adjustment transactions, not outflow
  income transactions.
- Confirmed-import reports and budget actuals should treat refunds as negative
  expense activity for the linked expense category.

Deferred:

- Any specialized UI for reviewing rare negative income adjustments before
  import.

Rationale:

- Product returns and reimbursements are part of the user's normal historical
  expense workflow, so they need explicit refund semantics.
- Negative income should not be modeled as regular income with an inverted
  direction, because that would make income reporting harder to understand.

## Block 6 - Confirmed Historical Excel Database Import

Accepted:

- Add `HistoricalExcelImportService` for the confirmed write workflow rather
  than extending the manual-entry `AccountingService`.
- Confirmed import writes only from an already parsed `HistoricalExcelPreview`.
  The importer still opens historical workbooks read-only.
- Require explicit user confirmation before writing to the database.
- Require a clean `Seguimiento` validation before writing:
  - no differences outside tolerance;
  - no expense categories only in `Registro`;
  - no categories only in `Seguimiento`;
  - at least one transaction candidate.
- Block import if the same profile already has a completed or
  completed-with-warnings `ImportBatch` for `source_system = excel_historical`
  and the same `source_file_hash`.
- Block import if candidate normalized hashes overlap with completed historical
  source rows already stored for the same profile.
- Block import if candidate dates fall on or before the selected profile's
  `transactions_locked_until` date, unless the workflow receives explicit
  additional user approval for the protected historical period.
- Block import if one source category appears with mixed income/expense column
  semantics in the same preview.
- Create or reuse the per-profile technical account named `Excel histórico`.
- Keep `ImportBatch.account_id` null for historical Excel imports because the
  batch is a mixed-account historical source.
- Store the technical account on each created `Transaction.account_id`.
- Reuse active categories by normalized source name.
- Treat inactive category name matches as conflicts that must be mapped to an
  active compatible category before import.
- Create missing categories from source category names, with canonical keys
  generated from those names.
- Block canonical-key conflicts instead of guessing a mapping.
- Create one `Transaction`, one `ImportedTransactionSource`, and one accepted
  `ClassificationDecision` for each imported candidate.
- Store compact source-row payload JSON and normalized source hashes for
  traceability and duplicate detection.
- Mark imported transactions as `review_status = user_confirmed` because the
  confirmed workflow requires clean validation and explicit user approval.
- Use `decision_source = historical_match` for classification decisions created
  from historical Excel category columns.
- Support rollback by marking the historical `ImportBatch` as `rolled_back` and
  soft-deleting all transactions created by its source rows.
- Do not hard-delete import batches, source rows, transactions, decisions, or
  categories during rollback.
- Do not automatically deactivate categories created by a rolled-back import in
  this first workflow.
- Expose the confirmed import action in the local Streamlit import tab only
  after the preparation checks pass.

Deferred:

- Persisting aggregate validation details as dedicated audit records.
- Replace/reimport workflows for corrected historical workbooks.
- Specialized review UI for rare negative income adjustments.

Rationale:

- The existing Phase 1 schema already has enough audit tables for a first
  reversible confirmed import, so no schema migration is needed yet.
- Keeping the workflow service-level makes the multi-table write auditable and
  testable without mixing import behavior into manual-entry workflows.
- Soft rollback keeps the database explainable while allowing corrected imports
  to be attempted later.

## Block 7 - Per-Import Historical Category Mapping

Accepted:

- When a historical Excel source category does not match an existing LXCell
  category by normalized name, the local UI should ask the user what to do
  before confirmed import.
- For each source category planned for creation, the user can either:
  - create a new LXCell category from the source name;
  - map the source category to an existing active LXCell category of the same
    category type.
- For source categories with canonical-key conflicts, the user must resolve the
  conflict by mapping to an existing compatible active category before import.
- Confirmed import should pass those per-import mapping choices into
  `HistoricalExcelImportService`.
- The service should validate mapped category IDs against the selected profile,
  active status, and compatible category type before writing transactions.
- Multiple historical source categories can map to the same LXCell category.
- Mapped categories should not create new `Category` records.
- Created transactions should point directly to the selected target category.
- This first mapping workflow creates persisted `CategoryMapping` rows scoped to
  the source file hash.

Deferred:

- Year-specific mapping review screens.
- Applying saved mappings automatically to later historical workbooks.

Rationale:

- Historical category structures can differ from the current canonical category
  model. A per-import mapping step lets the user preserve the current category
  design without creating obsolete categories just because an old workbook used
  them.
- Keeping confirmed mappings scoped to the source file hash preserves strict
  import traceability without assuming that the same category name always had
  the same meaning in every workbook.

## Block 8 - Persisted File-Scoped Historical Category Mappings

Accepted:

- Add `category_mappings` to the initial local schema.
- Historical Excel mappings are scoped by:
  - `user_profile_id`;
  - `source_system`;
  - `source_file_hash`;
  - normalized `source_category_key`.
- Store the original `source_file_name`, original `source_category_name`,
  normalized `source_category_key`, source column kind, target LXCell category,
  creating import batch, mapping status, notes, and timestamps.
- Enforce uniqueness for one mapping per profile, source system, source file
  hash, and source category key.
- Confirmed historical imports should create one confirmed mapping per source
  category, whether the category was reused by name, manually mapped, or newly
  created.
- Multiple source categories in one file may point to the same target category.
- Imported transactions still point directly to the target category.
- `ImportedTransactionSource` still preserves source-row details, including
  source category in `payload_raw_json`.
- The local UI may suggest a target category for an unresolved source category
  by looking at confirmed mappings from previous files with the same normalized
  source category key.
- Suggested mappings are preselected only as user-facing assistance. They are
  not applied silently; the confirmed import still writes mappings for the
  current file hash.
- Suggestions must be compatible with the source column type and target only
  active categories of the same category type.

Deferred:

- Automatically applying historical mappings without user review.
- Editing or superseding existing mapping rows.
- A dedicated mapping management screen.

Rationale:

- File-scoped mappings provide precise auditability for old workbook imports:
  the database can answer how each source category in each file was interpreted.
- Cross-file suggestions reduce repetitive work while preserving the rule that
  old category names may not mean the same thing in every workbook.
