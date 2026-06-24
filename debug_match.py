"""
ベネッセi-キャリア 34650円 の照合デバッグスクリプト
"""
import sys
sys.path.insert(0, '/home/ubuntu/notion-freee-auto')

from freee_client import FREEE_API_BASE, FREEE_COMPANY_ID, _api_headers
import requests
from datetime import datetime, timedelta

AMOUNT = 34650
ISSUE_DATE = "2026-06-02"
DATE_RANGE_DAYS = 90

base_date = datetime.strptime(ISSUE_DATE, "%Y-%m-%d")
start_date = (base_date - timedelta(days=DATE_RANGE_DAYS)).strftime("%Y-%m-%d")
end_date   = (base_date + timedelta(days=DATE_RANGE_DAYS)).strftime("%Y-%m-%d")

print(f"検索範囲: {start_date} 〜 {end_date}")
print(f"対象金額: {AMOUNT}円")
print()

# 発生日・支払期日の両方で検索
date_filter_sets = [
    ("発生日", {"start_issue_date": start_date, "end_issue_date": end_date}),
    ("支払期日", {"start_due_date": start_date, "end_due_date": end_date}),
]

for deal_type in ("income", "expense"):
    for label, date_filters in date_filter_sets:
        params = {
            "company_id": FREEE_COMPANY_ID,
            "type": deal_type,
            "limit": 100,
            "offset": 0,
            **date_filters,
        }
        resp = requests.get(f"{FREEE_API_BASE}/deals", headers=_api_headers(), params=params, timeout=30)
        if resp.status_code != 200:
            print(f"[{deal_type}/{label}] 取得失敗: {resp.status_code}")
            continue
        deals = resp.json().get("deals", [])
        print(f"[{deal_type}/{label}] {len(deals)}件取得")

        # ベネッセ関連 or 金額一致を表示
        for d in deals:
            partner = (d.get("partner") or {}).get("name", "")
            deal_amount = abs(int(d.get("amount", 0))) if d.get("amount") is not None else 0
            issue_date = d.get("issue_date", "")
            due_date = d.get("due_date", "")

            is_benesse = "ベネッセ" in partner or "benesse" in partner.lower()
            is_amount_match = deal_amount == AMOUNT

            if is_benesse or is_amount_match:
                flag = []
                if is_benesse: flag.append("★ベネッセ")
                if is_amount_match: flag.append("★金額一致")
                print(f"  [{' '.join(flag)}] id={d.get('id')} partner={partner!r} amount={deal_amount} issue={issue_date} due={due_date}")
