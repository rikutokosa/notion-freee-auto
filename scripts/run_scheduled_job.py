#!/usr/bin/env python3
"""
scripts/run_scheduled_job.py
Railway cron または手動実行から _do_scheduled_run を呼ぶ CLI エントリポイント。

使い方:
    python3 scripts/run_scheduled_job.py [--dry-run]

オプション:
    --dry-run   処理内容を確認するだけで、freee / Notion への書き込みを行わない

安全制約:
    - 停止フラグは DB 優先（app_settings.auto_stopped）、DB に値がなければ
      環境変数 FREEE_AUTO_STOPPED をフォールバックとして使用する
    - 停止中の場合は処理をスキップし、Slack 停止中通知のみ送る
    - job_lock を取得してから実行し、重複実行を防ぐ
    - 本番 DB migration は実行しない
    - Railway 環境変数は変更しない
    - freee / Notion / OpenAI / Slack への書き込みは --dry-run で抑制できる

注意:
    このスクリプトは APScheduler の代替ではなく、
    Railway cron から呼べる最小 CLI として提供する。
    APScheduler は引き続き app.py 内で動作する。
"""
import sys
import os
import logging

# プロジェクトルートを sys.path に追加
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


def _determine_stopped() -> bool:
    """
    停止フラグを判定して返す。

    DB 優先（app_settings.auto_stopped）、DB に値がなければ
    環境変数 FREEE_AUTO_STOPPED をフォールバックとして使用する。
    settings_store の読み取りで例外が発生した場合は fail-safe として
    True（停止扱い）を返す。本番経理系なので読み取り失敗時は停止側に倒す。

    Returns:
        True  → 停止中（処理をスキップすべき）
        False → 稼働中（処理を実行してよい）
    """
    try:
        from settings_store import get_auto_stopped, ensure_app_settings_table
        ensure_app_settings_table()
        stopped = get_auto_stopped()
        logger.info(f"[CLI] 停止フラグ確認: stopped={stopped} (DB優先、envフォールバック)")
        return stopped
    except Exception as e:
        logger.error(
            f"[CLI] settings_store 読み取り失敗のため fail-safe で停止扱いにします: {e}"
        )
        return True  # fail-safe: 本番経理系なので読み取り失敗時は停止側に倒す


def main():
    dry_run = "--dry-run" in sys.argv

    if dry_run:
        logger.info("[CLI] --dry-run モードで実行します（freee / Notion への書き込みなし）")

    # 停止フラグ確認: DB 優先、DB に値がなければ環境変数をフォールバック
    stopped = _determine_stopped()

    if stopped:
        logger.info("[CLI] 停止フラグが有効なため処理をスキップします")
        try:
            from app import send_slack_notification
            from datetime import datetime, timezone, timedelta
            JST = timezone(timedelta(hours=9))
            now_str = datetime.now(JST).strftime("%Y-%m-%d %H:%M")
            subject = f"⏸️ 本店経理自動化 日次実行停止中 {now_str}"
            body = "\n".join([
                "本店経理自動化システム 日次実行レポート",
                f"実行日時: {now_str} JST",
                "=" * 50,
                "",
                "停止フラグが有効なため、日次自動実行をスキップしました。",
                "freee / Notion / OpenAI への自動処理は実行していません。",
                "",
                "自動実行を再開するには、Railway Variables の FREEE_AUTO_STOPPED を 0 に変更し、",
                "または管理画面から停止フラグを解除してください。",
            ])
            if not dry_run:
                send_slack_notification(subject, body)
            else:
                logger.info(f"[CLI] dry_run: Slack 通知をスキップ: {subject}")
        except Exception as e:
            logger.warning(f"[CLI] Slack 通知失敗: {e}")
        sys.exit(0)

    # job_lock 取得
    try:
        from app import _acquire_job_lock, _release_job_lock, _do_scheduled_run, _init_db
    except ImportError as e:
        logger.error(f"[CLI] app.py のインポートに失敗: {e}")
        sys.exit(1)

    _init_db()

    if not _acquire_job_lock("daily_auto_run", ttl_seconds=7200):
        logger.warning("[CLI] 別プロセスが実行中のためスキップ（job_lock 取得失敗）")
        sys.exit(0)

    try:
        if dry_run:
            logger.info("[CLI] dry_run: _do_scheduled_run をスキップ")
        else:
            logger.info("[CLI] _do_scheduled_run を開始します")
            _do_scheduled_run()
            logger.info("[CLI] _do_scheduled_run が完了しました")
    except Exception as e:
        logger.exception(f"[CLI] _do_scheduled_run でエラー: {e}")
        sys.exit(1)
    finally:
        _release_job_lock("daily_auto_run")


if __name__ == "__main__":
    main()
