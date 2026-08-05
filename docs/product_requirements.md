# MyFinances Product Requirements

Last updated: 2026-08-05

## Purpose

MyFinances replaces yearly personal finance spreadsheets with a local, auditable application for transaction ingestion, categorization, budgeting, and financial review.

The project starts from existing yearly Excel workbooks and a small Python prototype. The target is not a one-to-one clone of the spreadsheet layout, but a scalable system that preserves the useful accounting logic behind it.

## Product Principles

- Auditability has priority over maximum automation.
- The user must be able to trace each imported transaction back to its source.
- The system should classify obvious repeated transactions automatically.
- Ambiguous classifications should remain pending until reviewed.
- It is better to require more user review than to introduce many categorization errors.
- User-facing screens, labels, messages, and reports must be in Spanish.

## Users

MyFinances should support multiple users or profiles over time.

Known use cases:

- The primary user manages personal finances in yearly workbooks.
- The primary user also prepares a finance workbook for his mother.
- Friends and family are interested in a solution that avoids hours of manual transaction entry.

Each user profile should keep categories, budgets, accounts, import history, and transaction data isolated.

## Financial Scope

- Primary currency: EUR.
- Non-EUR spending occurs mainly while traveling and is paid through Revolut.
- Priority bank sources:
  - Revolut personal account.
  - Shared Revolut account with Maria.
  - ING account for lower-volume but high-impact expenses such as rent, mortgage, and utilities.
- The 2026 category structure is canonical for future work, even if this makes year-over-year comparisons harder.
- Earlier category structures should be mapped into the 2026 structure during migration where appropriate.

## Existing Workbook Shape

The user's yearly Excel workbooks generally contain:

- `Registro`: manual daily transaction entry, with categories as columns.
- `Presupuestos`: budget inputs, expected income, and savings goals.
- `Seguimiento`: formula-driven monthly actuals vs budget tracking.
- Additional modules such as `Hipoteca`, `Deudas`, and `Proyección inversiones`.

Known issue with the spreadsheet model: using one row per day and one column per category does not scale well for many accounts, imported statements, or transaction-level audit trails.

## Initial Automation Scope

The first automation target is user-provided bank statement import.

The system should:

- Import bank statements supplied by the user.
- Start with Revolut personal and shared Revolut exports.
- Add ING after Revolut import is stable.
- Detect duplicate transactions across repeated imports.
- Keep original import metadata for auditability.
- Learn deterministic classification rules from historical classification patterns.
- Mark uncertain transactions for manual review.

## Interface Direction

Streamlit is acceptable and should be evaluated first because it can provide a useful Python-based local UI quickly.

The interface should support:

- Importing statements.
- Reviewing and confirming classifications.
- Editing transactions.
- Managing categories and budgets.
- Viewing monthly budget vs actuals.
- Viewing savings and income summaries.

## Data Direction

The application database should become the source of truth once migration starts.

Excel files should be treated as:

- historical read-only import sources;
- validation references during migration;
- optional future export/reporting targets.

Current technical leaning: SQLite for the first serious local version because it is free, local, portable, reliable, and easy to back up.

## Open Product Questions

- Confirm whether Streamlit remains the best UI stack after the first prototype.
- Confirm whether SQLite is sufficient for the first production-grade local version.
- Define the exact 2026 category list and category mapping from older workbooks.
- Obtain anonymized sample exports for Revolut personal, Revolut shared, and ING.
- Decide how to represent shared expenses and household accounts.
