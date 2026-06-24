"""一時デバッグ: freee receipts APIの生レスポンスを確認する"""
import json
import requests
from datetime import datetime, timedelta
from freee_client import FREEE_API_BASE, FREEE_COMPANY_ID, _api_headers, get_valid_token

def debug_receipts():
    get_valid_token()  # トークン有効性確認
    
    end_date = datetime.now().strftime("%Y-%m-%d")
    start_date = (datetime.now() - timedelta(days=180)).strftime("%Y-%m-%d")
    
    params = {
        "company_id": FREEE_COMPANY_ID,
        "category": "without_deal",
        "start_date": start_date,
        "end_date": end_date,
        "limit": 3,
    }
    resp = requests.get(
        f"{FREEE_API_BASE}/receipts",
        headers=_api_headers(),
        params=params,
        timeout=30,
    )
    print(f"Status: {resp.status_code}")
    print(f"Response (raw):")
    print(json.dumps(resp.json(), indent=2, ensure_ascii=False))
    
    # 個別の書類も取得してみる
    receipts = resp.json().get("receipts", [])
    if receipts:
        receipt_id = receipts[0]["id"]
        print(f"\n\n--- 個別取得: /receipts/{receipt_id} ---")
        resp2 = requests.get(
            f"{FREEE_API_BASE}/receipts/{receipt_id}",
            headers=_api_headers(),
            params={"company_id": FREEE_COMPANY_ID},
            timeout=30,
        )
        print(f"Status: {resp2.status_code}")
        print(json.dumps(resp2.json(), indent=2, ensure_ascii=False))

if __name__ == "__main__":
    debug_receipts()
