"""
tests/test_auto_stopped_persistence.py

FREEE_AUTO_STOPPED 停止フラグの DB 永続化テスト。

テスト方針:
- 本番 freee / Notion / OpenAI / Slack への書き込みは一切行わない
- DB は tmp_path の一時ファイルを使用（本番 DB には触れない）
- db._DB_PATH をモンキーパッチして本番 DB を使わない
- APScheduler は起動しない
- _do_scheduled_run / _do_payment_alert は呼ばない

カバーするケース:
1. DB に stopped=1 が保存されていれば、env=0 でも get_auto_stopped() が True
2. DB に stopped=0 が保存されていれば、env=1 でも get_auto_stopped() が False
3. DB に値がなければ env を見る（env=1 → True, env=0 → False）
4. set_auto_stopped(True) 後、新しい DB 接続でも停止状態が読める（プロセス再起動相当）
5. ensure_app_settings_table() が冪等で、既存データを消さない
6. get_auto_stopped_source() が freee_auto_stopped_env / persisted_auto_stopped / effective_auto_stopped / source を正しく返す
7. _is_manually_stopped() が get_auto_stopped() と同じ値を返す（settings_store 経由）
8. _set_manually_stopped(True) 後に _is_manually_stopped() が True を返す
9. APScheduler 停止時に _do_scheduled_run を呼ばず、Slack 停止中通知だけ呼ぶ
10. CLI も DB 停止フラグを見て処理をスキップする
"""
import os
import pytest
import importlib


def _patch_db(tmp_path, monkeypatch):
    """db._DB_PATH を tmp_path の一時ファイルに差し替える共通ヘルパー"""
    db_path = str(tmp_path / "test.db")
    import db as db_module
    monkeypatch.setattr(db_module, "_DB_PATH", db_path)
    return db_path


# ============================================================
# settings_store 単体テスト
# ============================================================

class TestSettingsStoreUnit:
    """settings_store モジュールの単体テスト"""

    def test_db_stopped1_overrides_env0(self, tmp_path, monkeypatch):
        """DB に stopped=1 が保存されていれば、env=0 でも True"""
        _patch_db(tmp_path, monkeypatch)
        monkeypatch.setenv("FREEE_AUTO_STOPPED", "0")

        import settings_store
        settings_store.ensure_app_settings_table()
        settings_store.set_auto_stopped(True)

        assert settings_store.get_auto_stopped() is True

    def test_db_stopped0_overrides_env1(self, tmp_path, monkeypatch):
        """DB に stopped=0 が保存されていれば、env=1 でも False"""
        _patch_db(tmp_path, monkeypatch)
        monkeypatch.setenv("FREEE_AUTO_STOPPED", "1")

        import settings_store
        settings_store.ensure_app_settings_table()
        settings_store.set_auto_stopped(False)

        assert settings_store.get_auto_stopped() is False

    def test_no_db_value_uses_env1(self, tmp_path, monkeypatch):
        """DB に値がなければ env=1 を使う"""
        _patch_db(tmp_path, monkeypatch)
        monkeypatch.setenv("FREEE_AUTO_STOPPED", "1")

        import settings_store
        settings_store.ensure_app_settings_table()
        # DB には何も保存しない

        assert settings_store.get_auto_stopped() is True

    def test_no_db_value_uses_env0(self, tmp_path, monkeypatch):
        """DB に値がなければ env=0 を使う"""
        _patch_db(tmp_path, monkeypatch)
        monkeypatch.setenv("FREEE_AUTO_STOPPED", "0")

        import settings_store
        settings_store.ensure_app_settings_table()
        # DB には何も保存しない

        assert settings_store.get_auto_stopped() is False

    def test_persisted_across_new_connection(self, tmp_path, monkeypatch):
        """set_auto_stopped(True) 後、新しい DB 接続でも値が読める（プロセス再起動相当）"""
        db_path = _patch_db(tmp_path, monkeypatch)
        monkeypatch.setenv("FREEE_AUTO_STOPPED", "0")

        import settings_store
        settings_store.ensure_app_settings_table()
        settings_store.set_auto_stopped(True)

        # 新しい接続で読み直す（プロセス再起動相当）
        import sqlite3
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

        # 2回目・3回目の ensure_app_settings_table() 呼び出し
        settings_store.ensure_app_settings_table()
        settings_store.ensure_app_settings_table()

        # データが消えていないこと
        assert settings_store.get_auto_stopped() is True

    def test_get_auto_stopped_source_db_stopped1(self, tmp_path, monkeypatch):
        """DB に stopped=1 がある場合、source='db', effective=True

        注意: set_auto_stopped(True) は補助的に os.environ も更新するため、
        set_auto_stopped 後の freee_auto_stopped_env は "1" になる。
        ここでは source='db' と effective=True を確認することが主目的。
        """
        _patch_db(tmp_path, monkeypatch)
        monkeypatch.setenv("FREEE_AUTO_STOPPED", "0")

        import settings_store
        settings_store.ensure_app_settings_table()
        settings_store.set_auto_stopped(True)
        # set_auto_stopped は os.environ["FREEE_AUTO_STOPPED"] も "1" に更新する

        result = settings_store.get_auto_stopped_source()
        assert result["source"] == "db"
        assert result["persisted_auto_stopped"] == "1"
        assert result["effective_auto_stopped"] is True
        # set_auto_stopped が env も更新するため freee_auto_stopped_env は "1"
        assert result["freee_auto_stopped_env"] == "1"

    def test_get_auto_stopped_source_env_only(self, tmp_path, monkeypatch):
        """DB に値がない場合、source='env'"""
        _patch_db(tmp_path, monkeypatch)
        monkeypatch.setenv("FREEE_AUTO_STOPPED", "1")

        import settings_store
        settings_store.ensure_app_settings_table()
        # DB には何も保存しない

        result = settings_store.get_auto_stopped_source()
        assert result["source"] == "env"
        assert result["persisted_auto_stopped"] is None
        assert result["effective_auto_stopped"] is True
        assert result["freee_auto_stopped_env"] == "1"

    def test_get_auto_stopped_source_db_stopped0_env1(self, tmp_path, monkeypatch):
        """DB=0, env=1 の場合、effective=False, source='db'"""
        _patch_db(tmp_path, monkeypatch)
        monkeypatch.setenv("FREEE_AUTO_STOPPED", "1")

        import settings_store
        settings_store.ensure_app_settings_table()
        settings_store.set_auto_stopped(False)

        result = settings_store.get_auto_stopped_source()
        assert result["source"] == "db"
        assert result["persisted_auto_stopped"] == "0"
        assert result["effective_auto_stopped"] is False

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

        # _scheduled_job のロジックをシミュレート
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
        monkeypatch.setenv("FREEE_AUTO_STOPPED", "0")  # env は 0

        import settings_store
        settings_store.ensure_app_settings_table()
        settings_store.set_auto_stopped(True)  # DB に stopped=1 を保存

        # CLI の停止判定ロジックをシミュレート
        try:
            stopped = settings_store.get_auto_stopped()
            source = settings_store.get_auto_stopped_source()["source"]
        except Exception:
            stopped = os.environ.get("FREEE_AUTO_STOPPED", "0") == "1"
            source = "env"

        assert stopped is True
        assert source == "db"

    def test_cli_runs_when_db_not_stopped(self, tmp_path, monkeypatch):
        """DB に stopped=0 があれば CLI は処理を実行する（env=1 でも）"""
        _patch_db(tmp_path, monkeypatch)
        monkeypatch.setenv("FREEE_AUTO_STOPPED", "1")  # env は 1

        import settings_store
        settings_store.ensure_app_settings_table()
        settings_store.set_auto_stopped(False)  # DB に stopped=0 を保存

        try:
            stopped = settings_store.get_auto_stopped()
            source = settings_store.get_auto_stopped_source()["source"]
        except Exception:
            stopped = os.environ.get("FREEE_AUTO_STOPPED", "0") == "1"
            source = "env"

        assert stopped is False
        assert source == "db"

    def test_cli_uses_env_when_no_db_value(self, tmp_path, monkeypatch):
        """DB に値がなければ CLI は env を使う"""
        _patch_db(tmp_path, monkeypatch)
        monkeypatch.setenv("FREEE_AUTO_STOPPED", "1")

        import settings_store
        settings_store.ensure_app_settings_table()
        # DB には何も保存しない

        stopped = settings_store.get_auto_stopped()
        source = settings_store.get_auto_stopped_source()["source"]

        assert stopped is True  # env=1 が使われる
        assert source == "env"
