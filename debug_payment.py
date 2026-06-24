"""
freeeの未決済支出仕訳を直接確認するデバッグスクリプト
"""
import os, sys, json, requests
from datetime import datetime, timedelta

sys.path.insert(0, "/home/ubuntu/notion-freee-auto")
from freee_client import FREEE_API_BASE, FREEE_COMPANY_ID, _api_headers, get_valid_token

# トークン確認
token = get_valid_token()
if not token:
    print("ERROR: freeeトークンが取得できません")
    sys.exit(1)
print(f"トークン取得OK")

today = datetime.now()

# 1. まず絞り込みなしで最近の支出仕訳を取得
print("\n=== 直近30日の支出仕訳（payment_status=unsettled）===")
resp = requests.get(f"{FREEE_API_BASE}/deals", headers=_api_headers(), params={
    "company_id": FREEE_COMPANY_ID,
    "type": "expense",
    "payment_status": "unsettled",
    "limit": 10,
    "offset": 0,
}, timeout=30)
print(f"ステータス: {resp.status_code}")
if resp.status_code == 200:
    deals = resp.json().get("deals", [])
    print(f"件数: {len(deals)}")
    for d in deals[:5]:
        section_ids = [det.get("section_id") for det in d.get("details", [])]
        receipts = d.get("receipts", [])
        print(f"  ID={d.get('id')} issue_date={d.get('issue_date')} due_date={d.get('due_date')} "
              f"amount={d.get('amount')} payment_status={d.get('payment_status')} "
              f"receipts={len(receipts)} sections={section_ids}")
else:
    print(resp.text[:300])

# 2. start_due_date付きで取得
print(f"\n=== start_due_date={today.strftime('%Y-%m-%d')} end_due_date={(today+timedelta(days=60)).strftime('%Y-%m-%d')} ===")
resp2 = requests.get(f"{FREEE_API_BASE}/deals", headers=_api_headers(), params={
    "company_id": FREEE_COMPANY_ID,
    "type": "expense",
    "payment_status": "unsettled",
    "start_due_date": today.strftime("%Y-%m-%d"),
    "end_due_date": (today + timedelta(days=60)).strftime("%Y-%m-%d"),
    "limit": 10,
    "offset": 0,
}, timeout=30)
print(f"ステータス: {resp2.status_code}")
if resp2.status_code == 200:
    deals2 = resp2.json().get("deals", [])
    print(f"件数: {len(deals2)}")
    for d in deals2[:5]:
        section_ids = [det.get("section_id") for det in d.get("details", [])]
        receipts = d.get("receipts", [])
        print(f"  ID={d.get('id')} issue_date={d.get('issue_date')} due_date={d.get('due_date')} "
              f"amount={d.get('amount')} payment_status={d.get('payment_status')} "
              f"receipts={len(receipts)} sections={section_ids}")
else:
    print(resp2.text[:300])

# 3. HONTEN_SECTION_IDS確認
HONTEN_SECTION_IDS = {2925134, 3423934, 3423935, 3423936, 3428069}
print(f"\n=== 本店部門ID: {HONTEN_SECTION_IDS} ===")
if resp.status_code == 200:
    deals = resp.json().get("deals", [])
    honten = []
    for d in deals:
        section_ids = {det.get("section_id") for det in d.get("details", [])}
        if section_ids & HONTEN_SECTION_IDS:
            honten.append(d)
    print(f"本店部門に属する仕訳: {len(honten)}件（全{len(deals)}件中）")
    for d in honten[:5]:
        section_ids = {det.get("section_id") for det in d.get("details", [])}
        receipts = d.get("receipts", [])
        print(f"  ID={d.get('id')} due_date={d.get('due_date')} amount={d.get('amount')} "
              f"receipts={len(receipts)} sections={section_ids}")
