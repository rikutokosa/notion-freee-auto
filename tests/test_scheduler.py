"""tests/test_scheduler.py

_scheduled_job の動作を検証するテスト。
外部API（freee / Notion / OpenAI / Slack）は一切叩かない（monkeypatch使用）。
"""
import pytest
from unittest.mock import MagicMock


def test_scheduled_job_stopped_sends_slack_without_running_jobs(monkeypatch):
    import app as app_module
    mock_acquire = MagicMock(return_value=True)
    mock_release = MagicMock()
    mock_send_slack = MagicMock()
    mock_scheduled_run = MagicMock()
    mock_payment_alert = MagicMock()
    monkeypatch.setattr(app_module, "_is_manually_stopped", lambda: True)
    monkeypatch.setattr(app_module, "_acquire_job_lock", mock_acquire)
    monkeypatch.setattr(app_module, "_release_job_lock", mock_release)
    monkeypatch.setattr(app_module, "send_slack_notification", mock_send_slack)
    monkeypatch.setattr(app_module, "_do_scheduled_run", mock_scheduled_run)
    monkeypatch.setattr(app_module, "_do_payment_alert", mock_payment_alert)
    app_module._scheduled_job()
    mock_acquire.assert_called_once_with("daily_auto_run", ttl_seconds=7200)
    mock_release.assert_called_once_with("daily_auto_run")
    mock_scheduled_run.assert_not_called()
    mock_payment_alert.assert_not_called()
    mock_send_slack.assert_called_once()
    subject, body = mock_send_slack.call_args.args
    assert "停止中" in subject
    assert "FREEE_AUTO_STOPPED=1" in body
    assert "自動処理は実行していません" in body
