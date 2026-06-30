#!/bin/bash
set -e

echo "=== pyflakes ==="
python3 -m pyflakes app.py freee_client.py notion_client.py processor.py rules.py matcher.py payment.py || true

echo "=== py_compile ==="
python3 -m py_compile app.py freee_client.py notion_client.py processor.py rules.py matcher.py payment.py || true

echo "=== git diff --check ==="
git diff --check || true

echo "=== git status --short ==="
git status --short || true

echo "=== secret grep ==="
grep -nE -r "ntn_|ghp_|github_pat_|sk-|xox[a-zA-Z0-9_]{10,}|1b2263ba" . \
  --exclude-dir=.git \
  --exclude-dir=__pycache__ \
  --exclude="*.db" \
  --exclude="notion-freee-auto-dump*.txt" \
  --exclude="notion-freee-auto-full-dump.txt" \
  | sed -E 's/(ntn_|ghp_|github_pat_|sk-|xox)[a-zA-Z0-9_-]+/[REDACTED]/g' \
  | sed -E 's/1b2263ba-[a-zA-Z0-9_-]+/[REDACTED]/g' || true

echo "Self check completed!"
