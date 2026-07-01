"""
共有フィクスチャ
- テスト用インメモリ SQLite DB
- Flask テストクライアント（Basic認証環境変数付き）
- 本番 freee / Notion / OpenAI は一切叩かない
"""
import os
import sys
import sqlite3
import tempfile
import pytest

# プロジェクトルートを sys.path に追加
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# ---------------------------------------------------------------------------
# テスト用 DB フィクスチャ
# ---------------------------------------------------------------------------

@pytest.fixture()
def tmp_db_path(tmp_path):
    """一時ファイルパスを返す（テスト終了後に自動削除）。"""
    return str(tmp_path / "test.db")


@pytest.fixture()
def db_conn(tmp_db_path, monkeypatch):
    """
    テスト用 SQLite 接続を返す。
    db._DB_PATH をモンキーパッチして本番 DB を使わない。
    """
    import db as db_module
    monkeypatch.setattr(db_module, "_DB_PATH", tmp_db_path)

    conn = sqlite3.connect(tmp_db_path)
    conn.row_factory = sqlite3.Row
    # idempotency_keys テーブルを作成
    conn.execute("""
        CREATE TABLE IF NOT EXISTS idempotency_keys (
            key TEXT PRIMARY KEY,
            page_id TEXT NOT NULL,
            action TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'processing',
            freee_ids TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
            updated_at TEXT NOT NULL DEFAULT (datetime('now','localtime'))
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS schema_migrations (
            version TEXT PRIMARY KEY,
            applied_at TEXT NOT NULL
        )
    """)
    conn.commit()
    yield conn
    conn.close()


# ---------------------------------------------------------------------------
# Flask テストクライアント フィクスチャ
# ---------------------------------------------------------------------------

@pytest.fixture()
def flask_client(monkeypatch, tmp_db_path):
    """
    Flask テストクライアントを返す。
    - BASIC_AUTH_USER / BASIC_AUTH_PASSWORD を設定
    - db._DB_PATH をテスト用 DB に向ける
    - 外部 API（freee / Notion / OpenAI）は呼ばない
    """
    # 環境変数を設定（app.py の import 前に設定する必要がある）
    monkeypatch.setenv("BASIC_AUTH_USER", "testuser")
    monkeypatch.setenv("BASIC_AUTH_PASSWORD", "testpass")
    monkeypatch.setenv("FREEE_CLIENT_ID", "dummy_client_id")
    monkeypatch.setenv("FREEE_CLIENT_SECRET", "dummy_client_secret")
    monkeypatch.setenv("FREEE_COMPANY_ID", "12345")
    monkeypatch.setenv("NOTION_TOKEN", "dummy_notion_token")
    monkeypatch.setenv("NOTION_DATABASE_ID", "dummy_db_id")
    monkeypatch.setenv("OPENAI_API_KEY", "dummy_openai_key")

    # db._DB_PATH をテスト用に差し替え
    import db as db_module
    monkeypatch.setattr(db_module, "_DB_PATH", tmp_db_path)

    # app をインポート（環境変数設定後）
    # app.py は module-level で _BASIC_USER/_BASIC_PASS を読むため、
    # importlib.reload で再読み込みする
    import importlib
    import app as app_module
    monkeypatch.setattr(app_module, "_BASIC_USER", "testuser")
    monkeypatch.setattr(app_module, "_BASIC_PASS", "testpass")

    app_module.app.config["TESTING"] = True
    with app_module.app.test_client() as client:
        yield client


@pytest.fixture()
def auth_headers():
    """正しい Basic 認証ヘッダーを返す。"""
    import base64
    creds = base64.b64encode(b"testuser:testpass").decode()
    return {"Authorization": f"Basic {creds}"}


@pytest.fixture()
def wrong_auth_headers():
    """間違った Basic 認証ヘッダーを返す。"""
    import base64
    creds = base64.b64encode(b"wrong:wrong").decode()
    return {"Authorization": f"Basic {creds}"}
