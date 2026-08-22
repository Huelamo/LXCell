# LXCell

LXCell is a local, auditable personal finance application.

## Local CLI

The first local workflow is available through:

```bash
PYTHONPATH=src .venv/bin/python -m lxcell.cli --database data/lxcell.db init-db
```

Create basic records:

```bash
PYTHONPATH=src .venv/bin/python -m lxcell.cli --database data/lxcell.db add-profile \
  --display-name "Sample User"

PYTHONPATH=src .venv/bin/python -m lxcell.cli --database data/lxcell.db add-account \
  --profile-id 1 \
  --name "Primary account" \
  --type checking

PYTHONPATH=src .venv/bin/python -m lxcell.cli --database data/lxcell.db add-category \
  --profile-id 1 \
  --name "Category A" \
  --type expense
```

Record a manual transaction:

```bash
PYTHONPATH=src .venv/bin/python -m lxcell.cli --database data/lxcell.db add-transaction \
  --profile-id 1 \
  --account-id 1 \
  --category-id 1 \
  --date 2026-01-10 \
  --description "Merchant A" \
  --amount 12.34 \
  --direction outflow \
  --type expense \
  --decided-by "Sample User"
```

List transactions:

```bash
PYTHONPATH=src .venv/bin/python -m lxcell.cli --database data/lxcell.db list-transactions \
  --profile-id 1
```

Local data files under `data/` are ignored by git.

## Local Streamlit UI

On macOS, double-click:

```text
Abrir LXCell.command
```

The launcher starts the local Streamlit server and opens the browser at
`http://localhost:8501`.

Run the local UI with:

```bash
PYTHONPATH=src .venv/bin/python -m streamlit run src/lxcell/ui/streamlit_app.py
```

The UI uses `data/lxcell.db` by default. Local data files under `data/` are
ignored by git.
