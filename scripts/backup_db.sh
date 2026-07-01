#!/usr/bin/env bash
# scripts/backup_db.sh
# SQLite DB の安全バックアップスクリプト
#
# 使い方:
#   bash scripts/backup_db.sh [DB_PATH] [BACKUP_DIR]
#
# 引数省略時のデフォルト:
#   DB_PATH    = /data/notion_freee.db  (Railway volume)
#   BACKUP_DIR = /data/backups
#
# 本番 DB migration は実行しない。読み取り専用コピーのみ。
# freee / Notion / OpenAI / Slack には一切アクセスしない。
set -euo pipefail

DB_PATH="${1:-/data/notion_freee.db}"
BACKUP_DIR="${2:-/data/backups}"
TIMESTAMP=$(date +"%Y%m%d_%H%M%S")
BACKUP_FILE="${BACKUP_DIR}/notion_freee_backup_${TIMESTAMP}.db"

if [ ! -f "${DB_PATH}" ]; then
    echo "[backup_db] ERROR: DB ファイルが存在しません: ${DB_PATH}" >&2
    exit 1
fi

mkdir -p "${BACKUP_DIR}"

# SQLite の .backup コマンドで安全にコピー（ロック中でも整合性を保つ）
sqlite3 "${DB_PATH}" ".backup '${BACKUP_FILE}'"

echo "[backup_db] バックアップ完了: ${BACKUP_FILE}"
echo "[backup_db] サイズ: $(du -sh "${BACKUP_FILE}" | cut -f1)"
