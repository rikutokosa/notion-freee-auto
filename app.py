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
import tempfile
from pathlib import Path
from flask import Flask, render_template, request, redirect, jsonify, url_for

from freee_client import (
    get_auth_url, exchange_code_for_token, get_valid_token,
    load_token, is_token_expired, clear_master_cache, get_master_cache,
    get_sections, get_tags, get_account_items, get_partners,
    create_deal, upload_receipt,
)
from matcher import run_matching
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
# バックグラウンド自動転記
# ============================================================
_polling_thread = None
_polling_active = False

# 停止フラグ：Railway環境変数 FREEE_AUTO_STOPPED=1 で永続化
# ファイルはコンテナ再起動で消えるため使わない
# メモリ内フラグ（プロセス内永続）
_manually_stopped: bool = os.environ.get("FREEE_AUTO_STOPPED", "0") == "1"


def _is_manually_stopped() -> bool:
    """手動停止中かどうかを返す（メモリ内フラグ）"""
    return _manually_stopped


def _set_manually_stopped(stopped: bool):
    """停止フラグをメモリ内に設定する"""
    global _manually_stopped
    _manually_stopped = stopped
    # Railway環境変数にも書き込む（プロセス内のみ有効、再起動後は環境変数の初期値に戻る）
    os.environ["FREEE_AUTO_STOPPED"] = "1" if stopped else "0"


def start_polling(force: bool = False):
    """自動転記を開始する。force=Trueの場合は手動停止フラグを無視して開始。"""
    global _polling_thread, _polling_active
    # 手動停止中は開始しない（forceの場合のみ例外）
    if not force and _is_manually_stopped():
        logger.info("自動転記: 手動停止中のため自動開始をスキップ")
        return
    if _polling_thread and _polling_thread.is_alive():
        return
    _polling_active = True
    _set_manually_stopped(False)

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
        logs=processing_log[:50],
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
    """freee自動転記を手動で開始（手動停止フラグをクリアして強制開始）"""
    start_polling(force=True)
    return jsonify({"status": "ok", "message": "freee自動転記開始"})


@app.route("/polling/stop", methods=["POST"])
def polling_stop():
    """freee自動転記を手動停止（フラグをファイルに保存して再認証後も自動開始しない）"""
    global _polling_active
    _polling_active = False
    _set_manually_stopped(True)
    return jsonify({"status": "ok", "message": "freee自動転記を停止しました。手動で「自動転記開始」を押すまで停止します。"})


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
    import os
    openai_key = os.environ.get("OPENAI_API_KEY", "")
    return jsonify({
        "token_ok": token_ok,
        "mode": "manual",
        "log_count": len(processing_log),
        "openai_key_set": bool(openai_key),
        "openai_key_prefix": openai_key[:10] + "..." if openai_key else "",
    })


@app.route("/api/refresh_cache", methods=["POST"])
def api_refresh_cache():
    clear_master_cache()
    return jsonify({"message": "マスタキャッシュをクリアしました"})


# ============================================================
# 書類照合
# ============================================================
@app.route("/api/match_receipts", methods=["POST"])
def api_match_receipts():
    """
    freeeファイルボックスの未登録書類を既存仕訳と自動照合・紐づけする
    """
    data = request.get_json() or {}
    dry_run = data.get("dry_run", False)
    try:
        result = run_matching(dry_run=dry_run)
        return jsonify(result)
    except Exception as e:
        logger.exception("書類照合エラー")
        return jsonify({"error": str(e)}), 500


@app.route("/api/debug_receipts")
def api_debug_receipts():
    """デバッグ用: freee receipts APIの生レスポンスを返す"""
    try:
        import requests as req
        from datetime import datetime, timedelta
        token = get_valid_token()
        end_date = datetime.now().strftime("%Y-%m-%d")
        start_date = (datetime.now() - timedelta(days=180)).strftime("%Y-%m-%d")
        from freee_client import FREEE_API_BASE, FREEE_COMPANY_ID, _api_headers
        # 一覧
        params = {"company_id": FREEE_COMPANY_ID, "category": "without_deal",
                  "start_date": start_date, "end_date": end_date, "limit": 2}
        resp = req.get(f"{FREEE_API_BASE}/receipts", headers=_api_headers(), params=params, timeout=30)
        list_data = resp.json()
        # 個別
        detail_data = None
        receipts = list_data.get("receipts", [])
        if receipts:
            rid = receipts[0]["id"]
            resp2 = req.get(f"{FREEE_API_BASE}/receipts/{rid}",
                           headers=_api_headers(), params={"company_id": FREEE_COMPANY_ID}, timeout=30)
            detail_data = resp2.json()
        return jsonify({"list_response": list_data, "detail_response": detail_data})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/iv_companies")
def api_iv_companies():
    """デバッグ用: freee請求書APIのcompany_id一覧を返す"""
    try:
        import requests as req
        token = get_valid_token()
        resp = req.get(
            "https://api.freee.co.jp/iv/companies",
            headers={"Authorization": f"Bearer {token}"},
            timeout=10,
        )
        return jsonify({"status": resp.status_code, "body": resp.json()})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


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


@app.route("/api/test_deal", methods=["POST"])
def api_test_deal():
    """デバッグ用: freeeに取引を直接登録してエラー詳細を返す"""
    try:
        token = get_valid_token()
        import requests as req
        payload = request.get_json()
        # freeeのAPIはフラットなJSONを直接送る（dealラップ不要）
        company_id = int(os.environ.get("FREEE_COMPANY_ID", "1856949"))
        deal_payload = {
            "company_id": company_id,
            "issue_date": payload["issue_date"],
            "type": payload.get("type", "income"),
            "details": payload.get("details", []),
        }
        if payload.get("due_date"):
            deal_payload["due_date"] = payload["due_date"]
        if payload.get("partner_name"):
            deal_payload["partner_name"] = payload["partner_name"]
        import logging
        logging.getLogger(__name__).info(f"test_deal送信ペイロード: {deal_payload}")
        resp = req.post(
            "https://api.freee.co.jp/api/1/deals",
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            json=deal_payload,
            timeout=30,
        )
        return jsonify({"status": resp.status_code, "body": resp.json()})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/test_invoice", methods=["POST"])
def api_test_invoice():
    """デバッグ用: freeeに請求書を直接登録してエラー詳細を返す"""
    try:
        token = get_valid_token()
        import requests as req
        payload = request.get_json()
        # freee請求書APIはフラットなJSONを直接送る（invoiceラップ不要）
        import logging
        logging.getLogger(__name__).info(f"test_invoice送信ペイロード: {payload}")
        resp = req.post(
            "https://api.freee.co.jp/iv/invoices",
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            json=payload,
            timeout=30,
        )
        return jsonify({"status": resp.status_code, "body": resp.json()})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/test_invoice_put", methods=["POST"])
def api_test_invoice_put():
    """デバッグ用: freee請求書APIのPUT /invoices/{id}をテスト"""
    try:
        token = get_valid_token()
        import requests as req
        data = request.get_json()
        invoice_id = data.pop("invoice_id")
        import logging
        logging.getLogger(__name__).info(f"test_invoice_put送信: id={invoice_id}, payload={data}")
        resp = req.put(
            f"https://api.freee.co.jp/iv/invoices/{invoice_id}",
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            json=data,
            timeout=30,
        )
        return jsonify({"status": resp.status_code, "body": resp.json()})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ============================================================
# 仕訳アシスタント
# ============================================================
@app.route("/assistant")
def assistant():
    """仕訳アシスタントページ"""
    token_ok = False
    try:
        get_valid_token()
        token_ok = True
    except Exception:
        pass
    return render_template("assistant.html", token_ok=token_ok)


@app.route("/api/assistant/register", methods=["POST"])
def api_assistant_register():
    """フォーム入力から直接freeeに仕訳登録する"""
    data = request.get_json() or {}
    try:
        cache = get_master_cache()
        deal_type = data.get("deal_type", "income")
        issue_date = data["issue_date"]
        due_date = data.get("due_date")
        partner_name = data.get("partner_name")
        details_raw = data.get("details", [])
        repeat = data.get("repeat", False)
        repeat_count = int(data.get("repeat_count", 1))

        # 明細行に section_name を付与（detail内にあれば使う）
        details = []
        section_name = None
        for d in details_raw:
            sn = d.pop("section_name", None)
            if sn and not section_name:
                section_name = sn
            details.append(d)

        registered_ids = []
        from dateutil.relativedelta import relativedelta
        from datetime import date
        base_date = date.fromisoformat(issue_date)

        count = repeat_count if repeat else 1
        for i in range(count):
            current_date = base_date + relativedelta(months=i)
            current_due = None
            if due_date:
                base_due = date.fromisoformat(due_date)
                current_due = (base_due + relativedelta(months=i)).isoformat()

            entry = {
                "issue_date": current_date.isoformat(),
                "due_date": current_due,
                "partner_name": partner_name,
                "section_name": section_name,
                "details": details,
            }
            deal = create_deal(entry, deal_type, cache)
            registered_ids.append(deal.get("id"))

        return jsonify({"status": "ok", "registered": len(registered_ids), "ids": registered_ids})
    except Exception as e:
        logger.exception("仕訳アシスタント登録エラー")
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route("/api/assistant/register_bulk", methods=["POST"])
def api_assistant_register_bulk():
    """AI生成の仕訳エントリを一括登録する"""
    data = request.get_json() or {}
    entries = data.get("entries", [])
    try:
        cache = get_master_cache()
        registered_ids = []
        for entry in entries:
            deal_type = entry.pop("deal_type", "income")
            deal = create_deal(entry, deal_type, cache)
            registered_ids.append(deal.get("id"))
        return jsonify({"status": "ok", "registered": len(registered_ids), "ids": registered_ids})
    except Exception as e:
        logger.exception("AI仕訳一括登録エラー")
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route("/api/assistant/ai", methods=["POST"])
def api_assistant_ai():
    """自然言語指示をAIで解釈してfreee仕訳エントリを生成する"""
    import os, json as _json
    data = request.get_json() or {}
    user_message = data.get("message", "")
    history = data.get("history", [])
    master = data.get("master", {})

    try:
        import requests as req
        openai_key = os.environ.get("OPENAI_API_KEY", "")
        openai_base = os.environ.get("OPENAI_API_BASE", "https://api.openai.com/v1")

        if not openai_key:
            return jsonify({"error": "OpenAI APIキーが設定されていません"}), 500

        # マスタデータのサマリーを作成
        sections_list = ", ".join([s["name"] for s in master.get("sections", [])])
        tags_list = ", ".join([t["name"] for t in master.get("tags", [])])
        account_items_list = ", ".join([a["name"] for a in master.get("account_items", [])])
        partners_list = ", ".join([p["name"] for p in master.get("partners", [])][:50])

        system_prompt = f"""あなたはfreee会計の仕訳登録アシスタントです。
ユーザーの自然言語の指示を解釈し、freeeに登録するための仕訳データをJSON形式で生成してください。

【freeeに登録されているマスタデータ】
部門: {sections_list}
メモタグ: {tags_list}
勘定科目（一部）: {account_items_list}
取引先（一部）: {partners_list}

【出力形式】
回答は以下の形式で返してください：
1. まず日本語で仕訳の内容を説明する
2. 次に以下のJSON形式でエントリを返す（```json と ``` で囲む）

```json
[
  {{
    "deal_type": "income" または "expense",
    "issue_date": "YYYY-MM-DD",
    "due_date": "YYYY-MM-DD" または null,
    "partner_name": "取引先名" または null,
    "details": [
      {{
        "account_item_name": "勘定科目名（マスタに存在するもの）",
        "amount": 金額（税抜・整数）,
        "tax_code": 1（課税売上10%）または 7（課税仕入10%）または 0（不課税）,
        "section_name": "部門名" または null,
        "tag_names": ["タグ名"] または [],
        "description": "備考"
      }}
    ]
  }}
]
```

繰り返し仕訳の場合は複数のエントリを配列で返してください。
勘定科目は必ずマスタデータに存在する名称を使用してください。"""

        messages = [{"role": "system", "content": system_prompt}]
        for h in history[-6:]:  # 直近3往復
            messages.append(h)
        messages.append({"role": "user", "content": user_message})

        resp = req.post(
            f"{openai_base}/chat/completions",
            headers={"Authorization": f"Bearer {openai_key}", "Content-Type": "application/json"},
            json={"model": "gpt-4o-mini", "messages": messages, "temperature": 0.2},
            timeout=30,
        )
        resp.raise_for_status()
        reply_text = resp.json()["choices"][0]["message"]["content"]

        # JSONブロックを抽出
        entries = []
        import re
        json_match = re.search(r'```json\s*([\s\S]+?)\s*```', reply_text)
        if json_match:
            try:
                entries = _json.loads(json_match.group(1))
                # JSONブロックを除いたテキストを返す
                reply_clean = reply_text[:json_match.start()].strip() + reply_text[json_match.end():].strip()
            except Exception:
                reply_clean = reply_text
        else:
            reply_clean = reply_text

        return jsonify({"reply": reply_clean, "entries": entries})

    except Exception as e:
        logger.exception("AI仕訳生成エラー")
        return jsonify({"error": str(e)}), 500


# ============================================================
# 証憑アップロード
# ============================================================
@app.route("/api/assistant/upload_receipt", methods=["POST"])
def api_upload_receipt():
    """証憑ファイルをfreeeにアップロードして取引に紐付ける"""
    if 'file' not in request.files:
        return jsonify({"status": "error", "message": "ファイルが指定されていません"}), 400
    file = request.files['file']
    deal_id = request.form.get('deal_id')
    description = request.form.get('description', file.filename)
    try:
        with tempfile.NamedTemporaryFile(delete=False, suffix=Path(file.filename).suffix) as tmp:
            file.save(tmp.name)
            receipt = upload_receipt(
                tmp.name,
                deal_id=int(deal_id) if deal_id else None,
                description=description,
            )
        return jsonify({"status": "ok", "receipt_id": receipt.get("id")})
    except Exception as e:
        logger.exception("証憑アップロードエラー")
        return jsonify({"status": "error", "message": str(e)}), 500


# ============================================================
# ファイル読み取り（AI仕訳提案用）
# ============================================================
@app.route("/api/assistant/extract_file", methods=["POST"])
def api_extract_file():
    """
    アップロードされたファイルからテキストを抽出してAIに渡す
    対応形式: PDF, 画像(PNG/JPG), Excel(xlsx/xls), CSV
    """
    files = request.files.getlist('files')
    user_message = request.form.get('message', '')
    if not files:
        return jsonify({"contents": []})

    contents = []
    for file in files:
        filename = file.filename
        suffix = Path(filename).suffix.lower()
        try:
            with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
                file.save(tmp.name)
                tmp_path = tmp.name

            text = _extract_file_text(tmp_path, filename, suffix)
            if text:
                contents.append(f"【{filename}】\n{text}")
        except Exception as e:
            logger.exception(f"ファイル読み取りエラー: {filename}")
            contents.append(f"【{filename}】読み取りエラー: {str(e)}")

    return jsonify({"contents": contents})


def _extract_file_text(path: str, filename: str, suffix: str) -> str:
    """ファイルからテキストを抽出する"""
    import subprocess

    # CSV
    if suffix == '.csv':
        import csv
        rows = []
        with open(path, 'r', encoding='utf-8-sig', errors='replace') as f:
            reader = csv.reader(f)
            for i, row in enumerate(reader):
                rows.append(','.join(row))
                if i > 200:  # 最大200行
                    rows.append('...(以下省略)')
                    break
        return '\n'.join(rows)

    # Excel
    if suffix in ('.xlsx', '.xls'):
        try:
            import openpyxl
            wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
            lines = []
            for sheet in wb.sheetnames[:3]:  # 最大3シート
                ws = wb[sheet]
                lines.append(f'=== シート: {sheet} ===')
                for i, row in enumerate(ws.iter_rows(values_only=True)):
                    if any(c is not None for c in row):
                        lines.append('\t'.join([str(c) if c is not None else '' for c in row]))
                    if i > 300:
                        lines.append('...(以下省略)')
                        break
            return '\n'.join(lines)
        except Exception:
            pass
        # xlsはxlrdで試みる
        try:
            import xlrd
            wb = xlrd.open_workbook(path)
            lines = []
            for sheet in wb.sheets()[:3]:
                lines.append(f'=== シート: {sheet.name} ===')
                for i in range(min(sheet.nrows, 300)):
                    lines.append('\t'.join([str(sheet.cell_value(i, j)) for j in range(sheet.ncols)]))
            return '\n'.join(lines)
        except Exception:
            pass
        return ''

    # PDF
    if suffix == '.pdf':
        try:
            result = subprocess.run(
                ['pdftotext', '-layout', path, '-'],
                capture_output=True, text=True, timeout=30
            )
            text = result.stdout.strip()
            if text:
                return text[:8000]  # 最大8000文字
        except Exception:
            pass
        # pdftotext失敗時はpdf2imageで画像化してOCR
        try:
            from pdf2image import convert_from_path
            images = convert_from_path(path, first_page=1, last_page=3, dpi=150)
            texts = []
            for img in images:
                img_path = path + '_page.png'
                img.save(img_path, 'PNG')
                t = _ocr_image_with_openai(img_path)
                if t:
                    texts.append(t)
            return '\n'.join(texts)[:8000]
        except Exception:
            pass
        return ''

    # 画像（PNG/JPG）
    if suffix in ('.png', '.jpg', '.jpeg'):
        return _ocr_image_with_openai(path)

    return ''


def _ocr_image_with_openai(image_path: str) -> str:
    """OpenAI Vision APIで画像からテキストを抽出する"""
    import base64
    import requests as req
    openai_key = os.environ.get("OPENAI_API_KEY", "")
    openai_base = os.environ.get("OPENAI_API_BASE", "https://api.openai.com/v1")
    if not openai_key:
        return ''
    try:
        with open(image_path, 'rb') as f:
            b64 = base64.b64encode(f.read()).decode()
        suffix = Path(image_path).suffix.lower().lstrip('.')
        mime = 'image/png' if suffix == 'png' else 'image/jpeg'
        resp = req.post(
            f"{openai_base}/chat/completions",
            headers={"Authorization": f"Bearer {openai_key}", "Content-Type": "application/json"},
            json={
                "model": "gpt-4o-mini",
                "messages": [{
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "この画像（請求書・領収書・返済スケジュール表など）のテキスト内容をすべて抽出してください。表形式の場合は表のまま出力してください。"},
                        {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}}
                    ]
                }],
                "max_tokens": 2000,
            },
            timeout=30,
        )
        if resp.status_code == 200:
            return resp.json()["choices"][0]["message"]["content"]
    except Exception as e:
        logger.warning(f"OCRエラー: {e}")
    return ''


# ============================================================
# 起動
# ============================================================
if __name__ == "__main__":
    try:
        get_valid_token()
        logger.info("トークン有効。手動実行待機中。")
    except Exception:
        logger.warning("freeeトークン未設定 → /auth/freee から認証してください")

    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
