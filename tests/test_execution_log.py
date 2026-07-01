"""
tests/test_execution_log.py
第6段階: 保守性改善テスト — execution_log の保存・取得

確認内容:
- _save_execution_log が DB に正しく保存される
- _get_execution_logs が正しく取得できる
- has_error フラグが正しく保存される
- log_type フィルタが機能する
- limit が機能する
- summary / detail が JSON として正しく保存・取得される

本番 freee / Notion / OpenAI / Slack は一切呼ばない。
本番 DB migration は実行しない。
"""
import os
import sys
import json
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


@pytest.fixture()
def isolated_db(tmp_path, monkeypatch):
    """テスト用 SQLite DB（本番 DB から完全に切り離す）"""
    db_path = str(tmp_path / "test_execlog.db")
    import db as db_module
    monkeypatch.setattr(db_module, "_DB_PATH", db_path)
    import app as app_module
    app_module._init_db()
    return db_path


class TestSaveExecutionLog:
    """_save_execution_log の動作テスト"""

    def test_save_and_retrieve_basic(self, isolated_db):
        """基本的な保存と取得"""
        import app as app_module
        summary = {"total": 3, "success": 2, "errors": 1}
        detail = {"results": [{"status": "done", "message": "OK"}]}

        app_module._save_execution_log(
            log_type="auto_transfer",
            summary=summary,
            detail=detail,
            trigger="scheduler",
            has_error=False,
        )

        logs = app_module._get_execution_logs("auto_transfer", limit=10)
        assert len(logs) == 1
        assert logs[0]["log_type"] == "auto_transfer"
        assert logs[0]["trigger"] == "scheduler"
        assert logs[0]["has_error"] is False
        assert logs[0]["summary"]["total"] == 3
        assert logs[0]["summary"]["success"] == 2
        assert logs[0]["detail"]["results"][0]["status"] == "done"

    def test_has_error_flag_true(self, isolated_db):
        """has_error=True が正しく保存される"""
        import app as app_module
        app_module._save_execution_log(
            log_type="auto_transfer",
            summary={"total": 1, "errors": 1},
            detail={"results": [{"status": "error", "message": "失敗"}]},
            trigger="scheduler",
            has_error=True,
        )

        logs = app_module._get_execution_logs("auto_transfer", limit=10)
        assert logs[0]["has_error"] is True

    def test_has_error_flag_false(self, isolated_db):
        """has_error=False が正しく保存される"""
        import app as app_module
        app_module._save_execution_log(
            log_type="auto_transfer",
            summary={"total": 1, "success": 1},
            detail={},
            trigger="manual",
            has_error=False,
        )

        logs = app_module._get_execution_logs("auto_transfer", limit=10)
        assert logs[0]["has_error"] is False

    def test_log_type_filter(self, isolated_db):
        """log_type フィルタが機能する"""
        import app as app_module
        app_module._save_execution_log("auto_transfer", {"total": 1}, {}, "scheduler", False)
        app_module._save_execution_log("payment_alert", {"total": 0}, {}, "scheduler", False)

        transfer_logs = app_module._get_execution_logs("auto_transfer", limit=10)
        payment_logs = app_module._get_execution_logs("payment_alert", limit=10)

        assert len(transfer_logs) == 1
        assert len(payment_logs) == 1
        assert transfer_logs[0]["log_type"] == "auto_transfer"
        assert payment_logs[0]["log_type"] == "payment_alert"

    def test_limit_is_respected(self, isolated_db):
        """limit が機能する"""
        import app as app_module
        for i in range(5):
            app_module._save_execution_log(
                "auto_transfer", {"total": i}, {}, "scheduler", False
            )

        logs = app_module._get_execution_logs("auto_transfer", limit=3)
        assert len(logs) == 3

    def test_order_is_newest_first(self, isolated_db):
        """最新のログが先頭に来る（DESC 順）"""
        import app as app_module
        app_module._save_execution_log("auto_transfer", {"seq": 1}, {}, "scheduler", False)
        app_module._save_execution_log("auto_transfer", {"seq": 2}, {}, "scheduler", False)
        app_module._save_execution_log("auto_transfer", {"seq": 3}, {}, "scheduler", False)

        logs = app_module._get_execution_logs("auto_transfer", limit=10)
        # id DESC なので seq=3 が先頭
        assert logs[0]["summary"]["seq"] == 3
        assert logs[-1]["summary"]["seq"] == 1

    def test_detail_none_defaults_to_empty_dict(self, isolated_db):
        """detail=None のとき {} として保存される"""
        import app as app_module
        app_module._save_execution_log(
            log_type="auto_transfer",
            summary={"total": 0},
            detail=None,
            trigger="manual",
            has_error=False,
        )

        logs = app_module._get_execution_logs("auto_transfer", limit=10)
        assert logs[0]["detail"] == {}

    def test_japanese_content_preserved(self, isolated_db):
        """日本語が文字化けせずに保存・取得される"""
        import app as app_module
        summary = {"message": "正常完了しました"}
        detail = {"results": [{"status": "done", "message": "株式会社テスト 登録完了"}]}

        app_module._save_execution_log("auto_transfer", summary, detail, "scheduler", False)

        logs = app_module._get_execution_logs("auto_transfer", limit=10)
        assert logs[0]["summary"]["message"] == "正常完了しました"
        assert logs[0]["detail"]["results"][0]["message"] == "株式会社テスト 登録完了"
