"""
DB接続ユーティリティ
app.py と processor.py の循環importを避けるため、
_get_db / _DB_PATH / _VOLUME_PATH をここに切り出す。
"""
import os
import sqlite3

_VOLUME_PATH = os.environ.get("RAILWAY_VOLUME_MOUNT_PATH", "/data")
_DB_PATH = os.path.join(_VOLUME_PATH, "chat_history.db")


def _get_db():
    """SQLite接続のみ返す（テーブル作成は app._init_db で行う）"""
    os.makedirs(os.path.dirname(_DB_PATH), exist_ok=True)
    return sqlite3.connect(_DB_PATH)
