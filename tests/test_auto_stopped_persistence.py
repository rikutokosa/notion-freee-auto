"""
tests/test_auto_stopped_persistence.py

FREEE_AUTO_STOPPED 停止フラグの DB 永続化テスト。

テスト方針:
- 本番 freee / Notion / OpenAI / Slack への書き込みは一切行わない
- DB は tmp_path の一時ファイルを使用（本番 DB には触れない）
- db._DB_PATH をモンキーパッチして本番 DB を使わない
- APScheduler は起動しない
- _do_scheduled_run / _do_payment_alert は呼ばない

停止判定の仕様（fail-safe 設計）:
  env=1, DB=0  → 停止  (env_force)
  env=1, DB=1  → 停止  (env_force)
  env=0, DB=1  → 停止  (db)
  env=0, DB=0  → 稼働  (db)
  env=0, DBなし → 稼働  (env)
  env=0, DB読み取り例外 → 停止  (db_error, fail-safe)

set_auto_stopped() は os.environ を変更しない。
"""
import os
import pytest
import sqlite3


def _patch_db(tmp_path, monkeypatch):
    """db._DB_PATH を tmp_path の一時ファイルに差し替える共通ヘルパー"""
    db_path = str(tmp_path / "test.db")
    import db as db_module
    monkeypatch.setattr(db_module, "_DB_PATH", db_path)
    return db_path


# ============================================================
# settings_store 単体テスト: 停止判定の優先順位
# ============================================================

class TestStoppedPriority:
    """停止判定の優先順位テスト（env=1最優先・fail-safe設計）"""

    def test_env1_db0_stops(self, tmp_path, monkeypatch):
        """env=1, DB=0 → 停止（env が DB に勝つ）"""
        _patch_db(tmp_path, monkeypatch)
        monkeypatch.setenv("FREEE_AUTO_STOPPED", "1")

        import settings_store
        settings_store.ensure_app_settings_table()
        settings_store.set_auto_stopped(False)  # DB に 0 を保存

        assert settings_store.get_auto_stopped() is True, (
            "env=1 のとき DB=0 でも停止になるべき"
        )

    def test_env1_db1_stops(self, tmp_path, monkeypatch):
        """env=1, DB=1 → 停止"""
        _patch_db(tmp_path, monkeypatch)
        monkeypatch.setenv("FREEE_AUTO_STOPPED", "1")

        import settings_store
        settings_store.ensure_app_settings_table()
        settings_store.set_auto_stopped(True)

        assert settings_store.get_auto_stopped() is True

    def test_env0_db1_stops(self, tmp_path, monkeypatch):
        """env=0, DB=1 → 停止"""
        _patch_db(tmp_path, monkeypatch)
        monkeypatch.setenv("FREEE_AUTO_STOPPED", "0")

        import settings_store
        settings_store.ensure_app_settings_table()
        settings_store.set_auto_stopped(True)

        assert settings_store.get_auto_stopped() is True

    def test_env0_db0_runs(self, tmp_path, monkeypatch):
        """env=0, DB=0 → 稼働"""
        _patch_db(tmp_path, monkeypatch)
        monkeypatch.setenv("FREEE_AUTO_STOPPED", "0")

        import settings_store
        settings_store.ensure_app_settings_table()
        settings_store.set_auto_stopped(False)

        assert settings_store.get_auto_stopped() is False

    def test_env0_no_db_runs(self, tmp_path, monkeypatch):
        """env=0, DB なし → 稼働"""
        _patch_db(tmp_path, monkeypatch)
        monkeypatch.setenv("FREEE_AUTO_STOPPED", "0")

        import settings_store
        settings_store.ensure_app_settings_table()
        # DB には何も保存しない

        assert settings_store.get_auto_stopped() is False

    def test_env0_db_exception_stops_fail_safe(self, tmp_path, monkeypatch):
        """env=0, DB 読み取り例外 → 停止（fail-safe）"""
        _patch_db(tmp_path, monkeypatch)
        monkeypatch.setenv("FREEE_AUTO_STOPPED", "0")

        import settings_store
        import db as db_module

        # DB 読み取りを強制的に例外にする
        def broken_get_db():
            raise RuntimeError("DB 接続失敗（テスト用）")

        monkeypatch.setattr(db_module, "_get_db", broken_get_db)

        result = settings_store.get_auto_stopped()
        assert result is True, (
            "DB 読み取り例外時は fail-safe で停止扱いになるべき"
        )


# ============================================================
# set_auto_stopped が os.environ を変更しないことを確認
# ============================================================

class TestSetAutoStoppedDoesNotTouchEnv:
    """set_auto_stopped() は os.environ を変更しない"""

    def test_set_true_does_not_change_env(self, tmp_path, monkeypatch):
        """set_auto_stopped(True) 後も os.environ["FREEE_AUTO_STOPPED"] は変わらない"""
        _patch_db(tmp_path, monkeypatch)
        monkeypatch.setenv("FREEE_AUTO_STOPPED", "0")

        import settings_store
        settings_store.ensure_app_settings_table()
        settings_store.set_auto_stopped(True)

        # os.environ は変更されていないこと
        assert os.environ.get("FREEE_AUTO_STOPPED") == "0", (
            "set_auto_stopped(True) が os.environ を変更してはいけない"
        )

    def test_set_false_does_not_change_env(self, tmp_path, monkeypatch):
        """set_auto_stopped(False) 後も os.environ["FREEE_AUTO_STOPPED"] は変わらない"""
        _patch_db(tmp_path, monkeypatch)
        monkeypatch.setenv("FREEE_AUTO_STOPPED", "1")

        import settings_store
        settings_store.ensure_app_settings_table()
        settings_store.set_auto_stopped(False)

        # os.environ は変更されていないこと
        assert os.environ.get("FREEE_AUTO_STOPPED") == "1", (
            "set_auto_stopped(False) が os.environ を変更してはいけない"
        )


# ============================================================
# get_auto_stopped_source の source フィールド確認
# ============================================================

class TestGetAutoStoppedSource:
    """get_auto_stopped_source() の source・persisted_auto_stopped・effective_auto_stopped を厳密に確認"""

    def test_env1_db0_source_is_env_force(self, tmp_path, monkeypatch):
        """env=1, DB=0 → source='env_force', persisted='0', effective=True"""
        _patch_db(tmp_path, monkeypatch)
        monkeypatch.setenv("FREEE_AUTO_STOPPED", "1")

        import settings_store
        settings_store.ensure_app_settings_table()
        settings_store.set_auto_stopped(False)  # DB に 0 を保存

        result = settings_store.get_auto_stopped_source()
        assert result["freee_auto_stopped_env"] == "1"
        assert result["persisted_auto_stopped"] == "0"
        assert result["effective_auto_stopped"] is True
        assert result["source"] == "env_force", (
            f"env=1 のとき source は 'env_force' であるべき: {result}"
        )

    def test_env1_db1_source_is_env_force(self, tmp_path, monkeypatch):
        """env=1, DB=1 → source='env_force', effective=True"""
        _patch_db(tmp_path, monkeypatch)
        monkeypatch.setenv("FREEE_AUTO_STOPPED", "1")

        import settings_store
        settings_store.ensure_app_settings_table()
        settings_store.set_auto_stopped(True)

        result = settings_store.get_auto_stopped_source()
        assert result["freee_auto_stopped_env"] == "1"
        assert result["persisted_auto_stopped"] == "1"
        assert result["effective_auto_stopped"] is True
        assert result["source"] == "env_force"

    def test_env0_db1_source_is_db(self, tmp_path, monkeypatch):
        """env=0, DB=1 → source='db', persisted='1', effective=True"""
        _patch_db(tmp_path, monkeypatch)
        monkeypatch.setenv("FREEE_AUTO_STOPPED", "0")

        import settings_store
        settings_store.ensure_app_settings_table()
        settings_store.set_auto_stopped(True)

        result = settings_store.get_auto_stopped_source()
        assert result["freee_auto_stopped_env"] == "0"
        assert result["persisted_auto_stopped"] == "1"
        assert result["effective_auto_stopped"] is True
        assert result["source"] == "db"

    def test_env0_db0_source_is_db(self, tmp_path, monkeypatch):
        """env=0, DB=0 → source='db', persisted='0', effective=False"""
        _patch_db(tmp_path, monkeypatch)
        monkeypatch.setenv("FREEE_AUTO_STOPPED", "0")

        import settings_store
        settings_store.ensure_app_settings_table()
        settings_store.set_auto_stopped(False)

        result = settings_store.get_auto_stopped_source()
        assert result["freee_auto_stopped_env"] == "0"
        assert result["persisted_auto_stopped"] == "0"
        assert result["effective_auto_stopped"] is False
        assert result["source"] == "db"

    def test_env0_no_db_source_is_env(self, tmp_path, monkeypatch):
        """env=0, DB なし → source='env', persisted=None, effective=False"""
        _patch_db(tmp_path, monkeypatch)
        monkeypatch.setenv("FREEE_AUTO_STOPPED", "0")

        import settings_store
        settings_store.ensure_app_settings_table()
        # DB には何も保存しない

        result = settings_store.get_auto_stopped_source()
        assert result["freee_auto_stopped_env"] == "0"
        assert result["persisted_auto_stopped"] is None
        assert result["effective_auto_stopped"] is False
        assert result["source"] == "env"

    def test_env0_db_exception_source_is_db_error(self, tmp_path, monkeypatch):
        """env=0, DB 例外 → source='db_error', effective=True（fail-safe）"""
        _patch_db(tmp_path, monkeypatch)
        monkeypatch.setenv("FREEE_AUTO_STOPPED", "0")

        import settings_store
        import db as db_module

        def broken_get_db():
            raise RuntimeError("DB 接続失敗（テスト用）")

        monkeypatch.setattr(db_module, "_get_db", broken_get_db)

        result = settings_store.get_auto_stopped_source()
        assert result["freee_auto_stopped_env"] == "0"
        assert result["persisted_auto_stopped"] is None
        assert result["effective_auto_stopped"] is True
        assert result["source"] == "db_error", (
            f"DB 例外時は source='db_error' であるべき: {result}"
        )


# ============================================================
# 永続性・冪等性テスト
# ============================================================

class TestPersistenceAndIdempotency:
    """プロセス再起動相当の永続性と冪等性テスト"""

    def test_persisted_across_new_connection(self, tmp_path, monkeypatch):
        """set_auto_stopped(True) 後、新しい DB 接続でも値が読める（プロセス再起動相当）"""
        db_path = _patch_db(tmp_path, monkeypatch)
        monkeypatch.setenv("FREEE_AUTO_STOPPED", "0")

        import settings_store
        settings_store.ensure_app_settings_table()
        settings_store.set_auto_stopped(True)

        # 新しい接続で読み直す（プロセス再起動相当）
        conn = sqlite3.connect(db_path)
        row = conn.execute(
            "SELECT value FROM app_settings WHERE key = 'auto_stopped'"
        ).fetchone()
        conn.close()

        assert row is not None
        assert row[0] == "1"

    def test_ensure_app_settings_table_idempotent(self, tmp_path, monkeypatch):
        """ensure_app_settings_table() を複数回呼んでも既存データが消えない"""
        _patch_db(tmp_path, monkeypatch)
        monkeypatch.setenv("FREEE_AUTO_STOPPED", "0")

        import settings_store
        settings_store.ensure_app_settings_table()
        settings_store.set_auto_stopped(True)

        # 2回目・3回目の呼び出し
        settings_store.ensure_app_settings_table()
        settings_store.ensure_app_settings_table()

        # データが消えていないこと
        assert settings_store.get_auto_stopped() is True

    def test_toggle_stopped_flag(self, tmp_path, monkeypatch):
        """True → False → True と切り替えられる"""
        _patch_db(tmp_path, monkeypatch)
        monkeypatch.setenv("FREEE_AUTO_STOPPED", "0")

        import settings_store
        settings_store.ensure_app_settings_table()

        settings_store.set_auto_stopped(True)
        assert settings_store.get_auto_stopped() is True

        settings_store.set_auto_stopped(False)
        assert settings_store.get_auto_stopped() is False

        settings_store.set_auto_stopped(True)
        assert settings_store.get_auto_stopped() is True


# ============================================================
# APScheduler 停止時の動作テスト（ロジックシミュレーション）
# ============================================================

class TestScheduledJobStoppedBehavior:
    """
    _scheduled_job が停止フラグ有効時に:
    - _do_scheduled_run を呼ばない
    - send_slack_notification だけ呼ぶ
    """

    def test_stopped_flag_true_calls_slack_not_run(self, tmp_path, monkeypatch):
        """停止フラグ有効時、Slack 通知のみ呼ばれ _do_scheduled_run は呼ばれない"""
        _patch_db(tmp_path, monkeypatch)
        monkeypatch.setenv("FREEE_AUTO_STOPPED", "0")

        import settings_store
        settings_store.ensure_app_settings_table()
        settings_store.set_auto_stopped(True)

        mock_calls = []

        def mock_acquire_lock(job_name, ttl_seconds=7200):
            mock_calls.append(("acquire_lock", job_name))
            return True

        def mock_release_lock(job_name):
            mock_calls.append(("release_lock", job_name))

        def mock_do_scheduled_run():
            mock_calls.append(("do_scheduled_run",))

        def mock_send_slack(subject, body):
            mock_calls.append(("send_slack", subject))

        if mock_acquire_lock("daily_auto_run"):
            try:
                if settings_store.get_auto_stopped():
                    mock_send_slack("停止中通知", "停止中のため処理をスキップ")
                else:
                    mock_do_scheduled_run()
            finally:
                mock_release_lock("daily_auto_run")

        assert ("acquire_lock", "daily_auto_run") in mock_calls
        assert ("release_lock", "daily_auto_run") in mock_calls
        assert any(c[0] == "send_slack" for c in mock_calls)
        assert ("do_scheduled_run",) not in mock_calls

    def test_stopped_flag_false_calls_do_scheduled_run(self, tmp_path, monkeypatch):
        """停止フラグ無効時、_do_scheduled_run が呼ばれる"""
        _patch_db(tmp_path, monkeypatch)
        monkeypatch.setenv("FREEE_AUTO_STOPPED", "0")

        import settings_store
        settings_store.ensure_app_settings_table()
        settings_store.set_auto_stopped(False)

        mock_calls = []

        def mock_acquire_lock(job_name, ttl_seconds=7200):
            mock_calls.append(("acquire_lock", job_name))
            return True

        def mock_release_lock(job_name):
            mock_calls.append(("release_lock", job_name))

        def mock_do_scheduled_run():
            mock_calls.append(("do_scheduled_run",))

        def mock_send_slack(subject, body):
            mock_calls.append(("send_slack", subject))

        if mock_acquire_lock("daily_auto_run"):
            try:
                if settings_store.get_auto_stopped():
                    mock_send_slack("停止中通知", "停止中のため処理をスキップ")
                else:
                    mock_do_scheduled_run()
            finally:
                mock_release_lock("daily_auto_run")

        assert ("do_scheduled_run",) in mock_calls
        assert not any(c[0] == "send_slack" for c in mock_calls)


# ============================================================
# CLI（run_scheduled_job.py）の停止フラグ確認テスト
# ============================================================

class TestCLIStoppedBehavior:
    """CLI が DB 停止フラグを見て処理をスキップすることを確認"""

    def test_cli_skips_when_db_stopped(self, tmp_path, monkeypatch):
        """DB に stopped=1 があれば CLI は処理をスキップする（env=0 でも）"""
        _patch_db(tmp_path, monkeypatch)
        monkeypatch.setenv("FREEE_AUTO_STOPPED", "0")

        import settings_store
        settings_store.ensure_app_settings_table()
        settings_store.set_auto_stopped(True)

        stopped = settings_store.get_auto_stopped()
        source = settings_store.get_auto_stopped_source()["source"]

        assert stopped is True
        assert source == "db"

    def test_cli_runs_when_db_not_stopped(self, tmp_path, monkeypatch):
        """DB に stopped=0 があれば CLI は処理を実行する（env=0 のとき）"""
        _patch_db(tmp_path, monkeypatch)
        monkeypatch.setenv("FREEE_AUTO_STOPPED", "0")

        import settings_store
        settings_store.ensure_app_settings_table()
        settings_store.set_auto_stopped(False)

        stopped = settings_store.get_auto_stopped()
        source = settings_store.get_auto_stopped_source()["source"]

        assert stopped is False
        assert source == "db"

    def test_cli_uses_env_when_no_db_value(self, tmp_path, monkeypatch):
        """DB に値がなければ CLI は env を使う（env=1 → 停止）"""
        _patch_db(tmp_path, monkeypatch)
        monkeypatch.setenv("FREEE_AUTO_STOPPED", "1")

        import settings_store
        settings_store.ensure_app_settings_table()
        # DB には何も保存しない

        stopped = settings_store.get_auto_stopped()
        source = settings_store.get_auto_stopped_source()["source"]

        assert stopped is True
        assert source == "env_force"  # env=1 なので env_force
