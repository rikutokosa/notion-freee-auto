"""
freee 書類照合モジュール

freeeのファイルボックス（証憑ファイル）に登録された未照合書類を
既存の取引（仕訳）と自動照合・紐づけする。

照合ロジック:
1. GET /api/1/receipts?category=without_deal で未登録書類一覧を取得
2. 各書類のOCR解析結果（金額・発行日・発行元）を取得
3. GET /api/1/deals で金額・日付範囲で仕訳を検索
4. 金額完全一致 → 候補として記録
5. 候補が1件 → 自動紐づけ
6. 候補が複数 → 取引先名で絞り込み → それでも複数なら日付が最も近いものを選択
7. POST /api/1/deals/{id}/receipts で紐づけ実行
"""
import logging
import requests
from datetime import datetime, timedelta
from typing import Optional

from freee_client import (
    FREEE_API_BASE,
    FREEE_COMPANY_ID,
    _api_headers,
    get_valid_token,
)

logger = logging.getLogger(__name__)

# 照合時の日付範囲（書類の発行日 ± DATE_RANGE_DAYS 日以内の仕訳を検索）
DATE_RANGE_DAYS = 60

# 金額の許容誤差（税抜・税込の差異を吸収するため）
# 0: 完全一致のみ
AMOUNT_TOLERANCE = 0


def get_unmatched_receipts(limit: int = 100) -> list:
    """
    未登録（取引未紐づけ）の書類一覧を取得する

    Returns:
        list of receipt dicts with OCR metadata
    """
    params = {
        "company_id": FREEE_COMPANY_ID,
        "category": "without_deal",
        "limit": limit,
        "offset": 0,
    }
    resp = requests.get(
        f"{FREEE_API_BASE}/receipts",
        headers=_api_headers(),
        params=params,
        timeout=30,
    )
    if resp.status_code != 200:
        raise ValueError(f"書類一覧取得失敗: {resp.status_code} {resp.text[:300]}")

    receipts = resp.json().get("receipts", [])
    logger.info(f"未登録書類: {len(receipts)}件取得")
    return receipts


def get_deals_by_amount_and_date(amount: int, issue_date: str,
                                  date_range_days: int = DATE_RANGE_DAYS) -> list:
    """
    金額と日付範囲で取引（仕訳）を検索する

    Args:
        amount: 金額（絶対値）
        issue_date: 発行日（yyyy-mm-dd）
        date_range_days: 日付範囲（±日数）

    Returns:
        list of deal dicts
    """
    try:
        base_date = datetime.strptime(issue_date, "%Y-%m-%d")
    except (ValueError, TypeError):
        logger.warning(f"日付フォーマット不正: {issue_date}")
        return []

    start_date = (base_date - timedelta(days=date_range_days)).strftime("%Y-%m-%d")
    end_date = (base_date + timedelta(days=date_range_days)).strftime("%Y-%m-%d")

    all_deals = []
    for deal_type in ("income", "expense"):
        params = {
            "company_id": FREEE_COMPANY_ID,
            "type": deal_type,
            "start_issue_date": start_date,
            "end_issue_date": end_date,
            "limit": 100,
            "offset": 0,
        }
        resp = requests.get(
            f"{FREEE_API_BASE}/deals",
            headers=_api_headers(),
            params=params,
            timeout=30,
        )
        if resp.status_code != 200:
            logger.warning(f"取引検索失敗 ({deal_type}): {resp.status_code}")
            continue

        deals = resp.json().get("deals", [])
        # 金額でフィルタリング（明細行の合計金額と照合）
        for deal in deals:
            deal_amount = _calc_deal_amount(deal)
            if abs(deal_amount - amount) <= AMOUNT_TOLERANCE:
                all_deals.append(deal)

    logger.info(f"金額{amount}円・日付{issue_date}±{date_range_days}日で{len(all_deals)}件の取引が候補")
    return all_deals


def _calc_deal_amount(deal: dict) -> int:
    """取引の合計金額を計算する（明細行の金額合計）"""
    details = deal.get("details", [])
    if not details:
        return 0
    # 明細行の金額合計（税込）
    total = sum(abs(d.get("amount", 0)) for d in details)
    return total


def _score_deal(deal: dict, receipt: dict) -> float:
    """
    取引と書類のマッチングスコアを計算する（高いほど一致度が高い）

    スコア基準:
    - 日付の近さ: 最大50点（同日=50点、1日ずれ=49点...）
    - 取引先名の一致: 30点
    """
    score = 0.0

    # 日付スコア
    receipt_meta = receipt.get("receipt_metadatum") or {}
    receipt_date_str = receipt_meta.get("issue_date")
    deal_date_str = deal.get("issue_date")

    if receipt_date_str and deal_date_str:
        try:
            receipt_date = datetime.strptime(receipt_date_str, "%Y-%m-%d")
            deal_date = datetime.strptime(deal_date_str, "%Y-%m-%d")
            diff_days = abs((receipt_date - deal_date).days)
            date_score = max(0, 50 - diff_days)
            score += date_score
        except ValueError:
            pass

    # 取引先名スコア
    receipt_partner = (receipt_meta.get("partner_name") or "").strip()
    deal_partner = (deal.get("partner", {}) or {}).get("name", "").strip()

    if receipt_partner and deal_partner:
        # 部分一致でもスコアを付与
        if receipt_partner == deal_partner:
            score += 30
        elif receipt_partner in deal_partner or deal_partner in receipt_partner:
            score += 15

    return score


def attach_receipt_to_deal(deal_id: int, receipt_id: int) -> bool:
    """
    取引に書類を紐づける

    Args:
        deal_id: freee取引ID
        receipt_id: freeeファイルボックスID

    Returns:
        True if successful
    """
    resp = requests.post(
        f"{FREEE_API_BASE}/deals/{deal_id}/receipts",
        headers=_api_headers(),
        json={
            "company_id": FREEE_COMPANY_ID,
            "receipt_id": receipt_id,
        },
        timeout=30,
    )
    if resp.status_code in (200, 201):
        logger.info(f"紐づけ成功: 取引ID={deal_id} ← 書類ID={receipt_id}")
        return True
    else:
        logger.warning(f"紐づけ失敗: 取引ID={deal_id}, 書類ID={receipt_id}, "
                       f"status={resp.status_code}, body={resp.text[:200]}")
        return False


def run_matching(dry_run: bool = False) -> dict:
    """
    書類照合のメイン処理

    Args:
        dry_run: Trueの場合は照合結果を返すだけで実際の紐づけは行わない

    Returns:
        {
            "matched": [{"receipt_id": ..., "deal_id": ..., "score": ..., "auto": True/False}],
            "unmatched": [{"receipt_id": ..., "reason": ...}],
            "errors": [...],
            "total_receipts": ...,
            "matched_count": ...,
            "unmatched_count": ...,
        }
    """
    result = {
        "matched": [],
        "unmatched": [],
        "errors": [],
        "total_receipts": 0,
        "matched_count": 0,
        "unmatched_count": 0,
        "dry_run": dry_run,
    }

    try:
        receipts = get_unmatched_receipts()
    except Exception as e:
        result["errors"].append(f"書類一覧取得エラー: {str(e)}")
        return result

    result["total_receipts"] = len(receipts)

    for receipt in receipts:
        receipt_id = receipt.get("id")
        receipt_meta = receipt.get("receipt_metadatum") or {}
        amount = receipt_meta.get("amount")
        issue_date = receipt_meta.get("issue_date")
        partner_name = receipt_meta.get("partner_name") or ""
        description = receipt.get("description") or ""

        # OCR解析結果がない場合はスキップ
        if not amount or not issue_date:
            reason = "OCR解析結果なし（金額または発行日が不明）"
            if not amount and not issue_date:
                reason = "OCR解析結果なし（金額・発行日ともに不明）"
            elif not amount:
                reason = "OCR解析結果なし（金額が不明）"
            elif not issue_date:
                reason = "OCR解析結果なし（発行日が不明）"

            result["unmatched"].append({
                "receipt_id": receipt_id,
                "description": description,
                "reason": reason,
                "amount": amount,
                "issue_date": issue_date,
                "partner_name": partner_name,
            })
            result["unmatched_count"] += 1
            continue

        # 金額・日付で取引を検索
        try:
            candidates = get_deals_by_amount_and_date(abs(amount), issue_date)
        except Exception as e:
            result["errors"].append(f"取引検索エラー (書類ID={receipt_id}): {str(e)}")
            continue

        if not candidates:
            result["unmatched"].append({
                "receipt_id": receipt_id,
                "description": description,
                "reason": f"一致する取引なし（金額={amount}円, 発行日={issue_date}）",
                "amount": amount,
                "issue_date": issue_date,
                "partner_name": partner_name,
            })
            result["unmatched_count"] += 1
            continue

        # スコアリングして最良候補を選択
        scored = sorted(
            [(deal, _score_deal(deal, receipt)) for deal in candidates],
            key=lambda x: x[1],
            reverse=True,
        )
        best_deal, best_score = scored[0]
        deal_id = best_deal.get("id")

        # 紐づけ実行
        matched_info = {
            "receipt_id": receipt_id,
            "deal_id": deal_id,
            "score": best_score,
            "receipt_amount": amount,
            "receipt_date": issue_date,
            "receipt_partner": partner_name,
            "receipt_description": description,
            "deal_date": best_deal.get("issue_date"),
            "deal_partner": (best_deal.get("partner") or {}).get("name", ""),
            "deal_amount": _calc_deal_amount(best_deal),
            "candidates_count": len(candidates),
            "auto": True,
            "attached": False,
        }

        if not dry_run:
            try:
                success = attach_receipt_to_deal(deal_id, receipt_id)
                matched_info["attached"] = success
                if not success:
                    matched_info["auto"] = False
                    result["unmatched"].append({
                        "receipt_id": receipt_id,
                        "description": description,
                        "reason": "紐づけAPI失敗",
                        "amount": amount,
                        "issue_date": issue_date,
                        "partner_name": partner_name,
                    })
                    result["unmatched_count"] += 1
                    continue
            except Exception as e:
                result["errors"].append(f"紐づけエラー (書類ID={receipt_id}): {str(e)}")
                continue
        else:
            matched_info["attached"] = False  # dry_runなので未実行

        result["matched"].append(matched_info)
        result["matched_count"] += 1

    return result
