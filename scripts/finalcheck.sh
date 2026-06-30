#!/bin/bash
# scripts/finalcheck.sh
# 完了報告の証跡を決まったコマンド出力で確認するスクリプト。
# 本番 freee / Notion / OpenAI / Slack / Railway には一切アクセスしない。
set -euo pipefail

echo "== git show --stat --oneline HEAD =="
git show --stat --oneline HEAD

echo ""
echo "== git show HEAD -- requirements-dev.txt scripts/selfcheck.sh tests/ =="
git show HEAD -- requirements-dev.txt scripts/selfcheck.sh tests/

echo ""
echo "== python3 -m pytest tests/ -vv =="
python3 -m pytest tests/ -vv

echo ""
echo "== bash scripts/selfcheck.sh =="
bash scripts/selfcheck.sh

echo ""
echo "== git status --short =="
git status --short

echo ""
echo "== git diff --stat HEAD =="
git diff --stat HEAD

echo ""
echo "== finalcheck done =="
