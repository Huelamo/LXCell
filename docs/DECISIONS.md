# LXCell Decisions

This file records durable project decisions so future work can recover the reasoning without relying on conversation memory.

## 2026-08-05 - Product And Repository Identity

Decision: use `LXCell` as the project and local repository name going forward.

Reason: the GitHub repository has moved to `Huelamo/LXCell`, and the local project folder has been renamed from `myFinances` to `LXCell`. Aligning documentation with the repository name avoids confusion in future work.

## 2026-08-05 - Preserve Pre-AI Code Baseline

Decision: preserve the existing code before AI-assisted changes in branch `codex/pre-ai-baseline`.

Reason: the project already had a working prototype and user-authored changes. Keeping a named baseline makes it easy to inspect or restore the prior state without confusing it with future "current" work.

Commit: `4787bc5` (`Preserve pre-AI intervention baseline`).

## 2026-08-05 - Historical Excel Files Are Read-Only

Decision: existing personal finance Excel workbooks must never be modified in place.

Reason: they are the user's financial history and source of truth for migration validation. Any accidental write would undermine trust in the migration.

Implementation note: `.xlsx` and `.xls` files are ignored by default. If spreadsheet fixtures are needed for tests, use anonymized files under `tests/fixtures/`.

## 2026-08-05 - 2026 Categories Are Canonical

Decision: use the 2026 category structure as the forward-looking category model.

Reason: the user has intentionally evolved categories based on what produces useful financial insight. For example, separating restaurants and leisure did not provide enough value, so the 2026 combined category is preferred.

Implication: historical imports may need a category mapping layer before year-over-year comparisons.

## 2026-08-05 - Auditability Over Automation

Decision: prioritize traceability and reviewability over aggressive automatic classification.

Reason: incorrect financial records are worse than a little extra manual review. The system should classify obvious repeated transactions automatically, but ambiguous cases should remain pending until reviewed.

Implication: every imported transaction should retain source metadata, classification status, and the rule or mechanism that assigned its category.

## 2026-08-05 - Database As Source Of Truth

Decision: use the application database as the source of truth once migration starts. Excel files are historical import sources and may later be export targets.

Reason: a normalized database scales better for transaction-level imports, multiple accounts, multiple users, deduplication, and audit trails.

Current leaning: SQLite for the first serious local version because it is free, local, portable, reliable, and easy to back up. This remains open to revision if requirements outgrow it.

## 2026-08-05 - Spanish UI, English Code

Decision: user-facing interface text should be in Spanish; code, comments, internal names, and tests should be in English.

Reason: this keeps the application comfortable for Spanish-speaking users while preserving conventional code readability.

## 2026-08-13 - Normalized Transaction Model

Decision: design LXCell around a normalized transaction model instead of reproducing the yearly spreadsheet layout directly.

Reason: one row per day with categories as columns does not scale for imported bank statements, multiple accounts, duplicate detection, transaction-level auditability, or user review workflows.

Implication: historical Excel workbooks should be imported read-only and converted into one transaction per financial movement, with category mappings and validation against existing spreadsheet totals.

## 2026-08-13 - Public Repository Privacy

Decision: documentation, examples, tests, fixtures, comments, and commit messages must avoid personal or sensitive financial details because the repository is public.

Reason: project documentation should preserve product and architecture intent without exposing real people, family relationships, account purposes, financial institutions tied to personal usage, merchants, statement filenames, amounts, debts, salary details, or raw exports.

Implication: use anonymized placeholders such as `Primary user`, `Shared account`, `Bank A`, and `Merchant A`. Real financial institutions may appear only when describing generic integrations or adapters, not the user's personal financial setup.

## 2026-08-13 - Phase 1 Persistence Stack

Decision: use SQLite with SQLAlchemy 2.x for the Phase 1 implementation.

Reason: SQLite keeps LXCell local, free, portable, and easy to back up. SQLAlchemy provides a structured Python layer for tables, relationships, and repositories while keeping the storage engine lightweight.

Implication: Alembic remains deferred until schema migrations become necessary. Initial tests and local development can create the schema from SQLAlchemy metadata.

## 2026-08-13 - Transaction Amount Representation

Decision: store `amount_minor` as a non-negative integer and use `direction` to represent inflow or outflow.

Reason: integer minor units avoid floating point drift, and an explicit direction field makes statement imports easier to normalize across source formats.

Implication: reports and calculations must combine `amount_minor` with `direction`; they must not assume signed stored amounts.

## 2026-08-13 - Phase 1 Minimum Schema Scope

Decision: the first implementation schema should include `user_profiles`, `accounts`, `categories`, `transactions`, `import_batches`, `imported_transaction_sources`, `classification_rules`, `classification_decisions`, `budgets`, and `budget_lines`.

Reason: this keeps the first version small enough to understand and review while still supporting transaction imports, basic categorization, budgets, and auditability.

Implication: `transaction_splits`, `transfer_links`, `savings_goals`, `merchants`, `category_mappings`, `excel_workbook_imports`, and `import_validation_issues` are deferred from the first schema. `merchants` remain a planned concept for automation and should be revisited before classification automation grows beyond simple description-based rules.

## 2026-08-17 - Phase 1 Package Structure

Decision: use `src/lxcell/` as the main application package. Keep the existing prototype code under `src/lxcell/engine/` and existing enums under `src/lxcell/enums/` during Phase 1.

Reason: the `src` layout makes the new package explicit while keeping legacy prototype code available during the transition.

Implication: new Phase 1 code should live beside, not inside, the legacy engine. The legacy engine remains preserved until replacement behavior is implemented and reviewed.

## 2026-08-17 - Persistence Layer Naming

Decision: use `src/lxcell/db/` for SQLAlchemy infrastructure and ORM model definitions, and `src/lxcell/repositories/` for persistence operations used by application services.

Reason: `db/` describes database mechanics such as SQLAlchemy base classes, engines, sessions, and table mappings. `repositories/` describes the application's data access layer in business terms, without exposing SQLAlchemy details to services and UI code.

Implication: application services should depend on repositories rather than directly constructing SQLAlchemy queries unless a future design review changes this boundary.

## 2026-08-17 - ORM As Initial Domain Model

Decision: use SQLAlchemy ORM classes as the main Phase 1 accounting model for now.

Reason: this avoids duplicating financial concepts across separate domain and persistence classes while the model is still small and actively evolving.

Implication: separating pure business-domain classes from ORM classes remains an open future option if LXCell needs stricter business invariants, easier non-database testing, or a cleaner persistence boundary.
