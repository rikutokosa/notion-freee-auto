"""
tests/test_db_migration.py
第3段階: migration / rollback 安全化テスト

確認内容:
- 空DBで _init_db() が正常に初期化できる
- _init_db() を複数回実行しても壊れない（CREATE TABLE IF NOT EXISTS の冪等性）
- 既存データが _init_db() 再実行で消えない
- 既存カラムがある状態（正常 schema）で _migrate_idempotency_keys が何もしない
- 旧 schema（不足カラムあり）に対して _migrate_idempotency_keys が安全に移行する
- job_locks テーブルが正しく作成される
- execution_logs テーブルが正しく作成される
- schema_migrations テーブルが正しく作成される

本番 DB migration は実行しない。
freee / Notion / OpenAI / Slack は一切呼ばない。
"""
import os
import sys
import sqlite3
import tempfile
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


@pytest.fixture()
def isolated_db(tmp_path, monkeypatch):
    """
    完全に独立したテスト用 SQLite DB。
    db._DB_PATH と app._init_db を本番 DB から切り離す。
    """
    db_path = str(tmp_path / "test_migration.db")
    import db as db_module
    monkeypatch.setattr(db_module, "_DB_PATH", db_path)
    return db_path


# ---------------------------------------------------------------------------
# 第3段階: 空DB初期化テスト
# ---------------------------------------------------------------------------

class TestInitDbFresh:
    """空DB で _init_db() が正常に動作する"""

    def test_init_db_creates_all_tables(self, isolated_db):
        import app as app_module
        app_module._init_db()

        conn = sqlite3.connect(isolated_db)
        tables = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()}
        conn.close()

        expected = {
            "schema_migrations",
            "chat_sessions",
            "rules_notes",
            "execution_logs",
            "job_locks",
            "idempotency_keys",
        }
        assert expected.issubset(tables), (
            f"テーブルが不足: {expected - tables}"
        )

    def test_init_db_idempotency_keys_has_all_columns(self, isolated_db):
        import app as app_module
        app_module._init_db()

        conn = sqlite3.connect(isolated_db)
        cols = {r[1] for r in conn.execute(
            "PRAGMA table_info(idempotency_keys)"
        ).fetchall()}
        conn.close()

        expected_cols = {"key", "page_id", "action", "status", "freee_ids", "created_at", "updated_at"}
        assert expected_cols.issubset(cols), (
            f"idempotency_keys カラム不足: {expected_cols - cols}"
        )

    def test_init_db_job_locks_has_all_columns(self, isolated_db):
        import app as app_module
        app_module._init_db()

        conn = sqlite3.connect(isolated_db)
        cols = {r[1] for r in conn.execute(
            "PRAGMA table_info(job_locks)"
        ).fetchall()}
        conn.close()

        assert {"job_name", "locked_at", "expires_at"}.issubset(cols), (
            f"job_locks カラム不足: {cols}"
        )

    def test_init_db_execution_logs_has_all_columns(self, isolated_db):
        import app as app_module
        app_module._init_db()

        conn = sqlite3.connect(isolated_db)
        cols = {r[1] for r in conn.execute(
            "PRAGMA table_info(execution_logs)"
        ).fetchall()}
        conn.close()

        expected_cols = {"id", "log_type", "executed_at", "trigger", "summary", "detail", "has_error"}
        assert expected_cols.issubset(cols), (
            f"execution_logs カラム不足: {expected_cols - cols}"
        )


# ---------------------------------------------------------------------------
# 第3段階: 冪等性テスト（複数回実行しても壊れない）
# ---------------------------------------------------------------------------

class TestInitDbIdempotent:
    """_init_db() を複数回実行しても壊れない"""

    def test_init_db_twice_does_not_raise(self, isolated_db):
        import app as app_module
        app_module._init_db()
        app_module._init_db()  # 2回目も例外なし

    def test_init_db_three_times_tables_intact(self, isolated_db):
        import app as app_module
        app_module._init_db()
        app_module._init_db()
        app_module._init_db()

        conn = sqlite3.connect(isolated_db)
        tables = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()}
        conn.close()

        expected = {"schema_migrations", "chat_sessions", "rules_notes",
                    "execution_logs", "job_locks", "idempotency_keys"}
        assert expected.issubset(tables)

    def test_existing_data_survives_reinit(self, isolated_db):
        """既存データが _init_db() 再実行で消えない"""
        import app as app_module
        app_module._init_db()

        # データを挿入
        conn = sqlite3.connect(isolated_db)
        conn.execute(
            "INSERT INTO idempotency_keys (key, page_id, action, status) VALUES (?, ?, ?, ?)",
            ("test_key_001", "page_abc", "register", "done")
        )
        conn.commit()
        conn.close()

        # 再初期化
        app_module._init_db()

        # データが残っているか確認
        conn = sqlite3.connect(isolated_db)
        row = conn.execute(
            "SELECT key FROM idempotency_keys WHERE key=?", ("test_key_001",)
        ).fetchone()
        conn.close()

        assert row is not None, "既存データが _init_db() 再実行で消えた"
        assert row[0] == "test_key_001"

    def test_execution_log_data_survives_reinit(self, isolated_db):
        """execution_logs のデータが _init_db() 再実行で消えない"""
        import app as app_module
        app_module._init_db()

        conn = sqlite3.connect(isolated_db)
        conn.execute(
            "INSERT INTO execution_logs (log_type, executed_at, trigger, summary, detail, has_error) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            ("auto_transfer", "2026-07-01 12:00", "scheduler", '{"total":1}', '{}', 0)
        )
        conn.commit()
        conn.close()

        app_module._init_db()

        conn = sqlite3.connect(isolated_db)
        count = conn.execute(
            "SELECT COUNT(*) FROM execution_logs WHERE log_type=?", ("auto_transfer",)
        ).fetchone()[0]
        conn.close()

        assert count == 1, f"execution_logs データが消えた: count={count}"


# ---------------------------------------------------------------------------
# 第3段階: _migrate_idempotency_keys テスト
# ---------------------------------------------------------------------------

class TestMigrateIdempotencyKeys:
    """_migrate_idempotency_keys の安全性テスト"""

    def test_no_table_does_nothing(self, isolated_db):
        """idempotency_keys テーブルが存在しない場合は何もしない"""
        import app as app_module
        conn = sqlite3.connect(isolated_db)
        # schema_migrations だけ作成（idempotency_keys は作らない）
        conn.execute("""
            CREATE TABLE IF NOT EXISTS schema_migrations (
                version TEXT PRIMARY KEY,
                applied_at TEXT NOT NULL,
                description TEXT NOT NULL DEFAULT ''
            )
        """)
        conn.commit()
        # 例外なく完了すること
        app_module._migrate_idempotency_keys(conn)
        conn.close()

    def test_correct_schema_does_nothing(self, isolated_db):
        """正常 schema の場合は migration しない（backup テーブルが作られない）"""
        import app as app_module
        conn = sqlite3.connect(isolated_db)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS schema_migrations (
                version TEXT PRIMARY KEY,
                applied_at TEXT NOT NULL,
                description TEXT NOT NULL DEFAULT ''
            )
        """)
        conn.execute("""
            CREATE TABLE idempotency_keys (
                key TEXT PRIMARY KEY,
                page_id TEXT NOT NULL,
                action TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'processing',
                freee_ids TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
                updated_at TEXT NOT NULL DEFAULT (datetime('now','localtime'))
            )
        """)
        conn.commit()

        app_module._migrate_idempotency_keys(conn)

        # backup テーブルが作られていないこと
        tables = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'idempotency_keys_backup%'"
        ).fetchall()}
        conn.close()
        assert len(tables) == 0, f"正常 schema なのに backup が作られた: {tables}"

    def test_old_schema_migrates_safely(self, isolated_db):
        """旧 schema（freee_ids カラムなし）に対して安全に移行する"""
        import app as app_module
        conn = sqlite3.connect(isolated_db)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS schema_migrations (
                version TEXT PRIMARY KEY,
                applied_at TEXT NOT NULL,
                description TEXT NOT NULL DEFAULT ''
            )
        """)
        # 旧 schema（freee_ids・updated_at なし）
        conn.execute("""
            CREATE TABLE idempotency_keys (
                key TEXT PRIMARY KEY,
                page_id TEXT NOT NULL,
                action TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'processing',
                created_at TEXT NOT NULL DEFAULT (datetime('now','localtime'))
            )
        """)
        # 旧データを挿入
        conn.execute(
            "INSERT INTO idempotency_keys (key, page_id, action, status) VALUES (?, ?, ?, ?)",
            ("old_key_001", "page_xyz", "register", "done")
        )
        conn.commit()

        app_module._migrate_idempotency_keys(conn)

        # 新 schema の idempotency_keys が作成されていること
        cols = {r[1] for r in conn.execute(
            "PRAGMA table_info(idempotency_keys)"
        ).fetchall()}
        assert "freee_ids" in cols, f"migration 後に freee_ids カラムがない: {cols}"
        assert "updated_at" in cols, f"migration 後に updated_at カラムがない: {cols}"

        # backup テーブルが作られていること
        tables = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'idempotency_keys_backup%'"
        ).fetchall()}
        assert len(tables) == 1, f"backup テーブルが作られていない: {tables}"

        # 旧データが backup に保存されていること
        backup_name = list(tables)[0]
        old_row = conn.execute(
            f"SELECT key FROM {backup_name} WHERE key=?", ("old_key_001",)
        ).fetchone()
        conn.close()
        assert old_row is not None, "旧データが backup に保存されていない"
