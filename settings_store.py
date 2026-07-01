"""
settings_store.py

app_settings テーブルを使ったアプリ設定の永続化モジュール。

設計方針:
- DB に値があれば DB を優先する
- DB に値がなければ os.environ.get("FREEE_AUTO_STOPPED", "0") をフォールバックとして使う
- Railway Variables 本体はアプリから変更しない
- APScheduler / API / CLI の停止判定をこのモジュールに統一する

安全制約:
- freee / Notion / OpenAI / Slack には一切アクセスしない
- Railway 環境変数は変更しない
- 本番 DB migration は実行しない（CREATE TABLE IF NOT EXISTS のみ許可）
"""
import os
import logging
from db import _get_db

logger = logging.getLogger(__name__)

KEY_AUTO_STOPPED = "auto_stopped"


def ensure_app_settings_table() -> None:
    """
    app_settings テーブルを作成する（冪等）。
    _init_db から呼ばれる想定だが、単独でも安全に呼べる。
    """
    conn = _get_db()
    try:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS app_settings (
                key        TEXT PRIMARY KEY,
                value      TEXT NOT NULL,
                updated_at TEXT NOT NULL DEFAULT (datetime('now','localtime'))
            )
        """)
        conn.commit()
    finally:
        conn.close()


def get_auto_stopped() -> bool:
    """
    停止フラグを取得する。

    優先順位:
    1. app_settings テーブルの auto_stopped キー（DB 優先）
    2. 環境変数 FREEE_AUTO_STOPPED（フォールバック）

    戻り値: True = 停止中, False = 実行中
    """
    try:
        conn = _get_db()
        try:
            row = conn.execute(
                "SELECT value FROM app_settings WHERE key = ?",
                (KEY_AUTO_STOPPED,)
            ).fetchone()
        finally:
            conn.close()

        if row is not None:
            # DB に値がある場合は DB を優先
            return row[0] == "1"
    except Exception as e:
        logger.warning(f"[settings_store] DB読み取り失敗、envにフォールバック: {e}")

    # DB に値がない場合は環境変数を使用
    return os.environ.get("FREEE_AUTO_STOPPED", "0") == "1"


def set_auto_stopped(stopped: bool) -> None:
    """
    停止フラグを DB に永続保存する。

    - DB に保存することでプロセス再起動後も状態が維持される
    - os.environ["FREEE_AUTO_STOPPED"] も補助的に更新する（同一プロセス内の整合性）
    - Railway Variables 本体は変更しない
    """
    value = "1" if stopped else "0"
    try:
        conn = _get_db()
        try:
            conn.execute("""
                INSERT INTO app_settings (key, value, updated_at)
                VALUES (?, ?, datetime('now','localtime'))
                ON CONFLICT(key) DO UPDATE SET
                    value      = excluded.value,
                    updated_at = excluded.updated_at
            """, (KEY_AUTO_STOPPED, value))
            conn.commit()
        finally:
            conn.close()
        logger.info(f"[settings_store] auto_stopped を DB に保存: {value}")
    except Exception as e:
        logger.error(f"[settings_store] DB書き込み失敗: {e}")
        raise

    # 補助: 同一プロセス内の環境変数も更新（Railway Variables は変更しない）
    os.environ["FREEE_AUTO_STOPPED"] = value


def get_auto_stopped_source() -> dict:
    """
    停止フラグの値とソース（db / env）を返す。
    /api/status・/api/healthcheck の scheduler 情報表示用。

    戻り値:
    {
        "freee_auto_stopped_env": "0" or "1",
        "persisted_auto_stopped": "0" or "1" or None,
        "effective_auto_stopped": True or False,
        "source": "db" or "env",
    }
    """
    env_val = os.environ.get("FREEE_AUTO_STOPPED", "0")
    db_val = None
    source = "env"

    try:
        conn = _get_db()
        try:
            row = conn.execute(
                "SELECT value FROM app_settings WHERE key = ?",
                (KEY_AUTO_STOPPED,)
            ).fetchone()
        finally:
            conn.close()

        if row is not None:
            db_val = row[0]
            source = "db"
    except Exception as e:
        logger.warning(f"[settings_store] DB読み取り失敗（source取得）: {e}")

    effective = (db_val == "1") if db_val is not None else (env_val == "1")

    return {
        "freee_auto_stopped_env": env_val,
        "persisted_auto_stopped": db_val,
        "effective_auto_stopped": effective,
        "source": source,
    }
