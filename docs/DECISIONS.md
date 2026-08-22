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

## 2026-08-17 - Phase 1 Schema Conventions

Decision: use integer autoincrement primary keys, UTC-style `created_at` and `updated_at` timestamps, plural snake_case table names, singular PascalCase Python classes, conservative nullable fields, and minimum database constraints from the first schema implementation.

Reason: these conventions make the schema simple, auditable, and consistent while keeping the implementation approachable.

Implication: new enum values should start in `src/lxcell/enums/core_enums.py`. Financially meaningful deleted transactions should use `transactions.is_deleted`; accounts and categories should use `is_active` for visibility and lifecycle management.

## 2026-08-17 - Phase 1 Core Identity Entities

Decision: `UserProfile` should use `is_active` instead of `archived_at`; `Account.user_profile_id` and `Category.user_profile_id` should point to `UserProfile.id`; account and category names should be unique within each user profile.

Reason: this keeps profile lifecycle state consistent with accounts and categories, and avoids ambiguous account or category names inside one profile.

Implication: `shared` is not an `account_type`; shared ownership is represented only through `ownership_type = shared`. Category `canonical_key` values should be generated by the application and remain unique per user profile.

## 2026-08-17 - Phase 1 Transaction Shape

Decision: `Transaction` should include `user_profile_id` directly, keep `account_id` required, use `description_clean` and `description_raw`, include nullable `payment_method`, and limit `review_status` to `pending_review`, `user_confirmed`, and `ignored`.

Reason: direct profile ownership improves multi-profile isolation and reporting. Clean/raw description naming improves traceability. Payment method captures how a movement was executed without overloading financial transaction type. Classification mechanics belong in `ClassificationDecision`, not in transaction review status.

Implication: transaction account and category links must be synchronized with `user_profile_id` using database constraints where practical. Historical records without known account provenance should use an explicit unknown historical account for the profile instead of nullable `Transaction.account_id`.

## 2026-08-17 - Phase 1 Import Traceability Shape

Decision: `ImportBatch.account_id`, `source_file_name`, and `source_file_hash` can be null. Manual entries use `source_system = manual_entry` and null file fields. Imported source fields should place qualifiers after the main concept, such as `date_raw`, `amount_raw`, `payload_raw_json`, `row_number_source`, and `record_id_source`.

Reason: some historical sources can contain transactions from multiple accounts or omit account provenance, while manual entries and API sources may have no source file. Putting qualifiers after the main concept keeps related fields easier to scan.

Implication: file hashes should still be required when a real file import provides enough information to hash the source. Import audit records should preserve compact raw source values without storing original personal finance files in the database by default.

Detailed review log: `docs/phase_1_schema_review.md`.

## 2026-08-17 - Decision Memory Discipline

Decision: accepted design decisions should be recorded proactively in repository documentation as part of the AI-assisted development workflow.

Reason: LXCell is a long-running project with detailed financial and architectural decisions. Relying on conversation memory alone risks losing nuance across sessions, context compaction, model changes, or future agents.

Implication: `docs/DECISIONS.md` should keep concise durable summaries, detailed review documents should capture field-level rationale and alternatives, and living design documents should be updated when decisions change the intended implementation. Documentation should remain structured and privacy-safe rather than trying to preserve every conversational sentence.

## 2026-08-17 - Phase 1 Classification Traceability

Decision: Phase 1 classification rules should only suggest classifications. `ClassificationRule` should include `match_field`; `ClassificationDecision` should be able to record suggested or accepted `category_id`, `transaction_type`, and `payment_method`.

Reason: auditability is more important than aggressive automation. Keeping rule output in append-only decision records preserves why a transaction was classified without hiding uncertainty.

Implication: `auto_apply` is included as a future extension point but remains false in Phase 1 behavior. Merchant-based classification rules are deferred until merchant normalization is introduced.

## 2026-08-17 - Phase 1 Budget Scope

Decision: Phase 1 budgets should support `monthly` and `annual` periods. `Budget.name` should be unique per user profile. `BudgetLine` should be unique by `budget_id` and `category_id`, and budget lines should be allowed for expense, income, saving, and investment categories.

Reason: income, savings, and investments are part of the basic budgeting workflow, not later reporting extras. Unique budget lines avoid ambiguous planned amounts for the same category inside one budget.

Implication: keep `rollover_policy` in the schema, but only `none` is supported in Phase 1 behavior. Additional rollover policies remain open for later design.

## 2026-08-17 - Phase 1 Enum Implementation

Decision: require Python 3.11+ and implement new Phase 1 enums with standard-library `StrEnum` in `src/lxcell/enums/core_enums.py`.

Reason: `StrEnum` behaves like strings, which keeps DataFrame handling and serialization cleaner than `str, Enum` patterns that often require explicit `.value` access.

Implication: CI should run on Python 3.11+. Keep `TransactionSourceType` and `ImportSourceSystem` separate because transaction origin and import source system are related but distinct concepts. `PaymentMethod` includes `peer_to_peer` for app-based person-to-person payments.

## 2026-08-22 - Phase 1 ORM Implementation Details

Decision: implement the initial Phase 1 SQLAlchemy ORM classes in `src/lxcell/db/models.py` using typed SQLAlchemy 2.x mappings, Python-side UTC timestamps, string-valued `StrEnum` persistence, and minimum database constraints. Store classification confidence as exact `Numeric(5, 4)` values from `0.0000` to `1.0000`. Require `ClassificationDecision.decided_by`, using `system` for non-human decisions.

Reason: typed ORM classes keep the implementation close to the reviewed schema while avoiding a separate domain-object layer for now. Exact numeric confidence avoids floating point artifacts in audit records. A non-null `decided_by` makes classification history easier to read because system-generated decisions are explicit rather than represented by a missing actor.

Implication: SQLite foreign keys must be enabled on connections so profile-isolation constraints are actually enforced in tests and local development. Phase 1 remains in progress; repositories, services, import behavior, and reporting behavior are still separate work.

## 2026-08-22 - Phase 1 Initial Repository Boundary

Decision: implement an initial `AccountingRepository` in `src/lxcell/repositories/accounting_repository.py` that receives an already-open SQLAlchemy `Session`, adds and queries the reviewed Phase 1 ORM entities, and leaves transaction commit/rollback ownership to `session_scope`.

Reason: keeping transaction boundaries outside the repository makes persistence behavior easier to reason about and test. A single small repository is enough while the Phase 1 schema is still compact, and explicit method arguments avoid introducing DTO classes before there is enough pressure for another abstraction.

Implication: repository reads that expose user-owned records should filter by `user_profile_id` where applicable. The repository should not contain classification automation, import workflows, reporting calculations, or destructive transaction deletion behavior; those remain later service-level design work.

## 2026-08-22 - Phase 1 Initial Accounting Service

Decision: implement an initial `AccountingService` for manual accounting workflows. Complete manual transactions with a user-selected category are stored as `user_confirmed`; manual transactions without a category remain `pending_review`. Both paths create manual-entry import traceability through `ImportBatch` and `ImportedTransactionSource`.

Reason: a user-entered transaction with an explicit category has already been reviewed by the user and should not require a second review step. Incomplete manual entries still need review, but they must not become audit-trail exceptions.

Implication: confirmed manual entries create accepted `manual_user` classification decisions. Later manual classification confirmations supersede prior decisions and update the active transaction fields. Bank and historical imports can still enter as pending review in future import workflows.

## 2026-08-22 - Phase 1 Minimum Reporting Service

Decision: implement a minimal `ReportingService` in `src/lxcell/services/reporting_service.py` for date-range cashflow and category totals. Reports use `transaction_date`, filter by `user_profile_id`, exclude soft-deleted transactions, exclude ignored transactions, and exclude transfers by default.

Reason: LXCell needs early accounting behavior that can be verified independently of UI, imports, and budget reports. Keeping the first reporting layer small makes direction handling, soft deletion, ignored records, and transfer exclusion explicit before adding budget-vs-actual calculations.

Implication: cashflow summaries return positive inflow, outflow, and neutral buckets in minor units, with `net_minor = inflow_minor - outflow_minor`. Category totals use signed minor units so inflows are positive, outflows are negative, and neutral movements contribute zero. Budget reporting remains a later block.

## 2026-08-22 - Phase 1 Budget Actual Reporting

Decision: extend `ReportingService` with minimum budget-vs-actual summaries for active budgets and their budget lines. The report compares each planned budget-line amount to actual transaction activity for that same category in a date range, defaulting to the budget start and end dates.

Reason: budget-vs-actual is one of the core spreadsheet replacement workflows, and it can now be tested safely on top of the ORM, repository, accounting service, and minimum reporting rules.

Implication: budget actuals inherit the minimum reporting filters: scoped by `user_profile_id`, inclusive `transaction_date` range, excluded soft-deleted records, excluded ignored records, and transfers excluded by default. `actual_amount_minor` is positive in the meaning of the budget line: outflows count positively for expense-like categories, and inflows count positively for income categories. Activity in categories without budget lines is reported separately as unbudgeted actuals with planned amount `0`. Monthly rollups, rollover behavior, and SQL aggregate optimization remain deferred.
