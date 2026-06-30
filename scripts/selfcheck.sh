#!/usr/bin/env bash
set -euo pipefail

TARGETS="app.py freee_client.py notion_client.py processor.py rules.py matcher.py payment.py"

echo "== pyflakes =="
python3 -m pyflakes $TARGETS

echo "== py_compile =="
python3 -m py_compile $TARGETS

echo "== git diff check =="
git diff --check

echo "== git status =="
git status --short

echo "== secret scan (masked, tracked files only) =="
set +e
git grep -nE '(ntn_[A-Za-z0-9]{10,}|ghp_[A-Za-z0-9]{10,}|github_pat_[A-Za-z0-9]{10,}|sk-[A-Za-z0-9]{20,}|xox[baprs]-[A-Za-z0-9-]{10,})' -- '*.py' '*.md' '*.html' '*.toml' '*.example' \
  | sed -E 's/(ntn_|ghp_|github_pat_|sk-)[A-Za-z0-9_=-]+/\1[REDACTED]/g' \
  | sed -E 's/(xox[baprs]-)[A-Za-z0-9-]+/\1[REDACTED]/g' \
  || true
set -e

echo "== pytest =="
python3 -m pytest tests/ -v --tb=short

echo "== done =="
