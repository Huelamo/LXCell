# Phase 4 Statement Import Design

Last updated: 2026-09-12

This document records the initial design for importing user-provided bank and
card statements into LXCell. The first supported source format is the Spanish
PDF statement layout represented by anonymized parser tests.

No real personal finance details should be added here. Use generic source names
such as `Bank A`, `Card A`, `Merchant A`, and `Sample User`.

## Review Status

Status: first read-only parser implemented.

Accepted:

- Statement imports write to a real user-selected account, not to the historical
  Excel technical account.
- The first implementation should be preview-first and read-only until the user
  explicitly confirms the import.
- Bank and card statements should only map to existing active categories.
  Statement imports should not create new categories from source data because
  bank descriptions are not category definitions.
- The first classification layer should be deterministic and conservative.
  High-confidence matches may assign categories automatically, while uncertain
  matches remain suggested or uncategorized for review.
- Every created or suggested classification should be recorded through
  `ClassificationDecision`.

Implemented:

- `PdfStatementDryRunImporter` parses the first Spanish PDF statement layout
  into in-memory candidates without database writes.
- The parser reads repeated statement table headers, operation date, value date,
  description, outgoing amount, incoming amount, and balance.
- The local Streamlit `Importar` tab can preview statement PDFs after the user
  selects an active account and source type.
- The preview shows parsed movement count, page count, parse issues, direction
  totals, first candidate rows, and completed-import file-hash warnings scoped
  to the selected account.
- Tests generate synthetic anonymized PDFs at runtime; no real statement file is
  committed.

Open:

- Whether later importers should support CSV, XLSX, or other account-specific
  exports.
- Whether the first UI should allow user-defined column mappings or begin with a
  source-specific parser for each export format.
- Whether pending imported transactions should be visually excluded from some
  dashboards until reviewed, even though current reporting includes
  `pending_review` transactions unless they are ignored.
- When to introduce merchant normalization as a separate table.
- When to introduce transfer matching between accounts.

## Product Goal

The user should be able to import statement exports instead of typing
transactions manually. The import should preserve source traceability, detect
duplicates, classify obvious recurring activity with high reliability, and leave
ambiguous records visible for user review.

## Non-Goals For The First Implementation

- Direct bank API integrations.
- Storing original statement files in the database.
- Committing real statement exports or real merchant examples.
- AI-based classification.
- Merchant normalization tables.
- Automatic transfer linking between accounts.
- Split transactions.
- Multi-file replacement or reimport workflows.

## Source Handling

Statement files are read-only source material.

Rules:

- The UI may save an uploaded file to a temporary location for parsing.
- The temporary copy must be deleted after preview or import processing.
- The original file must not be modified in place.
- The original file must not be committed.
- Tests must use anonymized fixtures under `tests/fixtures/`.

The first importer should normalize each source row into an in-memory candidate
with at least:

- source row number;
- source-provided record id, when available;
- transaction date;
- posted date, when available;
- raw description;
- clean description;
- amount in minor units;
- direction;
- currency;
- raw payload values;
- normalized row hash;
- parser warnings or errors.

## Account And Source System

Statement imports must require the user to select an existing active account for
the selected profile before previewing or confirming.

Rules:

- `ImportBatch.account_id` is required for bank and card statement imports.
- Use `source_system = bank_pdf` for bank/current-account PDF statement exports.
- Use `source_system = card_pdf` for card PDF statement exports.
- Use `source_system = bank_csv` or `card_csv` only for future CSV statement
  exports.
- Use `Transaction.source_type = bank_import` for both bank and card file
  imports unless a future enum split becomes useful.
- Imported transactions inherit `account_id`, `user_profile_id`, and account
  currency defaults from the selected account unless the source row explicitly
  provides a supported currency.

Historical Excel imports are different: they remain mixed-account historical
data and continue using the `Excel histórico` technical account.

## Preview Workflow

The first workflow should follow this shape:

1. User selects profile, account, statement source type, and uploads the file.
2. LXCell computes the source file hash.
3. LXCell parses the file into normalized candidates without writing to the
   database.
4. LXCell checks whether a completed import already exists for the same profile,
   account, source system, and file hash.
5. LXCell checks each candidate normalized hash against prior imported sources
   for the same profile and account.
6. LXCell runs deterministic classification suggestions.
7. UI shows preview metrics, blocking errors, duplicate candidates, category
   suggestions, and rows that will remain uncategorized.

Preview should be repeatable. Re-previewing the same file should not create
database rows.

## Confirmed Import Workflow

Confirmed statement import should create one `ImportBatch` and one
`ImportedTransactionSource` per parsed source row.

Rules:

- Explicit user confirmation is required before any write.
- Files with blocking parse errors cannot be imported.
- A previously completed import with the same profile, account, source system,
  and file hash blocks confirmation.
- Exact duplicate source rows should not create new transactions by default.
- Rows ignored as exact duplicates should still be traceable through
  `ImportedTransactionSource.import_action`.
- Non-duplicate rows create `Transaction` records.
- Confirmed import can use `completed_with_warnings` when rows were skipped as
  duplicates or left uncategorized.
- Rollback should soft-delete transactions created by the import and mark the
  batch as `rolled_back`; audit rows should remain.

## Transaction Defaults

The first import should use conservative defaults:

- Source outflows become `direction = outflow` and default
  `transaction_type = expense`.
- Source inflows become `direction = inflow`.
- Inflows should become `transaction_type = income` only when a deterministic
  rule or user review classifies them as income.
- Inflows that are not confidently classified should remain pending for user
  review using `transaction_type = adjustment` until corrected.
- Refunds should use `transaction_type = refund` only when a deterministic rule
  or user review links the inflow to an expense category.
- Transfers should use `transaction_type = transfer` only after deterministic
  evidence or user review.

Imported rows should start with `review_status = pending_review` even when a
high-confidence deterministic rule assigns a category. User review can later
change them to `user_confirmed` or `ignored`.

Current reporting includes `pending_review` transactions unless they are
ignored. As a result, uncategorized imported rows will appear as uncategorized
activity until reviewed.

## Classification Strategy

The first classifier should prioritize precision over coverage.

Inputs:

- active deterministic `ClassificationRule` rows for the profile;
- active categories for the profile;
- previously accepted `ClassificationDecision` rows;
- previously user-confirmed transactions;
- the normalized source description, amount, direction, and dates.

Suggested rule order:

1. Active exact or regex rules with no competing result.
2. Prior accepted classifications for the same normalized description and
   compatible direction/type, only when they are unanimous.
3. Description contains rules with strong, user-reviewed patterns.
4. Recurring transaction patterns when description and amount are stable.

Auto-assignment requirements:

- The target category must be active and belong to the same profile.
- The match must have no competing active category.
- Direction and transaction type must be compatible with the target category.
- Confidence must meet a high threshold.
- The rule must be marked `auto_apply = true`, or the learned match must come
  from repeated user-confirmed history with no conflict.

When auto-assignment is allowed:

- Set `Transaction.category_id`.
- Set the suggested `transaction_type` and `payment_method` when provided.
- Create an accepted `ClassificationDecision` with
  `decision_source = deterministic_rule`.
- Keep `Transaction.review_status = pending_review` until the user reviews the
  imported transaction itself.

When confidence is useful but not enough for auto-assignment:

- Leave `Transaction.category_id` null.
- Create a suggested `ClassificationDecision`.
- Show the suggestion in the review UI.

When there is no reliable signal:

- Leave `Transaction.category_id` null.
- Do not create a low-value classification decision.
- Show the row as uncategorized.

## Duplicate Detection

Duplicate detection should combine file-level and row-level signals.

File-level duplicate:

- Match on `user_profile_id`, `account_id`, `source_system`, and
  `source_file_hash`.
- Completed imports block reimport by default.
- Rolled-back imports do not prove the transactions still exist, but should be
  shown in the audit context before allowing reimport.

Row-level normalized hash should include:

- profile id;
- account id;
- source system;
- transaction date;
- posted date when available;
- amount minor;
- direction;
- currency;
- normalized description;
- source balance after the movement, when present;
- source-provided record id when available.

Rules:

- Stable source record ids should be preferred when present.
- Exact row hash matches should be skipped or matched to existing transactions
  by default, not duplicated.
- Near duplicates should be shown as duplicate candidates for review.
- Duplicate detection must be profile-scoped and account-scoped.

## UI Expectations

The local Streamlit UI should add a statement import path under `Importar`.

Expected preview content:

- selected account;
- file hash duplicate status;
- parsed row count;
- rows ready to import;
- rows with blocking errors;
- exact duplicate rows;
- rows with auto-assigned categories;
- rows with suggested categories;
- uncategorized rows;
- a table showing source description, date, amount, direction, suggested category,
  confidence, and duplicate status.

Expected review behavior after import:

- Imported transactions appear in `Transacciones`.
- Pending rows remain easy to filter.
- Suggested categories can be accepted or changed.
- User corrections supersede prior classification decisions.

## Required Tests For The Implementation PR

The first implementation PR should be blocked by tests for:

- parsing an anonymized statement fixture without writing to the database;
- rejecting files with missing required columns or invalid amounts;
- computing deterministic file hashes;
- computing stable normalized row hashes;
- blocking repeated completed file imports;
- detecting row-level duplicates across imports for the same account;
- not mixing duplicate detection across profiles or accounts;
- creating `ImportBatch` and `ImportedTransactionSource` audit rows on confirmed
  import;
- creating transactions only for non-duplicate rows;
- requiring explicit user confirmation before database writes;
- assigning high-confidence deterministic classifications with accepted
  decisions;
- leaving uncertain rows uncategorized with pending review;
- preserving existing classification decisions when user corrections supersede
  them;
- using synthetic/anonymized fixtures only.

## First Implementation Slice

Recommended first slice:

1. Add a source-specific parser for one anonymized PDF fixture. Done.
2. Add a read-only preview service. Partially done at importer level.
3. Expose preview in Streamlit. Done.
4. Add duplicate checks against existing import batches and source rows.
   File-hash checks are done at preview level; row-level source checks remain
   deferred until confirmed imports create statement source rows.
5. Defer confirmed write until preview behavior is trusted.

This mirrors the historical Excel approach and keeps the first bank-statement
PR small enough to review.
