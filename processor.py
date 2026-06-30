"""
メイン処理エンジン
Notionの②経理対応待ちレコードを取得し、freeeに登録する

対応処理パターン:
  - register / register_sales_only: 通常の仕訳登録（仕訳のみ）
  - register + needs_invoice: 請求書登録（仕訳は手動でfreee管理画面から）
  - send_invoice: 請求書送付（●入社済ステータス）
  - delete: 入社前辞退（取引削除）
  - refund: 返金（マイナス仕訳登録）
  - review: 手動確認が必要
"""
import logging
from datetime import datetime

from notion_client import (
    fetch_pending_records,
    get_record,
    get_invoice_id_from_record,
    mark_as_done,
    mark_as_error,
    clear_error,
    clear_error_set_processing,
    set_invoice_required_select,
    FREEE_STATUS_SHIWAKE_SUCCESS,
    FREEE_STATUS_INVOICE_REGISTERED,
    FREEE_STATUS_INVOICE_SUCCESS,
)
from rules import build_journal_entries, _extract_props
from db import _get_db
from freee_client import (
    register_journal,
    register_invoice_and_deal,
    send_invoice,
    delete_deals,
    create_deal,
    get_master_cache,
)

logger = logging.getLogger(__name__)

# 処理結果をメモリに保持（WebUIで表示するため）
processing_log: list[dict] = []
MAX_LOG = 200


def _build_invoice_entry(journal: dict, props: dict) -> dict:
    """
    請求書登録用エントリを構築する

    freee請求書の仕様:
    - 件名 (subject): 人材紹介手数料（固定）
    - 明細1 (item行): description=求職者名、unit_price=金額、tax_rate=10
    - 明細2 (text行): description=「入社企業：{入社企業名}様」
    - 備考 (invoice_note): 「振込手数料は貴社負担でお願いいたします。」（固定）
    - 社内メモ (memo): 「本店：CA」または「本店：PCA」
    """
    sales_entry = journal.get("sales_entry") or {}
    details = sales_entry.get("details", [])

    # 求職者名・入社企業名を取得
    jobseeker_name_raw = props.get("jobseeker_name") or ""
    jobseeker_name = f"{jobseeker_name_raw}様" if jobseeker_name_raw else ""
    company_name = props.get("company_name") or ""
    db_type = props.get("db_type", "honten")

    # 社内メモ: 部門名
    section_memo = "本店：PCA" if db_type == "pca" else "本店：CA"

    # 売上エントリから会計情報を取得（取引連携用）
    account_item_name = None
    section_name = sales_entry.get("section_name", "")
    tag_names = []
    if details:
        first_detail = details[0]
        account_item_name = first_detail.get("account_item_name")
        tag_names = first_detail.get("tag_names", [])

    # 明細行を構築
    invoice_lines = []
    for d in details:
        # 明細1の摘要: 求職者名のみ（入社企業名は明細2に記載）
        item_description = jobseeker_name if jobseeker_name else "人材紹介手数料"

        # 明細1: 品目行（求職者名 + 金額）
        item_line = {
            "name": jobseeker_name or "人材紹介手数料",
            "unit_price": abs(d.get("amount", 0)),
            "quantity": 1,
            "description": item_description,  # 求職者名のみ（仕訳転記時に摘要に反映）
            "tax_rate": 10,  # 税率10%（freee請求書API必須）
            "tax_code": d.get("tax_code", 129),  # 課税売上10%（取引連携用）
        }
        # 取引連携用の会計情報を追加
        if account_item_name:
            item_line["account_item_name"] = account_item_name
        if section_name:
            item_line["section_name"] = section_name
        if tag_names:
            item_line["tag_names"] = tag_names
        invoice_lines.append(item_line)
        # 明細2: テキスト行（入社企業名）
        if company_name:
            invoice_lines.append({
                "type": "text",
                "description": f"入社企業：{company_name}様",
            })

    return {
        "issue_date": sales_entry.get("issue_date", datetime.now().strftime("%Y-%m-%d")),
        "due_date": sales_entry.get("due_date"),
        "partner_name": sales_entry.get("partner_name"),
        "partner_id": sales_entry.get("partner_id"),  # Notionのfreee売上取引先ID（取引先ID）
        "title": "人材紹介手数料",
        "invoice_note": "振込手数料は貴社負担でお願いいたします。",
        "memo": section_memo,
        "details": invoice_lines,
    }



import json as _json

def _idem_key(page_id: str, action: str, journal: dict) -> str:
    """idempotency key を page_id:action:issue_date:amount で構成する。"""
    sales = journal.get("sales_entry") or {}
    purchase = journal.get("purchase_entry") or {}
    issue_date = sales.get("issue_date") or purchase.get("issue_date") or ""
    amount = abs(sales.get("amount", 0) or purchase.get("amount", 0))
    return f"{page_id}:{action}:{issue_date}:{amount}"

def _idem_start(key: str, page_id: str, action: str):
    """処理開始前に status='processing' を INSERT (IGNORE)。"""
    conn = _get_db()
    try:
        conn.execute(
            """INSERT OR IGNORE INTO idempotency_keys
               (key, page_id, action, status, freee_ids, created_at, updated_at)
               VALUES (?, ?, ?, 'processing', '{}', datetime('now','localtime'), datetime('now','localtime'))""",
            (key, page_id, action)
        )
        conn.commit()
    finally:
        conn.close()

def _idem_save(key: str, page_id: str, action: str, freee_ids: dict):
    """freee変更成功後に status='done', freee_ids を UPDATE する。"""
    conn = _get_db()
    try:
        conn.execute(
            """INSERT INTO idempotency_keys (key, page_id, action, status, freee_ids, created_at, updated_at)
               VALUES (?, ?, ?, 'done', ?, datetime('now','localtime'), datetime('now','localtime'))
               ON CONFLICT(key) DO UPDATE SET
                   status='done',
                   freee_ids=excluded.freee_ids,
                   updated_at=excluded.updated_at""",
            (key, page_id, action, _json.dumps(freee_ids, ensure_ascii=False))
        )
        conn.commit()
    finally:
        conn.close()

def process_record(record: dict, dry_run: bool = False) -> dict:
    """
    1件のNotionレコードを処理する

    dry_run=True の場合は仕訳データを生成するだけで実際の登録は行わない
    """
    page_id = record.get("id", "")
    props_raw = record.get("properties", {})

    # フェーズ（タイトル）を取得
    phase_info = props_raw.get("フェーズ", {})
    phase_list = phase_info.get("title", [])
    phase = "".join([x.get("plain_text", "") for x in phase_list])

    result = {
        "page_id": page_id,
        "phase": phase,
        "timestamp": datetime.now().isoformat(),
        "action": None,
        "status": None,
        "message": "",
        "sales_id": None,
        "purchase_id": None,
        "pca_id": None,
        "invoice_id": None,
        "journal": None,
        "errors": [],
    }



    try:
        # 仕訳データを構築
        journal = build_journal_entries(record)
        result["action"] = journal["action"]
        result["journal"] = journal
        result["job_db"] = journal.get("job_db", "")
        result["nyusha_date"] = journal.get("nyusha_date", "")
        result["name"] = journal.get("jobseeker_name", "")  # 実行ログの名前欄に表示
        current_status = journal.get("original_status", "")
        db_type = record.get("_db_type", "honten")

        # 請求有無セレクトを自動セット（フォーミュラ型→セレクト型移行対応）
        if not dry_run:
            job_db_val = journal.get("job_db", "")
            try:
                set_invoice_required_select(page_id, job_db_val)
            except Exception as e:
                logger.warning(f"請求有無セレクトセット失敗（処理は続行）: {e}")

        # ============================================================
        # スキップ（入社済・請求不要など）
        # ============================================================
        if journal["action"] == "skip":
            result["status"] = "skip"
            result["message"] = journal["message"]
            # Notionのエラー状態をクリア（エラーになっていた場合は解除）
            if not dry_run:
                clear_error(page_id)
            return result

        # ============================================================
        # 手動確認が必要なケース
        # ============================================================
        if journal["action"] in ("review", "error"):
            result["status"] = "review"
            result["message"] = journal["message"]
            if not dry_run:
                mark_as_error(page_id, f"要確認: {journal['message']}")
            return result

        # ============================================================
        # dry_run モード（プレビューのみ）
        # ============================================================
        if dry_run:
            result["status"] = "dry_run"
            result["message"] = f"[プレビュー] {journal['message']}"
            return result

        # idempotency チェック（action確定後、key単位で重複防止）
        if not dry_run:
            _idem_key_val = _idem_key(page_id, journal["action"], journal)
            _idem_conn = _get_db()
            try:
                _idem_row = _idem_conn.execute(
                    "SELECT status FROM idempotency_keys WHERE key=? AND status IN ('processing','done')",
                    (_idem_key_val,)
                ).fetchone()
            finally:
                _idem_conn.close()
            if _idem_row:
                result["status"] = "skip"
                result["message"] = f"既に登録済み（idempotency key={_idem_key_val}, status={_idem_row[0]}）"
                return result
            # 処理開始を記録
            _idem_start(_idem_key_val, page_id, journal["action"])

        # 処理中マーク
        clear_error_set_processing(page_id)

        # ============================================================
        # 入社前辞退: 取引を削除
        # ============================================================
        if journal["action"] == "delete":
            del_result = delete_deals(
                journal.get("delete_sales_id"),
                journal.get("delete_purchase_id"),
            )
            if del_result["errors"]:
                error_msg = " / ".join(del_result["errors"])
                mark_as_error(page_id, error_msg)
                result["status"] = "error"
                result["message"] = error_msg
                result["errors"] = del_result["errors"]
            else:
                ok = mark_as_done(page_id, current_status,
                                  db_type=db_type,
                                  freee_status=FREEE_STATUS_SHIWAKE_SUCCESS)
                if not ok:
                    result["status"] = "partial_error"
                    result["message"] = "freee削除は成功したが、Notion書き戻しに失敗。手動確認が必要"
                    result["needs_manual_check"] = True
                else:
                    # freee削除成功後にidempotency_keysに記録
                    _idem_save(_idem_key_val, page_id, "delete", {
                        "sales_id": journal.get("delete_sales_id"),
                        "purchase_id": journal.get("delete_purchase_id"),
                    })
                    result["status"] = "success"
                    result["message"] = "入社前辞退: 取引を削除しました"
            return result

        # ============================================================
        # 返金: マイナス仕訳を登録
        # ============================================================
        if journal["action"] == "refund":
            reg_result = register_journal(
                journal.get("sales_entry"),
                journal.get("purchase_entry"),
                journal.get("pca_entry"),
            )
            if reg_result["errors"]:
                error_msg = " / ".join(reg_result["errors"])
                mark_as_error(page_id, error_msg)
                result["status"] = "error"
                result["message"] = error_msg
                result["errors"] = reg_result["errors"]
            else:
                result["sales_id"] = reg_result["sales_id"]
                result["purchase_id"] = reg_result["purchase_id"]
                result["pca_id"] = reg_result["pca_id"]

                # freee成功後（Notion書き戻し前）にidempotency_keysに記録
                _idem_save(_idem_key_val, page_id, "refund", {
                    "sales_id": reg_result.get("sales_id"),
                    "purchase_id": reg_result.get("purchase_id"),
                    "pca_id": reg_result.get("pca_id"),
                })

                ok = mark_as_done(page_id, current_status,
                                  db_type=db_type,
                                  freee_status=FREEE_STATUS_SHIWAKE_SUCCESS,
                                  sales_id=reg_result["sales_id"],
                                  purchase_id=reg_result["purchase_id"])
                if not ok:
                    result["status"] = "partial_error"
                    result["message"] = f"freee登録は成功したが、Notion書き戻しに失敗（売上ID={result['sales_id']}）。手動確認が必要"
                    result["needs_manual_check"] = True
                else:
                    result["status"] = "success"
                    result["message"] = "返金マイナス仕訳を登録しました"
            return result

        # ============================================================
        # 入社済: 請求書を送付（●入社済ステータス）
        # ============================================================
        if journal["action"] == "send_invoice":
            invoice_id = get_invoice_id_from_record(record)
            if not invoice_id:
                error_msg = "freee請求書IDが見つかりません。本部確認済の処理で請求書が登録されているか確認してください。"
                mark_as_error(page_id, error_msg)
                result["status"] = "error"
                result["message"] = error_msg
                result["errors"] = [error_msg]
                return result

            send_result = send_invoice(invoice_id)
            if send_result.get("error"):
                error_msg = send_result["error"]
                mark_as_error(page_id, error_msg)
                result["status"] = "error"
                result["message"] = error_msg
                result["errors"] = [error_msg]
            else:
                result["invoice_id"] = invoice_id
                ok = mark_as_done(page_id, current_status,
                                  db_type=db_type,
                                  freee_status=FREEE_STATUS_INVOICE_SUCCESS,
                                  invoice_id=invoice_id)
                if not ok:
                    result["status"] = "partial_error"
                    result["message"] = f"freee請求書送付は成功したが、Notion書き戻しに失敗（請求書ID={invoice_id}）。手動確認が必要"
                    result["needs_manual_check"] = True
                else:
                    result["status"] = "success"
                    result["message"] = f"請求書(ID={invoice_id})を送付しました"
            return result

        # ============================================================
        # 通常登録（請求書あり: 要請求の場合）
        # 請求書を登録し、仕入仕訳・PCA外注支払仕訳も登録する。
        # 売上仕訳は請求書から手動で「取引登録」ボタンを押す。
        # ============================================================
        if journal.get("needs_invoice"):
            props = _extract_props(record)
            invoice_entry = _build_invoice_entry(journal, props)
            inv_result = register_invoice_and_deal(
                invoice_entry,
                journal.get("sales_entry"),
            )
            if inv_result["errors"]:
                error_msg = " / ".join(inv_result["errors"])
                mark_as_error(page_id, error_msg)
                result["status"] = "error"
                result["message"] = error_msg
                result["errors"] = inv_result["errors"]
                return result

            result["invoice_id"] = inv_result["invoice_id"]

            # 仕入仕訳・PCA外注支払仕訳も登録（ある場合）
            purchase_entry = journal.get("purchase_entry")
            pca_entry = journal.get("pca_entry")
            if purchase_entry or pca_entry:
                cache = get_master_cache()
                sub_errors = []
                if purchase_entry:
                    try:
                        deal = create_deal(purchase_entry, "expense", cache)
                        result["purchase_id"] = deal.get("id")
                    except Exception as e:
                        sub_errors.append(f"仕入仕訳エラー: {str(e)}")
                if pca_entry:
                    try:
                        deal = create_deal(pca_entry, "expense", cache)
                        result["pca_id"] = deal.get("id")
                    except Exception as e:
                        sub_errors.append(f"PCA外注支払仕訳エラー: {str(e)}")
                if sub_errors:
                    # 請求書は登録済みだが仕入/PCAが失敗した場合はエラーとして記録
                    error_msg = f"請求書(ID={result['invoice_id']})登録済みだが: " + " / ".join(sub_errors)
                    mark_as_error(page_id, error_msg)
                    result["status"] = "error"
                    result["message"] = error_msg
                    result["errors"] = sub_errors
                    return result

            ok = mark_as_done(page_id, current_status,
                             db_type=db_type,
                             freee_status=FREEE_STATUS_INVOICE_REGISTERED,
                             invoice_id=result["invoice_id"])
            if not ok:
                result["status"] = "partial_error"
                result["message"] = f"freee請求書登録は成功したが、Notion書き戻しに失敗（請求書ID={result['invoice_id']}）。手動確認が必要"
                result["needs_manual_check"] = True
                return result
            # freee成功後（Notion書き戻し前）にidempotency_keysに記録
            _idem_save(_idem_key_val, page_id, "send_invoice", {"invoice_id": result.get("invoice_id")})
            result["status"] = "success"
            msg_parts = [f"請求書(ID={result['invoice_id']})を登録しました"]
            if result.get("purchase_id"):
                msg_parts.append(f"仕入ID={result['purchase_id']}")
            if result.get("pca_id"):
                msg_parts.append(f"PCA外注支払ID={result['pca_id']}")
            msg_parts.append("（仕訳はfreee管理画面で取引登録ボタンを押してください）")
            result["message"] = " / ".join(msg_parts)
            return result

        # ============================================================
        # CSS求人: スカウト手数料（仕入）のみ登録（売上仕訳なし）
        # ============================================================
        if journal["action"] == "register_scout_only":
            purchase_entry = journal.get("purchase_entry")
            if purchase_entry:
                cache = get_master_cache()
                try:
                    deal = create_deal(purchase_entry, "expense", cache)
                    result["purchase_id"] = deal.get("id")
                except Exception as e:
                    error_msg = f"スカウト手数料登録エラー: {str(e)}"
                    mark_as_error(page_id, error_msg)
                    result["status"] = "error"
                    result["message"] = error_msg
                    result["errors"] = [error_msg]
                    return result
            ok = mark_as_done(page_id, current_status,
                             db_type=db_type,
                             freee_status=FREEE_STATUS_SHIWAKE_SUCCESS,
                             purchase_id=result.get("purchase_id"))
            if not ok:
                result["status"] = "partial_error"
                result["message"] = f"freee登録は成功したが、Notion書き戻しに失敗（仕入ID={result.get('purchase_id')}）。手動確認が必要"
                result["needs_manual_check"] = True
                return result
            # freee成功後（Notion書き戻し前）にidempotency_keysに記録
            _idem_save(_idem_key_val, page_id, "register_scout_only", {"purchase_id": result.get("purchase_id")})
            result["status"] = "success"
            result["message"] = (
                f"スカウト手数料仕訳を登録しました "
                f"(仕入ID={result.get('purchase_id')})。売上仕訳は登録していません。"
            )
            return result

        # ============================================================
        # 通常登録（仕訳のみ）
        # ============================================================
        reg_result = register_journal(
            journal.get("sales_entry"),
            journal.get("purchase_entry"),
            journal.get("pca_entry"),
        )
        if reg_result["errors"]:
            error_msg = " / ".join(reg_result["errors"])
            mark_as_error(page_id, error_msg)
            result["status"] = "error"
            result["message"] = error_msg
            result["errors"] = reg_result["errors"]
        else:
            result["sales_id"] = reg_result["sales_id"]
            result["purchase_id"] = reg_result["purchase_id"]
            result["pca_id"] = reg_result["pca_id"]
            ok = mark_as_done(page_id, current_status,
                              db_type=db_type,
                              freee_status=FREEE_STATUS_SHIWAKE_SUCCESS,
                              sales_id=reg_result["sales_id"],
                              purchase_id=reg_result["purchase_id"],
                              pca_id=reg_result.get("pca_id"))
            if not ok:
                result["status"] = "partial_error"
                result["message"] = f"freee登録は成功したが、Notion書き戻しに失敗（売上ID={result['sales_id']}, 仕入ID={result['purchase_id']}）。手動確認が必要"
                result["needs_manual_check"] = True
            else:
                # freee成功後（Notion書き戻し前）にidempotency_keysに記録
                _idem_save(_idem_key_val, page_id, "register", {
                    "sales_id": result.get("sales_id"),
                    "purchase_id": result.get("purchase_id"),
                    "pca_id": result.get("pca_id"),
                })
                result["status"] = "success"
                result["message"] = (
                    f"仕訳を登録しました "
                    f"(売上ID={result['sales_id']}, 仕入ID={result['purchase_id']})"
                )

    except Exception as e:
        logger.exception(f"[EXCEPTION] {phase}: {e}")
        error_msg = str(e)
        if not dry_run:
            try:
                mark_as_error(page_id, error_msg[:2000])
            except Exception:
                pass
        result["status"] = "error"
        result["message"] = error_msg
        result["errors"] = [error_msg]

    return result


def run_once(db_type: str = "all", dry_run: bool = False) -> list[dict]:
    """
    1回のポーリングを実行する
    ②経理対応待ちレコードを全件処理する
    """
    logger.info(f"ポーリング開始 (db_type={db_type}, dry_run={dry_run})")
    results = []

    try:
        records = fetch_pending_records(db_type)
        logger.info(f"対象レコード数: {len(records)}")

        for record in records:
            # DBクエリ失敗のエラーマーカーを検出し、結果に警告として追加
            if record.get("_is_error_marker"):
                for err_msg in record.get("_fetch_errors", []):
                    logger.warning(f"DB取得警告: {err_msg}")
                    results.append({
                        "page_id": "",
                        "phase": "",
                        "timestamp": datetime.now().isoformat(),
                        "action": None,
                        "status": "warning",
                        "message": f"⚠️ {err_msg}（一部のDBからの取得に失敗しました）",
                        "errors": [err_msg],
                    })
                continue

            result = process_record(record, dry_run=dry_run)
            results.append(result)
            if not dry_run:
                processing_log.insert(0, result)
                while len(processing_log) > MAX_LOG:
                    processing_log.pop()

    except Exception as e:
        logger.exception(f"ポーリングエラー: {e}")
        results.append({
            "status": "error",
            "message": f"ポーリングエラー: {str(e)}",
            "timestamp": datetime.now().isoformat(),
        })

    logger.info(f"ポーリング完了: {len(results)}件処理")
    return results


def process_single_by_id(page_id: str, db_type: str = "honten",
                          dry_run: bool = False) -> dict:
    """
    特定のページIDのレコードを処理する
    """
    record = get_record(page_id)
    record["_db_type"] = db_type
    return process_record(record, dry_run=dry_run)

