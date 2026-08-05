# Agent Instructions For MyFinances

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
