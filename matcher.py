"""
freee 書類照合モジュール

freeeのファイルボックス（証憑ファイル）に登録された未照合書類を
既存の取引（仕訳）と自動照合・紐づけする。

照合ロジック:
1. GET /api/1/receipts?start_date=...&end_date=... で書類一覧を取得
2. 各書類のfreee OCR解析結果（金額・発行日・発行元）を確認
3. freee OCRが不十分な場合（金額または発行日がnull）は
   GET /api/1/receipts/{id}/download でPDFをダウンロードし
   OpenAI Vision API で AI-OCR を実行して補完する
4. GET /api/1/deals で金額・日付範囲で仕訳を検索
5. 金額完全一致 → 候補として記録
6. 候補が1件 → 自動紐づけ
7. 候補が複数 → 取引先名で絞り込み → それでも複数なら日付が最も近いものを選択
8. POST /api/1/deals/{id}/receipts で紐づけ実行
"""
import logging
import os
import re
import tempfile
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

# 取引先名キャッシュ（partner_id -> name）
_partner_name_cache: dict = {}


def get_partner_name(partner_id: int) -> str:
    """partner_id から取引先名を取得する（キャッシュ付き）"""
    if not partner_id:
        return ""
    if partner_id in _partner_name_cache:
        return _partner_name_cache[partner_id]
    try:
        resp = requests.get(
            f"{FREEE_API_BASE}/partners/{partner_id}",
            headers=_api_headers(),
            params={"company_id": FREEE_COMPANY_ID},
            timeout=15,
        )
        if resp.status_code == 200:
            name = resp.json().get("partner", {}).get("name", "")
            _partner_name_cache[partner_id] = name
            return name
    except Exception as e:
        logger.warning(f"取引先名取得失敗: partner_id={partner_id}, {e}")
    return ""


# 照合時の日付範囲（書類の発行日 ± DATE_RANGE_DAYS 日以内の仕訳を検索）
DATE_RANGE_DAYS = 90

# 金額の許容誤差（円）
# 0: 完全一致のみ
AMOUNT_TOLERANCE = 0


# ============================================================
# 書類一覧取得
# ============================================================

def get_unmatched_receipts() -> list:
    """
    未登録（取引未紐づけ）の書類一覧を全件取得する（ページング対応）
    freee APIの仕様上、start_date/end_dateが必須のため過去1年分を対象とする。

    Returns:
        list of receipt dicts with OCR metadata
    """
    # freee receipts APIはstart_date/end_dateが必須
    end_date = datetime.today().strftime("%Y-%m-%d")
    start_date = (datetime.today() - timedelta(days=365)).strftime("%Y-%m-%d")

    all_receipts = []
    offset = 0
    while True:
        params = {
            "company_id": FREEE_COMPANY_ID,
            "category": "without_deal",
            "start_date": start_date,
            "end_date": end_date,
            "limit": 100,
            "offset": offset,
        }
        resp = requests.get(
            f"{FREEE_API_BASE}/receipts",
            headers=_api_headers(),
            params=params,
            timeout=30,
        )
        if resp.status_code != 200:
            raise ValueError(f"書類一覧取得失敗: {resp.status_code} {resp.text[:300]}")

        page = resp.json().get("receipts", [])
        all_receipts.extend(page)
        if len(page) < 100:
            break  # 最後のページ
        offset += 100

    logger.info(f"未登録書類: {len(all_receipts)}件取得")
    return all_receipts


# ============================================================
# AI-OCR（freee OCR が不十分な場合のフォールバック）
# ============================================================

def _download_receipt_pdf(receipt_id: int) -> Optional[bytes]:
    """freeeからPDFをダウンロードして bytes を返す"""
    resp = requests.get(
        f"{FREEE_API_BASE}/receipts/{receipt_id}/download",
        headers=_api_headers(),
        params={"company_id": FREEE_COMPANY_ID},
        timeout=30,
    )
    if resp.status_code == 200 and len(resp.content) > 100:
        return resp.content
    logger.warning(f"PDFダウンロード失敗: ID={receipt_id}, status={resp.status_code}")
    return None


def _pdf_to_images(pdf_bytes: bytes) -> list:
    """PDF bytes を PIL Image のリストに変換する（最大3ページ）"""
    try:
        from pdf2image import convert_from_bytes
        images = convert_from_bytes(pdf_bytes, first_page=1, last_page=3, dpi=150)
        return images
    except Exception as e:
        logger.warning(f"PDF→画像変換失敗: {e}")
        return []


def _ocr_image_with_ai(image) -> str:
    """PIL Image を OpenAI Vision API に送ってテキストを抽出する"""
    import base64
    import io

    openai_key = os.environ.get("OPENAI_API_KEY", "")
    if not openai_key:
        return ""

    # API BaseURL: 環境変数から取得（本番VPSでも動くように）
    openai_base = os.environ.get("OPENAI_API_BASE", "https://api.openai.com/v1").rstrip("/")

    # PIL Image → JPEG bytes → base64
    buf = io.BytesIO()
    image.save(buf, format="JPEG", quality=90)
    b64 = base64.b64encode(buf.getvalue()).decode()

    try:
        resp = requests.post(
            f"{openai_base}/chat/completions",
            headers={"Authorization": f"Bearer {openai_key}", "Content-Type": "application/json"},
            json={
                "model": "gpt-4o-mini",
                "messages": [{
                    "role": "user",
                    "content": [
                        {
                            "type": "image_url",
                            "image_url": {"url": f"data:image/jpeg;base64,{b64}", "detail": "high"},
                        },
                        {
                            "type": "text",
                            "text": (
                                "これは日本語の請求書PDFです。以下の情報を抽出してください。\n"
                                "1. 請求金額（合計金額・税込金額）: 数字のみ\n"
                                "2. 請求日または発行日: YYYY-MM-DD形式\n"
                                "3. 請求元会社名（発行者・請求元）\n\n"
                                "回答は必ず以下のJSON形式で返してください:\n"
                                '{"amount": 数値または null, "issue_date": "YYYY-MM-DD"または null, "partner_name": "会社名"または null}\n'
                                "金額はカンマや円記号を除いた整数で返してください。"
                            ),
                        },
                    ],
                }],
                "max_tokens": 500,
            },
            timeout=30,
        )
        if resp.status_code == 200:
            content = resp.json()["choices"][0]["message"]["content"]
            return content
        else:
            logger.warning(f"AI-OCR APIエラー: {resp.status_code} {resp.text[:200]}")
    except Exception as e:
        logger.warning(f"AI-OCRエラー: {e}")
    return ""


def _parse_ai_ocr_result(text: str) -> dict:
    """AI-OCRの結果テキストからJSONを抽出してパースする"""
    if not text:
        return {}
    # JSONブロックを抽出
    json_match = re.search(r'\{[^{}]+\}', text, re.DOTALL)
    if not json_match:
        return {}
    try:
        import json
        data = json.loads(json_match.group())
        result = {}
        # amount
        if data.get("amount") is not None:
            try:
                result["amount"] = int(str(data["amount"]).replace(",", "").replace("円", "").strip())
            except (ValueError, TypeError):
                pass
        # issue_date
        if data.get("issue_date"):
            date_str = str(data["issue_date"]).strip()
            # YYYY-MM-DD形式に正規化
            date_match = re.search(r'(\d{4})[/-](\d{1,2})[/-](\d{1,2})', date_str)
            if date_match:
                y, m, d = date_match.groups()
                result["issue_date"] = f"{y}-{int(m):02d}-{int(d):02d}"
        # partner_name
        if data.get("partner_name"):
            result["partner_name"] = str(data["partner_name"]).strip()
        return result
    except Exception as e:
        logger.warning(f"AI-OCR結果パース失敗: {e}, text={text[:200]}")
        return {}


def ai_ocr_receipt(receipt_id: int) -> dict:
    """
    freeeのPDFをダウンロードしてAI-OCRで金額・日付・取引先を抽出する

    Returns:
        {"amount": int|None, "issue_date": str|None, "partner_name": str|None, "ai_ocr": True}
    """
    logger.info(f"AI-OCR開始: receipt_id={receipt_id}")

    pdf_bytes = _download_receipt_pdf(receipt_id)
    if not pdf_bytes:
        return {"amount": None, "issue_date": None, "partner_name": None, "ai_ocr": True, "ai_ocr_error": "PDFダウンロード失敗"}

    images = _pdf_to_images(pdf_bytes)
    if not images:
        return {"amount": None, "issue_date": None, "partner_name": None, "ai_ocr": True, "ai_ocr_error": "PDF→画像変換失敗"}

    # 最初のページ（表紙・請求書ページ）を解析
    ocr_text = _ocr_image_with_ai(images[0])
    parsed = _parse_ai_ocr_result(ocr_text)

    logger.info(f"AI-OCR結果: receipt_id={receipt_id}, parsed={parsed}")
    return {
        "amount": parsed.get("amount"),
        "issue_date": parsed.get("issue_date"),
        "partner_name": parsed.get("partner_name"),
        "ai_ocr": True,
        "ai_ocr_raw": ocr_text[:500],
    }


# ============================================================
# 仕訳検索・スコアリング
# ============================================================

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
    seen_ids = set()

    # 発生日（issue_date）と支払期日（due_date）の両方で検索してマージ
    date_filter_sets = [
        {"start_issue_date": start_date, "end_issue_date": end_date},
        {"start_due_date": start_date, "end_due_date": end_date},
    ]

    for deal_type in ("income", "expense"):
        for date_filters in date_filter_sets:
            offset = 0
            while True:
                params = {
                    "company_id": FREEE_COMPANY_ID,
                    "type": deal_type,
                    "limit": 100,
                    "offset": offset,
                    **date_filters,
                }
                resp = requests.get(
                    f"{FREEE_API_BASE}/deals",
                    headers=_api_headers(),
                    params=params,
                    timeout=30,
                )
                if resp.status_code != 200:
                    logger.warning(f"取引検索失敗 ({deal_type}, {list(date_filters.keys())[0]}): {resp.status_code}")
                    break

                deals = resp.json().get("deals", [])
                # 金額でフィルタリング（重複除去）
                for deal in deals:
                    did = deal.get("id")
                    if did in seen_ids:
                        continue
                    deal_amount = _calc_deal_amount(deal)
                    if abs(deal_amount - amount) <= AMOUNT_TOLERANCE:  # 完全一致
                        all_deals.append(deal)
                        seen_ids.add(did)

                if len(deals) < 100:
                    break  # 最後のページ
                offset += 100

    logger.info(f"金額{amount}円・日付{issue_date}±{date_range_days}日で{len(all_deals)}件の取引が候補")
    return all_deals


def _calc_deal_amount(deal: dict) -> int:
    """取引の合計金額を計算する。
    deal.amount（仕訳全体の金額）を優先し、なければ明細行の合計を使用する。
    """
    # deal.amountがあればそれを優先使用（freeeの公式合計金額）
    if deal.get("amount") is not None:
        return abs(int(deal["amount"]))
    # なければ明細行の合計
    details = deal.get("details", [])
    if not details:
        return 0
    total = sum(abs(d.get("amount", 0)) for d in details)
    return total


def _score_deal(deal: dict, receipt_meta: dict) -> dict:
    """
    取引と書類のマッチングスコアを計算する（高いほど一致度が高い）

    スコア基準:
    - 日付の近さ: 最大50点（同日=50点、1日ずれ=49点...）
    - 取引先名の一致: 完全一致30点 / 部分一致15点

    Returns:
        dict: {"total": float, "breakdown": [...], "warnings": [...]}
    """
    score = 0.0
    breakdown = []  # 一致した項目
    warnings = []   # 不安点

    # 日付スコア
    receipt_date_str = receipt_meta.get("issue_date")
    deal_date_str = deal.get("issue_date")

    if receipt_date_str and deal_date_str:
        try:
            receipt_date = datetime.strptime(receipt_date_str, "%Y-%m-%d")
            deal_date = datetime.strptime(deal_date_str, "%Y-%m-%d")
            diff_days = abs((receipt_date - deal_date).days)
            date_score = max(0, 50 - diff_days)
            score += date_score
            if diff_days == 0:
                breakdown.append(f"日付完全一致 (+50点)")
            elif diff_days <= 7:
                breakdown.append(f"日付近い（{diff_days}日差） (+{date_score}点)")
            else:
                warnings.append(f"日付{diff_days}日差 (+{date_score}点)")
        except ValueError:
            warnings.append("日付比較不可")
    else:
        if not receipt_date_str:
            warnings.append("書類の発行日が不明")
        if not deal_date_str:
            warnings.append("仕訳の発生日が不明")

    # 取引先名スコア
    receipt_partner = (receipt_meta.get("partner_name") or "").strip()
    deal_partner_obj = deal.get("partner") or {}
    deal_partner = (deal_partner_obj.get("name") or "").strip()
    if not deal_partner and deal.get("partner_id"):
        deal_partner = get_partner_name(deal["partner_id"]).strip()

    if receipt_partner and deal_partner:
        if receipt_partner == deal_partner:
            score += 30
            breakdown.append(f"取引先完全一致: {deal_partner} (+30点)")
        elif receipt_partner in deal_partner or deal_partner in receipt_partner:
            score += 15
            breakdown.append(f"取引先部分一致: {receipt_partner} ≒ {deal_partner} (+15点)")
        else:
            warnings.append(f"取引先不一致: 書類={receipt_partner} / 仕訳={deal_partner}")
    elif not receipt_partner:
        warnings.append("書類の取引先名が不明")
    elif not deal_partner:
        warnings.append("仕訳の取引先が未設定")

    # 金額は候補検索時に完全一致で絞り込み済みなので常に一致
    amount = receipt_meta.get("amount")
    if amount:
        breakdown.append(f"金額一致: {int(amount):,}円")

    return {"total": score, "breakdown": breakdown, "warnings": warnings}


# ============================================================
# 紐づけ実行
# ============================================================

def attach_receipt_to_deal(deal_id: int, receipt_id: int) -> bool:
    """
    取引に書類を紐づける

    Args:
        deal_id: freee取引ID
        receipt_id: freeeファイルボックスID

    Returns:
        True if successful
    """
    # まず仕訳の現在の内容を取得
    get_resp = requests.get(
        f"{FREEE_API_BASE}/deals/{deal_id}",
        headers=_api_headers(),
        params={"company_id": FREEE_COMPANY_ID},
        timeout=30,
    )
    if get_resp.status_code != 200:
        logger.warning(f"紐づけ失敗(取引取得エラー): 取引ID={deal_id}, status={get_resp.status_code}, body={get_resp.text[:200]}")
        return False

    deal = get_resp.json().get("deal", {})

    # 既存のreceipt_idsに追加（重複除去）
    existing_ids = [r.get("id") for r in deal.get("receipts", []) if r.get("id")]
    new_receipt_ids = list(set(existing_ids + [receipt_id]))

    # 明細行の必須フィールドを整備
    details = []
    for d in deal.get("details", []):
        detail = {
            "account_item_id": d.get("account_item_id"),
            "tax_code": d.get("tax_code"),
            "amount": d.get("amount"),
        }
        if d.get("section_id"):
            detail["section_id"] = d["section_id"]
        if d.get("item_id"):
            detail["item_id"] = d["item_id"]
        if d.get("segment_1_tag_id"):
            detail["segment_1_tag_id"] = d["segment_1_tag_id"]
        if d.get("description"):
            detail["description"] = d["description"]
        details.append(detail)

    put_payload = {
        "company_id": FREEE_COMPANY_ID,
        "issue_date": deal.get("issue_date"),
        "type": deal.get("type"),
        "receipt_ids": new_receipt_ids,
        "details": details,
    }
    if deal.get("due_date"):
        put_payload["due_date"] = deal["due_date"]
    if deal.get("partner_id"):
        put_payload["partner_id"] = deal["partner_id"]

    resp = requests.put(
        f"{FREEE_API_BASE}/deals/{deal_id}",
        headers=_api_headers(),
        json=put_payload,
        timeout=30,
    )
    if resp.status_code in (200, 201):
        logger.info(f"紐づけ成功: 取引ID={deal_id} ← 書類ID={receipt_id}")
        return True
    else:
        logger.warning(f"紐づけ失敗: 取引ID={deal_id}, 書類ID={receipt_id}, "
                       f"status={resp.status_code}, body={resp.text[:500]}")
        return False


# ============================================================
# メイン処理
# ============================================================

def run_matching(dry_run: bool = False) -> dict:
    """
    書類照合のメイン処理

    フロー:
    1. freeeから未紐づけ書類一覧を取得
    2. freee OCR結果（receipt_metadatum）を確認
    3. OCR結果が不十分（金額または発行日がnull）な場合はAI-OCRでPDFを直接解析
    4. 金額・日付で仕訳を検索して最良候補に紐づけ

    Args:
        dry_run: Trueの場合は照合結果を返すだけで実際の紐づけは行わない

    Returns:
        {
            "matched": [...],
            "unmatched": [...],
            "errors": [...],
            "total_receipts": int,
            "matched_count": int,
            "unmatched_count": int,
            "ai_ocr_count": int,  # AI-OCRを使用した件数
        }
    """
    result = {
        "matched": [],
        "unmatched": [],
        "errors": [],
        "total_receipts": 0,
        "matched_count": 0,
        "unmatched_count": 0,
        "ai_ocr_count": 0,
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
        mime_type = receipt.get("mime_type", "")
        used_ai_ocr = False

        # freee OCRが不十分な場合はAI-OCRでフォールバック
        if (not amount or not issue_date) and mime_type in ("application/pdf", "image/jpeg", "image/png", "image/gif"):
            logger.info(f"freee OCR不十分 → AI-OCR実行: receipt_id={receipt_id} (amount={amount}, date={issue_date})")
            try:
                ai_result = ai_ocr_receipt(receipt_id)
                used_ai_ocr = True
                result["ai_ocr_count"] += 1

                # AI-OCR結果で補完（freee OCRの値を優先）
                if not amount and ai_result.get("amount"):
                    amount = ai_result["amount"]
                if not issue_date and ai_result.get("issue_date"):
                    issue_date = ai_result["issue_date"]
                if not partner_name and ai_result.get("partner_name"):
                    partner_name = ai_result["partner_name"]

                # receipt_metaを更新（スコアリングで使用するため）
                receipt_meta = {
                    "amount": amount,
                    "issue_date": issue_date,
                    "partner_name": partner_name,
                }
                logger.info(f"AI-OCR補完後: amount={amount}, date={issue_date}, partner={partner_name}")
            except Exception as e:
                logger.warning(f"AI-OCRエラー (receipt_id={receipt_id}): {e}")

        # それでも金額・日付が取れない場合はスキップ
        if not amount or not issue_date:
            reason = "OCR解析結果なし（AI-OCRでも取得不可）" if used_ai_ocr else "OCR解析結果なし（金額または発行日が不明）"
            if not amount and not issue_date:
                reason = ("AI-OCRでも金額・発行日を取得できませんでした" if used_ai_ocr
                          else "OCR解析結果なし（金額・発行日ともに不明）")
            elif not amount:
                reason = "AI-OCRでも金額を取得できませんでした" if used_ai_ocr else "OCR解析結果なし（金額が不明）"
            elif not issue_date:
                reason = "AI-OCRでも発行日を取得できませんでした" if used_ai_ocr else "OCR解析結果なし（発行日が不明）"

            result["unmatched"].append({
                "receipt_id": receipt_id,
                "description": description,
                "reason": reason,
                "amount": amount,
                "issue_date": issue_date,
                "partner_name": partner_name,
                "used_ai_ocr": used_ai_ocr,
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
                "reason": f"一致する取引なし（金額={amount}円, 発行日={issue_date}, 検索範囲=発行日±{DATE_RANGE_DAYS}日）",
                "amount": amount,
                "issue_date": issue_date,
                "partner_name": partner_name,
                "used_ai_ocr": used_ai_ocr,
            })
            result["unmatched_count"] += 1
            continue

        # スコアリングして最良候補を選択
        scored = sorted(
            [(deal, _score_deal(deal, receipt_meta)) for deal in candidates],
            key=lambda x: x[1]["total"],
            reverse=True,
        )
        best_deal, best_score_info = scored[0]
        best_score = best_score_info["total"]
        deal_id = best_deal.get("id")

        # 紐づけ実行
        matched_info = {
            "receipt_id": receipt_id,
            "deal_id": deal_id,
            "score": best_score,
            "score_breakdown": best_score_info["breakdown"],
            "score_warnings": best_score_info["warnings"],
            "receipt_amount": amount,
            "receipt_date": issue_date,
            "receipt_partner": partner_name,
            "receipt_description": description,
            "deal_date": best_deal.get("issue_date"),
            "deal_partner": ((best_deal.get("partner") or {}).get("name") or get_partner_name(best_deal.get("partner_id") or 0)),
            "deal_amount": _calc_deal_amount(best_deal),
            "candidates_count": len(candidates),
            "auto": True,
            "attached": False,
            "used_ai_ocr": used_ai_ocr,
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
                        "used_ai_ocr": used_ai_ocr,
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
