"""tests/test_safe_scheduled_run_api.py

/api/scheduled_run エンドポイントの安全化テスト。

テスト方針:
- 本番 freee / Notion / OpenAI / Slack への書き込みは一切行わない
- DB は tmp_path の一時ファイルを使用（本番 DB には触れない）
- _do_scheduled_run / _do_payment_alert は monkeypatch でモック化する
- _scheduled_job は呼ばない
- テスト内に本体ロジックのコピーは持たない
  → app.scheduled_run（Flask ビュー）を flask_client 経由で直接叩く

安全化要件:
1. 認証なしでは 401 を返し _do_scheduled_run が呼ばれない
2. 停止中（_is_manually_stopped=True）は 503 を返し _do_scheduled_run が呼ばれない
3. job_lock 取得失敗時は 409 を返し _do_scheduled_run が呼ばれない
4. 停止判定で例外が発生した場合は 503 を返し _do_scheduled_run が呼ばれない
5. job_lock 取得で例外が発生した場合は 503 を返し _do_scheduled_run が呼ばれない
6. 稼働中かつ job_lock 取得成功時のみ 202 を返し _do_scheduled_run が呼ばれる
"""
import base64
import pytest
from unittest.mock import MagicMock


def _auth_header(user="testuser", password="testpass"):
    """Basic 認証ヘッダーを生成する"""
    creds = base64.b64encode(f"{user}:{password}".encode()).decode()
    return {"Authorization": f"Basic {creds}"}


@pytest.fixture()
def client_with_safe_mocks(flask_client, monkeypatch):
    """
    /api/scheduled_run テスト用の Flask クライアント。
    _do_scheduled_run / _do_payment_alert をモック化し、
    本番 API を一切呼ばない状態にする。
    """
    import app as app_module

    mock_do_run = MagicMock()
    mock_do_alert = MagicMock()

    monkeypatch.setattr(app_module, "_do_scheduled_run", mock_do_run)
    monkeypatch.setattr(app_module, "_do_payment_alert", mock_do_alert)

    return flask_client, mock_do_run, mock_do_alert


# ============================================================
# 認証テスト
# ============================================================

class TestScheduledRunAuth:
    """Basic 認証が正しく機能することを確認する"""

    def test_no_auth_returns_401(self, client_with_safe_mocks, monkeypatch):
        """認証なしでは 401 を返し _do_scheduled_run が呼ばれない"""
        flask_client, mock_do_run, _ = client_with_safe_mocks
        import app as app_module
        monkeypatch.setattr(app_module, "_is_manually_stopped", lambda: False)
        monkeypatch.setattr(app_module, "_acquire_job_lock", MagicMock(return_value=True))
        monkeypatch.setattr(app_module, "_release_job_lock", MagicMock())

        resp = flask_client.post("/api/scheduled_run")

        assert resp.status_code == 401, (
            f"認証なしで 401 が返るべき（実際: {resp.status_code}）"
        )
        mock_do_run.assert_not_called()

    def test_wrong_auth_returns_401(self, client_with_safe_mocks, monkeypatch):
        """誤った認証情報では 401 を返し _do_scheduled_run が呼ばれない"""
        flask_client, mock_do_run, _ = client_with_safe_mocks
        import app as app_module
        monkeypatch.setattr(app_module, "_is_manually_stopped", lambda: False)
        monkeypatch.setattr(app_module, "_acquire_job_lock", MagicMock(return_value=True))
        monkeypatch.setattr(app_module, "_release_job_lock", MagicMock())

        resp = flask_client.post(
            "/api/scheduled_run",
            headers=_auth_header("wrong", "wrong"),
        )

        assert resp.status_code == 401, (
            f"誤認証で 401 が返るべき（実際: {resp.status_code}）"
        )
        mock_do_run.assert_not_called()


# ============================================================
# 停止判定テスト
# ============================================================

class TestScheduledRunStoppedFlag:
    """停止フラグが有効な場合に _do_scheduled_run が呼ばれないことを確認する"""

    def test_stopped_returns_503_and_skips_run(self, client_with_safe_mocks, monkeypatch):
        """
        _is_manually_stopped=True のとき 503 を返し _do_scheduled_run が呼ばれない。
        本体の stopped チェック経路（L2318-2323）を直接通す。
        """
        flask_client, mock_do_run, _ = client_with_safe_mocks
        import app as app_module
        monkeypatch.setattr(app_module, "_is_manually_stopped", lambda: True)
        monkeypatch.setattr(app_module, "_acquire_job_lock", MagicMock(return_value=True))
        monkeypatch.setattr(app_module, "_release_job_lock", MagicMock())

        resp = flask_client.post(
            "/api/scheduled_run",
            headers=_auth_header(),
        )

        assert resp.status_code == 503, (
            f"停止中は 503 が返るべき（実際: {resp.status_code}）"
        )
        data = resp.get_json()
        assert data["status"] == "skipped", (
            f"status が 'skipped' であるべき（実際: {data.get('status')}）"
        )
        mock_do_run.assert_not_called()

    def test_stopped_flag_exception_returns_503_and_skips_run(
        self, client_with_safe_mocks, monkeypatch
    ):
        """
        _is_manually_stopped が例外を投げた場合、fail-safe で 503 を返し
        _do_scheduled_run が呼ばれない。
        本体の except 節（L2309-2316）を直接通す。
        """
        flask_client, mock_do_run, _ = client_with_safe_mocks
        import app as app_module

        def broken_is_stopped():
            raise RuntimeError("停止判定DB例外（テスト用）")

        monkeypatch.setattr(app_module, "_is_manually_stopped", broken_is_stopped)
        monkeypatch.setattr(app_module, "_acquire_job_lock", MagicMock(return_value=True))
        monkeypatch.setattr(app_module, "_release_job_lock", MagicMock())

        resp = flask_client.post(
            "/api/scheduled_run",
            headers=_auth_header(),
        )

        assert resp.status_code == 503, (
            f"停止判定例外時は 503 が返るべき（実際: {resp.status_code}）"
        )
        data = resp.get_json()
        assert data["status"] == "error", (
            f"status が 'error' であるべき（実際: {data.get('status')}）"
        )
        mock_do_run.assert_not_called()


# ============================================================
# job_lock テスト
# ============================================================

class TestScheduledRunJobLock:
    """job_lock が正しく機能することを確認する"""

    def test_lock_failure_returns_409_and_skips_run(self, client_with_safe_mocks, monkeypatch):
        """
        _acquire_job_lock が False を返した場合、409 を返し _do_scheduled_run が呼ばれない。
        本体の lock 取得失敗経路（L2337-2342）を直接通す。
        """
        flask_client, mock_do_run, _ = client_with_safe_mocks
        import app as app_module
        monkeypatch.setattr(app_module, "_is_manually_stopped", lambda: False)
        monkeypatch.setattr(app_module, "_acquire_job_lock", MagicMock(return_value=False))
        monkeypatch.setattr(app_module, "_release_job_lock", MagicMock())

        resp = flask_client.post(
            "/api/scheduled_run",
            headers=_auth_header(),
        )

        assert resp.status_code == 409, (
            f"lock 取得失敗時は 409 が返るべき（実際: {resp.status_code}）"
        )
        data = resp.get_json()
        assert data["status"] == "conflict", (
            f"status が 'conflict' であるべき（実際: {data.get('status')}）"
        )
        mock_do_run.assert_not_called()

    def test_lock_exception_returns_503_and_skips_run(self, client_with_safe_mocks, monkeypatch):
        """
        _acquire_job_lock が例外を投げた場合、fail-safe で 503 を返し
        _do_scheduled_run が呼ばれない。
        本体の lock except 節（L2328-2335）を直接通す。
        """
        flask_client, mock_do_run, _ = client_with_safe_mocks
        import app as app_module
        monkeypatch.setattr(app_module, "_is_manually_stopped", lambda: False)

        def broken_acquire(*args, **kwargs):
            raise RuntimeError("job_lock DB例外（テスト用）")

        monkeypatch.setattr(app_module, "_acquire_job_lock", broken_acquire)
        monkeypatch.setattr(app_module, "_release_job_lock", MagicMock())

        resp = flask_client.post(
            "/api/scheduled_run",
            headers=_auth_header(),
        )

        assert resp.status_code == 503, (
            f"lock 例外時は 503 が返るべき（実際: {resp.status_code}）"
        )
        data = resp.get_json()
        assert data["status"] == "error", (
            f"status が 'error' であるべき（実際: {data.get('status')}）"
        )
        mock_do_run.assert_not_called()


# ============================================================
# 正常実行テスト
# ============================================================

class TestScheduledRunNormalExecution:
    """稼働中かつ lock 取得成功時に _do_scheduled_run が呼ばれることを確認する"""

    def test_running_with_lock_returns_202(self, client_with_safe_mocks, monkeypatch):
        """
        _is_manually_stopped=False かつ _acquire_job_lock=True のとき、
        202 を返し _do_scheduled_run がバックグラウンドで呼ばれる。
        本体の正常経路（L2344-2355）を直接通す。
        """
        import threading
        import time

        flask_client, mock_do_run, _ = client_with_safe_mocks
        import app as app_module
        monkeypatch.setattr(app_module, "_is_manually_stopped", lambda: False)
        monkeypatch.setattr(app_module, "_acquire_job_lock", MagicMock(return_value=True))
        monkeypatch.setattr(app_module, "_release_job_lock", MagicMock())

        resp = flask_client.post(
            "/api/scheduled_run",
            headers=_auth_header(),
        )

        assert resp.status_code == 202, (
            f"稼働中かつ lock 取得成功時は 202 が返るべき（実際: {resp.status_code}）"
        )
        data = resp.get_json()
        assert data["status"] == "accepted", (
            f"status が 'accepted' であるべき（実際: {data.get('status')}）"
        )

        # バックグラウンドスレッドの完了を待つ（最大2秒）
        for _ in range(20):
            if mock_do_run.call_count > 0:
                break
            time.sleep(0.1)

        mock_do_run.assert_called_once()
