"""
tests/test_inbox_api.py
第5段階: 要対応インボックス UI テスト

確認内容:
- 未認証 /api/inbox は 401
- 正しい Basic 認証つき /api/inbox は 200
- レスポンスに error_count / review_count / last_run / recent_errors / recent_reviews が含まれる
- ログなしの場合は 0 件で正常に返る
- error ステータスのログが recent_errors に集計される
- review ステータスのログが recent_reviews に集計される
- /health には inbox 情報が出ない

本番 freee / Notion / OpenAI / Slack は一切呼ばない。
本番 DB migration は実行しない。
"""
import os
import sys
import json
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


@pytest.fixture()
def client_with_db(tmp_path, monkeypatch):
    """テスト用 Flask クライアント（独立 DB）"""
    db_path = str(tmp_path / "test_inbox.db")
    import db as db_module
    monkeypatch.setattr(db_module, "_DB_PATH", db_path)

    import app as app_module
    app_module._init_db()

    monkeypatch.setenv("BASIC_AUTH_USER", "testuser")
    monkeypatch.setenv("BASIC_AUTH_PASS", "testpass")
    monkeypatch.setattr(app_module, "_BASIC_USER", "testuser")
    monkeypatch.setattr(app_module, "_BASIC_PASS", "testpass")

    app_module.app.config["TESTING"] = True
    with app_module.app.test_client() as c:
        yield c, app_module, db_path


def _auth_headers():
    import base64
    creds = base64.b64encode(b"testuser:testpass").decode()
    return {"Authorization": f"Basic {creds}"}


# ---------------------------------------------------------------------------
# 第5段階: 認証テスト
# ---------------------------------------------------------------------------

class TestInboxAuth:
    """/api/inbox の認証テスト"""

    def test_unauthenticated_returns_401(self, client_with_db):
        client, _, _ = client_with_db
        resp = client.get("/api/inbox")
        assert resp.status_code == 401, f"未認証で 401 でない: {resp.status_code}"

    def test_authenticated_returns_200(self, client_with_db):
        client, _, _ = client_with_db
        resp = client.get("/api/inbox", headers=_auth_headers())
        assert resp.status_code == 200, f"認証済みで 200 でない: {resp.status_code}"

    def test_wrong_password_returns_401(self, client_with_db):
        import base64
        client, _, _ = client_with_db
        creds = base64.b64encode(b"testuser:wrongpass").decode()
        resp = client.get("/api/inbox", headers={"Authorization": f"Basic {creds}"})
        assert resp.status_code == 401


# ---------------------------------------------------------------------------
# 第5段階: レスポンス構造テスト
# ---------------------------------------------------------------------------

class TestInboxResponse:
    """/api/inbox のレスポンス内容テスト"""

    def test_empty_logs_returns_zero_counts(self, client_with_db):
        """ログなしの場合は 0 件で正常に返る"""
        client, _, _ = client_with_db
        resp = client.get("/api/inbox", headers=_auth_headers())
        data = resp.get_json()

        assert "error_count" in data, "error_count キーがない"
        assert "review_count" in data, "review_count キーがない"
        assert "last_run" in data, "last_run キーがない"
        assert "recent_errors" in data, "recent_errors キーがない"
        assert "recent_reviews" in data, "recent_reviews キーがない"

        assert data["error_count"] == 0
        assert data["review_count"] == 0
        assert data["last_run"] is None
        assert data["recent_errors"] == []
        assert data["recent_reviews"] == []

    def test_error_log_appears_in_recent_errors(self, client_with_db):
        """error ステータスのログが recent_errors に集計される"""
        import sqlite3
        client, app_module, db_path = client_with_db

        # error を含む execution_log を挿入
        detail = json.dumps({
            "results": [
                {"status": "error", "message": "freee API エラー", "page_id": "page_001", "action": "register"},
                {"status": "done", "message": "正常完了", "page_id": "page_002", "action": "register"},
            ]
        }, ensure_ascii=False)
        summary = json.dumps({"total": 2, "success": 1, "errors": 1}, ensure_ascii=False)

        conn = sqlite3.connect(db_path)
        conn.execute(
            "INSERT INTO execution_logs (log_type, executed_at, trigger, summary, detail, has_error) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            ("auto_transfer", "2026-07-01 12:00", "scheduler", summary, detail, 1)
        )
        conn.commit()
        conn.close()

        resp = client.get("/api/inbox", headers=_auth_headers())
        data = resp.get_json()

        assert data["error_count"] == 1, f"error_count が 1 でない: {data['error_count']}"
        assert len(data["recent_errors"]) == 1
        assert data["recent_errors"][0]["status"] == "error"
        assert data["recent_errors"][0]["page_id"] == "page_001"

    def test_review_log_appears_in_recent_reviews(self, client_with_db):
        """review ステータスのログが recent_reviews に集計される"""
        import sqlite3
        client, app_module, db_path = client_with_db

        detail = json.dumps({
            "results": [
                {"status": "review", "message": "要確認: freee ID なし", "page_id": "page_003", "action": "delete"},
            ]
        }, ensure_ascii=False)
        summary = json.dumps({"total": 1, "reviews": 1}, ensure_ascii=False)

        conn = sqlite3.connect(db_path)
        conn.execute(
            "INSERT INTO execution_logs (log_type, executed_at, trigger, summary, detail, has_error) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            ("auto_transfer", "2026-07-01 12:00", "scheduler", summary, detail, 0)
        )
        conn.commit()
        conn.close()

        resp = client.get("/api/inbox", headers=_auth_headers())
        data = resp.get_json()

        assert data["review_count"] == 1, f"review_count が 1 でない: {data['review_count']}"
        assert len(data["recent_reviews"]) == 1
        assert data["recent_reviews"][0]["status"] == "review"
        assert data["recent_reviews"][0]["page_id"] == "page_003"

    def test_last_run_is_populated_when_logs_exist(self, client_with_db):
        """ログがある場合 last_run が返る"""
        import sqlite3
        client, app_module, db_path = client_with_db

        detail = json.dumps({"results": []}, ensure_ascii=False)
        summary = json.dumps({"total": 0}, ensure_ascii=False)

        conn = sqlite3.connect(db_path)
        conn.execute(
            "INSERT INTO execution_logs (log_type, executed_at, trigger, summary, detail, has_error) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            ("auto_transfer", "2026-07-01 12:00", "scheduler", summary, detail, 0)
        )
        conn.commit()
        conn.close()

        resp = client.get("/api/inbox", headers=_auth_headers())
        data = resp.get_json()

        assert data["last_run"] is not None, "last_run が None"
        assert data["last_run"]["executed_at"] == "2026-07-01 12:00"
        assert data["last_run"]["trigger"] == "scheduler"

    def test_health_does_not_include_inbox_info(self, client_with_db):
        """/health には inbox 情報が出ない"""
        client, _, _ = client_with_db
        resp = client.get("/health")
        assert resp.status_code == 200
        data = resp.get_json()
        assert "error_count" not in data, "/health に error_count が含まれている"
        assert "review_count" not in data, "/health に review_count が含まれている"
        assert "recent_errors" not in data, "/health に recent_errors が含まれている"

    def test_recent_errors_capped_at_10(self, client_with_db):
        """recent_errors は最大 10 件に制限される"""
        import sqlite3
        client, app_module, db_path = client_with_db

        # 15件の error を挿入
        conn = sqlite3.connect(db_path)
        for i in range(15):
            detail = json.dumps({
                "results": [
                    {"status": "error", "message": f"エラー {i}", "page_id": f"page_{i:03d}", "action": "register"},
                ]
            }, ensure_ascii=False)
            summary = json.dumps({"total": 1, "errors": 1}, ensure_ascii=False)
            conn.execute(
                "INSERT INTO execution_logs (log_type, executed_at, trigger, summary, detail, has_error) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                ("auto_transfer", f"2026-07-01 12:{i:02d}", "scheduler", summary, detail, 1)
            )
        conn.commit()
        conn.close()

        resp = client.get("/api/inbox", headers=_auth_headers())
        data = resp.get_json()

        assert len(data["recent_errors"]) <= 10, (
            f"recent_errors が 10 件を超えている: {len(data['recent_errors'])}"
        )
