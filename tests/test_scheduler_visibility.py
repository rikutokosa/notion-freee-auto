"""
tests/test_scheduler_visibility.py
scheduler 状態の見える化テスト

確認内容:
- 正しい Basic 認証つき /api/status に scheduler キーが含まれる
- 正しい Basic 認証つき /api/healthcheck に scheduler キーが含まれる
- scheduler キーに必要なフィールドが揃っている
- /health には scheduler 詳細が含まれない
- 未認証 /api/status は 401 のまま
- 未認証 /api/healthcheck は 401 のまま
- FREEE_AUTO_STOPPED=1 の場合に is_manually_stopped が True になる

freee / Notion / OpenAI / Slack は一切呼ばない。
本番 API は絶対に叩かない。
"""
import pytest
from unittest.mock import MagicMock, patch


# ---------------------------------------------------------------------------
# /health には scheduler 詳細が含まれないこと
# ---------------------------------------------------------------------------

class TestHealthNoSchedulerInfo:
    """GET /health は scheduler 詳細を返さない"""

    def test_health_has_no_scheduler_key(self, flask_client):
        resp = flask_client.get("/health")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data is not None
        assert "scheduler" not in data, (
            f"/health に scheduler キーが含まれている: {data}"
        )

    def test_health_only_returns_status_ok(self, flask_client):
        resp = flask_client.get("/health")
        data = resp.get_json()
        assert data == {"status": "ok"}, (
            f"/health のレスポンスが想定外: {data}"
        )


# ---------------------------------------------------------------------------
# 未認証保護ルートは 401 のまま
# ---------------------------------------------------------------------------

class TestUnauthProtectedRoutes:
    """未認証では /api/status・/api/healthcheck は 401"""

    def test_api_status_no_auth_returns_401(self, flask_client):
        resp = flask_client.get("/api/status")
        assert resp.status_code == 401, (
            f"Expected 401, got {resp.status_code}"
        )

    def test_api_healthcheck_no_auth_returns_401(self, flask_client):
        resp = flask_client.get("/api/healthcheck")
        assert resp.status_code == 401, (
            f"Expected 401, got {resp.status_code}"
        )


# ---------------------------------------------------------------------------
# /api/status に scheduler キーが含まれること
# ---------------------------------------------------------------------------

class TestApiStatusSchedulerInfo:
    """GET /api/status（認証あり）に scheduler 情報が含まれる"""

    def test_api_status_returns_200_with_auth(self, flask_client, auth_headers):
        resp = flask_client.get("/api/status", headers=auth_headers)
        assert resp.status_code == 200, (
            f"Expected 200, got {resp.status_code}"
        )

    def test_api_status_has_scheduler_key(self, flask_client, auth_headers):
        resp = flask_client.get("/api/status", headers=auth_headers)
        data = resp.get_json()
        assert data is not None
        assert "scheduler" in data, (
            f"/api/status に scheduler キーが存在しない: {data}"
        )

    def test_api_status_scheduler_has_required_fields(self, flask_client, auth_headers):
        resp = flask_client.get("/api/status", headers=auth_headers)
        data = resp.get_json()
        scheduler = data["scheduler"]
        required_fields = [
            "freee_auto_stopped_env",
            "is_manually_stopped",
            "scheduler_exists",
            "job_registered",
            "next_run_time",
        ]
        for field in required_fields:
            assert field in scheduler, (
                f"scheduler に {field} キーが存在しない: {scheduler}"
            )

    def test_api_status_scheduler_exists_is_bool(self, flask_client, auth_headers):
        resp = flask_client.get("/api/status", headers=auth_headers)
        data = resp.get_json()
        assert isinstance(data["scheduler"]["scheduler_exists"], bool), (
            f"scheduler_exists が bool でない: {data['scheduler']}"
        )

    def test_api_status_is_manually_stopped_is_bool(self, flask_client, auth_headers):
        resp = flask_client.get("/api/status", headers=auth_headers)
        data = resp.get_json()
        assert isinstance(data["scheduler"]["is_manually_stopped"], bool), (
            f"is_manually_stopped が bool でない: {data['scheduler']}"
        )

    def test_api_status_freee_auto_stopped_env_is_string(self, flask_client, auth_headers):
        resp = flask_client.get("/api/status", headers=auth_headers)
        data = resp.get_json()
        assert isinstance(data["scheduler"]["freee_auto_stopped_env"], str), (
            f"freee_auto_stopped_env が str でない: {data['scheduler']}"
        )


# ---------------------------------------------------------------------------
# /api/healthcheck に scheduler キーが含まれること
# ---------------------------------------------------------------------------

class TestApiHealthcheckSchedulerInfo:
    """GET /api/healthcheck（認証あり）に scheduler 情報が含まれる"""

    def test_api_healthcheck_returns_200_with_auth(self, flask_client, auth_headers, monkeypatch):
        """freee/Notion API 呼び出しをすべて mock して 200 を確認"""
        import app as app_module
        # freee/Notion への外部 HTTP リクエストを mock
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"sections": [], "tags": []}
        monkeypatch.setattr("app.requests.get", lambda *a, **kw: mock_resp)
        monkeypatch.setattr("app.requests.post", lambda *a, **kw: mock_resp)
        monkeypatch.setattr("app.get_valid_token", lambda: "dummy_token")

        resp = flask_client.get("/api/healthcheck", headers=auth_headers)
        assert resp.status_code == 200, (
            f"Expected 200, got {resp.status_code}"
        )

    def test_api_healthcheck_has_scheduler_key(self, flask_client, auth_headers, monkeypatch):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"sections": [], "tags": []}
        monkeypatch.setattr("app.requests.get", lambda *a, **kw: mock_resp)
        monkeypatch.setattr("app.requests.post", lambda *a, **kw: mock_resp)
        monkeypatch.setattr("app.get_valid_token", lambda: "dummy_token")

        resp = flask_client.get("/api/healthcheck", headers=auth_headers)
        data = resp.get_json()
        assert data is not None
        assert "scheduler" in data, (
            f"/api/healthcheck に scheduler キーが存在しない: {data}"
        )

    def test_api_healthcheck_scheduler_has_required_fields(
        self, flask_client, auth_headers, monkeypatch
    ):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"sections": [], "tags": []}
        monkeypatch.setattr("app.requests.get", lambda *a, **kw: mock_resp)
        monkeypatch.setattr("app.requests.post", lambda *a, **kw: mock_resp)
        monkeypatch.setattr("app.get_valid_token", lambda: "dummy_token")

        resp = flask_client.get("/api/healthcheck", headers=auth_headers)
        data = resp.get_json()
        scheduler = data["scheduler"]
        required_fields = [
            "freee_auto_stopped_env",
            "is_manually_stopped",
            "scheduler_exists",
            "job_registered",
            "next_run_time",
        ]
        for field in required_fields:
            assert field in scheduler, (
                f"scheduler に {field} キーが存在しない: {scheduler}"
            )


# ---------------------------------------------------------------------------
# FREEE_AUTO_STOPPED=1 の場合の挙動
# ---------------------------------------------------------------------------

class TestFreeeAutoStoppedFlag:
    """FREEE_AUTO_STOPPED=1 の場合 is_manually_stopped=True が返る"""

    def test_stopped_flag_true_when_env_is_1(self, flask_client, auth_headers, monkeypatch):
        import app as app_module
        # _is_manually_stopped を True を返すように差し替え
        monkeypatch.setattr(app_module, "_manually_stopped", True)
        monkeypatch.setenv("FREEE_AUTO_STOPPED", "1")

        resp = flask_client.get("/api/status", headers=auth_headers)
        data = resp.get_json()
        scheduler = data["scheduler"]
        assert scheduler["is_manually_stopped"] is True, (
            f"FREEE_AUTO_STOPPED=1 なのに is_manually_stopped が False: {scheduler}"
        )
        assert scheduler["freee_auto_stopped_env"] == "1", (
            f"freee_auto_stopped_env が '1' でない: {scheduler}"
        )

    def test_stopped_flag_false_when_env_is_0(self, flask_client, auth_headers, monkeypatch):
        import app as app_module
        monkeypatch.setattr(app_module, "_manually_stopped", False)
        monkeypatch.setenv("FREEE_AUTO_STOPPED", "0")

        resp = flask_client.get("/api/status", headers=auth_headers)
        data = resp.get_json()
        scheduler = data["scheduler"]
        assert scheduler["is_manually_stopped"] is False, (
            f"FREEE_AUTO_STOPPED=0 なのに is_manually_stopped が True: {scheduler}"
        )
        assert scheduler["freee_auto_stopped_env"] == "0", (
            f"freee_auto_stopped_env が '0' でない: {scheduler}"
        )


# ---------------------------------------------------------------------------
# _get_scheduler_info 単体テスト
# ---------------------------------------------------------------------------

class TestGetSchedulerInfoUnit:
    """_get_scheduler_info 関数の単体テスト"""

    def test_returns_dict_with_all_keys(self, monkeypatch):
        import app as app_module
        monkeypatch.setattr(app_module, "_manually_stopped", False)
        monkeypatch.setenv("FREEE_AUTO_STOPPED", "0")

        info = app_module._get_scheduler_info()
        assert isinstance(info, dict)
        for key in [
            "freee_auto_stopped_env",
            "is_manually_stopped",
            "scheduler_exists",
            "job_registered",
            "next_run_time",
        ]:
            assert key in info, f"{key} が存在しない: {info}"

    def test_scheduler_exists_true_when_scheduler_running(self, monkeypatch):
        import app as app_module
        # _scheduler が None でないことを確認
        assert app_module._scheduler is not None, (
            "_scheduler が None（テスト環境でスケジューラが起動していない）"
        )
        info = app_module._get_scheduler_info()
        assert info["scheduler_exists"] is True

    def test_job_registered_true_for_daily_auto_run(self, monkeypatch):
        import app as app_module
        info = app_module._get_scheduler_info()
        # テスト環境でもスケジューラが起動していれば job_registered=True
        assert info["job_registered"] is True, (
            f"daily_auto_run ジョブが登録されていない: {info}"
        )

    def test_scheduler_none_returns_false_fields(self, monkeypatch):
        import app as app_module
        # _scheduler を None に差し替えて scheduler_exists=False を確認
        monkeypatch.setattr(app_module, "_scheduler", None)
        info = app_module._get_scheduler_info()
        assert info["scheduler_exists"] is False
        assert info["job_registered"] is False
        assert info["next_run_time"] is None
