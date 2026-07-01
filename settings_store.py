"""
settings_store.py

app_settings テーブルを使ったアプリ設定の永続化モジュール。

停止判定の優先順位（fail-safe 設計）:
  1. env=1 → 必ず停止（Railway Variables による強制停止、最優先）
  2. env=0, DB=1 → 停止（UI/API 経由の停止）
  3. env=0, DB=0 → 稼働
  4. env=0, DBなし → 稼働
  5. env=0, DB読み取り例外 → 停止（fail-safe: 本番経理系なので fail-open しない）

役割分担:
  - env FREEE_AUTO_STOPPED: Railway Variables による外部からの強制停止専用
  - DB auto_stopped: UI/API/CLI 経由の停止状態の永続化
  - set_auto_stopped(): DB のみ更新。os.environ は一切変更しない

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
    停止フラグを取得する（fail-safe 設計）。

    優先順位:
    1. env=1 → 必ず停止（Railway Variables 強制停止、最優先）
    2. env=0, DB=1 → 停止
    3. env=0, DB=0 → 稼働
    4. env=0, DBなし → 稼働
    5. env=0, DB読み取り例外 → 停止（fail-safe）

    戻り値: True = 停止中, False = 実行中
    """
    env_val = os.environ.get("FREEE_AUTO_STOPPED", "0")

    # env=1 は最優先の強制停止（DB 値に関わらず停止）
    if env_val == "1":
        logger.info("[settings_store] env FREEE_AUTO_STOPPED=1 により強制停止")
        return True

    # env=0 の場合は DB を確認
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
            # DB に値がある場合は DB の値を使用
            return row[0] == "1"
        else:
            # DB に値がない場合は稼働
            return False

    except Exception as e:
        # DB 読み取り例外は fail-safe: 停止扱い（本番経理系なので fail-open しない）
        logger.error(
            f"[settings_store] DB読み取り例外 → fail-safe により停止扱い: {e}"
        )
        return True


def set_auto_stopped(stopped: bool) -> None:
    """
    停止フラグを DB に永続保存する。

    - DB のみ更新する
    - os.environ["FREEE_AUTO_STOPPED"] は変更しない（Railway Variables 専用のため）
    - Railway Variables 本体は変更しない
    """
    value = "1" if stopped else "0"
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


def get_auto_stopped_source() -> dict:
    """
    停止フラグの値とソース（env_force / db / env）を返す。
    /api/status・/api/healthcheck の scheduler 情報表示用。

    source の値:
      "env_force"  : env=1 による強制停止（DB 値に関わらず停止）
      "db"         : env=0 で DB に値がある
      "env"        : env=0 で DB に値がない（env フォールバック）
      "db_error"   : env=0 で DB 読み取り例外（fail-safe 停止）

    戻り値:
    {
        "freee_auto_stopped_env": "0" or "1",
        "persisted_auto_stopped": "0" or "1" or None,
        "effective_auto_stopped": True or False,
        "source": "env_force" or "db" or "env" or "db_error",
    }
    """
    env_val = os.environ.get("FREEE_AUTO_STOPPED", "0")

    # env=1 は最優先の強制停止
    if env_val == "1":
        # DB 値も読もうとするが、source は env_force
        db_val = None
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
        except Exception:
            pass  # DB 読み取り失敗は無視（env=1 で既に停止確定）

        return {
            "freee_auto_stopped_env": env_val,
            "persisted_auto_stopped": db_val,
            "effective_auto_stopped": True,
            "source": "env_force",
        }

    # env=0 の場合は DB を確認
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
        # DB に値がない場合は source="env" のまま

    except Exception as e:
        logger.warning(f"[settings_store] DB読み取り失敗（source取得）: {e}")
        # fail-safe: DB 読み取り例外は停止扱い
        return {
            "freee_auto_stopped_env": env_val,
            "persisted_auto_stopped": None,
            "effective_auto_stopped": True,
            "source": "db_error",
        }

    effective = (db_val == "1") if db_val is not None else False

    return {
        "freee_auto_stopped_env": env_val,
        "persisted_auto_stopped": db_val,
        "effective_auto_stopped": effective,
        "source": source,
    }
