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
