# Agent Instructions For LXCell

These instructions are for AI coding agents working in this repository. They are operational rules, not product documentation.

## Must Read First

Before making project changes, read:

- `docs/product_requirements.md`
- `docs/ROADMAP.md`
- `docs/DECISIONS.md`

## Hard Safety Rules

- Never modify existing historical Excel workbooks in place.
- Treat personal finance workbooks as read-only source material.
- If workbook experimentation is required, make an explicit copy in a controlled working location.
- Do not commit real `.xlsx` or `.xls` personal finance files.
- Use anonymized fixtures under `tests/fixtures/` when spreadsheet test files are needed.
- Do not remove or rewrite user-authored work unless the user explicitly asks for it.

## Public Repository Privacy Rules

- This repository is public. Do not commit personal, sensitive, or identifying financial details in Markdown docs, tests, fixtures, examples, comments, or commit messages.
- Avoid real names, family relationships, account ownership details, real bank-account purposes, merchants, salaries, debts, mortgage details, exact categories tied to real people, statement filenames, or raw exports.
- Use generic placeholders such as `Primary user`, `Shared account`, `Bank A`, `Merchant A`, and `Sample User`.
- Real financial institutions may be named only when describing a generic product integration or adapter, and must not be tied to the user's personal account usage.
- If a detail is useful for implementation but sensitive in a public repo, keep it in conversation or local-only notes, not in versioned files.

## Architecture Ownership

- The user retains ownership of both financial concepts and software architecture.
- Agents may propose designs, tradeoffs, package structures, classes, methods, schemas, and workflows, but must not implement major architectural structures without explicit user review.
- Clearly label architectural ideas as `Proposed`, `Accepted`, or `Open` before implementation.
- Before creating or reorganizing packages, classes, methods, database tables, services, or public APIs, explain the intended design and wait for user approval unless the change is tiny and purely mechanical.
- Prefer helping the user maintain a complete mental model of the codebase over maximizing autonomous implementation speed.

## Language Rules

- Code, comments, internal identifiers, tests, and technical docs should be in English.
- User-facing text in the application should be in Spanish.

## Development Rules

- Prefer small, auditable changes with tests.
- Keep the database as the long-term source of truth.
- Keep Excel importers read-only and validation-oriented.
- Prefer deterministic rules before AI for transaction classification.
- AI-generated classifications must be traceable and reviewable.
- Ambiguous financial records should require user confirmation.

## Git Context

- The pre-AI-intervention code snapshot is preserved in branch `codex/pre-ai-baseline`.
- Snapshot commit: `4787bc5` (`Preserve pre-AI intervention baseline`).
- AI-assisted foundation work starts from branch `codex/project-foundation`.
