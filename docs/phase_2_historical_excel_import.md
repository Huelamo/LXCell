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

Pending blocks:

- Category mapping from historical source categories to current LXCell
  categories.
- Confirmed database import.

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
