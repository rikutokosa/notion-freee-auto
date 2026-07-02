#!/usr/bin/env bash
# scripts/backup_db.sh
# SQLite DB の安全バックアップスクリプト
#
# 使い方:
#   bash scripts/backup_db.sh [DB_PATH] [BACKUP_DIR]
#
# 引数省略時のデフォルト:
#   DB_PATH    = ${RAILWAY_VOLUME_MOUNT_PATH:-/data}/chat_history.db
#   BACKUP_DIR = ${RAILWAY_VOLUME_MOUNT_PATH:-/data}/backups
#
# 本番 DB migration は実行しない。読み取り専用コピーのみ。
# freee / Notion / OpenAI / Slack には一切アクセスしない。
set -euo pipefail

DEFAULT_DB_PATH="${RAILWAY_VOLUME_MOUNT_PATH:-/data}/chat_history.db"
DB_PATH="${1:-${DEFAULT_DB_PATH}}"
BACKUP_DIR="${2:-${RAILWAY_VOLUME_MOUNT_PATH:-/data}/backups}"
TIMESTAMP=$(date +"%Y%m%d_%H%M%S")
BACKUP_FILE="${BACKUP_DIR}/chat_history_backup_${TIMESTAMP}.db"

if [ ! -f "${DB_PATH}" ]; then
    echo "[backup_db] ERROR: DB ファイルが存在しません: ${DB_PATH}" >&2
    exit 1
fi

mkdir -p "${BACKUP_DIR}"

# SQLite の .backup コマンドで安全にコピー（ロック中でも整合性を保つ）
sqlite3 "${DB_PATH}" ".backup '${BACKUP_FILE}'"

echo "[backup_db] バックアップ完了: ${BACKUP_FILE}"
echo "[backup_db] サイズ: $(du -sh "${BACKUP_FILE}" | cut -f1)"
