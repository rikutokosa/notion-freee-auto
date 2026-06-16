"""
Notion → freee 自動仕訳登録システム
Flask Webアプリ本体

エンドポイント:
  GET  /                     ダッシュボード
  GET  /preview              経理対応待ちプレビュー
  GET  /log                  処理ログ
  GET  /chat                 自然言語指示チャット
  POST /chat                 チャット送信
  GET  /auth/freee           freee OAuth認証開始
  GET  /auth/freee/callback  freee OAuth認証コールバック
  POST /run                  手動で全件処理
  POST /run/single           1件処理
  POST /polling/start        ポーリング開始
  POST /polling/stop         ポーリング停止
  GET  /api/pending          経理対応待ち一覧（JSON）
  GET  /api/logs             処理ログ（JSON）
  GET  /api/status           システム状態（JSON）
  POST /api/refresh_cache    freeeマスタキャッシュ更新
"""
import os
import json
import logging
import threading
from datetime import datetime
from flask import Flask, render_template, request, redirect, jsonify, url_for

from freee_client import (
    get_auth_url, exchange_code_for_token, get_valid_token,
    load_token, is_token_expired, clear_master_cache, get_master_cache,
    get_sections, get_tags, get_account_items, get_partners,
)
from notion_client import fetch_pending_records, get_record
from rules import build_journal_entries
from processor import run_once, process_single_by_id, processing_log

# ============================================================
# ロギング設定
# ============================================================
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

# ============================================================
# Flask アプリ
# ============================================================
app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "notion-freee-secret-2026")

POLL_INTERVAL = int(os.environ.get("POLL_INTERVAL", "3600"))

# ============================================================
# バックグラウンドポーリング
# ============================================================
_polling_thread = None
_polling_active = False


def start_polling():
    global _polling_thread, _polling_active
    if _polling_thread and _polling_thread.is_alive():
        return
    _polling_active = True

    def loop():
        import time
        while _polling_active:
            try:
                token = load_token()
                if token and not is_token_expired(token):
                    logger.info("freee自動転記実行")
                    run_once(db_type="all")
                else:
                    logger.warning("freeeトークン未設定または期限切れ。freee自動転記をスキップ。")
            except Exception as e:
                logger.exception(f"freee自動転記エラー: {e}")
            time.sleep(POLL_INTERVAL)

    _polling_thread = threading.Thread(target=loop, daemon=True)
    _polling_thread.start()
    logger.info(f"freee自動転記開始 (間隔: {POLL_INTERVAL}秒)")


# ============================================================
# ページルート
# ============================================================
@app.route("/")
def index():
    """ダッシュボード"""
    token_ok = False
    try:
        get_valid_token()
        token_ok = True
    except Exception:
        pass

    return render_template(
        "index.html",
        token_ok=token_ok,
        polling_active=_polling_thread is not None and _polling_thread.is_alive(),
        logs=processing_log[:50],
        poll_interval=POLL_INTERVAL,
    )


@app.route("/preview")
def preview():
    """②経理対応待ちレコードのプレビュー"""
    db_type = request.args.get("db", "all")
    try:
        records = fetch_pending_records(db_type)
        previews = []
        for record in records:
            journal = build_journal_entries(record)
            previews.append({
                "page_id": record.get("id"),
                "phase": journal.get("phase", ""),
                "job_db": journal.get("job_db", ""),
                "nyusha_date": journal.get("nyusha_date", ""),
                "action": journal.get("action", ""),
                "message": journal.get("message", ""),
                "needs_invoice": journal.get("needs_invoice", False),
                "original_status": journal.get("original_status", ""),
                "db_type": record.get("_db_type", "honten"),
                "sales_entry": journal.get("sales_entry"),
                "purchase_entry": journal.get("purchase_entry"),
                "pca_entry": journal.get("pca_entry"),
            })
        return render_template("preview.html", previews=previews, db_type=db_type)
    except Exception as e:
        logger.exception("プレビューエラー")
        return render_template("error.html", message=str(e))


@app.route("/log")
def log_page():
    """処理ログ一覧"""
    return render_template("log.html", logs=processing_log)


@app.route("/chat", methods=["GET", "POST"])
def chat():
    """自然言語指示チャット"""
    if request.method == "GET":
        token_ok = False
        try:
            get_valid_token()
            token_ok = True
        except Exception:
            pass
        return render_template("chat.html", token_ok=token_ok)

    data = request.get_json() or {}
    user_message = data.get("message", "").strip()
    if not user_message:
        return jsonify({"reply": "メッセージを入力してください"})

    reply = handle_chat_command(user_message)
    return jsonify({"reply": reply})


# ============================================================
# freee OAuth認証
# ============================================================
@app.route("/auth/freee")
def auth_freee():
    """freee OAuth認証開始"""
    redirect_uri = url_for("auth_freee_callback", _external=True)
    # APP_BASE_URL が設定されている場合はそちらを優先
    base_url = os.environ.get("APP_BASE_URL", "").rstrip("/")
    if base_url:
        redirect_uri = f"{base_url}/auth/freee/callback"
    auth_url = get_auth_url(redirect_uri, state="notion_freee")
    return redirect(auth_url)


@app.route("/auth/freee/callback")
def auth_freee_callback():
    """freee OAuth認証コールバック"""
    code = request.args.get("code")
    error = request.args.get("error")

    if error:
        return render_template("error.html", message=f"認証エラー: {error}")
    if not code:
        return render_template("error.html", message="認証コードが取得できませんでした")

    try:
        redirect_uri = url_for("auth_freee_callback", _external=True)
        base_url = os.environ.get("APP_BASE_URL", "").rstrip("/")
        if base_url:
            redirect_uri = f"{base_url}/auth/freee/callback"
        exchange_code_for_token(code, redirect_uri)
        start_polling()
        return redirect(url_for("index"))
    except Exception as e:
        logger.exception("トークン取得エラー")
        return render_template("error.html", message=f"トークン取得エラー: {str(e)}")


# ============================================================
# 手動実行
# ============================================================
@app.route("/run", methods=["POST"])
def run_manual():
    """手動で全件処理を実行する"""
    data = request.get_json() or {}
    db_type = data.get("db_type", "all")
    dry_run = data.get("dry_run", False)
    try:
        results = run_once(db_type=db_type, dry_run=dry_run)
        success = sum(1 for r in results if r.get("status") == "success")
        errors = sum(1 for r in results if r.get("status") == "error")
        reviews = sum(1 for r in results if r.get("status") == "review")
        return jsonify({
            "status": "ok",
            "total": len(results),
            "success": success,
            "errors": errors,
            "reviews": reviews,
            "dry_run": dry_run,
            "results": results,
        })
    except Exception as e:
        logger.exception("手動実行エラー")
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route("/run/single", methods=["POST"])
def run_single():
    """1件を処理する"""
    data = request.get_json() or {}
    page_id = data.get("page_id")
    db_type = data.get("db_type", "honten")
    dry_run = data.get("dry_run", False)
    if not page_id:
        return jsonify({"status": "error", "message": "page_id が必要です"}), 400
    try:
        result = process_single_by_id(page_id, db_type=db_type, dry_run=dry_run)
        return jsonify(result)
    except Exception as e:
        logger.exception("単件処理エラー")
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route("/polling/start", methods=["POST"])
def polling_start():
    start_polling()
    return jsonify({"status": "ok", "message": "freee自動転記開始"})


@app.route("/polling/stop", methods=["POST"])
def polling_stop():
    global _polling_active
    _polling_active = False
    return jsonify({"status": "ok", "message": "freee自動転記停止（再起動まで）"})


# ============================================================
# チャット指示（自然言語）
# ============================================================
def handle_chat_command(message: str) -> str:
    """自然言語コマンドを解釈して実行する"""
    msg = message.lower()

    # 全件実行
    if any(k in msg for k in ["実行", "登録", "転記", "処理", "run", "今すぐ", "全件"]):
        try:
            results = run_once(db_type="all")
            success = [r for r in results if r.get("status") == "success"]
            errors = [r for r in results if r.get("status") == "error"]
            reviews = [r for r in results if r.get("status") == "review"]
            lines = [f"処理完了: {len(results)}件"]
            if success:
                lines.append(f"✅ 成功: {len(success)}件")
                for r in success:
                    lines.append(f"  - {r.get('phase', '')} [{r.get('action','')}]: {r.get('message','')}")
            if reviews:
                lines.append(f"⚠️ 要確認: {len(reviews)}件")
                for r in reviews:
                    lines.append(f"  - {r.get('phase', '')}: {r.get('message', '')}")
            if errors:
                lines.append(f"❌ エラー: {len(errors)}件")
                for r in errors:
                    lines.append(f"  - {r.get('phase', '')}: {r.get('message', '')}")
            if not results:
                lines.append("経理対応待ちのレコードはありませんでした。")
            return "\n".join(lines)
        except Exception as e:
            return f"エラーが発生しました: {str(e)}"

    # 件数確認
    if any(k in msg for k in ["状態", "ステータス", "確認", "status", "何件", "いくつ"]):
        try:
            records = fetch_pending_records("all")
            if not records:
                return "経理対応待ちのレコードはありません。"
            lines = [f"経理対応待ちレコード: {len(records)}件"]
            for r in records:
                j = build_journal_entries(r)
                db = r.get("_db_type", "honten")
                lines.append(f"  [{db}] {j.get('phase','')} ({j.get('original_status','')}) → {j.get('action','')}")
            return "\n".join(lines)
        except Exception as e:
            return f"Notion接続エラー: {str(e)}"

    # ログ確認
    if any(k in msg for k in ["ログ", "履歴", "log", "history"]):
        if not processing_log:
            return "処理履歴はまだありません。"
        lines = ["直近の処理履歴:"]
        icons = {"success": "✅", "error": "❌", "review": "⚠️", "dry_run": "👁"}
        for r in processing_log[:10]:
            icon = icons.get(r.get("status", ""), "?")
            ts = r.get("timestamp", "")[:16]
            lines.append(f"{icon} {ts} {r.get('phase','')} - {r.get('message','')}")
        return "\n".join(lines)

    # 認証
    if any(k in msg for k in ["認証", "ログイン", "auth", "token"]):
        return "freee認証は /auth/freee から行ってください。"

    # ポーリング制御
    if any(k in msg for k in ["停止", "stop", "止め"]):
        global _polling_active
        _polling_active = False
        return "自動ポーリングを停止しました。"
    if any(k in msg for k in ["開始", "start", "再開"]):
        start_polling()
        return "自動ポーリングを開始しました。"

    # プレビュー
    if any(k in msg for k in ["プレビュー", "preview", "確認", "見せ"]):
        return "プレビューは /preview から確認できます。"

    return (
        "以下のコマンドが使えます:\n"
        "・「今すぐ処理して」「全件登録」 → 経理対応待ちレコードをfreeeに登録\n"
        "・「何件ある？」「状態確認」 → 未処理件数と内容を確認\n"
        "・「ログ見せて」「履歴」 → 処理履歴を表示\n"
        "・「ポーリング開始/停止」 → 自動処理の制御\n"
        "・「プレビュー」 → 登録前の仕訳内容を確認\n"
    )


# ============================================================
# API エンドポイント（JSON）
# ============================================================
@app.route("/api/pending")
def api_pending():
    db_type = request.args.get("db", "all")
    try:
        records = fetch_pending_records(db_type)
        result = []
        for r in records:
            j = build_journal_entries(r)
            result.append({
                "page_id": r.get("id"),
                "phase": j.get("phase"),
                "job_db": j.get("job_db"),
                "nyusha_date": j.get("nyusha_date"),
                "action": j.get("action"),
                "message": j.get("message"),
                "original_status": j.get("original_status"),
                "db_type": r.get("_db_type", "honten"),
            })
        return jsonify({"count": len(result), "records": result})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/logs")
def api_logs():
    limit = int(request.args.get("limit", 100))
    return jsonify(processing_log[:limit])


@app.route("/api/status")
def api_status():
    token_ok = False
    try:
        get_valid_token()
        token_ok = True
    except Exception:
        pass
    return jsonify({
        "token_ok": token_ok,
        "polling_active": _polling_thread is not None and _polling_thread.is_alive(),
        "log_count": len(processing_log),
        "poll_interval": POLL_INTERVAL,
    })


@app.route("/api/refresh_cache", methods=["POST"])
def api_refresh_cache():
    clear_master_cache()
    return jsonify({"message": "マスタキャッシュをクリアしました"})


@app.route("/api/freee_master")
def api_freee_master():
    """デバッグ用: freeeのマスタデータ（部門・メモタグ・動定科目・取引先）を返す"""
    try:
        sections = get_sections()
        tags = get_tags()
        account_items = get_account_items()
        partners = get_partners()
        return jsonify({
            "sections": [{"id": s.get("id"), "name": s.get("name")} for s in sections],
            "tags": [{"id": t.get("id"), "name": t.get("name")} for t in tags],
            "account_items": [{"id": a.get("id"), "name": a.get("name")} for a in account_items],
            "partners": [{"id": p.get("id"), "name": p.get("name")} for p in partners],
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ============================================================
# 起動
# ============================================================
if __name__ == "__main__":
    try:
        get_valid_token()
        start_polling()
        logger.info("トークン有効 → ポーリング自動開始")
    except Exception:
        logger.warning("freeeトークン未設定 → /auth/freee から認証してください")

    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
