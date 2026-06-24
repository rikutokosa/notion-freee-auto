"""
Notion APIクライアント
本店CA成約管理DB・PCA成約管理DBを監視し、
「②経理対応待ち」フィルターのレコードを取得する
"""
import os
import requests
from datetime import datetime, timezone
from typing import Optional


NOTION_TOKEN = os.environ.get("NOTION_TOKEN", "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")

# 本店CA成約管理DB
NOTION_DB_ID_HONTEN = os.environ.get("NOTION_DB_ID_HONTEN", "320a7a34-dbe2-8082-8055-c57f9b8a04bb")
# PCA成約管理DB
NOTION_DB_ID_PCA = os.environ.get("NOTION_DB_ID_PCA", "32fa7a34-dbe2-8005-ab91-ff33d64506e0")

NOTION_VERSION = "2022-06-28"
NOTION_BASE = "https://api.notion.com/v1"

# ②経理対応待ちフィルターに該当するステータス（本店CA）
# 「本部確認済」→ 請求書登録または仕訳登録
# 「●入社済」→ 請求が必要な場合のみ手動で②経理対応待ちビューに移動してくる（監視対象に含める）
# 「●入社前辞退」→ 取引削除
# 「●返金（短期離職）」→ マイナス仕訳登録
# ※「●入社済」は②経理対応待ちビューには自動的に入らない。
#   請求が必要なレコードのみ手動で対応待ちに移動されるため、ここでは監視しない。
PENDING_STATUSES_HONTEN = [
    "本部確認済",
    "●入社前辞退",
    "●返金（短期離職）",
]

# ②経理対応待ちフィルターに該当するステータス（PCA）
PENDING_STATUSES_PCA = [
    "本部確認済",
    "入社前辞退",
    "●返金（短期離職）",
]

# 処理完了後のステータスマッピング（本店CA）
STATUS_DONE_MAP_HONTEN = {
    "本部確認済": "□承諾→freee登録済",
    "●入社済": "入社→請求済",
    "●入社前辞退": "□入社前辞退→freee更新済",
    "●返金（短期離職）": "□返金→freee・請求書更新済",
}

# 処理完了後のステータスマッピング（PCA）
STATUS_DONE_MAP_PCA = {
    "本部確認済": "承諾→freee登録済",
    "入社前辞退": "入社前辞退→freee更新済",
    "●返金（短期離職）": "返金→freee・請求書更新済",
}

# freee処理状態の値（Notionのselectオプション名と一致させること）
FREEE_STATUS_SHIWAKE_SUCCESS = "仕訳成功"
FREEE_STATUS_SHIWAKE_FAILURE = "仕訳失敗"
FREEE_STATUS_ERROR = "エラー"
FREEE_STATUS_PROCESSING = "処理中"
FREEE_STATUS_INVOICE_SUCCESS = "請求成功"
FREEE_STATUS_INVOICE_REGISTERED = "請求登録成功（要仕訳転記）"


def _headers():
    return {
        "Authorization": f"Bearer {NOTION_TOKEN}",
        "Notion-Version": NOTION_VERSION,
        "Content-Type": "application/json",
    }


def _query_db(db_id: str, pending_statuses: list) -> list[dict]:
    """
    DBをクエリして全件取得する（ページネーション対応）
    """
    url = f"{NOTION_BASE}/databases/{db_id}/query"

    # 経理対応待ちフィルター（複数ステータスのOR条件）
    status_filters = [
        {"property": "請求ステータス", "select": {"equals": s}}
        for s in pending_statuses
    ]
    filter_payload = {"or": status_filters}

    payload = {
        "filter": filter_payload,
        "sorts": [{"property": "入社日", "direction": "ascending"}],
    }

    results = []
    has_more = True
    start_cursor = None

    while has_more:
        if start_cursor:
            payload["start_cursor"] = start_cursor

        resp = requests.post(url, headers=_headers(), json=payload, timeout=30)
        resp.raise_for_status()
        data = resp.json()

        results.extend(data.get("results", []))
        has_more = data.get("has_more", False)
        start_cursor = data.get("next_cursor")

    return results


def _query_db_by_freee_status(db_id: str, freee_status: str) -> list[dict]:
    """
    freee処理状態でDBをクエリして全件取得する（ページネーション対応）
    """
    url = f"{NOTION_BASE}/databases/{db_id}/query"

    payload = {
        "filter": {
            "property": "freee処理状態",
            "select": {"equals": freee_status}
        },
        "sorts": [{"property": "入社日", "direction": "ascending"}],
    }

    results = []
    has_more = True
    start_cursor = None

    while has_more:
        if start_cursor:
            payload["start_cursor"] = start_cursor

        resp = requests.post(url, headers=_headers(), json=payload, timeout=30)
        resp.raise_for_status()
        data = resp.json()

        results.extend(data.get("results", []))
        has_more = data.get("has_more", False)
        start_cursor = data.get("next_cursor")

    return results


def fetch_pending_records(db_type: str = "honten") -> list[dict]:
    """
    経理対応待ちレコードを取得する

    db_type: "honten" | "pca" | "all"
    """
    import logging
    logger = logging.getLogger(__name__)
    records = []

    if db_type in ("honten", "all"):
        try:
            honten = _query_db(NOTION_DB_ID_HONTEN, PENDING_STATUSES_HONTEN)
            for r in honten:
                r["_db_type"] = "honten"
            records.extend(honten)
        except Exception as e:
            logger.error(f"本店CA DBクエリ失敗: {e}")

    if db_type in ("pca", "all"):
        try:
            pca = _query_db(NOTION_DB_ID_PCA, PENDING_STATUSES_PCA)
            for r in pca:
                r["_db_type"] = "pca"
            records.extend(pca)
        except Exception as e:
            logger.error(f"PCA DBクエリ失敗: {e}")

    return records


def fetch_pending_manual_journal_records(db_type: str = "all") -> list[dict]:
    """
    freee処理状態が「請求登録成功（要仕訳転記）」のレコードを取得する
    （freee管理画面で手動「取引登録」ボタンを押す必要があるレコード）
    """
    import logging
    logger = logging.getLogger(__name__)
    records = []

    if db_type in ("honten", "all"):
        try:
            honten = _query_db_by_freee_status(
                NOTION_DB_ID_HONTEN, FREEE_STATUS_INVOICE_REGISTERED
            )
            for r in honten:
                r["_db_type"] = "honten"
            records.extend(honten)
        except Exception as e:
            logger.error(f"本店CA DB(要仕訳転記)クエリ失敗: {e}")

    if db_type in ("pca", "all"):
        try:
            pca = _query_db_by_freee_status(
                NOTION_DB_ID_PCA, FREEE_STATUS_INVOICE_REGISTERED
            )
            for r in pca:
                r["_db_type"] = "pca"
            records.extend(pca)
        except Exception as e:
            logger.error(f"PCA DB(要仕訳転記)クエリ失敗: {e}")

    return records


def get_record(page_id: str) -> dict:
    """
    特定のページを取得する
    """
    url = f"{NOTION_BASE}/pages/{page_id}"
    resp = requests.get(url, headers=_headers(), timeout=30)
    resp.raise_for_status()
    return resp.json()


def get_jobseeker_name(record: dict) -> str:
    """
    レコードの求職者relationから求職者名を取得する
    取得できない場合は空文字を返す
    """
    try:
        props = record.get("properties", {})
        jobseeker_rel = props.get("求職者", {}).get("relation", [])
        if not jobseeker_rel:
            return ""
        page_id = jobseeker_rel[0]["id"]
        url = f"{NOTION_BASE}/pages/{page_id}"
        resp = requests.get(url, headers=_headers(), timeout=15)
        if resp.status_code != 200:
            return ""
        page_data = resp.json()
        page_props = page_data.get("properties", {})
        for key, val in page_props.items():
            if val.get("type") == "title":
                texts = val.get("title", [])
                return texts[0].get("plain_text", "") if texts else ""
    except Exception:
        pass
    return ""


def get_company_name(record: dict) -> str:
    """
    レコードの「決定企業（DB）」relationから入社企業名を取得する
    取得できない場合は空文字を返す
    """
    try:
        props = record.get("properties", {})
        company_rel = props.get("決定企業", {}).get("relation", [])
        if not company_rel:
            return ""
        page_id = company_rel[0]["id"]
        url = f"{NOTION_BASE}/pages/{page_id}"
        resp = requests.get(url, headers=_headers(), timeout=15)
        if resp.status_code != 200:
            return ""
        page_data = resp.json()
        page_props = page_data.get("properties", {})
        for key, val in page_props.items():
            if val.get("type") == "title":
                texts = val.get("title", [])
                return texts[0].get("plain_text", "") if texts else ""
    except Exception:
        pass
    return ""


def get_invoice_required(record: dict) -> bool:
    """
    レコードの「請求有無」フォーミュラプロパティを取得する
    「要請求」の場合はTrue、「請求不要」またはその他の場合はFalseを返す
    """
    try:
        props = record.get("properties", {})
        field = props.get("請求有無", {})
        field_type = field.get("type", "")

        if field_type == "formula":
            formula = field.get("formula", {})
            ft = formula.get("type", "")
            if ft == "string":
                val = formula.get("string", "")
                return val == "要請求"
        elif field_type == "select":
            sel = field.get("select")
            if sel:
                return sel.get("name", "") == "要請求"
        elif field_type == "rich_text":
            texts = field.get("rich_text", [])
            val = "".join([x.get("plain_text", "") for x in texts])
            return val == "要請求"
    except Exception:
        pass
    return False


def get_invoice_id_from_record(record: dict) -> Optional[int]:
    """
    レコードの「freee請求書ID」プロパティを取得する
    取得できない場合はNoneを返す
    """
    try:
        props = record.get("properties", {})
        field = props.get("freee請求書ID", {})
        field_type = field.get("type", "")
        if field_type == "number":
            val = field.get("number")
            return int(val) if val is not None else None
    except Exception:
        pass
    return None


def get_current_status(record: dict) -> str:
    """
    レコードの現在の請求ステータスを取得する
    """
    props = record.get("properties", {})
    status_info = props.get("請求ステータス", {})
    sel = status_info.get("select")
    return sel.get("name", "") if sel else ""


def get_done_status(original_status: str, db_type: str) -> str:
    """
    処理完了後のステータスを取得する
    """
    if db_type == "pca":
        return STATUS_DONE_MAP_PCA.get(original_status, "承諾→freee登録済")
    else:
        return STATUS_DONE_MAP_HONTEN.get(original_status, "□承諾→freee登録済")


def mark_as_done(page_id: str, original_status: str,
                 db_type: str = "honten",
                 freee_status: str = FREEE_STATUS_SHIWAKE_SUCCESS,
                 sales_id: Optional[int] = None,
                 purchase_id: Optional[int] = None,
                 pca_id: Optional[int] = None,
                 invoice_id: Optional[int] = None) -> bool:
    """
    freee登録完了後にNotionのステータスを更新する
    original_status に応じて遷移先ステータスを決定する
    freee_status で「freee処理状態」の値を指定する（デフォルト: 仕訳成功）
    sales_id: freeeの売上仕訳ID（deal_id）→ Notionの「freee売上取引ID」に保存
    purchase_id: freeeの仕入仕訳ID（deal_id）→ Notionの「freee仕入取引ID」に保存
    pca_id: freeeのPCA仕入仕訳ID（deal_id）→ Notionの「freee仕入取引ID（PCA）」に保存（PCA DBのみ）
    """
    done_status = get_done_status(original_status, db_type)

    url = f"{NOTION_BASE}/pages/{page_id}"
    props = {
        "請求ステータス": {
            "select": {"name": done_status}
        },
        "freee処理状態": {
            "select": {"name": freee_status}
        },
        "処理日時": {
            "date": {"start": datetime.now(timezone.utc).isoformat()}
        }
    }
    # freee仕訳IDをNotionに保存（登録時のみ。削除時はIDが消えるので書き込まない）
    if sales_id is not None:
        props["freee売上取引ID"] = {"number": sales_id}
    if purchase_id is not None:
        props["freee仕入取引ID"] = {"number": purchase_id}
    if pca_id is not None and db_type == "pca":
        props["freee仕入取引ID（PCA）"] = {"number": pca_id}

    payload = {"properties": props}
    resp = requests.patch(url, headers=_headers(), json=payload, timeout=30)
    if resp.status_code != 200:
        import logging
        logging.getLogger(__name__).error(
            f"mark_as_done失敗: page_id={page_id}, status={resp.status_code}, "
            f"freee_status={freee_status}, resp={resp.text[:500]}"
        )
    return resp.status_code == 200


def mark_as_error(page_id: str, error_msg: str) -> bool:
    """
    エラー時にNotionのステータスを更新する
    """
    url = f"{NOTION_BASE}/pages/{page_id}"
    payload = {
        "properties": {
            "freee処理状態": {
                "select": {"name": FREEE_STATUS_ERROR}
            },
            "エラー内容": {
                "rich_text": [{"text": {"content": error_msg[:2000]}}]
            },
            "処理日時": {
                "date": {"start": datetime.now(timezone.utc).isoformat()}
            }
        }
    }
    resp = requests.patch(url, headers=_headers(), json=payload, timeout=30)
    return resp.status_code == 200


def clear_error(page_id: str) -> bool:
    """
    エラー内容をクリアする（再処理前に呼ぶ）
    """
    url = f"{NOTION_BASE}/pages/{page_id}"
    payload = {
        "properties": {
            "エラー内容": {
                "rich_text": []
            },
            "freee処理状態": {
                "select": None  # クリア（空欄に戻す）
            }
        }
    }
    resp = requests.patch(url, headers=_headers(), json=payload, timeout=30)
    return resp.status_code == 200


def clear_error_set_processing(page_id: str) -> bool:
    """
    エラー内容をクリアして「処理中」にセットする（実際の処理開始前に呼ぶ）
    """
    url = f"{NOTION_BASE}/pages/{page_id}"
    payload = {
        "properties": {
            "エラー内容": {
                "rich_text": []
            },
            "freee処理状態": {
                "select": {"name": FREEE_STATUS_PROCESSING}
            }
        }
    }
    resp = requests.patch(url, headers=_headers(), json=payload, timeout=30)
    return resp.status_code == 200
