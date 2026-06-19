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
    create_deal, upload_receipt, create_partner,
    search_deals, search_invoices, delete_deal, delete_invoice,
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
# 仕訳アシスタント登録ログ（メモリ内、最新200件）
assistant_log: list = []


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
        return render_template("preview.html", previews=previews, db_type=db_type, assistant_log=assistant_log[:50])
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


@app.route("/api/get_refresh_token")
def api_get_refresh_token():
    """現在のリフレッシュトークンを返す（Railway環境変数設定用）"""
    token_data = load_token()
    if not token_data:
        return jsonify({"error": "トークン未認証。/auth/freee から認証してください。"}), 401
    refresh_token = token_data.get("refresh_token", "")
    if not refresh_token:
        return jsonify({"error": "リフレッシュトークンがありません。再認証してください。"}), 401
    return jsonify({
        "refresh_token": refresh_token,
        "instruction": "Railwayダッシュボードの環境変数に FREEE_REFRESH_TOKEN = <上記の値> を設定してください。以後はデプロイ後も自動復元されます。"
    })


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


@app.route("/api/debug_partners")
def api_debug_partners():
    """デバッグ用: freee取引先一覧とdeals検索結果を返す"""
    try:
        import requests as req
        from freee_client import FREEE_API_BASE, FREEE_COMPANY_ID, _api_headers, get_partners
        # 取引先一覧
        partners = get_partners()
        stelify_partners = [p for p in partners if 'stel' in p.get('name','').lower() or 'ステリ' in p.get('name','')]
        all_partner_names = [p.get('name') for p in partners]
        # deals検索（Stellify partner_id=110745827, 2026-07-01以降）
        stellify_partner_ids = [p.get('id') for p in stelify_partners]
        deals_stellify = []
        for pid in stellify_partner_ids:
            params = {"company_id": FREEE_COMPANY_ID, "start_issue_date": "2026-07-01", "limit": 100, "partner_id": pid}
            resp = req.get(f"{FREEE_API_BASE}/deals", headers=_api_headers(), params=params, timeout=30)
            deals_stellify.extend(resp.json().get("deals", []))
        return jsonify({
            "total_partners": len(partners),
            "all_partner_names": all_partner_names,
            "stelify_partners": stelify_partners,
            "stellify_deals_count": len(deals_stellify),
            "stellify_deals": [{"id": d.get("id"), "issue_date": d.get("issue_date"), "amount": d.get("amount"), "partner_id": d.get("partner_id")} for d in deals_stellify[:10]],
            "deals_sample_fields": list(deals_stellify[0].keys()) if deals_stellify else []
        })
    except Exception as e:
        import traceback
        return jsonify({"error": str(e), "trace": traceback.format_exc()}), 500


@app.route("/api/debug_invoices")
def api_debug_invoices():
    """デバッグ用: freee請求書APIの生レスポンスを返す"""
    try:
        import requests as req
        from freee_client import FREEE_COMPANY_ID, _api_headers
        # partner_idなしで全件
        params_all = {"company_id": FREEE_COMPANY_ID, "limit": 5}
        resp_all = req.get("https://api.freee.co.jp/iv/invoices", headers=_api_headers(), params=params_all, timeout=30)
        invs_all = resp_all.json().get("invoices", [])
        # partner_ids=110745827（株式会社Stellify）※請求書APIはpartner_ids（複数形）
        partner_id_param = request.args.get("partner_id", "110745827")
        start_date = request.args.get("start_date", "")
        params_s = {"company_id": FREEE_COMPANY_ID, "limit": 100, "partner_ids": partner_id_param}
        if start_date:
            params_s["start_billing_date"] = start_date
        resp_s = req.get("https://api.freee.co.jp/iv/invoices", headers=_api_headers(), params=params_s, timeout=30)
        invs_s = resp_s.json().get("invoices", [])
        return jsonify({
            "all_count": len(invs_all),
            "all_sample": [{"id": i.get("id"), "partner": i.get("partner_name"), "date": i.get("issue_date")} for i in invs_all[:3]],
            "stellify_count": len(invs_s),
            "stellify_invoices": [{"id": i.get("id"), "partner": i.get("partner_name"), "billing_date": i.get("billing_date"), "issue_date": i.get("issue_date"), "total_amount": i.get("total_amount")} for i in invs_s],
            "stellify_raw_keys": list(invs_s[0].keys()) if invs_s else [],
            "all_raw_keys": list(invs_all[0].keys()) if invs_all else [],
        })
    except Exception as e:
        import traceback
        return jsonify({"error": str(e), "trace": traceback.format_exc()}), 500


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

        # assistant_logに記録
        from datetime import datetime as _dt
        for i, rid in enumerate(registered_ids):
            assistant_log.insert(0, {
                "source": "assistant",
                "freee_id": rid,
                "issue_date": (base_date + __import__('dateutil.relativedelta', fromlist=['relativedelta']).relativedelta(months=i)).isoformat() if repeat else issue_date,
                "partner_name": partner_name or "",
                "deal_type": deal_type,
                "amount": sum(d.get("amount", 0) for d in details),
                "registered_at": _dt.now().strftime("%Y-%m-%d %H:%M"),
                "note": "",
            })
        del assistant_log[200:]
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
        # assistant_logに記録
        from datetime import datetime as _dt2
        for entry_orig, rid in zip(entries, registered_ids):
            assistant_log.insert(0, {
                "source": "assistant",
                "freee_id": rid,
                "issue_date": entry_orig.get("issue_date", ""),
                "partner_name": entry_orig.get("partner_name", ""),
                "deal_type": entry_orig.get("deal_type", "income"),
                "amount": sum(d.get("amount", 0) for d in entry_orig.get("details", [])),
                "registered_at": _dt2.now().strftime("%Y-%m-%d %H:%M"),
                "note": "",
            })
        del assistant_log[200:]
        return jsonify({"status": "ok", "registered": len(registered_ids), "ids": registered_ids})
    except Exception as e:
        logger.exception("AI仕訳一括登録エラー")
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route("/api/assistant/ai", methods=["POST"])
def api_assistant_ai():
    """自然言語指示をFunction Calling対応のAIエージェントで処理する"""
    import os, json as _json, re
    from datetime import date as _date
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

        # 現在日付・年度情報
        today = _date.today()
        today_str = today.strftime("%Y年%m月%d日")
        fiscal_year = today.year if today.month >= 4 else today.year - 1
        fiscal_start = f"{fiscal_year}-04-01"
        fiscal_end = f"{fiscal_year + 1}-03-31"

        # マスタデータ：サーバー側で最新を取得（フロントエンドからの master は補助として使用）
        try:
            server_master = get_master_cache()
        except Exception:
            server_master = master  # freeeトークンなければフロントエンドのマスタを使用

        def _names(lst): return ", ".join([x["name"] for x in lst if x.get("name")])
        sections_list = _names(server_master.get("sections", []))
        tags_list = _names(server_master.get("tags", []))
        account_items_list = _names(server_master.get("account_items", []))
        partners_list = _names(server_master.get("partners", []))

        system_prompt = f"""あなたはfreee会計のエキスパートアシスタントです。
ユーザーの自然言語の指示を正確に解釈し、提供されたツールを使って実行してください。

【現在の日付】{today_str}
【今年度】{fiscal_year}年4月〜{fiscal_year+1}年3月（{fiscal_start} 〜 {fiscal_end}）

【freeeマスタデータ（完全一覧）】
取引先: {partners_list}
部門: {sections_list}
メモタグ: {tags_list}
勘定科目: {account_items_list}

【重要なルール】
- ユーザーが入力した名前がマスタと完全一致しなくても、上記マスタ一覧から最も近いものを推測して使用する
  例: 「ステリファイ」→「stellify」、「マイナビ」→「株式会社マイナビ」、「サーカス」→「circus株式会社」
  例: 「売上」→「売上高」、「広告費」→「広告宣伝費」など勘定科目も同様に推測する
- 日付は必ず現在の年度を基準に解釈する（「7月以降」→{fiscal_year}年7月以降、「先月」→{fiscal_year}年{today.month - 1 if today.month > 1 else 12}月）
- 削除指示の場合は必ずsearch_deals/search_invoicesで対象を検索し、その検索結果に含まれる実際のid値のみをdelete_deals/delete_invoicesに渡すこと。絶対に自分でIDを数値で指定してはいけない（必ず検索結果から取得すること）
- 登録指示の場合はマスタの勘定科目・取引先を正確に選んでregister_dealを呼び出す
- 不明な点があれば自分で推測して実行する（ユーザーに質問する前に試みる）
- search_dealsに渡すpartner_nameは必ずマスタの正確な名前を使用すること（推測後の正式名）
- 登録指示時に取引先マスタに該当する取引先がない場合は、create_partnerで新規作成してからregister_dealを呼び出すこと（ユーザーに確認する必要なし）"""

        # Function Callingのツール定義
        tools = [
            {
                "type": "function",
                "function": {
                    "name": "search_deals",
                    "description": "freeeの仕訳（取引）を検索する。削除対象の確認や存在確認に使用する。",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "partner_name": {"type": "string", "description": "取引先名（部分一致）"},
                            "start_issue_date": {"type": "string", "description": "発生日開始（YYYY-MM-DD）"},
                            "end_issue_date": {"type": "string", "description": "発生日終了（YYYY-MM-DD）"},
                            "deal_type": {"type": "string", "enum": ["income", "expense"], "description": "取引種別"},
                        },
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "search_invoices",
                    "description": "freeeの請求書を検索する。削除対象の確認や存在確認に使用する。",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "partner_name": {"type": "string", "description": "取引先名（部分一致）"},
                            "start_issue_date": {"type": "string", "description": "発生日開始（YYYY-MM-DD）"},
                            "end_issue_date": {"type": "string", "description": "発生日終了（YYYY-MM-DD）"},
                        },
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "register_deal",
                    "description": "freeeに仕訳（取引）を登録する。",
                    "parameters": {
                        "type": "object",
                        "required": ["deal_type", "issue_date", "details"],
                        "properties": {
                            "deal_type": {"type": "string", "enum": ["income", "expense"]},
                            "issue_date": {"type": "string", "description": "YYYY-MM-DD"},
                            "due_date": {"type": "string", "description": "YYYY-MM-DD"},
                            "partner_name": {"type": "string"},
                            "details": {
                                "type": "array",
                                "items": {
                                    "type": "object",
                                    "required": ["account_item_name", "amount", "tax_code"],
                                    "properties": {
                                        "account_item_name": {"type": "string"},
                                        "amount": {"type": "integer", "description": "税抜金額"},
                                        "tax_code": {"type": "integer", "description": "1=課税売上10%, 7=課税仕入10%, 0=不課税"},
                                        "section_name": {"type": "string"},
                                        "tag_names": {"type": "array", "items": {"type": "string"}},
                                        "description": {"type": "string"},
                                    },
                                },
                            },
                        },
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "delete_deals",
                    "description": "指定したIDの仕訳を削除する。必ず事前にsearch_dealsで対象を検索し、その検索結果に含まれる実際のid値のみを使用すること。実在しないダミーID（1,2,3など）を渡してはいけない。",
                    "parameters": {
                        "type": "object",
                        "required": ["deal_ids", "confirmation_message"],
                        "properties": {
                            "deal_ids": {"type": "array", "items": {"type": "integer"}, "description": "削除する仕訳IDのリスト。search_dealsの結果から取得した実際のid値のみ使用すること。"},
                            "confirmation_message": {"type": "string", "description": "ユーザーに表示する削除内容の説明"},
                        },
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "delete_invoices",
                    "description": "指定したIDの請求書を削除する。必ず事前にsearch_invoicesで対象を検索し、その検索結果に含まれる実際のid値のみを使用すること。実在しないダミーID（1,2,3など）を渡してはいけない。",
                    "parameters": {
                        "type": "object",
                        "required": ["invoice_ids", "confirmation_message"],
                        "properties": {
                            "invoice_ids": {"type": "array", "items": {"type": "integer"}, "description": "削除する請求書IDのリスト。search_invoicesの結果から取得した実際のid値のみ使用すること。"},
                            "confirmation_message": {"type": "string", "description": "ユーザーに表示する削除内容の説明"},
                        },
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "create_partner",
                    "description": "freeeに新規取引先を作成する。取引先マスタに存在しない取引先への仕訳登録時に使用する。register_dealの前に呼び出すこと。",
                    "parameters": {
                        "type": "object",
                        "required": ["name"],
                        "properties": {
                            "name": {"type": "string", "description": "取引先名（正式名で登録する）"},
                            "shortcut1": {"type": "string", "description": "ショートカット（省略可）"},
                        },
                    },
                },
            },
        ]

        # ツール実行関数
        def execute_tool(name, args):
            if name == "search_deals":
                deals = search_deals(**args)
                return _json.dumps([
                    {"id": d["id"], "issue_date": d.get("issue_date"), "partner_name": d.get("partner_name"),
                     "amount": d.get("amount"), "type": d.get("type")}
                    for d in deals
                ], ensure_ascii=False)
            elif name == "search_invoices":
                invoices = search_invoices(**args)
                return _json.dumps([
                    {"id": inv["id"], "issue_date": inv.get("issue_date"), "partner_name": inv.get("partner_name"),
                     "total_amount": inv.get("total_amount"), "invoice_number": inv.get("invoice_number")}
                    for inv in invoices
                ], ensure_ascii=False)
            elif name == "register_deal":
                cache = get_master_cache()
                deal_type = args.pop("deal_type")
                result = create_deal(args, deal_type, cache)
                # assistant_logに記録
                from datetime import datetime as _dt3
                assistant_log.insert(0, {
                    "source": "assistant",
                    "freee_id": result.get("id"),
                    "issue_date": args.get("issue_date", ""),
                    "partner_name": args.get("partner_name", ""),
                    "deal_type": deal_type,
                    "amount": sum(d.get("amount", 0) for d in args.get("details", [])),
                    "registered_at": _dt3.now().strftime("%Y-%m-%d %H:%M"),
                    "note": user_message[:80],
                })
                del assistant_log[200:]
                return _json.dumps({"status": "ok", "id": result.get("id")}, ensure_ascii=False)
            elif name == "delete_deals":
                # 削除はユーザー確認が必要なので、削除内容を返すのみ（実行はフロントエンドで行う）
                return _json.dumps({
                    "status": "pending_confirmation",
                    "deal_ids": args["deal_ids"],
                    "message": args["confirmation_message"]
                }, ensure_ascii=False)
            elif name == "delete_invoices":
                return _json.dumps({
                    "status": "pending_confirmation",
                    "invoice_ids": args["invoice_ids"],
                    "message": args["confirmation_message"]
                }, ensure_ascii=False)
            elif name == "create_partner":
                partner = create_partner(
                    name=args["name"],
                    shortcut1=args.get("shortcut1", ""),
                )
                return _json.dumps({
                    "status": "ok",
                    "id": partner.get("id"),
                    "name": partner.get("name"),
                    "message": f"取引先「{partner.get('name')}」を新規登録しました（ID: {partner.get('id')}）"
                }, ensure_ascii=False)
            return "{}"

        # エージェントループ（最大6回までツール呼び出しを繰り返す）
        messages = [{"role": "system", "content": system_prompt}]
        for h in history[-6:]:
            messages.append(h)
        messages.append({"role": "user", "content": user_message})

        pending_deletes = {"deal_ids": [], "invoice_ids": [], "message": ""}
        registered_ids = []
        final_reply = ""

        for _ in range(6):
            resp = req.post(
                f"{openai_base}/chat/completions",
                headers={"Authorization": f"Bearer {openai_key}", "Content-Type": "application/json"},
                json={"model": "gpt-4o", "messages": messages, "tools": tools, "tool_choice": "auto", "temperature": 0.1},
                timeout=60,
            )
            resp.raise_for_status()
            choice = resp.json()["choices"][0]
            msg = choice["message"]
            messages.append(msg)

            # ツール呼び出しがなければ終了
            if not msg.get("tool_calls"):
                final_reply = msg.get("content", "")
                break

            # 各ツールを実行
            for tc in msg["tool_calls"]:
                fn_name = tc["function"]["name"]
                fn_args = _json.loads(tc["function"]["arguments"])
                tool_result = execute_tool(fn_name, fn_args)
                messages.append({
                    "role": "tool",
                    "tool_call_id": tc["id"],
                    "content": tool_result,
                })

                # 削除待機中の場合はフロントに渡す
                result_obj = _json.loads(tool_result)
                if isinstance(result_obj, dict):
                    if result_obj.get("status") == "pending_confirmation":
                        if "deal_ids" in result_obj:
                            pending_deletes["deal_ids"].extend(result_obj["deal_ids"])
                        if "invoice_ids" in result_obj:
                            pending_deletes["invoice_ids"].extend(result_obj["invoice_ids"])
                        pending_deletes["message"] = result_obj.get("message", "")

                    # 登録成功の場合
                    if fn_name == "register_deal" and result_obj.get("status") == "ok":
                        registered_ids.append(result_obj.get("id"))

        return jsonify({
            "reply": final_reply,
            "entries": [],
            "delete_actions": [],
            "pending_deletes": pending_deletes if (pending_deletes["deal_ids"] or pending_deletes["invoice_ids"]) else None,
            "registered_ids": registered_ids,
        })

    except Exception as e:
        logger.exception("AIエージェントエラー")
        return jsonify({"error": str(e)}), 500



@app.route("/api/assistant/search_delete", methods=["POST"])
def api_assistant_search_delete():
    """削除对象の仕訳・請求書を検索してプレビューを返す"""
    from freee_client import search_deals, search_invoices
    data = request.get_json() or {}
    action = data.get("action", {})
    partner_name = action.get("partner_name")
    start_date = action.get("start_date")
    end_date = action.get("end_date")
    deal_type = action.get("deal_type")
    target = action.get("target", "both")

    # 日付範囲のデフォルト（今年度）
    from datetime import date
    today = date.today()
    fiscal_start = f"{today.year}-04-01" if today.month >= 4 else f"{today.year - 1}-04-01"
    fiscal_end = f"{today.year + 1}-03-31" if today.month >= 4 else f"{today.year}-03-31"
    if not start_date:
        start_date = fiscal_start
    if not end_date:
        end_date = fiscal_end

    try:
        result = {"deals": [], "invoices": []}
        if target in ("deal", "both"):
            deals = search_deals(
                partner_name=partner_name,
                start_issue_date=start_date,
                end_issue_date=end_date,
                deal_type=deal_type,
            )
            result["deals"] = [
                {
                    "id": d["id"],
                    "issue_date": d.get("issue_date"),
                    "partner_name": d.get("partner_name"),
                    "amount": d.get("amount"),
                    "type": d.get("type"),
                    "ref_number": d.get("ref_number"),
                }
                for d in deals
            ]
        if target in ("invoice", "both"):
            invoices = search_invoices(
                partner_name=partner_name,
                start_issue_date=start_date,
                end_issue_date=end_date,
            )
            result["invoices"] = [
                {
                    "id": inv["id"],
                    "issue_date": inv.get("issue_date"),
                    "partner_name": inv.get("partner_name"),
                    "total_amount": inv.get("total_amount"),
                    "invoice_number": inv.get("invoice_number"),
                    "title": inv.get("title"),
                }
                for inv in invoices
            ]
        return jsonify(result)
    except Exception as e:
        logger.exception("削除対象検索エラー")
        return jsonify({"error": str(e)}), 500


@app.route("/api/assistant/execute_delete", methods=["POST"])
def api_assistant_execute_delete():
    """仕訳・請求書を実際に削除する"""
    from freee_client import delete_deal, delete_invoice
    data = request.get_json() or {}
    deal_ids = data.get("deal_ids", [])
    invoice_ids = data.get("invoice_ids", [])

    results = {"deleted_deals": [], "failed_deals": [], "deleted_invoices": [], "failed_invoices": []}
    for did in deal_ids:
        try:
            delete_deal(int(did))
            results["deleted_deals"].append(did)
        except Exception as e:
            results["failed_deals"].append({"id": did, "error": str(e)})
    for iid in invoice_ids:
        try:
            delete_invoice(int(iid))
            results["deleted_invoices"].append(iid)
        except Exception as e:
            results["failed_invoices"].append({"id": iid, "error": str(e)})

    return jsonify(results)


# ============================================================
# 証桯アップロード
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
