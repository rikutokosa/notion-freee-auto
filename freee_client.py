"""
freee会計 APIクライアント
OAuth2認証・トークン自動更新・取引（仕訳）登録・請求書登録・取引削除を担当する
"""
import os
import json
import time
import requests
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional


# ============================================================
# 設定
# ============================================================
FREEE_CLIENT_ID = os.environ.get("FREEE_CLIENT_ID", "740864584696172")
FREEE_CLIENT_SECRET = os.environ.get("FREEE_CLIENT_SECRET",
    "IIj_jZ1tTpacblsP9hmBAvWfx7f84bfA6OlDTE57eaecLldYTWjCIUs8rb7X627F9nKE4To5C5ByvJgWehTytg")
FREEE_COMPANY_ID = int(os.environ.get("FREEE_COMPANY_ID", "1856949"))

FREEE_AUTH_URL = "https://accounts.secure.freee.co.jp/public_api/authorize"
FREEE_TOKEN_URL = "https://accounts.secure.freee.co.jp/public_api/token"
FREEE_API_BASE = "https://api.freee.co.jp/api/1"
FREEE_IV_BASE = "https://api.freee.co.jp/iv"  # 請求書API

# トークン保存ファイル（本番はDBに保存）
TOKEN_FILE = Path(os.environ.get("TOKEN_FILE", "/tmp/freee_token.json"))


# ============================================================
# トークン管理
# ============================================================
def save_token(token_data: dict):
    """トークンをファイルに保存する"""
    token_data["saved_at"] = time.time()
    TOKEN_FILE.write_text(json.dumps(token_data, ensure_ascii=False, indent=2))


def load_token() -> Optional[dict]:
    """保存済みトークンを読み込む"""
    if not TOKEN_FILE.exists():
        return None
    try:
        return json.loads(TOKEN_FILE.read_text())
    except Exception:
        return None


def is_token_expired(token_data: dict) -> bool:
    """トークンが期限切れかどうか判定（5分前に期限切れとみなす）"""
    saved_at = token_data.get("saved_at", 0)
    expires_in = token_data.get("expires_in", 86400)
    return time.time() > saved_at + expires_in - 300


def refresh_access_token(token_data: dict) -> dict:
    """リフレッシュトークンでアクセストークンを更新する"""
    refresh_token = token_data.get("refresh_token")
    if not refresh_token:
        raise ValueError("リフレッシュトークンがありません。再認証が必要です。")

    resp = requests.post(FREEE_TOKEN_URL, data={
        "grant_type": "refresh_token",
        "client_id": FREEE_CLIENT_ID,
        "client_secret": FREEE_CLIENT_SECRET,
        "refresh_token": refresh_token,
    }, timeout=30)

    if resp.status_code != 200:
        raise ValueError(f"トークン更新失敗: {resp.status_code} {resp.text}")

    new_token = resp.json()
    save_token(new_token)
    return new_token


def get_valid_token() -> str:
    """有効なアクセストークンを取得する（必要に応じて自動更新）"""
    token_data = load_token()
    if not token_data:
        raise ValueError("freeeトークンが未設定です。/auth/freee から認証を行ってください。")

    if is_token_expired(token_data):
        token_data = refresh_access_token(token_data)

    return token_data["access_token"]


def get_auth_url(redirect_uri: str, state: str = "") -> str:
    """OAuth認証URLを生成する"""
    params = {
        "response_type": "code",
        "client_id": FREEE_CLIENT_ID,
        "redirect_uri": redirect_uri,
        "prompt": "select_company",
    }
    if state:
        params["state"] = state
    query = "&".join(f"{k}={v}" for k, v in params.items())
    return f"{FREEE_AUTH_URL}?{query}"


def exchange_code_for_token(code: str, redirect_uri: str) -> dict:
    """認証コードをトークンに交換する"""
    resp = requests.post(FREEE_TOKEN_URL, data={
        "grant_type": "authorization_code",
        "client_id": FREEE_CLIENT_ID,
        "client_secret": FREEE_CLIENT_SECRET,
        "code": code,
        "redirect_uri": redirect_uri,
    }, timeout=30)

    if resp.status_code != 200:
        raise ValueError(f"トークン取得失敗: {resp.status_code} {resp.text}")

    token_data = resp.json()
    save_token(token_data)
    return token_data


# ============================================================
# freee API ヘルパー
# ============================================================
def _api_headers() -> dict:
    token = get_valid_token()
    return {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }


def get_account_items() -> list:
    """勘定科目一覧を取得する"""
    resp = requests.get(
        f"{FREEE_API_BASE}/account_items",
        headers=_api_headers(),
        params={"company_id": FREEE_COMPANY_ID},
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json().get("account_items", [])


def get_partners() -> list:
    """取引先一覧を取得する"""
    resp = requests.get(
        f"{FREEE_API_BASE}/partners",
        headers=_api_headers(),
        params={"company_id": FREEE_COMPANY_ID},
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json().get("partners", [])


def get_sections() -> list:
    """部門一覧を取得する（権限がない場合は空リストを返す）"""
    try:
        resp = requests.get(
            f"{FREEE_API_BASE}/sections",
            headers=_api_headers(),
            params={"company_id": FREEE_COMPANY_ID},
            timeout=30,
        )
        if resp.status_code == 403:
            return []
        resp.raise_for_status()
        return resp.json().get("sections", [])
    except Exception:
        return []


def get_items() -> list:
    """品目一覧を取得する（権限がない場合は空リストを返す）"""
    try:
        resp = requests.get(
            f"{FREEE_API_BASE}/items",
            headers=_api_headers(),
            params={"company_id": FREEE_COMPANY_ID},
            timeout=30,
        )
        if resp.status_code == 403:
            return []
        resp.raise_for_status()
        return resp.json().get("items", [])
    except Exception:
        return []


def get_tags() -> list:
    """メモタグ一覧を取得する（権限がない場合は空リストを返す）"""
    try:
        resp = requests.get(
            f"{FREEE_API_BASE}/tags",
            headers=_api_headers(),
            params={"company_id": FREEE_COMPANY_ID},
            timeout=30,
        )
        if resp.status_code == 403:
            return []
        resp.raise_for_status()
        return resp.json().get("tags", [])
    except Exception:
        return []


# ============================================================
# マスタキャッシュ（1回取得して使い回す）
# ============================================================
_cache: dict = {}


def get_master_cache() -> dict:
    """勘定科目・取引先・品目・メモタグ・部門のキャッシュを取得する"""
    import logging
    logger = logging.getLogger(__name__)
    global _cache
    if not _cache:
        try:
            account_items = get_account_items()
            partners = get_partners()
            items = get_items()
            tags = get_tags()
            sections = get_sections()
            logger.info(f"マスタキャッシュ取得: 勘定科目={len(account_items)}件, 取引先={len(partners)}件, タグ={len(tags)}件, 部門={len(sections)}件")
            _cache = {
                "account_items": account_items,
                "partners": partners,
                "items": items,
                "tags": tags,
                "sections": sections,
            }
        except Exception as e:
            logger.error(f"マスタキャッシュ取得エラー: {e}")
            raise ValueError(f"freeeマスタデータ取得失敗: {e}")
    return _cache


def clear_master_cache():
    """マスタキャッシュをクリアする"""
    global _cache
    _cache = {}


def _find_account_item_id(name: str, items_cache: list) -> Optional[int]:
    for item in items_cache:
        if item.get("name") == name:
            return item.get("id")
    return None


def _find_partner_id(name: str, partners_cache: list) -> Optional[int]:
    for p in partners_cache:
        if p.get("name") == name:
            return p.get("id")
    return None


def _find_item_id(name: str, items_cache: list) -> Optional[int]:
    for item in items_cache:
        if item.get("name") == name:
            return item.get("id")
    return None


def _find_tag_id(name: str, tags_cache: list) -> Optional[int]:
    for tag in tags_cache:
        if tag.get("name") == name:
            return tag.get("id")
    return None


# ============================================================
# 取引（仕訳）登録
# ============================================================
def _find_section_id(name: str, sections_cache: list) -> Optional[int]:
    for sec in sections_cache:
        if sec.get("name") == name:
            return sec.get("id")
    return None


def create_deal(entry: dict, deal_type: str, cache: dict) -> dict:
    """
    freeeに取引（収入/支出）を登録する

    entry: {
        issue_date, due_date, partner_name, memo, section_name,
        details: [{account_item_name, tax_code, amount, description, item_name, tag_names}]
    }
    deal_type: "income" | "expense"
    """
    account_items = cache["account_items"]
    partners = cache["partners"]
    items = cache["items"]
    tags = cache["tags"]
    sections = cache.get("sections", [])

    # 取引先ID
    partner_id = None
    partner_name = entry.get("partner_name")
    if partner_name:
        partner_id = _find_partner_id(partner_name, partners)

    # 明細行を構築
    details = []
    for d in entry.get("details", []):
        account_item_id = _find_account_item_id(d["account_item_name"], account_items)
        if not account_item_id:
            import logging
            logging.getLogger(__name__).error(f"勘定科目が見つかりません: '{d['account_item_name']}' / 登録済み科目数={len(account_items)}")
            raise ValueError(f"勘定科目「{d['account_item_name']}」が見つかりません（freeeに登録されていないか名称が異なります）")

        item_id = _find_item_id(d.get("item_name", ""), items) if d.get("item_name") else None

        tag_ids = []
        for tag_name in d.get("tag_names", []):
            tid = _find_tag_id(tag_name, tags)
            if tid:
                tag_ids.append(tid)

        # 部門ID（entryのsection_nameから解決）
        section_name = entry.get("section_name")
        section_id = _find_section_id(section_name, sections) if section_name else None

        detail = {
            "account_item_id": account_item_id,
            "tax_code": d["tax_code"],
            "amount": d["amount"],
            "description": d.get("description", ""),
        }
        if item_id:
            detail["item_id"] = item_id
        if tag_ids:
            detail["tag_ids"] = tag_ids
        if section_id:
            detail["section_id"] = section_id
        elif section_name:
            # IDが見つからない場合は名前で登録を試みる
            detail["section_name"] = section_name

        details.append(detail)

    payload = {
        "company_id": FREEE_COMPANY_ID,
        "issue_date": entry["issue_date"],
        "type": deal_type,
        "details": details,
    }
    if entry.get("due_date"):
        payload["due_date"] = entry["due_date"]
    if partner_id:
        payload["partner_id"] = partner_id
    elif partner_name:
        payload["partner_name"] = partner_name

    resp = requests.post(
        f"{FREEE_API_BASE}/deals",
        headers=_api_headers(),
        json=payload,
        timeout=30,
    )

    import logging
    logger = logging.getLogger(__name__)
    logger.info(f"仕訳登録リクエスト: type={deal_type}, issue_date={entry['issue_date']}, details={len(details)}件, section={entry.get('section_name')}, tags={[d.get('tag_names') for d in entry.get('details', [])]}")

    if resp.status_code not in (200, 201):
        logger.error(f"仕訳登録失敗: {resp.status_code} {resp.text[:1000]}")
        raise ValueError(f"freee取引登録失敗: {resp.status_code} {resp.text[:500]}")

    logger.info(f"仕訳登録成功: ID={resp.json().get('deal', {}).get('id')}")
    return resp.json().get("deal", {})


def delete_deal(deal_id: int) -> bool:
    """
    freeeの取引を削除する（入社前辞退時に使用）
    """
    resp = requests.delete(
        f"{FREEE_API_BASE}/deals/{deal_id}",
        headers=_api_headers(),
        params={"company_id": FREEE_COMPANY_ID},
        timeout=30,
    )
    if resp.status_code in (200, 204):
        return True
    raise ValueError(f"freee取引削除失敗: {resp.status_code} {resp.text[:300]}")


# ============================================================
# 請求書登録
# ============================================================
def create_invoice(entry: dict, cache: dict) -> dict:
    """
    freeeに請求書を登録する

    entry: {
        issue_date,        # 請求日
        due_date,          # 入金期日
        partner_name,      # 取引先名
        title,             # 件名（例: 人材紹介手数料）
        invoice_number,    # 請求書番号（省略可）
        details: [{
            name,          # 品目名
            unit_price,    # 単価
            quantity,      # 数量
            description,   # 備考
        }]
    }
    """
    partners = cache["partners"]

    partner_id = None
    partner_name = entry.get("partner_name")
    if partner_name:
        partner_id = _find_partner_id(partner_name, partners)

    # 明細行
    invoice_lines = []
    for d in entry.get("details", []):
        line = {
            "name": d.get("name", "人材紹介手数料"),
            "quantity": d.get("quantity", 1),
            "unit_price": d.get("unit_price", 0),
            "description": d.get("description", ""),
            "tax_code": d.get("tax_code", 1),
            "type": "normal",
        }
        invoice_lines.append(line)

    payload = {
        "company_id": FREEE_COMPANY_ID,
        "issue_date": entry["issue_date"],
        "due_date": entry.get("due_date"),
        "title": entry.get("title", "人材紹介手数料"),
        "invoice_lines": invoice_lines,
        "invoice_status": "issue",  # 発行済み
    }
    if partner_id:
        payload["partner_id"] = partner_id
    elif partner_name:
        payload["partner_name"] = partner_name
    if entry.get("invoice_number"):
        payload["invoice_number"] = entry["invoice_number"]

    import logging
    logger = logging.getLogger(__name__)
    logger.info(f"請求書登録リクエスト: partner={partner_name}, issue_date={entry['issue_date']}, lines={len(invoice_lines)}件")

    resp = requests.post(
        f"{FREEE_IV_BASE}/invoices",
        headers=_api_headers(),
        json={"invoice": payload},
        timeout=30,
    )

    if resp.status_code not in (200, 201):
        logger.error(f"請求書登録失敗: {resp.status_code} {resp.text[:1000]}")
        raise ValueError(f"freee請求書登録失敗: {resp.status_code} {resp.text[:500]}")

    logger.info(f"請求書登録成功: ID={resp.json().get('invoice', {}).get('id')}")
    return resp.json().get("invoice", {})


# ============================================================
# 統合登録関数
# ============================================================
def register_journal(sales_entry: Optional[dict],
                     purchase_entry: Optional[dict],
                     pca_entry: Optional[dict] = None) -> dict:
    """
    売上仕訳・仕入仕訳・PCA仕訳をfreeeに登録する
    キャッシュを1回取得して全ての登録に使い回す
    """
    cache = get_master_cache()
    result = {"sales_id": None, "purchase_id": None, "pca_id": None, "errors": []}

    if sales_entry:
        try:
            deal = create_deal(sales_entry, "income", cache)
            result["sales_id"] = deal.get("id")
        except Exception as e:
            result["errors"].append(f"売上仕訳エラー: {str(e)}")

    if purchase_entry:
        try:
            deal = create_deal(purchase_entry, "expense", cache)
            result["purchase_id"] = deal.get("id")
        except Exception as e:
            result["errors"].append(f"仕入仕訳エラー: {str(e)}")

    if pca_entry:
        try:
            deal = create_deal(pca_entry, "expense", cache)
            result["pca_id"] = deal.get("id")
        except Exception as e:
            result["errors"].append(f"PCA仕訳エラー: {str(e)}")

    return result


def register_invoice_and_deal(invoice_entry: dict, sales_entry: dict) -> dict:
    """
    請求書を登録し、その後取引（仕訳）も登録する
    （請求書登録タイプの場合に使用）
    """
    cache = get_master_cache()
    result = {"invoice_id": None, "sales_id": None, "errors": []}

    try:
        invoice = create_invoice(invoice_entry, cache)
        result["invoice_id"] = invoice.get("id")
    except Exception as e:
        result["errors"].append(f"請求書登録エラー: {str(e)}")
        return result

    # 請求書登録後に売上仕訳も登録
    if sales_entry:
        try:
            deal = create_deal(sales_entry, "income", cache)
            result["sales_id"] = deal.get("id")
        except Exception as e:
            result["errors"].append(f"売上仕訳エラー（請求書登録後）: {str(e)}")

    return result


def delete_deals(sales_id: Optional[int], purchase_id: Optional[int]) -> dict:
    """
    取引を削除する（入社前辞退時に使用）
    """
    result = {"deleted_sales": False, "deleted_purchase": False, "errors": []}

    if sales_id:
        try:
            delete_deal(sales_id)
            result["deleted_sales"] = True
        except Exception as e:
            result["errors"].append(f"売上取引削除エラー: {str(e)}")

    if purchase_id:
        try:
            delete_deal(purchase_id)
            result["deleted_purchase"] = True
        except Exception as e:
            result["errors"].append(f"仕入取引削除エラー: {str(e)}")

    return result
