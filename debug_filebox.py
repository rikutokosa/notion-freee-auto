"""
freeeファイルボックスの未紐づけ書類を直接確認するデバッグスクリプト
"""
import sys
sys.path.insert(0, '/home/ubuntu/notion-freee-auto')

import requests
from freee_client import FREEE_API_BASE, FREEE_COMPANY_ID, _api_headers

def check_filebox():
    # category=without_deal で未紐づけ書類を取得
    params = {
        "company_id": FREEE_COMPANY_ID,
        "category": "without_deal",
        "limit": 100,
        "offset": 0,
    }
    resp = requests.get(
        f"{FREEE_API_BASE}/receipts",
        headers=_api_headers(),
        params=params,
        timeout=30,
    )
    print(f"ステータス: {resp.status_code}")
    data = resp.json()
    receipts = data.get("receipts", [])
    print(f"未紐づけ書類数 (category=without_deal): {len(receipts)}件")
    for r in receipts[:5]:
        meta = r.get("receipt_metadatum") or {}
        print(f"  ID={r.get('id')}, 金額={meta.get('amount')}, 日付={meta.get('issue_date')}, "
              f"取引先={meta.get('partner_name')}, mime={r.get('mime_type')}, desc={r.get('description','')[:30]}")

    # categoryなしで全件も確認
    print()
    params2 = {
        "company_id": FREEE_COMPANY_ID,
        "limit": 5,
        "offset": 0,
    }
    resp2 = requests.get(
        f"{FREEE_API_BASE}/receipts",
        headers=_api_headers(),
        params=params2,
        timeout=30,
    )
    data2 = resp2.json()
    receipts2 = data2.get("receipts", [])
    print(f"全書類数 (categoryなし, 最大5件): {len(receipts2)}件")
    for r in receipts2[:5]:
        meta = r.get("receipt_metadatum") or {}
        deal_ids = [d.get("id") for d in r.get("deals", [])] if r.get("deals") else []
        print(f"  ID={r.get('id')}, 金額={meta.get('amount')}, 日付={meta.get('issue_date')}, "
              f"deals={deal_ids}, desc={r.get('description','')[:30]}")

    # レスポンスのキー確認
    if receipts:
        print(f"\n書類のキー一覧: {list(receipts[0].keys())}")
        if receipts[0].get("receipt_metadatum"):
            print(f"receipt_metadatumのキー: {list(receipts[0]['receipt_metadatum'].keys())}")

if __name__ == "__main__":
    check_filebox()
