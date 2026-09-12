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

Implication: SQLite foreign keys must be enabled on connections so profile-isolation constraints are actually enforced in tests and local development. At the time of this decision, repositories, services, import behavior, and reporting behavior were still separate follow-up work.

## 2026-08-22 - Phase 1 Initial Repository Boundary

Decision: implement an initial `AccountingRepository` in `src/lxcell/repositories/accounting_repository.py` that receives an already-open SQLAlchemy `Session`, adds and queries the reviewed Phase 1 ORM entities, and leaves transaction commit/rollback ownership to `session_scope`.

Reason: keeping transaction boundaries outside the repository makes persistence behavior easier to reason about and test. A single small repository is enough while the Phase 1 schema is still compact, and explicit method arguments avoid introducing DTO classes before there is enough pressure for another abstraction.

Implication: repository reads that expose user-owned records should filter by `user_profile_id` where applicable. The repository should not contain classification automation, import workflows, reporting calculations, or destructive transaction deletion behavior; those remain later service-level design work.

## 2026-08-22 - Phase 1 Initial Accounting Service

Decision: implement an initial `AccountingService` for manual accounting workflows. Manual transactions entered by the user are stored as `user_confirmed`, including entries without a category. Both categorized and uncategorized manual entries create manual-entry import traceability through `ImportBatch` and `ImportedTransactionSource`.

Reason: a user-entered transaction has already been intentionally reviewed enough to become part of the ledger. Missing category information should remain visible as uncategorized data, not as an automatic pending-review state.

Implication: categorized manual entries create accepted `manual_user` classification decisions. Uncategorized manual entries are confirmed transactions without a classification decision until the user later assigns a category. Later manual classification confirmations supersede prior decisions and update the active transaction fields. Bank and historical imports can still enter as pending review in future import workflows.

## 2026-08-22 - Phase 1 Minimum Reporting Service

Decision: implement a minimal `ReportingService` in `src/lxcell/services/reporting_service.py` for date-range cashflow and category totals. Reports use `transaction_date`, filter by `user_profile_id`, exclude soft-deleted transactions, exclude ignored transactions, and exclude transfers by default.

Reason: LXCell needs early accounting behavior that can be verified independently of UI, imports, and budget reports. Keeping the first reporting layer small makes direction handling, soft deletion, ignored records, and transfer exclusion explicit before adding budget-vs-actual calculations.

Implication: cashflow summaries return positive inflow, outflow, and neutral buckets in minor units, with `net_minor = inflow_minor - outflow_minor`. Category totals use signed minor units so inflows are positive, outflows are negative, and neutral movements contribute zero. Budget reporting remains a later block.

## 2026-08-22 - Phase 1 Budget Actual Reporting

Decision: extend `ReportingService` with minimum budget-vs-actual summaries for active budgets and their budget lines. The report compares each planned budget-line amount to actual transaction activity for that same category in a date range, defaulting to the budget start and end dates.

Reason: budget-vs-actual is one of the core spreadsheet replacement workflows, and it can now be tested safely on top of the ORM, repository, accounting service, and minimum reporting rules.

Implication: budget actuals inherit the minimum reporting filters: scoped by `user_profile_id`, inclusive `transaction_date` range, excluded soft-deleted records, excluded ignored records, and transfers excluded by default. `actual_amount_minor` is positive in the meaning of the budget line: outflows count positively for expense-like categories, and inflows count positively for income categories. Activity in categories without budget lines is reported separately as unbudgeted actuals with planned amount `0`. Uncategorized activity is reported separately with amount and transaction count as a review signal. Monthly rollups, rollover behavior, and SQL aggregate optimization remain deferred.

## 2026-08-22 - Phase 1 Local CLI For Manual Entry

Decision: add a minimal local command line interface in `src/lxcell/cli.py` for initializing a SQLite database, creating profiles/accounts/categories, recording manual transactions, and listing transactions.

Reason: manual data entry is now more valuable than additional reporting refinements because it lets LXCell start accumulating real local records through the reviewed ORM, repository, service, and audit-trail paths.

Implication: the default local database path is `data/lxcell.db`, and `data/` remains ignored by git. The CLI uses `AccountingService` for manual transactions so manual entries keep the same confirmation, classification, and import-source audit behavior as the service layer. Bank/CSV/Excel importers remain separate future work.

## 2026-08-22 - Phase 1 Local Streamlit UI

Decision: add a minimal local Streamlit UI in `src/lxcell/ui/streamlit_app.py` for creating profiles, accounts, categories, recording manual transactions, viewing transactions, editing transactions, soft deleting transactions, viewing categories, editing categories, and removing categories from active use.

Reason: a UI is more practical than the CLI for day-to-day manual entry. The backend now has enough reviewed behavior to expose a small local interface without inventing new financial logic in the presentation layer.

Implication: the UI uses `data/lxcell.db` by default and routes writes through `AccountingService` and `AccountingRepository`. Successful writes show a visible confirmation after Streamlit reruns. If a manual transaction matches an existing registered transaction exactly by date, account, category, description, amount, currency, direction, transaction type, and payment method, the UI asks for explicit duplicate confirmation before writing another row. Transaction edits are made directly in the transactions table and then saved in batch. Classification-changing edits supersede previous classification decisions and create a new accepted manual decision when a category is assigned. Transaction delete actions are soft deletes through `transactions.is_deleted`, not hard deletes. Category edits are made directly in a categories table and can update name, type, display order, and active state. Category delete actions set `categories.is_active = false`, preserving historical transaction links; inactive categories disappear from the normal UI and remain visible only in the advanced category view. In the advanced category view, `estado` is read-only and `acción` is the editable user intent: active categories can be removed from active use, and inactive categories can be reactivated. `canonical_key` is generated automatically from the name on creation, preserved during normal renames, and shown/editable only in an advanced view. It remains a local Phase 1 interface, not the final product UI. Importers, richer review screens, and polished reporting remain later work.

## 2026-08-22 - Phase 2 Historical Excel Dry-Run Preview

Decision: begin historical Excel import with a read-only dry-run parser in `src/lxcell/importers/excel_historical.py`. The parser opens the `Registro` sheet with `openpyxl`, detects a date header row, treats numeric category columns as historical transaction candidates, computes source file hash metadata, and returns preview summaries without writing to the database. Source spreadsheet amounts are preserved separately from signed accounting impact. Expense columns treat positive source amounts as outflows and negative source amounts as inflows for refunds. Income columns treat positive source amounts as inflows and negative source amounts as outflows for reversals. Income columns are detected from green header fill first, then from generic income-like header names.

Reason: historical workbooks are trusted source material and should not be mutated or imported blindly. A dry-run preview lets the user inspect source categories, parsed candidate transactions, monthly/category totals, and ignored rows before any `ImportBatch` or `Transaction` records are created.

Implication: Phase 2 is now in progress. The first importer produces in-memory `HistoricalExcelPreview` and `HistoricalExcelTransactionCandidate` objects only. Category mapping, duplicate detection, confirmed DB import, budget import, validation against `Seguimiento`, and confirmed-import UI behavior remain separate follow-up blocks. Real personal finance workbooks must remain uncommitted; tests use anonymized synthetic workbooks.

Detailed review log: `docs/phase_2_historical_excel_import.md`.

## 2026-08-23 - Phase 2 Local Historical Excel Preview UI

Decision: expose the historical Excel dry-run parser in the local Streamlit UI through an `Importar` tab. The UI accepts a `.xlsx` upload, reads a selected sheet name, writes only a temporary copy for parsing, displays preview metrics and candidate tables, and deletes the temporary copy after parsing.

Reason: the user needs a visible workflow for checking whether historical workbooks are understood by LXCell before category mapping and database import are designed.

Implication: the local import UI remains preview-only. It does not create import batches, transactions, category mappings, or validation rows. Confirmed database import, duplicate handling, source-category mapping, and persisted validation records remain later Phase 2 blocks.

## 2026-08-23 - Phase 2 Seguimiento Aggregate Validation

Decision: during historical Excel preview, read the `Seguimiento` sheet when present and compare its first monthly spending-by-category table against expense aggregates calculated from `Registro`. Income categories from `Registro` are intentionally excluded from this first-table validation because historical income summaries live in a later `Seguimiento` table that is deferred. The comparison uses source spreadsheet amounts, matches categories by normalized names, stops before annual totals and later analysis/budget tables, compares by month and source category, aggregates raw `Registro` amounts before rounding to minor units, and allows a one-cent tolerance.

Reason: the historical workbooks are the trusted reference system. Comparing LXCell's parsed `Registro` aggregates against `Seguimiento` gives an early acceptance test for date parsing, category matching, amount signs, and aggregation behavior before any records are written.

Implication: validation results are displayed in the local UI as matches and differences by month/category, plus unmatched expense categories at header level only. This remains dry-run only: no import batches, transactions, mappings, or validation rows are persisted yet. Workbooks with formulas must have cached values available for `openpyxl` `data_only=True` reads.

## 2026-08-23 - Phase 2 Historical Import Preparation

Decision: add a read-only preparation step before confirmed historical Excel import. Historical Excel rows are treated as mixed-account historical data and will use a technical per-profile account named `Excel histórico` when the write step is implemented. Category names from the workbook can seed the user's category set: existing categories are reused by normalized name, missing categories are planned for creation, and canonical-key collisions are shown as conflicts. Completed imports are detected by profile, `source_system = excel_historical`, and source file hash.

Reason: historical workbooks did not track the originating bank account per movement, so a technical account is more accurate than inventing provenance. Category creation from the workbook matches the expected migration path for users whose category set already lives in Excel. File-hash checks prevent accidental double import without blocking corrected workbooks whose contents have changed.

Implication: the UI can now show whether an import is ready, which technical account will be used, which categories will be reused or created, and whether the file hash has already been imported. The actual database write remains deferred until the final confirmation workflow is implemented.

## 2026-08-23 - Historical Excel Refund And Adjustment Semantics

Decision: when historical Excel imports are confirmed into the database, expense columns with negative source amounts should become inflow transactions with `transaction_type = refund` and the original expense category. Income columns with negative source amounts are rare and should become adjustment transactions, not outflow income transactions.

Reason: product returns and reimbursements reduce spending in the original expense category, while negative income represents a correction-like case that should not be mixed into ordinary income semantics.

Implication: confirmed-import reporting and budget actuals should treat refunds as negative expense activity for the linked expense category.

## 2026-08-23 - Phase 2 Confirmed Historical Excel Import

Decision: implement confirmed historical Excel database import in `HistoricalExcelImportService`. The workflow writes from an already parsed `HistoricalExcelPreview`, requires clean `Seguimiento` validation and explicit user confirmation, creates or reuses the per-profile technical account `Excel histórico`, keeps `ImportBatch.account_id` null, reuses or creates categories from source category names, creates one transaction/source/accepted classification decision per candidate, blocks duplicate file hashes and normalized source-row overlaps, and exposes the action in the local Streamlit import tab only after preparation checks pass.

Reason: the existing Phase 1 schema already supports a first auditable import without new tables. A dedicated service keeps the multi-entity workflow separate from manual entry and makes rollback behavior testable.

Implication: historical Excel imports can now write confirmed transactions into the local database while preserving source-row traceability. Rollback marks the import batch as `rolled_back` and soft-deletes created transactions; it does not hard-delete audit rows or automatically deactivate categories. Persisted validation records, user-managed source category mappings, and replace/reimport workflows remain deferred.

## 2026-08-23 - Per-Import Historical Category Mapping

Decision: before confirmed historical Excel import, allow source categories that would otherwise be created or blocked by canonical-key conflict to be mapped to existing active LXCell categories of the same category type. The mapping choice is passed into `HistoricalExcelImportService` and is persisted as part of the confirmed file-scoped import mapping audit.

Reason: older workbooks can contain categories that were later merged or removed from the canonical category model. Mapping them during import prevents obsolete categories from being recreated while preserving transaction-level traceability.

Implication: multiple historical source categories can point to the same current LXCell category, and imported transactions will use that target category directly. Reusable mapping management beyond file-scoped import records remains deferred.

## 2026-08-23 - File-Scoped Historical Category Mapping Records

Decision: add persisted `category_mappings` for historical Excel imports, scoped by profile, source system, source file hash, and normalized source category key. Confirmed imports create mapping rows for every source category in the file, whether the category was reused, manually mapped, or newly created. The UI can suggest target categories from confirmed mappings in previous files with the same normalized source category key, but suggestions remain user-reviewed and are not applied silently.

Reason: mappings need durable auditability at the file level because the same source category name may not mean exactly the same thing across different historical workbooks. Suggestions are still useful for repeated migrations, but they should assist rather than override the user's review.

Implication: LXCell can now answer how each category in each imported workbook was interpreted. Multiple source categories can map to one target category. Automatic mapping application, mapping editing/superseding, and a dedicated mapping management screen remain deferred.

## 2026-08-23 - Inactive Categories In Historical Imports And Transaction UI

Decision: transaction tables should display inactive accounts and categories when historical transactions still reference them, labeling them as eliminated instead of showing blank category cells. Confirmed historical Excel imports should not silently reuse inactive categories by normalized name; inactive name matches should be treated as conflicts that require mapping to an active compatible category before import.

Reason: inactive categories preserve historical links, but hiding them in the transaction table makes categorized transactions look uncategorized. Reusing inactive categories during new imports can also hide the fact that the user intentionally removed or merged that category.

Implication: old transactions remain traceable to inactive categories, while future historical imports ask the user to resolve old category structures into the active category model.

## 2026-08-23 - Phase 1 Complete As Local Accounting Foundation

Decision: mark Phase 1 as complete. LXCell now has a reviewed local SQLite and SQLAlchemy accounting foundation with user profiles, accounts, categories, transactions, import batches, imported source rows, classification rules and decisions, budgets, budget lines, file-scoped category mappings, repository/service boundaries, manual-entry workflows, soft deletion, cashflow reporting, category totals, budget actuals, a minimal CLI, a local Streamlit UI, and focused tests.

Reason: the original Phase 1 goal was a Python core that can create, store, query, and summarize transactions independently of a GUI. That foundation is now implemented and tested. Remaining work such as richer import behavior, spreadsheet-equivalent reporting, classification automation, polished review screens, and rollover behavior belongs to later roadmap phases rather than blocking the core model.

Implication: future work should treat Phase 1 as the stable local accounting foundation. Design changes to the core schema remain possible, but they should be driven by Phase 2+ requirements and documented as migrations or follow-up decisions, not as unfinished Phase 1 setup.

## 2026-09-12 - Phase 4 Statement Import Direction

Decision: start Phase 4 with preview-first imports for user-provided bank and card statement files. Statement imports must require a user-selected active account, preserve file and row audit metadata through `ImportBatch` and `ImportedTransactionSource`, block repeated completed file imports by hash, detect row-level duplicates through normalized hashes, and write only after explicit user confirmation.

Reason: statement files represent known account activity, unlike historical Excel workbooks that mixed account provenance. Requiring a selected account keeps reports, duplicate detection, and audit trails scoped correctly. A preview-first flow preserves the trust model established during historical Excel imports.

Implication: `ImportBatch.account_id` is required for bank and card statement imports, even though it remains nullable for manual entries and historical Excel. The original statement file is not stored in the database or committed to the repository. The first implementation should use anonymized fixtures and can begin with one concrete export format before generalizing column mapping.

Detailed review log: `docs/phase_4_statement_import.md`.

## 2026-09-12 - Conservative Statement Classification

Decision: the first statement import classifier should use deterministic, high-precision rules against existing active categories. Auto-assignment is allowed only when a rule or repeated user-confirmed history has high confidence, no competing category, compatible direction/type, and enough traceability to create an accepted `ClassificationDecision`. Lower-confidence results should be suggestions, and weak matches should leave the transaction uncategorized for review.

Reason: the useful goal is to reduce repetitive manual categorization without silently polluting the ledger. Bank descriptions are not category definitions, so statement imports should not create categories from source text. Deterministic classification keeps behavior explainable before AI or merchant normalization are introduced.

Implication: imported transactions may receive a category automatically while still keeping `review_status = pending_review` until the user reviews the transaction. Suggested classifications should be visible in the UI and recorded append-only. User corrections supersede previous decisions rather than deleting them. Merchant normalization, AI classification, transfer matching, and broader rule management remain later work.
