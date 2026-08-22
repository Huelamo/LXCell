# Phase 1 Schema Review

Last updated: 2026-08-22

This document records the guided schema review for the Phase 1 LXCell accounting model. It is intentionally more detailed than `docs/DECISIONS.md`, which only keeps durable decision summaries.

No real personal finance details should be added here. Use generic examples only.

## Review Status

Accepted blocks:

- Block 1: cross-table schema conventions.
- Block 2: `UserProfile`, `Account`, and `Category`.
- Block 3: `Transaction`.
- Block 4: import traceability entities.
- Block 5: classification rules and decisions.
- Block 6: budgets and budget lines.
- Block 7: Phase 1 enums.
- Block 8: initial SQLAlchemy ORM implementation details.

Pending blocks:

- Repository, service, import, and reporting behavior built on top of the ORM.

## Block 1 - Cross-Table Schema Conventions

Accepted:

- Use integer autoincrement primary keys for Phase 1 tables.
- Each table owns its own `id` sequence. IDs identify rows inside their table, not globally across all tables.
- Use `created_at` and `updated_at` timestamps on main entities.
- Treat fields ending in `_at` as timestamps.
- Use plural snake_case table names, such as `transactions` and `budget_lines`.
- Use singular PascalCase Python class names, such as `Transaction` and `BudgetLine`.
- Use conservative nullable fields. A field should be nullable only when import, review, or financial meaning requires it.
- Add minimum constraints from the first schema implementation.
- Store money as integer minor units in `amount_minor`.
- Store `amount_minor` as non-negative and use `direction` to represent inflow or outflow.
- Add new Phase 1 enums initially to `src/lxcell/enums/core_enums.py`.

Accepted lifecycle rules:

- Use `transactions.is_deleted` for soft deletion of financial movements.
- Use `accounts.is_active` and `categories.is_active` for active/inactive lifecycle state.
- Use `UserProfile.is_active` for profile lifecycle consistency.
- Do not physically delete meaningful financial history as the default behavior.

Rationale:

- Soft delete preserves auditability. A deleted transaction should stop affecting normal reports while still explaining what happened historically.
- `is_active` is sufficient for accounts, categories, and profiles in Phase 1 because inactive records can remain linked to historical transactions.

## Block 2 - UserProfile

Accepted fields:

- `id`
- `display_name`
- `default_currency`
- `locale`
- `is_active`
- `created_at`
- `updated_at`

Accepted rules:

- `UserProfile` represents an isolated financial workspace.
- All accounts, categories, transactions, imports, budgets, and classification rules belong to a user profile.
- `locale` represents display locale, such as `es_ES` for Spanish conventions in Spain.
- `locale` can default to `es_ES`.
- Use `is_active`, not `archived_at`, in Phase 1.

Rejected or deferred:

- `archived_at` is deferred because `is_active` is simpler and consistent with accounts and categories.

## Block 2 - Account

Accepted fields:

- `id`
- `user_profile_id`
- `name`
- `institution_name`
- `account_type`
- `currency`
- `ownership_type`
- `external_account_ref`
- `is_active`
- `created_at`
- `updated_at`

Accepted relationships:

- `Account.user_profile_id` points to `UserProfile.id`.
- One `UserProfile` can have many `Account` records.
- One `Account` belongs to exactly one `UserProfile`.

Accepted `account_type` values:

- `checking`
- `savings`
- `credit_card`
- `cash`
- `investment`
- `loan`
- `mortgage`
- `other`

Accepted `ownership_type` values:

- `personal`
- `shared`
- `managed_for_someone_else`

Accepted rules:

- `shared` is not an `account_type`.
- Shared ownership is represented with `ownership_type = shared`.
- `account_type` describes the financial nature of the account.
- `ownership_type` describes who the account belongs to or is managed for.
- `Account.name` should be unique per `user_profile_id`.
- A user profile can still have many accounts; the uniqueness rule only prevents two accounts with the exact same name inside the same profile.
- `external_account_ref` must never store sensitive full credentials or full account numbers.

Accepted historical import rule:

- `Transaction.account_id` should remain required.
- Historical imports without known account provenance should use an explicit unknown historical account for that profile.
- This preserves schema consistency without pretending to know the real source account.

## Block 2 - Category

Accepted fields:

- `id`
- `user_profile_id`
- `name`
- `parent_category_id`
- `category_type`
- `canonical_key`
- `display_order`
- `is_active`
- `created_at`
- `updated_at`

Accepted relationships:

- `Category.user_profile_id` points to `UserProfile.id`.
- `Category.parent_category_id` points to `Category.id` and is nullable.
- One `UserProfile` can have many `Category` records.

Accepted `category_type` values:

- `expense`
- `income`
- `transfer`
- `saving`
- `investment`
- `debt`
- `adjustment`

Accepted rules:

- `Category.name` should be unique per `user_profile_id` in Phase 1.
- `Category.canonical_key` should be unique per `user_profile_id`.
- `canonical_key` should normally be generated by the application from the category name.
- Regular users should not need to manually define `canonical_key` values.
- `parent_category_id` remains nullable so category hierarchy can be added without forcing it from the beginning.

Rationale:

- Unique category names per profile keep the UI, imports, and review workflows simpler.
- `canonical_key` protects reports and mappings if a visible category name changes later.

## Block 3 - Transaction

Accepted fields:

- `id`
- `user_profile_id`
- `account_id`
- `transaction_date`
- `posted_date`
- `description_clean`
- `description_raw`
- `category_id`
- `amount_minor`
- `currency`
- `direction`
- `transaction_type`
- `payment_method`
- `review_status`
- `source_type`
- `source_id`
- `is_duplicate_candidate`
- `is_deleted`
- `created_at`
- `updated_at`

Accepted naming rules:

- Use `description_clean`, not `description`.
- Use `description_raw`, not `original_description`.
- Put qualifiers after the main field concept.
- Examples: `description_raw`, `date_raw`, `amount_raw`, `row_number_source`.

Accepted relationships:

- `Transaction.user_profile_id` points to `UserProfile.id`.
- `Transaction.account_id` points to `Account.id` and is required.
- `Transaction.category_id` points to `Category.id` and is nullable.
- `Transaction.user_profile_id` must stay synchronized with linked account and category ownership.

Accepted synchronization rule:

- The database should prevent a transaction from referencing an account or category belonging to another user profile where practical.
- The intended implementation is to use composite database constraints or equivalent safeguards.

Accepted nullable rules:

- `posted_date` is nullable because not all sources provide a meaningful posted date.
- `description_raw` is nullable because manual entries may have no bank-source description.
- `description_clean` can be nullable during import or review.
- `description_clean` must be present before a transaction becomes user-confirmed.
- `category_id` is nullable while classification is pending.
- `payment_method` is nullable because not every source identifies it.
- `source_id` is nullable because not every source provides a stable external record ID.

Accepted `direction` values:

- `inflow`
- `outflow`
- `neutral`

Accepted `transaction_type` values:

- `expense`
- `income`
- `transfer`
- `refund`
- `fee`
- `tax`
- `saving`
- `investment`
- `debt_payment`
- `adjustment`

Accepted interpretation:

- `saving` represents movement toward liquid or near-liquid reserves.
- `investment` represents movement toward investment assets such as brokerage or fund contributions.
- A direct debit is not a `transaction_type`; it is a `payment_method`.

Accepted `payment_method` values:

- `card`
- `bank_transfer`
- `peer_to_peer`
- `direct_debit`
- `cash`
- `standing_order`
- `other`

Accepted `review_status` values:

- `pending_review`
- `user_confirmed`
- `ignored`

Rejected from `Transaction.review_status`:

- `auto_classified`
- `user_corrected`
- `ai_suggestion`

Rationale:

- Those concepts describe how classification happened, so they belong in `ClassificationDecision`, not in the transaction review status.

Accepted `source_type` values:

- `manual`
- `bank_import`
- `excel_import`
- `api`
- `other`

Accepted constraints:

- `amount_minor >= 0`.
- `currency` length is 3.
- `transaction_date` is required.
- `account_id` is required.
- `is_deleted` supports soft delete.

## Block 4 - ImportBatch

Accepted fields:

- `id`
- `user_profile_id`
- `account_id`
- `source_system`
- `source_file_name`
- `source_file_hash`
- `imported_at`
- `import_status`
- `imported_by`
- `notes`
- `created_at`
- `updated_at`

Accepted nullable rules:

- `account_id` is nullable.
- `source_file_name` is nullable.
- `source_file_hash` is nullable.
- `imported_by` is nullable in Phase 1.
- `notes` is nullable.

Accepted rationale for nullable `account_id`:

- Some historical import files can contain transactions from multiple accounts.
- Some historical import files may not identify the source account.
- Future bank statement imports can still set `account_id` when the source file clearly belongs to one account.

Accepted manual import rule:

- Manual entries use `source_system = manual_entry`.
- Manual entries use null file fields because there is no source file.

Accepted `source_system` values:

- `manual_entry`
- `bank_csv`
- `card_csv`
- `excel_historical`
- `api`
- `other`

Accepted `import_status` values:

- `pending`
- `completed`
- `completed_with_warnings`
- `failed`
- `rolled_back`

Accepted file handling rules:

- Store source file names only when a real file source exists.
- Store `source_file_hash` for file imports when possible.
- Do not store original personal finance files in the database by default.

## Block 4 - ImportedTransactionSource

Accepted fields:

- `id`
- `import_batch_id`
- `row_number_source`
- `record_id_source`
- `date_raw`
- `description_raw`
- `amount_raw`
- `currency_raw`
- `payload_raw_json`
- `normalized_hash`
- `created_transaction_id`
- `import_action`
- `created_at`

Accepted nullable rules:

- `row_number_source` is nullable for APIs or sources without row numbers.
- `record_id_source` is nullable because not every source provides a stable record ID.
- `created_transaction_id` is nullable because a source record may be ignored, matched, duplicate, or failed.

Accepted raw value rules:

- Raw import fields preserve the source-level representation for auditability.
- Raw qualifiers go after the main concept, such as `date_raw`, not `raw_date`.
- `payload_raw_json` stores compact JSON text with source columns and values where practical.

Accepted `import_action` values:

- `created_transaction`
- `matched_existing`
- `marked_duplicate`
- `ignored`
- `failed_validation`

Accepted relationship rules:

- One `ImportBatch` can have many `ImportedTransactionSource` records.
- One `ImportedTransactionSource` can create zero or one `Transaction`.
- One source record should create at most one transaction.

## Block 5 - ClassificationRule

Accepted fields:

- `id`
- `user_profile_id`
- `name`
- `rule_type`
- `match_field`
- `pattern`
- `category_id`
- `transaction_type`
- `payment_method`
- `direction`
- `amount_min_minor`
- `amount_max_minor`
- `priority`
- `confidence`
- `auto_apply`
- `is_active`
- `created_at`
- `updated_at`

Accepted relationship rules:

- `ClassificationRule.user_profile_id` points to `UserProfile.id`.
- `ClassificationRule.category_id` points to `Category.id` and is nullable if a rule only suggests non-category metadata.
- Rule category ownership must remain synchronized with `user_profile_id` where practical.

Accepted `rule_type` values:

- `description_contains`
- `description_regex`
- `amount_and_description`
- `recurring_transaction`
- `historical_match`

Accepted `match_field` values:

- `description_raw`
- `description_clean`

Accepted nullable rules:

- `category_id` is nullable.
- `transaction_type` is nullable.
- `payment_method` is nullable.
- `direction` is nullable.
- `amount_min_minor` is nullable.
- `amount_max_minor` is nullable.

Accepted behavior:

- Phase 1 classification rules should only suggest classifications.
- `auto_apply` is included as a future extension point but should remain false in Phase 1 behavior.
- Rule output should create a `ClassificationDecision`.
- Rule output should not silently overwrite a transaction.
- Rule output should not auto-confirm a transaction in Phase 1.
- Higher `priority` rules should run before lower `priority` rules.
- `confidence` represents the expected confidence of the rule output.

Deferred:

- Merchant-based rules are deferred until merchant normalization is introduced.
- `merchant_id` should not be part of the Phase 1 `ClassificationRule` schema.

## Block 5 - ClassificationDecision

Accepted fields:

- `id`
- `transaction_id`
- `category_id`
- `transaction_type`
- `payment_method`
- `decision_source`
- `classification_rule_id`
- `confidence`
- `decision_status`
- `decided_by`
- `decided_at`
- `superseded_at`
- `notes`

Accepted relationship rules:

- `ClassificationDecision.transaction_id` points to `Transaction.id`.
- `ClassificationDecision.category_id` points to `Category.id` and is nullable.
- `ClassificationDecision.classification_rule_id` points to `ClassificationRule.id` and is nullable.
- A manual decision can exist without a linked classification rule.
- A rule-based decision should link to the rule that produced it.

Accepted nullable rules:

- `category_id` is nullable when a decision only concerns `transaction_type` or `payment_method`.
- `transaction_type` is nullable.
- `payment_method` is nullable.
- `classification_rule_id` is nullable.
- `superseded_at` is nullable until a later decision replaces this one.
- `notes` is nullable.

Accepted `decision_source` values:

- `manual_user`
- `deterministic_rule`
- `historical_match`
- `ai_suggestion`
- `import_default`

Accepted `decision_status` values:

- `suggested`
- `accepted`
- `rejected`
- `superseded`

Accepted behavior:

- Every classification attempt should create an append-only decision record.
- Classification decisions can suggest or accept `category_id`, `transaction_type`, and `payment_method`.
- `auto_classified`, `user_corrected`, and `ai_suggestion` do not belong in `Transaction.review_status`.
- The active transaction values should reflect the latest accepted non-superseded decision.
- User corrections should supersede prior decisions instead of deleting them.
- AI suggestions should never be indistinguishable from user-confirmed records.

## Block 6 - Budget

Accepted fields:

- `id`
- `user_profile_id`
- `name`
- `period_type`
- `start_date`
- `end_date`
- `currency`
- `is_active`
- `created_at`
- `updated_at`

Accepted relationship rules:

- `Budget.user_profile_id` points to `UserProfile.id`.
- One `UserProfile` can have many budgets.
- One `Budget` belongs to exactly one `UserProfile`.

Accepted `period_type` values for Phase 1:

- `monthly`
- `annual`

Deferred:

- `custom` budget periods are deferred from Phase 1.

Accepted nullable rules:

- `start_date` is required.
- `end_date` is required.
- `currency` is required.
- `is_active` is required and defaults to true.

Accepted constraints:

- `Budget.name` should be unique per `user_profile_id`.
- `currency` length is 3.

Accepted behavior:

- Annual budgets can be converted into monthly expectations for reporting.
- Inactive budgets remain stored for audit/history but should be hidden from normal active-budget workflows.

## Block 6 - BudgetLine

Accepted fields:

- `id`
- `budget_id`
- `category_id`
- `amount_minor`
- `rollover_policy`
- `notes`
- `created_at`
- `updated_at`

Accepted relationship rules:

- `BudgetLine.budget_id` points to `Budget.id`.
- `BudgetLine.category_id` points to `Category.id`.
- A budget can have many budget lines.
- One budget line belongs to exactly one budget and one category.
- Budget line category ownership must remain synchronized with the budget user profile where practical.

Accepted category scope:

- Budget lines are allowed for expense, income, saving, and investment categories in Phase 1.
- Reports should separate meaning by `Category.category_type` rather than restricting budget lines to expenses only.

Accepted `rollover_policy` approach:

- Keep `rollover_policy` in the Phase 1 schema.
- Define only `none` as supported Phase 1 behavior.
- Leave additional policies open for future design because multiple rollover alternatives will be needed later.

Accepted `rollover_policy` values for Phase 1 behavior:

- `none`

Accepted nullable rules:

- `category_id` is required.
- `notes` is nullable.

Accepted constraints:

- `BudgetLine` should be unique by `budget_id` and `category_id`.
- `amount_minor >= 0`.

## Open Questions For Later Blocks

- Exact repository and service methods after ORM classes are accepted.
- Additional `rollover_policy` alternatives beyond `none`.

## Block 7 - Phase 1 Enums

Accepted implementation:

- Use Python 3.11+.
- Use standard-library `StrEnum` for new Phase 1 enums.
- Store enum values as snake_case strings.
- Keep all new Phase 1 enums in `src/lxcell/enums/core_enums.py` initially.
- Split enum files later only if the single file becomes hard to navigate.

Rationale:

- `StrEnum` members behave like strings, which makes DataFrame handling and serialization less noisy than plain `Enum` values that require frequent `.value` access.
- Requiring Python 3.11+ is acceptable for LXCell at this stage.

Accepted enum classes:

- `AccountType`
- `OwnershipType`
- `CategoryType`
- `Direction`
- `TransactionType`
- `PaymentMethod`
- `TransactionReviewStatus`
- `TransactionSourceType`
- `ImportSourceSystem`
- `ImportStatus`
- `ImportAction`
- `ClassificationRuleType`
- `ClassificationMatchField`
- `ClassificationDecisionSource`
- `ClassificationDecisionStatus`
- `BudgetPeriodType`
- `RolloverPolicy`

Accepted `PaymentMethod` values:

- `card`
- `bank_transfer`
- `peer_to_peer`
- `direct_debit`
- `cash`
- `standing_order`
- `other`

Accepted source distinction:

- Keep `TransactionSourceType` separate from `ImportSourceSystem`.
- `TransactionSourceType` describes the general origin of a transaction.
- `ImportSourceSystem` describes the concrete source system or file type of an import batch.

Accepted account type clarifications:

- `checking` represents a bank current account.
- `cash` represents physical cash or a manual cash wallet.
- `loan` remains a valid account type because liabilities can be represented as accounts.

## Block 8 - Initial SQLAlchemy ORM Implementation Details

Accepted:

- Implement the Phase 1 ORM classes in `src/lxcell/db/models.py`.
- Use SQLAlchemy 2.x typed mappings with `Mapped[...]` and `mapped_column(...)`.
- Keep the first implementation in one model file while the Phase 1 schema is still small.
- Use small shared mixins for integer primary keys and timestamps.
- Persist `StrEnum` values as their snake_case string values, not Python enum member names.
- Store `confidence` as exact `Numeric(5, 4)` values constrained from `0.0000` to `1.0000`.
- Require `ClassificationDecision.decided_by`.
- Use `decided_by = system` for rule-based, import-default, historical-match, AI-suggestion, or other non-human decisions.
- Enable SQLite foreign key enforcement in the database engine helper.
- Add focused tests for table creation, enum persistence, classification defaults, required confirmed descriptions, and profile isolation constraints.

Rationale:

- Exact numeric confidence keeps audit data stable and avoids floating point artifacts.
- A non-null `decided_by` makes system actions explicit instead of relying on null to mean non-human.
- SQLite does not enforce foreign keys unless enabled per connection, so tests and local development must turn them on explicitly.
