"""
freee会計 APIクライアント
OAuth2認証・トークン自動更新・取引（仕訳）登録・請求書登録・取引削除・証憑アップロードを担当する
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

# トークン保存ファイル
# Railwayボリュームが /data にマウントされていればそこに保存（デプロイ後も永続）
# なければ /tmp にフォールバック（コンテナ再起動で消える）
def _resolve_token_file() -> Path:
    data_dir = Path("/data")
    try:
        data_dir.mkdir(parents=True, exist_ok=True)
        test_file = data_dir / ".write_test"
        test_file.write_text("ok")
        test_file.unlink()
        return data_dir / "freee_token.json"
    except Exception:
        return Path("/tmp/freee_token.json")

_token_file_env = os.environ.get("TOKEN_FILE", "").strip()
TOKEN_FILE = Path(_token_file_env) if _token_file_env else _resolve_token_file()


# ============================================================
# トークン管理（環境変数ベース + ファイルフォールバック）
# ============================================================
def save_token(token_data: dict):
    """トークンをファイルと環境変数（プロセス内）に保存する"""
    token_data["saved_at"] = time.time()
    token_json = json.dumps(token_data, ensure_ascii=False)
    # ファイルに保存（/tmp、コンテナ再起動で消える）
    TOKEN_FILE.write_text(token_json)
    # プロセス内環境変数にも保存（同一プロセス内で有効）
    os.environ["FREEE_TOKEN_JSON"] = token_json
    # リフレッシュトークンを個別環境変数にも保存（起動時の復元用）
    if token_data.get("refresh_token"):
        os.environ["FREEE_REFRESH_TOKEN"] = token_data["refresh_token"]


def load_token() -> Optional[dict]:
    """保存済みトークンを読み込む（環境変数 → ファイル → リフレッシュトークンの順で試みる）"""
    # 1. プロセス内環境変数から読み込む（最優先）
    token_json = os.environ.get("FREEE_TOKEN_JSON", "")
    if token_json:
        try:
            return json.loads(token_json)
        except Exception:
            pass

    # 2. ファイルから読み込む
    if TOKEN_FILE.exists():
        try:
            data = json.loads(TOKEN_FILE.read_text())
            # ファイルから読み込んだ場合は環境変数にも同期
            os.environ["FREEE_TOKEN_JSON"] = json.dumps(data, ensure_ascii=False)
            return data
        except Exception:
            pass

    # 3. FREEE_REFRESH_TOKEN 環境変数からトークンを復元する
    refresh_token = os.environ.get("FREEE_REFRESH_TOKEN", "")
    if refresh_token:
        # リフレッシュトークンだけで最低限のtokenデータを作成
        # saved_at=0にすることで期限切れ扱いにし、自動更新を促す
        return {
            "refresh_token": refresh_token,
            "access_token": "",
            "expires_in": 86400,
            "saved_at": 0,
        }

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
    """取引先一覧を全件取得する（最大1000件）"""
    all_partners = []
    offset = 0
    limit = 100
    while True:
        resp = requests.get(
            f"{FREEE_API_BASE}/partners",
            headers=_api_headers(),
            params={"company_id": FREEE_COMPANY_ID, "limit": limit, "offset": offset},
            timeout=30,
        )
        resp.raise_for_status()
        batch = resp.json().get("partners", [])
        all_partners.extend(batch)
        if len(batch) < limit:
            break
        offset += limit
        if offset >= 1000:
            break
    return all_partners


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
            logger.info(
                f"マスタキャッシュ取得: 勘定科目={len(account_items)}件, "
                f"取引先={len(partners)}件, タグ={len(tags)}件, 部門={len(sections)}件"
            )
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

    # 取引先ID: Notionに直接入力されたIDを優先、ない場合は名前で検索
    partner_id = entry.get("partner_id")
    partner_name = entry.get("partner_name")
    if not partner_id and partner_name:
        partner_id = _find_partner_id(partner_name, partners)

    # 明細行を構築
    details = []
    for d in entry.get("details", []):
        account_item_id = _find_account_item_id(d["account_item_name"], account_items)
        if not account_item_id:
            import logging
            logging.getLogger(__name__).error(
                f"勘定科目が見つかりません: '{d['account_item_name']}' "
                f"/ 登録済み科目数={len(account_items)}"
            )
            raise ValueError(
                f"勘定科目「{d['account_item_name']}」が見つかりません"
                "（freeeに登録されていないか名称が異なります）"
            )

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
        payload["partner_id"] = int(partner_id)
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
    logger.info(
        f"仕訳登録リクエスト: type={deal_type}, issue_date={entry['issue_date']}, "
        f"details={len(details)}件, section={entry.get('section_name')}, "
        f"tags={[d.get('tag_names') for d in entry.get('details', [])]}"
    )

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


def resolve_partner_id(partner_name: str, partners: list) -> Optional[int]:
    """
    取引先名からpartner_idを解決する
    1. 完全一致（大文字小文字無視）
    2. 部分一致（大文字小文字無視）
    """
    name_lower = partner_name.lower()
    # 1. 完全一致
    for p in partners:
        if p.get("name", "").lower() == name_lower:
            return p.get("id")
    # 2. 部分一致（マスタ名に検索名が含まれる、または逆）
    for p in partners:
        pname_lower = p.get("name", "").lower()
        if name_lower in pname_lower or pname_lower in name_lower:
            return p.get("id")
    return None


def create_partner(name: str, shortcut1: str = "") -> dict:
    """
    freeeに新規取引先を作成する
    name: 取引先名（必須）
    shortcut1: ショートカット（省略可）
    戻り値: 作成された取引先情報（id, nameを含む）
    """
    import logging
    logger = logging.getLogger(__name__)
    payload = {
        "company_id": FREEE_COMPANY_ID,
        "name": name,
    }
    if shortcut1:
        payload["shortcut1"] = shortcut1
    resp = requests.post(
        f"{FREEE_API_BASE}/partners",
        headers=_api_headers(),
        json=payload,
        timeout=30,
    )
    if resp.status_code not in (200, 201):
        logger.error(f"取引先作成失敗: {resp.status_code} {resp.text[:500]}")
        raise ValueError(f"freee取引先作成失敗: {resp.status_code} {resp.text[:300]}")
    partner = resp.json().get("partner", {})
    logger.info(f"取引先作成成功: {name} (ID={partner.get('id')})")
    # マスタキャッシュをクリアして次回から新しい取引先を反映させる
    clear_master_cache()
    return partner


def search_deals(
    partner_name: Optional[str] = None,
    start_issue_date: Optional[str] = None,
    end_issue_date: Optional[str] = None,
    deal_type: Optional[str] = None,
    limit: int = 100,
) -> list:
    """
    freeeの取引一覧を検索する
    partner_nameが指定された場合、取引先マスタからpartner_idに変換してAPIに渡す
    deal_type: 'income' | 'expense' | None(両方)
    """
    params = {
        "company_id": FREEE_COMPANY_ID,
        "limit": limit,
    }
    if start_issue_date:
        params["start_issue_date"] = start_issue_date
    if end_issue_date:
        params["end_issue_date"] = end_issue_date
    if deal_type:
        params["type"] = deal_type

    # partner_nameをpartner_idに変換してAPIに渡す
    # freee APIはpartner_idフィルタに対応しているが、partner_nameフィルタは非対応
    if partner_name:
        try:
            partners = get_partners()
            partner_id = resolve_partner_id(partner_name, partners)
            if partner_id:
                params["partner_id"] = partner_id
        except Exception:
            pass  # 取引先マスタ取得失敗時はフィルタなしで全件取得

    resp = requests.get(
        f"{FREEE_API_BASE}/deals",
        headers=_api_headers(),
        params=params,
        timeout=30,
    )
    resp.raise_for_status()
    deals = resp.json().get("deals", [])

    # partner_idが解決できなかった場合のフォールバック：partner_nameでローカルフィルタ
    if partner_name and "partner_id" not in params:
        partner_name_lower = partner_name.lower()
        deals = [
            d for d in deals
            if partner_name_lower in (d.get("partner_name") or "").lower()
        ]

    return deals


def delete_invoice(invoice_id: int) -> bool:
    """
    freee請求書を取消する（DELETEエンドポイントは存在しないため PUT /cancel を使用）
    freee請求書APIには DELETE /iv/invoices/{id} が存在しない。
    代わりに PUT /iv/invoices/{id}/cancel で取消を行う。
    """
    import logging
    logger = logging.getLogger(__name__)
    resp = requests.put(
        f"{FREEE_IV_BASE}/invoices/{invoice_id}/cancel",
        headers=_api_headers(),
        json={"company_id": FREEE_COMPANY_ID},
        timeout=30,
    )
    logger.info(f"請求書cancel: invoice_id={invoice_id}, status={resp.status_code}, body={resp.text[:200]}")
    if resp.status_code in (200, 201, 204):
        return True
    raise ValueError(f"freee請求書取消失敗: {resp.status_code} {resp.text[:300]}")


def search_invoices(
    partner_name: Optional[str] = None,
    start_issue_date: Optional[str] = None,
    end_issue_date: Optional[str] = None,
    limit: int = 100,
) -> list:
    """
    freee請求書一覧を検索する
    partner_nameが指定された場合、取引先マスタからpartner_idに変換してAPIに渡す
    注意: freee請求書APIのパラメータ名は会計APIと異なる
      - partner_ids (複数形、カンマ区切り)
      - start_billing_date / end_billing_date
    """
    params = {
        "company_id": FREEE_COMPANY_ID,
        "limit": min(limit, 100),  # 請求書APIの最大は100
    }
    # 請求書APIは start_billing_date / end_billing_date を使用
    if start_issue_date:
        params["start_billing_date"] = start_issue_date
    if end_issue_date:
        params["end_billing_date"] = end_issue_date

    # partner_nameをpartner_idに変換してAPIに渡す
    # 請求書APIは partner_ids (複数形、カンマ区切り)
    if partner_name:
        try:
            partners = get_partners()
            partner_id = resolve_partner_id(partner_name, partners)
            if partner_id:
                params["partner_ids"] = str(partner_id)  # 請求書APIは文字列で渡す
        except Exception:
            pass

    resp = requests.get(
        f"https://api.freee.co.jp/iv/invoices",
        headers=_api_headers(),
        params=params,
        timeout=30,
    )
    resp.raise_for_status()
    invoices = resp.json().get("invoices", [])

    # partner_idが解決できなかった場合のフォールバック
    if partner_name and "partner_ids" not in params:
        partner_name_lower = partner_name.lower()
        invoices = [
            inv for inv in invoices
            if partner_name_lower in (inv.get("partner_name") or "").lower()
        ]

    return invoices


# ============================================================
# 請求書登録
# ============================================================
def create_invoice(entry: dict, cache: dict) -> dict:
    """
    freee請求書に請求書を登録する（freee請求書API /iv/invoices）

    entry: {
        issue_date,        # 請求日（billing_dateに対応）
        due_date,          # 入金期日（payment_dateに対応）
        partner_name,      # 取引先名
        title,             # 件名（subjectに対応）
        invoice_number,    # 請求書番号（省略可）
        details: [{
            name,          # 品目名（descriptionに対応）
            unit_price,    # 単価
            quantity,      # 数量
            type,          # "item" または "text"
            account_item_name,  # 勘定科目名（取引連携用）
            tax_code,      # 税区分コード（取引連携用）
            section_name,  # 部門名（取引連携用）
            tag_names,     # メモタグ名リスト（取引連携用）
        }]
    }
    """
    import logging
    logger = logging.getLogger(__name__)
    partners = cache["partners"]
    account_items = cache.get("account_items", [])
    tags = cache.get("tags", [])
    sections = cache.get("sections", [])

    # 取引先ID: Notionに直接入力されたIDを優先、ない場合は名前で検索
    partner_id = entry.get("partner_id")
    partner_name = entry.get("partner_name")
    if not partner_id and partner_name:
        partner_id = _find_partner_id(partner_name, partners)

    # 明細行（freee請求書APIの正しい形式）
    lines = []
    for d in entry.get("details", []):
        line_type = d.get("type", "item")
        if line_type == "text":
            # テキスト行（入社企業名など）
            line = {
                "type": "text",
                "description": d.get("description", ""),
            }
        else:
            # 品目行
            # freee請求書APIではtax_rate（0,8,10）が必須
            # tax_codeは取引連携（下書き保存）用のオプション項目
            # description: 請求書の品目名として表示され、仕訳転記時に「備考」に入る
            # d["description"]には「求職者名 + 入社企業名」が入っている
            line = {
                "type": "item",
                "description": d.get("description") or d.get("name", "人材紹介手数料"),
                "quantity": d.get("quantity", 1),
                "unit_price": str(d.get("unit_price", 0)),
                "tax_rate": d.get("tax_rate", 10),  # 税率10%（必須）
            }
            # 取引連携用の会計情報を追加（これにより請求書から取引が自動作成される）
            account_item_name = d.get("account_item_name")
            if account_item_name:
                account_item_id = _find_account_item_id(account_item_name, account_items)
                if account_item_id:
                    line["account_item_id"] = account_item_id
            # tax_code: 取引登録の下書き保存で利用される（オプション）
            if d.get("tax_code") is not None:
                line["tax_code"] = d["tax_code"]
            section_name = d.get("section_name") or entry.get("section_name")
            if section_name:
                section_id = _find_section_id(section_name, sections)
                if section_id:
                    line["section_id"] = section_id
            tag_names = d.get("tag_names", [])
            tag_ids = []
            for tag_name in tag_names:
                tid = _find_tag_id(tag_name, tags)
                if tid:
                    tag_ids.append(tid)
            if tag_ids:
                line["tag_ids"] = tag_ids

        lines.append(line)

    # freee請求書APIの必須フィールドを含むペイロード
    payload = {
        "company_id": FREEE_COMPANY_ID,
        "billing_date": entry.get("issue_date", ""),
        # ⚠️ 重要: tax_entry_method は必ず "in"（税込入力）のこと。
        # Notionの「税込売上」「税込集客手数料」はすでに消費税込みの金額。
        # "out"（税抜入力）にすると freee が自動で10%加算し二重課税になる。絶対に変更しないこと。
        # withholding_tax_entry_methodは tax_entry_methodと履歴方式を合わせる必要があるため "in" 固定。
        "tax_entry_method": "in",
        "tax_fraction": "round",
        "withholding_tax_entry_method": "in",
        "partner_title": "御中",
        "lines": lines,
    }
    if entry.get("due_date"):
        payload["payment_date"] = entry["due_date"]
    if entry.get("title"):
        payload["subject"] = entry["title"]
    if entry.get("invoice_number"):
        payload["invoice_number"] = entry["invoice_number"]
    if partner_id:
        payload["partner_id"] = int(partner_id)
    elif partner_name:
        logger.warning(f"請求書登録: 取引先「{partner_name}」が見つかりません。freeeに取引先を登録してください。")
        raise ValueError(f"取引先「{partner_name}」が見つかりません。freeeに取引先を登録してください。")

    logger.info(
        f"請求書登録リクエスト: partner={partner_name}(id={partner_id}), "
        f"billing_date={entry.get('issue_date')}, lines={len(lines)}件"
    )

    resp = requests.post(
        f"{FREEE_IV_BASE}/invoices",
        headers=_api_headers(),
        json=payload,
        timeout=30,
    )

    if resp.status_code not in (200, 201):
        logger.error(f"請求書登録失敗: {resp.status_code} {resp.text[:1000]}")
        raise ValueError(f"freee請求書登録失敗: {resp.status_code} {resp.text[:500]}")

    resp_data = resp.json()
    invoice_data = resp_data.get("invoice", resp_data)
    logger.info(f"請求書登録成功: ID={invoice_data.get('id')}")
    return invoice_data


def register_invoice_and_deal(invoice_entry: dict, sales_entry: dict) -> dict:
    """
    請求書を登録する（請求書登録タイプの場合に使用）
    請求書の明細行に会計情報を含めることで、freeeが自動的に取引（仕訳）を連携する。
    """
    cache = get_master_cache()
    result = {"invoice_id": None, "sales_id": None, "errors": []}
    try:
        invoice = create_invoice(invoice_entry, cache)
        result["invoice_id"] = invoice.get("id")
        deal_id = invoice.get("deal_id")
        if deal_id:
            result["sales_id"] = deal_id
    except Exception as e:
        result["errors"].append(f"請求書登録エラー: {str(e)}")
    return result


# ============================================================
# 証憷アップロード
# ============================================================
def upload_receipt(
    file_path: str,
    deal_id: Optional[int] = None,
    description: str = "",
) -> dict:
    """
    freeeファイルボックスに証憷をアップロードする
    deal_id が指定された場合は取引に紐付ける
    """
    import logging
    logger = logging.getLogger(__name__)

    token = get_valid_token()
    headers = {"Authorization": f"Bearer {token}"}

    with open(file_path, "rb") as f:
        files = {"receipt": (Path(file_path).name, f)}
        data = {"company_id": str(FREEE_COMPANY_ID)}
        if description:
            data["description"] = description
        resp = requests.post(
            f"{FREEE_API_BASE}/receipts",
            headers=headers,
            files=files,
            data=data,
            timeout=60,
        )

    if resp.status_code not in (200, 201):
        error_msg = f"証憷アップロード失敗: {resp.status_code} {resp.text[:500]}"
        logger.error(error_msg)
        raise ValueError(error_msg)

    receipt_data = resp.json().get("receipt", {})
    receipt_id = receipt_data.get("id")
    logger.info(f"証憷アップロード成功: ID={receipt_id}")

    # 取引に紐付ける
    if deal_id and receipt_id:
        try:
            link_resp = requests.post(
                f"{FREEE_API_BASE}/deals/{deal_id}/receipts",
                headers={**headers, "Content-Type": "application/json"},
                json={"company_id": FREEE_COMPANY_ID, "receipt_id": receipt_id},
                timeout=30,
            )
            if link_resp.status_code not in (200, 201):
                logger.warning(
                    f"証憷と取引の紐付け失敗: {link_resp.status_code} {link_resp.text[:300]}"
                )
        except Exception as e:
            logger.warning(f"証憷と取引の紐付けエラー: {e}")

    return receipt_data


def send_invoice(invoice_id: int, contact_email: Optional[str] = None) -> dict:
    """
    freee請求書を送付する（PUT /iv/invoices/{id}）
    sending_status を "sent" に変更することでメール送付が実行される
    """
    import logging
    logger = logging.getLogger(__name__)
    payload = {
        "company_id": FREEE_COMPANY_ID,
        "sending_status": "sent",
    }
    if contact_email:
        payload["partner_contact_email_to"] = contact_email
    logger.info(f"請求書送付リクエスト: invoice_id={invoice_id}, payload={payload}")
    resp = requests.put(
        f"{FREEE_IV_BASE}/invoices/{invoice_id}",
        headers=_api_headers(),
        json=payload,
        timeout=30,
    )
    if resp.status_code not in (200, 201):
        error_msg = f"freee請求書送付失敗: {resp.status_code} {resp.text[:500]}"
        logger.error(error_msg)
        return {"error": error_msg}
    logger.info(f"請求書送付成功: ID={invoice_id}")
    return {"invoice_id": invoice_id, "status": "sent"}


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


# ============================================================
# エージェント拡張ツール（自然言語AIエージェント用）
# ============================================================

def get_deal(deal_id: int) -> dict:
    """
    freeeの仕訳（取引）を1件取得する
    """
    import logging
    logger = logging.getLogger(__name__)
    resp = requests.get(
        f"{FREEE_API_BASE}/deals/{deal_id}",
        headers=_api_headers(),
        params={"company_id": FREEE_COMPANY_ID},
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json().get("deal", {})


def update_deal(deal_id: int, update_fields: dict) -> dict:
    """
    freeeの仕訳（取引）を更新する
    update_fields: issue_date, due_date, partner_name, details 等
    """
    import logging
    logger = logging.getLogger(__name__)
    payload = {"company_id": FREEE_COMPANY_ID}
    payload.update(update_fields)
    resp = requests.put(
        f"{FREEE_API_BASE}/deals/{deal_id}",
        headers=_api_headers(),
        json=payload,
        timeout=30,
    )
    if resp.status_code not in (200, 201):
        logger.error(f"仕訳更新失敗: {resp.status_code} {resp.text[:500]}")
        raise ValueError(f"freee仕訳更新失敗: {resp.status_code} {resp.text[:300]}")
    logger.info(f"仕訳更新成功: ID={deal_id}")
    return resp.json().get("deal", {})


def list_deals(
    partner_name: Optional[str] = None,
    start_issue_date: Optional[str] = None,
    end_issue_date: Optional[str] = None,
    deal_type: Optional[str] = None,
    account_item_id: Optional[int] = None,
    limit: int = 100,
    offset: int = 0,
) -> dict:
    """
    freeeの仕訳一覧を取得する（ページネーション対応・集計情報付き）
    戻り値: {"deals": [...], "total_count": N, "summary": {...}}
    """
    params = {
        "company_id": FREEE_COMPANY_ID,
        "limit": min(limit, 100),
        "offset": offset,
    }
    if start_issue_date:
        params["start_issue_date"] = start_issue_date
    if end_issue_date:
        params["end_issue_date"] = end_issue_date
    if deal_type:
        params["type"] = deal_type
    if account_item_id:
        params["account_item_id"] = account_item_id

    if partner_name:
        try:
            partners = get_partners()
            partner_id = resolve_partner_id(partner_name, partners)
            if partner_id:
                params["partner_id"] = partner_id
        except Exception:
            pass

    resp = requests.get(
        f"{FREEE_API_BASE}/deals",
        headers=_api_headers(),
        params=params,
        timeout=30,
    )
    resp.raise_for_status()
    data = resp.json()
    deals = data.get("deals", [])

    # partner_idが解決できなかった場合のフォールバック
    if partner_name and "partner_id" not in params:
        partner_name_lower = partner_name.lower()
        deals = [d for d in deals if partner_name_lower in (d.get("partner_name") or "").lower()]

    # 集計
    total_income = sum(d.get("amount", 0) for d in deals if d.get("type") == "income")
    total_expense = sum(d.get("amount", 0) for d in deals if d.get("type") == "expense")

    return {
        "deals": deals,
        "total_count": len(deals),
        "summary": {
            "total_income": total_income,
            "total_expense": total_expense,
            "net": total_income - total_expense,
        }
    }


def get_account_item_balance(
    account_item_id: Optional[int] = None,
    account_item_name: Optional[str] = None,
    fiscal_year: Optional[int] = None,
) -> dict:
    """
    freeeの勘定科目残高・推移を取得する（試算表API）
    account_item_id または account_item_name を指定する
    """
    import logging
    from datetime import date as _date
    logger = logging.getLogger(__name__)

    if not fiscal_year:
        today = _date.today()
        fiscal_year = today.year if today.month >= 4 else today.year - 1

    # 勘定科目IDを名前から解決
    if not account_item_id and account_item_name:
        try:
            items = get_account_items()
            for item in items:
                if account_item_name in item.get("name", ""):
                    account_item_id = item.get("id")
                    break
        except Exception:
            pass

    params = {
        "company_id": FREEE_COMPANY_ID,
        "fiscal_year": fiscal_year,
    }
    if account_item_id:
        params["account_item_id"] = account_item_id

    resp = requests.get(
        f"{FREEE_API_BASE}/reports/trial_bs",
        headers=_api_headers(),
        params=params,
        timeout=30,
    )
    if resp.status_code != 200:
        # 損益計算書も試みる
        resp2 = requests.get(
            f"{FREEE_API_BASE}/reports/trial_pl",
            headers=_api_headers(),
            params=params,
            timeout=30,
        )
        if resp2.status_code == 200:
            return resp2.json()
        logger.warning(f"試算表取得失敗: {resp.status_code}")
        return {"error": f"試算表取得失敗: {resp.status_code}"}
    return resp.json()


def get_invoice(invoice_id: int) -> dict:
    """
    freeeの請求書を1件取得する
    """
    resp = requests.get(
        f"{FREEE_IV_BASE}/invoices/{invoice_id}",
        headers=_api_headers(),
        params={"company_id": FREEE_COMPANY_ID},
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json().get("invoice", {})


def list_invoices(
    partner_name: Optional[str] = None,
    start_billing_date: Optional[str] = None,
    end_billing_date: Optional[str] = None,
    invoice_status: Optional[str] = None,
    limit: int = 100,
) -> dict:
    """
    freeeの請求書一覧を取得する
    invoice_status: draft / issue / sent / unsubmitted / submitted
    戻り値: {"invoices": [...], "total_count": N}
    """
    params = {
        "company_id": FREEE_COMPANY_ID,
        "limit": min(limit, 100),
    }
    if start_billing_date:
        params["start_billing_date"] = start_billing_date
    if end_billing_date:
        params["end_billing_date"] = end_billing_date
    if invoice_status:
        params["invoice_status"] = invoice_status

    if partner_name:
        try:
            partners = get_partners()
            partner_id = resolve_partner_id(partner_name, partners)
            if partner_id:
                params["partner_ids"] = str(partner_id)
        except Exception:
            pass

    resp = requests.get(
        f"https://api.freee.co.jp/iv/invoices",
        headers=_api_headers(),
        params=params,
        timeout=30,
    )
    resp.raise_for_status()
    invoices = resp.json().get("invoices", [])

    if partner_name and "partner_ids" not in params:
        partner_name_lower = partner_name.lower()
        invoices = [inv for inv in invoices if partner_name_lower in (inv.get("partner_name") or "").lower()]

    return {"invoices": invoices, "total_count": len(invoices)}


def register_invoice_agent(
    issue_date: str,
    due_date: str,
    partner_name: str,
    items: list,
    title: str = "請求書",
    memo: str = "",
) -> dict:
    """
    freeeに請求書を登録する（エージェント用シンプルインターフェース）
    items: [{"name": str, "quantity": int, "unit_price": int, "tax_code": int, "description": str}]
    """
    cache = get_master_cache()
    entry = {
        "issue_date": issue_date,
        "due_date": due_date,
        "partner_name": partner_name,
        "title": title,
        "memo": memo,
        "details": [
            {
                "name": item.get("name", ""),
                "quantity": item.get("quantity", 1),
                "unit_price": item.get("unit_price", 0),
                "tax_code": item.get("tax_code", 1),
                "description": item.get("description", ""),
            }
            for item in items
        ],
    }
    return create_invoice(entry, cache)


def execute_delete_deal(deal_id: int) -> dict:
    """
    仕訳を1件削除する（エージェントが確認後に呼び出す）
    """
    delete_deal(deal_id)
    return {"status": "deleted", "deal_id": deal_id}


def execute_delete_invoice(invoice_id: int) -> dict:
    """
    請求書を1件取消する（エージェントが確認後に呼び出す）
    """
    delete_invoice(invoice_id)
    return {"status": "cancelled", "invoice_id": invoice_id}


# ============================================================
# 支払管理・振込データ生成用関数
# ============================================================

# 本店部門IDセット
HONTEN_SECTION_IDS = {2925134, 3423934, 3423935, 3423936, 3428069}

# 部門ID → 表示名マッピング
SECTION_ID_TO_NAME = {
    2925134: "本店：その他",
    3423934: "本店：CA",
    3423935: "本店：PCA",
    3423936: "本店：PRA",
    3428069: "本店：AIスカウト",
}

# 「振込依頼済」メモタグID
FURIKOMI_TAG_ID = 35285961

# CSS部門IDセット（1つでも含まれていたら除外）
CSS_SECTION_IDS = {3423929, 3423932, 3425277, 3423930, 3403442, 3428068}

# 振込除外対象の取引先名（部分一致）
EXCLUDE_PARTNER_KEYWORDS = [
    "給与", "シンクバンク", "日本政策金融公庫", "横浜銀行", "さわやか信用金庫", "西武信用金庫",
    "WANTEDLY", "wantedly", "ウォンテッドリー",
    "L MESSAGE", "Lメッセージ",
    "オリコフォレント",
    "フォンデスク",
    "MJE",
    "年金事務所", "労働局", "税務署", "都税", "市税", "区役所",
]

# 振込除外対象の勘定科目名（部分一致）
EXCLUDE_ACCOUNT_KEYWORDS = [
    "給与", "賞与", "役員報酬", "法定福利費", "長期借入金", "短期借入金",
    "所得税", "住民税", "消費税", "法人税", "印紙税",
    "社宅",
]


def get_payment_deals(
    months_back: int = 3,
    alert_days: int = 10,
) -> dict:
    """
    本店部門・未決済の支出仕訳を取得し、振込対象とアラート対象に分類する。

    Returns:
        {
            "transfer_targets": [...],   # 添付ファイルあり・除外対象外 → 振込FBファイル生成対象
            "alert_targets": [...],      # 添付ファイルなし・支払期日まで10日以内 → アラート対象
            "excluded": [...],           # 除外対象（参考）
        }
    """
    from datetime import timedelta
    today = datetime.now()
    start = today.strftime("%Y-%m-%d")  # 今日以降の支払期日のみ対象
    end = (today + timedelta(days=60)).strftime("%Y-%m-%d")

    # 未決済の支出仕訳を全件取得（ページング対応）
    # ※「振込依頼済」タグ(ID=35285961)が付いている仕訳はスキップする
    all_deals = []
    offset = 0
    while True:
        resp = requests.get(f"{FREEE_API_BASE}/deals", headers=_api_headers(), params={
            "company_id": FREEE_COMPANY_ID,
            "type": "expense",
            "payment_status": "unsettled",
            "start_due_date": start,
            "end_due_date": end,
            "limit": 100,
            "offset": offset,
        }, timeout=30)
        if resp.status_code != 200:
            raise ValueError(f"deals取得失敗: {resp.status_code} {resp.text[:200]}")
        deals = resp.json().get("deals", [])
        all_deals.extend(deals)
        if len(deals) < 100:
            break
        offset += 100

    # 本店部門に属する仕訳のみ絞り込み（CSS部門が1つでも含まれていたら除外）
    honten_deals = []
    for d in all_deals:
        section_ids = {det.get("section_id") for det in d.get("details", [])}
        if not (section_ids & HONTEN_SECTION_IDS):
            continue  # 本店部門が含まれていない
        if section_ids & CSS_SECTION_IDS:
            continue  # CSS部門が1つでも含まれていたら除外
        honten_deals.append(d)

    transfer_targets = []
    alert_targets = []
    excluded = []

    for d in honten_deals:
        # freee APIの payment_status フィルターが機能しない場合に備え、アプリ側でも決済済みを除外
        # payment_status: "settled" または payments 配列に決済済みのレコードがある場合はスキップ
        if d.get("payment_status") == "settled":
            continue
        # payments配列に決済済みレコードがある場合も除外
        payments = d.get("payments", [])
        if payments and all(p.get("amount", 0) > 0 for p in payments):
            # 合計決済額と仕訳金額を比較して完全決済済みを除外
            total_paid = sum(p.get("amount", 0) for p in payments)
            if total_paid >= abs(d.get("amount", 0)):
                continue

        partner_id = d.get("partner_id")
        partner_name = _get_partner_name_cached(partner_id) if partner_id else ""
        due_date_str = d.get("due_date") or d.get("issue_date", "")
        has_receipt = len(d.get("receipts", [])) > 0
        amount = d.get("amount", 0)

        # 除外判定
        if _is_excluded(d, partner_name):
            excluded.append({**d, "_partner_name": partner_name})
            continue

        # 支払期日までの日数
        days_until_due = None
        if due_date_str:
            try:
                due_dt = datetime.strptime(due_date_str, "%Y-%m-%d")
                days_until_due = (due_dt - today).days
            except ValueError:
                pass

        # 「振込依頼済」タグが付いている仕訳はスキップ
        tag_ids = {t.get("id") for t in d.get("tags", [])}
        if FURIKOMI_TAG_ID in tag_ids:
            continue

        if has_receipt:
            # 添付あり → 振込対象。口座情報も事前に確認してアラートに振り分ける
            bank = get_partner_bank(partner_id) if partner_id else {}
            has_bank = bool(bank.get("bank_code") and bank.get("account_number"))
            # 部門名リストを取得
            section_ids_in_deal = {det.get("section_id") for det in d.get("details", []) if det.get("section_id")}
            section_names = [SECTION_ID_TO_NAME.get(sid, f"部門{sid}") for sid in section_ids_in_deal if sid in HONTEN_SECTION_IDS]
            entry = {
                "deal_id": d.get("id"),
                "partner_id": partner_id,
                "partner_name": partner_name,
                "issue_date": d.get("issue_date"),
                "due_date": due_date_str,
                "amount": amount,
                "days_until_due": days_until_due,
                "receipts": d.get("receipts", []),
                "bank_registered": has_bank,
                "section_names": section_names,
                "group_key": f"{due_date_str}|{partner_name}",  # グループ化用キー
            }
            if has_bank:
                transfer_targets.append(entry)
            else:
                # 口座未登録 → アラート対象（振込できない）
                entry["alert_reason"] = "bank_missing"
                alert_targets.append(entry)
        else:
            # 添付なし・期日10日以内 → アラート対象
            if days_until_due is not None and days_until_due <= alert_days:
                section_ids_in_deal = {det.get("section_id") for det in d.get("details", []) if det.get("section_id")}
                section_names = [SECTION_ID_TO_NAME.get(sid, f"部門{sid}") for sid in section_ids_in_deal if sid in HONTEN_SECTION_IDS]
                alert_targets.append({
                    "deal_id": d.get("id"),
                    "partner_id": partner_id,
                    "partner_name": partner_name,
                    "issue_date": d.get("issue_date"),
                    "due_date": due_date_str,
                    "amount": amount,
                    "days_until_due": days_until_due,
                    "alert_reason": "no_receipt",
                    "bank_registered": False,
                    "section_names": section_names,
                    "group_key": f"{due_date_str}|{partner_name}",
                })

    return {
        "transfer_targets": transfer_targets,
        "alert_targets": alert_targets,
        "excluded": excluded,
        "total_honten": len(honten_deals),
    }


# 取引先名キャッシュ（APIコール削減）
_partner_name_cache: dict = {}


def _get_partner_name_cached(partner_id: int) -> str:
    if partner_id in _partner_name_cache:
        return _partner_name_cache[partner_id]
    try:
        r = requests.get(f"{FREEE_API_BASE}/partners/{partner_id}", headers=_api_headers(),
                         params={"company_id": FREEE_COMPANY_ID}, timeout=15)
        if r.status_code == 200:
            name = r.json().get("partner", {}).get("name", "")
            _partner_name_cache[partner_id] = name
            return name
    except Exception:
        pass
    return ""


def get_partner_bank(partner_id: int) -> dict:
    """取引先の銀行口座情報を取得する"""
    r = requests.get(f"{FREEE_API_BASE}/partners/{partner_id}", headers=_api_headers(),
                     params={"company_id": FREEE_COMPANY_ID}, timeout=15)
    if r.status_code != 200:
        return {}
    partner = r.json().get("partner", {})
    bank = partner.get("partner_bank_account_attributes", {})
    return {
        "partner_name": partner.get("name", ""),
        "bank_code": bank.get("bank_code", ""),
        "bank_name": bank.get("bank_name", ""),
        "branch_code": bank.get("branch_code", ""),
        "branch_name": bank.get("branch_name", ""),
        "account_type": bank.get("account_type", "ordinary"),
        "account_number": bank.get("account_number", ""),
        "account_name": bank.get("account_name", ""),
    }


# 勘定科目IDから名称へのキャッシュ（起動時に1回ロード）
_account_id_to_name: dict = {}


def _get_account_id_to_name() -> dict:
    """勘定科目ID→名称のマップを返す（キャッシュ付き）"""
    global _account_id_to_name
    if not _account_id_to_name:
        items = get_account_items()
        _account_id_to_name = {item["id"]: item["name"] for item in items if "id" in item and "name" in item}
    return _account_id_to_name


def _is_excluded(deal: dict, partner_name: str) -> bool:
    """振込除外対象かどうか判定する（取引先名 + 勘定科目名の両方で判定）"""
    # 取引先名で除外
    for kw in EXCLUDE_PARTNER_KEYWORDS:
        if kw in partner_name:
            return True

    # 勘定科目名で除外（明細の全科目を確認）
    account_map = _get_account_id_to_name()
    for det in deal.get("details", []):
        acct_id = det.get("account_item_id")
        if not acct_id:
            continue
        acct_name = account_map.get(acct_id, "")
        for kw in EXCLUDE_ACCOUNT_KEYWORDS:
            if kw in acct_name:
                return True

    return False


def add_furikomi_tag(deal_id: int) -> bool:
    """
    仕訳に「振込依頼済」メモタグ(ID=35285961)を付与する。
    既存のタグを保持したまま追加する。
    Returns: True=成功, False=失敗
    """
    # 現在の仕訳詳細を取得してタグ一覧を確認
    r = requests.get(f"{FREEE_API_BASE}/deals/{deal_id}", headers=_api_headers(),
                     params={"company_id": FREEE_COMPANY_ID}, timeout=15)
    if r.status_code != 200:
        return False
    deal = r.json().get("deal", {})
    existing_tag_ids = [t.get("id") for t in deal.get("tags", [])]

    # 既にタグが付いている場合はスキップ
    if FURIKOMI_TAG_ID in existing_tag_ids:
        return True

    # タグを追加してPUT
    new_tag_ids = existing_tag_ids + [FURIKOMI_TAG_ID]
    details = deal.get("details", [])
    # PUT用にdetailsを整形
    put_details = []
    for det in details:
        put_details.append({
            "id": det.get("id"),
            "account_item_id": det.get("account_item_id"),
            "tax_code": det.get("tax_code"),
            "amount": det.get("amount"),
            "description": det.get("description", ""),
            "section_id": det.get("section_id"),
            "tag_ids": [t.get("id") for t in det.get("tags", [])],
        })

    payload = {
        "company_id": FREEE_COMPANY_ID,
        "issue_date": deal.get("issue_date"),
        "due_date": deal.get("due_date"),
        "type": deal.get("type"),
        "partner_id": deal.get("partner_id"),
        "ref_number": deal.get("ref_number"),
        "details": put_details,
        "tag_ids": new_tag_ids,
    }
    put_r = requests.put(f"{FREEE_API_BASE}/deals/{deal_id}", headers=_api_headers(),
                         json={"deal": payload}, timeout=15)
    return put_r.status_code == 200
