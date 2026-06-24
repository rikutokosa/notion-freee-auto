"""
本店CA・PCA両DBに「freee請求書ID」フィールド（number型）を追加するスクリプト
"""
import requests
import json

NOTION_TOKEN = "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"
NOTION_VERSION = "2022-06-28"
NOTION_BASE = "https://api.notion.com/v1"

NOTION_DB_ID_HONTEN = "320a7a34-dbe2-8082-8055-c57f9b8a04bb"
NOTION_DB_ID_PCA    = "32fa7a34-dbe2-8005-ab91-ff33d64506e0"

HEADERS = {
    "Authorization": f"Bearer {NOTION_TOKEN}",
    "Notion-Version": NOTION_VERSION,
    "Content-Type": "application/json",
}

def add_number_field(db_id: str, field_name: str, db_label: str):
    url = f"{NOTION_BASE}/databases/{db_id}"
    payload = {
        "properties": {
            field_name: {
                "number": {
                    "format": "number"
                }
            }
        }
    }
    resp = requests.patch(url, headers=HEADERS, json=payload, timeout=30)
    if resp.status_code == 200:
        print(f"[OK] {db_label}: 「{field_name}」を追加しました")
    else:
        print(f"[ERROR] {db_label}: {resp.status_code} {resp.text[:200]}")

if __name__ == "__main__":
    add_number_field(NOTION_DB_ID_HONTEN, "freee請求書ID", "本店CA")
    add_number_field(NOTION_DB_ID_PCA,    "freee請求書ID", "PCA")
    print("完了")
