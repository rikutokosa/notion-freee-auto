"""
メイン処理エンジン
Notionの②経理対応待ちレコードを取得し、freeeに登録する

対応処理パターン:
  - register / register_sales_only: 通常の仕訳登録（仕訳のみ）
  - register + needs_invoice: 請求書登録＋仕訳登録
  - delete: 入社前辞退（取引削除）
  - refund: 返金（マイナス仕訳登録）
  - review: 手動確認が必要
"""
import logging
import time
from datetime import datetime
from typing import Optional

from notion_client import (
    fetch_pending_records,
    get_record,
    mark_as_done,
    mark_as_error,
    clear_error,
)
from rules import build_journal_entries, _extract_props
from freee_client import (
    register_journal,
    register_invoice_and_deal,
    delete_deals,
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
    jobseeker_name = props.get("jobseeker_name") or ""
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
        # 明細1: 品目行（求職者名 + 金額）
        item_line = {
            "name": jobseeker_name or "人材紹介手数料",
            "unit_price": abs(d.get("amount", 0)),
            "quantity": 1,
            "description": jobseeker_name,
            "tax_code": d.get("tax_code", 1),
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
        "title": "人材紹介手数料",
        "invoice_note": "振込手数料は貴社負担でお願いいたします。",
        "memo": section_memo,
        "details": invoice_lines,
    }


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
        current_status = journal.get("original_status", "")

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

        # 処理中マーク
        clear_error(page_id)

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
                mark_as_done(page_id, current_status)
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
                mark_as_done(page_id, current_status,
                             sales_id=reg_result["sales_id"],
                             purchase_id=reg_result["purchase_id"])
                result["status"] = "success"
                result["message"] = "返金マイナス仕訳を登録しました"
            return result

        # ============================================================
        # 通常登録（請求書あり）
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
            result["sales_id"] = inv_result["sales_id"]

            # 仕入仕訳も別途登録
            if journal.get("purchase_entry") or journal.get("pca_entry"):
                pur_result = register_journal(
                    None,
                    journal.get("purchase_entry"),
                    journal.get("pca_entry"),
                )
                result["purchase_id"] = pur_result.get("purchase_id")
                result["pca_id"] = pur_result.get("pca_id")
                if pur_result.get("errors"):
                    error_msg = " / ".join(pur_result["errors"])
                    mark_as_error(page_id, error_msg)
                    result["status"] = "error"
                    result["message"] = error_msg
                    result["errors"] = pur_result["errors"]
                    return result

            mark_as_done(page_id, current_status,
                         sales_id=result["sales_id"],
                         purchase_id=result["purchase_id"],
                         invoice_id=result["invoice_id"])
            result["status"] = "success"
            result["message"] = f"請求書(ID={result['invoice_id']})と仕訳を登録しました"
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
            mark_as_done(page_id, current_status,
                         sales_id=reg_result["sales_id"],
                         purchase_id=reg_result["purchase_id"])
            result["status"] = "success"
            result["message"] = f"仕訳を登録しました (売上ID={result['sales_id']}, 仕入ID={result['purchase_id']})"

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


def run_polling_loop(interval_seconds: int = 300):
    """
    定期ポーリングループ（バックグラウンドスレッドで実行）
    """
    logger.info(f"ポーリングループ開始 (間隔: {interval_seconds}秒)")
    while True:
        try:
            run_once()
        except Exception as e:
            logger.exception(f"ポーリングループエラー: {e}")
        time.sleep(interval_seconds)
