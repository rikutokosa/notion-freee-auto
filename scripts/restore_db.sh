#!/usr/bin/env bash
# scripts/restore_db.sh
# SQLite DB のリストアスクリプト（rollback 手順）
#
# 使い方:
#   bash scripts/restore_db.sh <BACKUP_FILE> [DB_PATH]
#
# 引数省略時のデフォルト:
#   DB_PATH = /data/notion_freee.db  (Railway volume)
#
# 注意:
#   - 現在の DB を上書きする前に自動で退避バックアップを作成する
#   - 本番 DB migration は実行しない
#   - freee / Notion / OpenAI / Slack には一切アクセスしない
#   - Railway 環境変数は変更しない
set -euo pipefail

if [ $# -lt 1 ]; then
    echo "使い方: bash scripts/restore_db.sh <BACKUP_FILE> [DB_PATH]" >&2
    exit 1
fi

BACKUP_FILE="${1}"
DB_PATH="${2:-/data/notion_freee.db}"
TIMESTAMP=$(date +"%Y%m%d_%H%M%S")
PRE_RESTORE_BACKUP="${DB_PATH}.pre_restore_${TIMESTAMP}"

if [ ! -f "${BACKUP_FILE}" ]; then
    echo "[restore_db] ERROR: バックアップファイルが存在しません: ${BACKUP_FILE}" >&2
    exit 1
fi

# リストア前に現在の DB を退避
if [ -f "${DB_PATH}" ]; then
    echo "[restore_db] 現在の DB を退避: ${PRE_RESTORE_BACKUP}"
    cp "${DB_PATH}" "${PRE_RESTORE_BACKUP}"
fi

# バックアップから復元
sqlite3 "${BACKUP_FILE}" ".backup '${DB_PATH}'"

echo "[restore_db] リストア完了: ${BACKUP_FILE} -> ${DB_PATH}"
echo "[restore_db] 退避バックアップ: ${PRE_RESTORE_BACKUP}"
echo "[restore_db] ロールバックが必要な場合: cp ${PRE_RESTORE_BACKUP} ${DB_PATH}"
