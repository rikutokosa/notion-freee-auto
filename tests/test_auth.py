"""
Basic認証テスト

- /health は未認証で 200
- /api/status は未認証で 401
- /api/healthcheck は未認証で 401
- 正しい Basic 認証なら保護 API にアクセスできる
"""
import base64
import pytest


def _basic(user: str, password: str) -> dict:
    creds = base64.b64encode(f"{user}:{password}".encode()).decode()
    return {"Authorization": f"Basic {creds}"}


class TestHealthEndpoint:
    """GET /health は認証不要で 200 を返す"""

    def test_health_no_auth_returns_200(self, flask_client):
        resp = flask_client.get("/health")
        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}"

    def test_health_returns_ok_json(self, flask_client):
        resp = flask_client.get("/health")
        data = resp.get_json()
        assert data is not None
        assert data.get("status") == "ok"


class TestProtectedEndpoints:
    """認証が必要なエンドポイントは未認証で 401 を返す"""

    def test_api_status_no_auth_returns_401(self, flask_client):
        resp = flask_client.get("/api/status")
        assert resp.status_code == 401, f"Expected 401, got {resp.status_code}"

    def test_api_healthcheck_no_auth_returns_401(self, flask_client):
        resp = flask_client.get("/api/healthcheck")
        assert resp.status_code == 401, f"Expected 401, got {resp.status_code}"

    def test_root_no_auth_returns_401(self, flask_client):
        resp = flask_client.get("/")
        assert resp.status_code == 401, f"Expected 401, got {resp.status_code}"

    def test_wrong_auth_returns_401(self, flask_client):
        resp = flask_client.get("/api/status", headers=_basic("wrong", "wrong"))
        assert resp.status_code == 401, f"Expected 401, got {resp.status_code}"


class TestCorrectAuth:
    """正しい Basic 認証なら保護 API にアクセスできる"""

    def test_api_status_with_correct_auth(self, flask_client, auth_headers):
        resp = flask_client.get("/api/status", headers=auth_headers)
        # /api/status は get_valid_token() の例外を握りつぶして常に 200 を返す
        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}"
        data = resp.get_json()
        assert data is not None, "レスポンスが JSON でない"
        assert "token_ok" in data, f"token_ok キーが存在しない: {data}"

    def test_api_healthcheck_with_correct_auth(self, flask_client, auth_headers):
        resp = flask_client.get("/api/healthcheck", headers=auth_headers)
        # /api/healthcheck は freee 接続エラーを warnings に入れて 200 を返す
        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}"
        data = resp.get_json()
        assert data is not None, "レスポンスが JSON でない"
        assert "status" in data, f"status キーが存在しない: {data}"
