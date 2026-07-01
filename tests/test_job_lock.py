"""
tests/test_job_lock.py
第4段階: job化 / 自動実行方式の安全化テスト

確認内容:
- _acquire_job_lock が成功した場合 True を返す
- 同一 job_name で 2 回 _acquire_job_lock を呼ぶと 2 回目は False（重複実行防止）
- _release_job_lock 後は再取得できる
- TTL 切れのロックは再取得できる
- _scheduled_job で job_lock 取得失敗時に _do_scheduled_run が呼ばれない
- FREEE_AUTO_STOPPED=1 のとき _do_scheduled_run が呼ばれない
- FREEE_AUTO_STOPPED=1 のとき Slack 停止中通知が送られる
- FREEE_AUTO_STOPPED=0 のとき _do_scheduled_run が呼ばれる

本番 _do_scheduled_run / freee / Notion / OpenAI / Slack は一切呼ばない。
"""
import os
import sys
import sqlite3
import tempfile
from datetime import datetime, timezone, timedelta
from unittest.mock import MagicMock, patch, call
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


@pytest.fixture()
def isolated_db(tmp_path, monkeypatch):
    """テスト用 SQLite DB（本番 DB から完全に切り離す）"""
    db_path = str(tmp_path / "test_joblocks.db")
    import db as db_module
    monkeypatch.setattr(db_module, "_DB_PATH", db_path)
    # テーブルを初期化
    import app as app_module
    app_module._init_db()
    return db_path


# ---------------------------------------------------------------------------
# 第4段階: _acquire_job_lock / _release_job_lock 単体テスト
# ---------------------------------------------------------------------------

class TestAcquireJobLock:
    """_acquire_job_lock / _release_job_lock の動作テスト"""

    def test_acquire_returns_true_on_success(self, isolated_db):
        import app as app_module
        result = app_module._acquire_job_lock("test_job_001", ttl_seconds=3600)
        assert result is True, "初回取得が True でない"
        app_module._release_job_lock("test_job_001")

    def test_acquire_returns_false_when_locked(self, isolated_db):
        """同一 job_name で 2 回目は False（重複実行防止）"""
        import app as app_module
        result1 = app_module._acquire_job_lock("test_job_002", ttl_seconds=3600)
        result2 = app_module._acquire_job_lock("test_job_002", ttl_seconds=3600)
        assert result1 is True, "1回目が True でない"
        assert result2 is False, "2回目が False でない（重複実行防止が機能していない）"
        app_module._release_job_lock("test_job_002")

    def test_release_allows_reacquire(self, isolated_db):
        """_release_job_lock 後は再取得できる"""
        import app as app_module
        app_module._acquire_job_lock("test_job_003", ttl_seconds=3600)
        app_module._release_job_lock("test_job_003")
        result = app_module._acquire_job_lock("test_job_003", ttl_seconds=3600)
        assert result is True, "_release 後の再取得が True でない"
        app_module._release_job_lock("test_job_003")

    def test_expired_lock_can_be_reacquired(self, isolated_db):
        """TTL 切れのロックは再取得できる"""
        import app as app_module
        import db as db_module

        # 期限切れのロックを直接 DB に挿入
        conn = sqlite3.connect(isolated_db)
        past = (datetime.now(timezone.utc) - timedelta(seconds=10)).isoformat()
        conn.execute(
            "INSERT INTO job_locks (job_name, locked_at, expires_at) VALUES (?, ?, ?)",
            ("test_job_004", past, past)
        )
        conn.commit()
        conn.close()

        # 期限切れなので取得できるはず
        result = app_module._acquire_job_lock("test_job_004", ttl_seconds=3600)
        assert result is True, "TTL 切れのロックが再取得できない"
        app_module._release_job_lock("test_job_004")

    def test_different_jobs_can_be_locked_simultaneously(self, isolated_db):
        """異なる job_name は同時にロック取得できる"""
        import app as app_module
        r1 = app_module._acquire_job_lock("job_a", ttl_seconds=3600)
        r2 = app_module._acquire_job_lock("job_b", ttl_seconds=3600)
        assert r1 is True
        assert r2 is True
        app_module._release_job_lock("job_a")
        app_module._release_job_lock("job_b")


# ---------------------------------------------------------------------------
# 第4段階: _scheduled_job の安全性テスト
# ---------------------------------------------------------------------------

class TestScheduledJobSafety:
    """_scheduled_job の安全性テスト（本番 API は一切呼ばない）"""

    def test_lock_failure_skips_do_scheduled_run(self, isolated_db, monkeypatch):
        """job_lock 取得失敗時に _do_scheduled_run が呼ばれない"""
        import app as app_module

        mock_acquire = MagicMock(return_value=False)  # ロック取得失敗
        mock_run = MagicMock()
        mock_release = MagicMock()

        monkeypatch.setattr(app_module, "_acquire_job_lock", mock_acquire)
        monkeypatch.setattr(app_module, "_do_scheduled_run", mock_run)
        monkeypatch.setattr(app_module, "_release_job_lock", mock_release)

        app_module._scheduled_job()

        mock_acquire.assert_called_once_with("daily_auto_run", ttl_seconds=7200)
        mock_run.assert_not_called()
        mock_release.assert_not_called()

    def test_stopped_flag_skips_do_scheduled_run(self, isolated_db, monkeypatch):
        """FREEE_AUTO_STOPPED=1 のとき _do_scheduled_run が呼ばれない"""
        import app as app_module

        monkeypatch.setattr(app_module, "_manually_stopped", True)
        mock_acquire = MagicMock(return_value=True)
        mock_run = MagicMock()
        mock_release = MagicMock()
        mock_slack = MagicMock()

        monkeypatch.setattr(app_module, "_acquire_job_lock", mock_acquire)
        monkeypatch.setattr(app_module, "_do_scheduled_run", mock_run)
        monkeypatch.setattr(app_module, "_release_job_lock", mock_release)
        monkeypatch.setattr(app_module, "send_slack_notification", mock_slack)

        app_module._scheduled_job()

        mock_run.assert_not_called()
        mock_release.assert_called_once_with("daily_auto_run")

    def test_stopped_flag_sends_slack_notification(self, isolated_db, monkeypatch):
        """FREEE_AUTO_STOPPED=1 のとき Slack 停止中通知が送られる"""
        import app as app_module

        monkeypatch.setattr(app_module, "_manually_stopped", True)
        mock_acquire = MagicMock(return_value=True)
        mock_release = MagicMock()
        mock_slack = MagicMock()

        monkeypatch.setattr(app_module, "_acquire_job_lock", mock_acquire)
        monkeypatch.setattr(app_module, "_release_job_lock", mock_release)
        monkeypatch.setattr(app_module, "send_slack_notification", mock_slack)
        monkeypatch.setattr(app_module, "_do_scheduled_run", MagicMock())

        app_module._scheduled_job()

        mock_slack.assert_called_once()
        # 件名に「停止中」が含まれること
        subject = mock_slack.call_args[0][0]
        assert "停止中" in subject, f"Slack 通知の件名に '停止中' が含まれない: {subject}"

    def test_normal_run_calls_do_scheduled_run(self, isolated_db, monkeypatch):
        """FREEE_AUTO_STOPPED=0 のとき _do_scheduled_run が呼ばれる"""
        import app as app_module

        monkeypatch.setattr(app_module, "_manually_stopped", False)
        mock_acquire = MagicMock(return_value=True)
        mock_run = MagicMock()
        mock_alert = MagicMock()
        mock_release = MagicMock()

        monkeypatch.setattr(app_module, "_acquire_job_lock", mock_acquire)
        monkeypatch.setattr(app_module, "_do_scheduled_run", mock_run)
        monkeypatch.setattr(app_module, "_do_payment_alert", mock_alert)
        monkeypatch.setattr(app_module, "_release_job_lock", mock_release)

        app_module._scheduled_job()

        mock_run.assert_called_once()
        mock_release.assert_called_once_with("daily_auto_run")

    def test_lock_released_even_on_exception(self, isolated_db, monkeypatch):
        """_do_scheduled_run が例外を投げても _release_job_lock が呼ばれる"""
        import app as app_module

        monkeypatch.setattr(app_module, "_manually_stopped", False)
        mock_acquire = MagicMock(return_value=True)
        mock_run = MagicMock(side_effect=RuntimeError("test error"))
        mock_alert = MagicMock()
        mock_release = MagicMock()

        monkeypatch.setattr(app_module, "_acquire_job_lock", mock_acquire)
        monkeypatch.setattr(app_module, "_do_scheduled_run", mock_run)
        monkeypatch.setattr(app_module, "_do_payment_alert", mock_alert)
        monkeypatch.setattr(app_module, "_release_job_lock", mock_release)

        app_module._scheduled_job()  # 例外は握りつぶされる

        mock_release.assert_called_once_with("daily_auto_run")
